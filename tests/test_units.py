"""Excursion units against known paths: quantity, scale changes, metadata and absent data."""
from concurrent.futures import ThreadPoolExecutor
import threading

import pytest

from mwjournal import Execution, Journal, PriceSample
from mwjournal.matching import match
from mwjournal.metrics import excursions
from mwjournal import units


def fill(id, side, qty, price, minute=0, symbol='ESZ6'):
    return Execution('test', 'paper', symbol, side, qty, price, f'2026-10-07T14:{minute:02d}:00Z', execution_id=id)


def tick(minute, price, symbol='ESZ6', **kw):
    return PriceSample(symbol, f'2026-10-07T14:{minute:02d}:30Z', 'test', price, price, **kw)


@pytest.mark.parametrize('qty', [1, 2, 10])
def test_points_are_not_multiplied_by_trade_size(qty):
    trade = match([fill('in', 'buy', qty, 100), fill('out', 'sell', qty, 102, 3)])[0]
    m = excursions(trade, [tick(0, 98), tick(1, 105)], 'test')
    assert (m['mae'], m['mfe']) == (100 * qty, 250 * qty)
    assert (m['mae_points'], m['mfe_points']) == (2, 5)
    assert units.value(m, 'mfe', 'points') == 5
    assert units.value(m, 'mfe', 'ticks', tick=.25) == 20
    assert units.value(m, 'mfe', 'r', risk=100 * qty) == 2.5


def test_short_scale_in_and_partial_exits_use_the_held_average_not_final_size():
    trade = match([fill('a', 'sell', 2, 100), fill('b', 'buy', 1, 96, 1),
                   fill('c', 'sell', 1, 104, 2), fill('d', 'buy', 2, 101, 4)])[0]
    m = excursions(trade, [tick(0, 102), tick(1, 94), tick(2, 99), tick(3, 107)], 'test')
    assert m['mae_points'] == 5  # after scale-in: average 102, adverse price 107
    assert m['mae'] == 500      # 5 points on the 2 held after the scale-in; 0.4.7 netted the banked 4 and said 300
    # 4 points on 2 at the partial exit beats 6 on the 1 left ($400 against $300, $50/point, nothing banked added),
    # so the points are that moment's 4, not the 6 at 94.
    assert (m['mfe'], m['mfe_points']) == (400, 4)
    assert m['path'][2]['point_high'] == 3
    assert m['path'][2]['average_cost'] == 102


def test_points_come_from_the_moment_of_the_dollars_so_switching_units_only_rescales():
    """Long 2 NQ at 100, a dip to 97, sell 1 at 102, then the runner dips to 96: the worst moment is the dip with
    2 held, $120, so MAE is 3 points there, not the runner's 4. Every unit is that one moment."""
    trade = match([fill('a', 'buy', 2, 100, symbol='NQZ6'), fill('b', 'sell', 1, 102, 2, symbol='NQZ6'),
                   fill('c', 'sell', 1, 101, 4, symbol='NQZ6')])[0]
    m = excursions(trade, [tick(1, 97, 'NQZ6'), tick(2, 103, 'NQZ6'), tick(3, 96, 'NQZ6')], 'test')
    assert (m['mae'], m['mae_points']) == (120, 3)
    # MFE: 2 points on the 2 held at the partial exit ($80) beats 3 on the 1 left at 103 ($60).
    assert (m['mfe'], m['mfe_points']) == (80, 2)
    held_then = 2
    assert m['mfe_points'] == m['mfe'] / (held_then * trade.point_value)
    assert units.value(m, 'mae', 'ticks', tick=.25) == 12 and units.value(m, 'mae', 'r', risk=60) == 2
    # Two moments worth the same dollars: the larger move counts. A dip of 1 with 2 held, then of 2 with 1 held.
    even = match([fill('a', 'buy', 2, 100, symbol='NQZ6'), fill('b', 'sell', 1, 101, 2, symbol='NQZ6'),
                  fill('c', 'sell', 1, 100, 4, symbol='NQZ6')])[0]
    m = excursions(even, [tick(1, 99, 'NQZ6'), tick(3, 98, 'NQZ6')], 'test')
    assert (m['mae'], m['mae_points']) == (40, 2)


def test_exit_fills_mark_points_but_missing_history_stays_unavailable():
    trade = match([fill('a', 'buy', 2, 100), fill('b', 'sell', 2, 110, 2)])[0]
    m = excursions(trade, [tick(0, 101)], 'test')
    assert m['mfe_points'] == 10
    assert m['mfe'] == 1000
    absent = excursions(trade, [], 'test')
    assert absent['mfe_points'] is None and absent['mfe'] is None


def test_point_compaction_preserves_extrema_and_quotes_play_no_part():
    trade = match([fill('a', 'buy', 2, 100), fill('b', 'sell', 2, 102, 3)])[0]
    samples = [tick(0, 99.75, bid=99.5, ask=100), tick(1, 102.75, bid=102.5, ask=103)]
    full = excursions(trade, samples, 'test', max_path_points=None)
    compact = excursions(trade, samples, 'test', max_path_points=1)
    assert full['mae_points'] == compact['mae_points'] == .25
    assert full['mfe_points'] == compact['mfe_points'] == 2.75
    assert compact['path'][0]['point_low'] == -.25
    assert compact['path'][0]['point_high'] == 2.75


def test_points_work_without_cash_multiplier_and_never_invent_dollars():
    trade = match([fill('a', 'buy', 2, 100, symbol='UNKNOWN'), fill('b', 'sell', 2, 102, 3, symbol='UNKNOWN')])[0]
    m = excursions(trade, [tick(0, 99, 'UNKNOWN'), tick(1, 103, 'UNKNOWN')], 'test', max_path_points=1)
    assert m['mae'] is None and m['mfe'] is None
    assert (m['mae_points'], m['mfe_points']) == (1, 3)
    assert all(p['high'] is None and p['low'] is None for p in m['path'])
    assert units.value(m, 'mfe', 'r', risk=100) is None


def test_ticks_and_r_require_metadata_and_do_not_turn_missing_into_zero():
    m = {'mae': 0, 'mfe': 100, 'mae_points': 0, 'mfe_points': 2}
    for risk in (None, 0, -1, float('nan'), float('inf'), True):
        assert units.value(m, 'mfe', 'r', risk=risk) is None
    for tick_size in (None, 0, -1, float('nan'), True):
        assert units.value(m, 'mfe', 'ticks', tick=tick_size) is None
    assert units.value({'mae': 0}, 'mae', 'points') is None
    assert units.value(m, 'mae', 'ticks', tick=.25) == 0
    assert units.path_value({'point_high': 2}, 'high', 'ticks', tick=.25) == 8
    assert units.path_value({'high': 100}, 'high', 'r', risk=50) == 2
    assert units.runner_value(100, 'points', multiplier=20, quantity=2) == 2.5
    assert units.runner_value(100, 'ticks', multiplier=20, quantity=2, tick=.25) == 10


def test_known_tick_sizes_do_not_guess_other_products_and_overrides_are_exact():
    for symbol in ('ESZ6', 'MESM7', '/NQ', 'MNQZ26', 'NQZ6.CME', 'NQ=F'):
        assert units.tick_size(symbol) == .25
    for symbol in ('CLZ6', 'SPY', 'NQZ6-C-20000', 'UNKNOWN'):
        assert units.tick_size(symbol) is None
    assert units.tick_size('CLZ6', {'CLZ6': .01}) == .01
    assert units.tick_size('CLH7', {'CLZ6': .01}) is None


def test_preferences_persist_independently_and_concurrent_changes_merge(tmp_path):
    live = Journal(tmp_path / 'live.db')
    replay = Journal(tmp_path / 'replay.db', dataset='replay')
    assert live.display_settings()['excursion_unit'] == 'currency'
    barrier = threading.Barrier(2)
    def set_unit():
        barrier.wait()
        live.set_excursion_unit('ticks')
    def set_tick():
        barrier.wait()
        live.set_tick_size('custom', .01)
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(set_unit), pool.submit(set_tick)]
        for f in futures:
            f.result()
    assert Journal(live.path).display_settings() == {'excursion_unit': 'ticks', 'tick_sizes': {'CUSTOM': .01}}
    assert replay.display_settings() == {'excursion_unit': 'currency', 'tick_sizes': {}}
    with pytest.raises(ValueError):
        live.set_excursion_unit('made-up')
    with pytest.raises(ValueError):
        live.set_tick_size('CUSTOM', 0)
