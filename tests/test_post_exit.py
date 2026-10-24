"""After Exit on invented prices: the 15 minutes after an exit, held until the trade's stop or else breakeven, from
recorded ticks only, and the one sentence the trade shows. No platform access and no trade recommendations."""
import dataclasses
import datetime as dt

import pytest

import after_exit
from mwjournal import Execution, Journal, PriceSample, merging
from mwjournal.model import EXIT_MISSING, instant
from mwjournal.post_exit import AFTER_EXIT_MINUTES, best_post_exit, post_exit, resolve_stop, runner_groups

START = '2026-10-08T14:00:00+00:00'
EXIT = '2026-10-08T14:01:00+00:00'
LATER = instant(EXIT) + dt.timedelta(minutes=30)


def after(seconds, exit=EXIT):
    return (instant(exit) + dt.timedelta(seconds=seconds)).isoformat()


def trade(tmp_path, side='buy', partial=False, replay=False, exit_price=5002, start=START, exit=EXIT):
    """Long (or short) 1 ES from 5000, out at `exit_price`: $50 a point, no stop order."""
    j = Journal(tmp_path / ('replay.sqlite3' if replay else 'live.sqlite3'), dataset='replay' if replay else 'live')
    provider = 'motivewave-native:w:replay:run-a' if replay else 'test'
    opposite = 'sell' if side == 'buy' else 'buy'
    fills = [Execution(provider, 'paper', 'ESZ6', side, 2 if partial else 1, 5000, start, execution_id='a', point_value=50)]
    if partial:
        fills.append(Execution(provider, 'paper', 'ESZ6', opposite, 1, 5001, after(-30, exit), execution_id='partial',
                               point_value=50))
    fills.append(Execution(provider, 'paper', 'ESZ6', opposite, 1, exit_price, exit, execution_id='exit', point_value=50))
    j.ingest([(f, {'mode': 'replay'} if replay else {}) for f in fills])
    return j, j.trades()[0]


def prints(pairs, source='ticks', exit=EXIT):
    return [PriceSample('ESZ6', after(t, exit), source, price, price) for t, price in pairs]


def ticks(source='prices'):
    """Up 4 points two minutes after the exit, never back to 5000, and a print in the 15th minute's last second."""
    return prints([(1, 5002), (60, 5001), (120, 5006), (900, 5003)], source)


def test_one_horizon_the_peak_and_the_move_against_on_the_final_exit_quantity(tmp_path):
    _, t = trade(tmp_path)
    assert AFTER_EXIT_MINUTES == 15
    result = post_exit(t, ticks(), 'prices', 4990, now=LATER, initial_risk=100)
    assert (result['extra_mfe'], result['extra_mae'], result['extra_mfe_r'], result['extra_mae_r']) == (200, 50, 2, .5)
    assert result['coverage'] == 'window-observed' and result['termination'] == 'still-running-at-cutoff'
    assert result['precision'] == 'observed-ticks' and result['observations'] == 4 and not result['stop_touched']
    assert post_exit(t, ticks(), 'prices', 4990, now=LATER)['extra_mfe_r'] is None
    # A print after the 15 minutes is never counted.
    later = post_exit(t, [*ticks(), *prints([(901, 5100)], 'prices')], 'prices', 4990, now=LATER)
    assert later['extra_mfe'] == 200 and later['observations'] == 4


def test_short_direction_and_final_partial_quantity(tmp_path):
    _, t = trade(tmp_path, 'sell', partial=True)
    samples = prints([(1, 5002), (60, 5003), (300, 4998), (900, 5000)], 'prices')
    result = post_exit(t, samples, 'prices', 5010, now=LATER)
    assert result['exit_qty'] == 1 and t.entry_qty == 2
    assert result['extra_mfe'] == 200 and result['extra_mae'] == 50


def test_candles_and_simulated_replay_prints_are_never_measured(tmp_path):
    _, t = trade(tmp_path)
    bars = [PriceSample('ESZ6', after(60), 'bars', 5004, 5001, 60, open=5002, close=5003)]
    result = post_exit(t, bars, 'bars', 4990, now=LATER)
    assert result['precision'] == 'estimated-bars' and result['extra_mfe'] is None
    journal, t = trade(tmp_path, replay=True)
    correct = 'motivewave-replay:run-a:simulated'
    journal.ingest_prices(ticks(correct))
    simulated = post_exit(t, ticks(correct), correct, 4990, now=LATER)
    assert simulated['precision'] == 'replay-simulated' and simulated['extra_mfe'] is None
    wrong = post_exit(t, ticks('motivewave-replay:run-b:ticks'), 'motivewave-replay:run-b:ticks', 4990, now=LATER)
    assert wrong['extra_mfe'] is None and wrong['precision'] == 'pending'
    assert best_post_exit(journal, t)['precision'] == 'pending'


def test_minutes_not_all_recorded_are_never_averaged_in(tmp_path):
    journal, t = trade(tmp_path)
    missing = post_exit(t, ticks()[:-1], 'prices', 4990, now=LATER)
    assert missing['coverage'] == 'partial-window' and missing['termination'] == 'awaiting-prices'
    assert not runner_groups([{'tags': ['A+'], **missing}])
    none = post_exit(t, [], 'prices', 4990, now=LATER)
    assert none['extra_mfe'] is None and none['termination'] == 'unavailable'
    # A tick within a minute of the exit, and of the end of the 15 minutes, counts as recorded from and to there.
    def coverage(*seconds):
        return post_exit(t, prints([(s, 5002) for s in seconds], 'prices'), 'prices', 4990, now=LATER)['coverage']
    assert coverage(60, 840) == 'window-observed'
    assert coverage(61, 900) == coverage(1, 839) == 'partial-window'
    # Ticks with a gap the study reported never cover the minutes.
    journal.ingest_prices(ticks('motivewave-ticks'))
    assert best_post_exit(journal, t)['coverage'] == 'window-observed'
    journal.ingest_gaps([{'provider': 'motivewave-ticks', 'symbol': 'ESZ6', 'dropped': 3,
                          'start_ts': after(30), 'end_ts': after(31), 'ts': after(31)}])
    assert best_post_exit(journal, t)['coverage'] == 'partial-window'


def test_minutes_still_running_are_not_over_until_the_stop_is_touched(tmp_path):
    _, t = trade(tmp_path)
    running = post_exit(t, ticks()[:2], 'prices', 4990, now=instant(after(90)))
    assert running['termination'] == 'not-over' and running['coverage'] == 'partial-window'
    assert post_exit(t, [], 'prices', 4990, now=instant(after(90)))['termination'] == 'not-over'
    stopped = post_exit(t, prints([(1, 5002), (60, 4990)], 'prices'), 'prices', 4990, now=instant(after(90)))
    assert stopped['termination'] == 'stop-touch' and stopped['coverage'] == 'stop-observed'


def test_stop_touch_ends_tracking_before_a_later_peak(tmp_path):
    _, t = trade(tmp_path)
    samples = prints([(1, 5002), (60, 5006), (120, 4998), (180, 5100), (900, 5003)])
    result = post_exit(t, samples, 'ticks', 4998, now=LATER)
    assert result['extra_mfe'] == 200 and result['extra_mae'] == 200
    assert result['stop_touched'] and result['stop_ts'] == after(120) and result['observations'] == 3
    assert result['coverage'] == 'stop-observed' and result['termination'] == 'stop-touch'


def test_an_exit_at_or_past_the_stop_has_nothing_left_to_hold(tmp_path):
    _, t = trade(tmp_path)
    for stop in (5002, 5003):
        result = post_exit(t, ticks(), 'prices', stop, now=LATER)
        assert result['termination'] == 'stop-already-touched-at-exit' and result['extra_mfe'] == 0


def test_one_stop_rule_the_stop_r_uses_else_breakeven(tmp_path):
    journal, t = trade(tmp_path)
    samples = prints([(1, 5002), (60, 5000), (120, 5008), (900, 5003)])
    journal.ingest_prices(samples)
    assert resolve_stop(journal, t) == (5000, 'breakeven')            # no stop: the average entry
    breakeven = best_post_exit(journal, t)
    assert breakeven['held_to'] == 'breakeven' and breakeven['stop_price'] == 5000
    assert breakeven['extra_mfe'] == 0 and breakeven['stop_touched']
    journal.annotate(t.id, {'planned_stop': 4998})
    assert resolve_stop(journal, t) == (4998, 'stop')
    stop = best_post_exit(journal, t, 100)
    assert stop['held_to'] == 'stop' and stop['extra_mfe'] == 300 and not stop['stop_touched']
    assert stop['extra_mfe_r'] == 3 and stop['termination'] == 'still-running-at-cutoff'


def test_groups_count_every_trade_once_per_tag_and_need_one_currency(tmp_path):
    _, t = trade(tmp_path)
    result = post_exit(t, ticks(), 'prices', 5000, now=LATER, initial_risk=100)
    base = {'tags': ['A+', 'A+'], 'currency': 'USD', **result}
    groups = runner_groups([base, {**base, 'tags': []}])
    assert [(g['group'], g['trades'], g['additional_1R_reached_pct']) for g in groups] == [('A+', 1, 100), ('Untagged', 1, 100)]
    with pytest.raises(ValueError, match='one currency'):
        runner_groups([base, {**base, 'currency': 'EUR'}])
    categories = runner_groups([{**base, 'tag_categories': ['Orderflow', 'Psychology', 'Orderflow']},
                                {**base, 'tag_categories': []}], field='tag_categories')
    assert [(g['group'], g['trades']) for g in categories] == [('Orderflow', 1), ('Psychology', 1), ('Uncategorized', 1)]


# ---------------------------------------------------------------- the sentence on the trade

OPENING = 'In the 15 minutes after you exited, price'


def sentence(tmp_path, pairs, *, stop=None, unit='points', exit_price=5002, start=START, exit=EXIT):
    journal, t = trade(tmp_path, exit_price=exit_price, start=start, exit=exit)
    journal.ingest_prices(prints(pairs, 'motivewave-ticks', exit))
    if stop is not None:
        journal.annotate(t.id, {'planned_stop': stop})
    return after_exit.line(journal, t, unit)


def test_each_sentence_held_to_your_stop(tmp_path):
    up, touched = [(1, 5002), (120, 5006), (900, 5003)], [(1, 5002), (60, 5006), (120, 4998), (900, 5003)]
    assert sentence(tmp_path / 'a', up, stop=4998) == (
        f'{OPENING} went 4.00 pts further your way and did not reach your stop.')
    assert sentence(tmp_path / 'b', touched, stop=4998) == (
        f'{OPENING} went 4.00 pts further your way before reaching your stop.')
    assert sentence(tmp_path / 'c', [(1, 5001), (60, 4998), (900, 5003)], stop=4998) == (
        f'{OPENING} reached your stop without going any further your way.')
    assert sentence(tmp_path / 'd', [(1, 5001), (900, 5001)], stop=4998) == (
        f'{OPENING} went no further your way and did not reach your stop.')
    assert sentence(tmp_path / 'e', up, stop=5003) == 'You exited at or past your stop, so there was nothing left to hold.'


def test_without_a_stop_the_sentence_says_breakeven(tmp_path):
    assert sentence(tmp_path / 'a', [(1, 5002), (120, 5006), (900, 5003)]) == (
        f'{OPENING} went 4.00 pts further your way and did not reach breakeven.')
    assert sentence(tmp_path / 'b', [(1, 5002), (120, 5006), (300, 5000), (900, 5003)]) == (
        f'{OPENING} went 4.00 pts further your way before reaching breakeven.')
    assert sentence(tmp_path / 'c', [(1, 4999), (900, 4999)], exit_price=4999) == (
        'You exited at or past breakeven, so there was nothing left to hold.')


def test_the_sentence_uses_the_mae_unit_and_r_needs_a_stop(tmp_path):
    up = [(1, 5002), (120, 5006), (900, 5003)]
    assert sentence(tmp_path / 'a', up, stop=4998, unit='currency') == (
        f'{OPENING} went $200.00 further your way and did not reach your stop.')
    assert sentence(tmp_path / 'b', up, stop=4998, unit='ticks') == (
        f'{OPENING} went 16.00 ticks further your way and did not reach your stop.')
    assert sentence(tmp_path / 'c', up, stop=4998, unit='r') == (                 # $100 at risk to 4998
        f'{OPENING} went 2.00R further your way and did not reach your stop.')
    # In R without a stop there is no R amount: the MAE tile's '—' and its reason, never another unit.
    assert sentence(tmp_path / 'd', up, unit='r') == (
        f'{OPENING} went — further your way and did not reach breakeven. '
        'No stop order 10 seconds after entry. Type your stop to get R.')


def test_minutes_not_over_or_not_recorded_say_so(tmp_path):
    now = dt.datetime.now(dt.timezone.utc).replace(microsecond=0)
    start, exit = (now - dt.timedelta(minutes=3)).isoformat(), (now - dt.timedelta(minutes=1)).isoformat()
    assert sentence(tmp_path / 'a', [(1, 5002), (20, 5004)], stop=4998, start=start, exit=exit) == (
        'The 15 minutes after your exit are not over yet.')
    fix = 'recorded in the 15 minutes after your exit. Keep the study on a chart of this contract while you trade.'
    assert sentence(tmp_path / 'b', [], stop=4998) == 'No prices were ' + fix
    assert sentence(tmp_path / 'c', [(1, 5002), (120, 5006)], stop=4998) == 'Not every price was ' + fix
    # A replay trade's minutes go by as the replay plays them.
    journal, t = trade(tmp_path / 'd', replay=True)
    journal.ingest_prices(prints([(1, 5002), (120, 5006)], 'motivewave-replay:run-a:ticks'))
    assert after_exit.line(journal, t, 'points') == 'The 15 minutes after your exit are not over yet.'
    journal.ingest_prices(prints([(900, 5003)], 'motivewave-replay:run-a:ticks'))
    assert after_exit.line(journal, t, 'points') == f'{OPENING} went 4.00 pts further your way and did not reach breakeven.'


def test_nothing_for_an_open_incomplete_or_merged_trade(tmp_path):
    journal = Journal(tmp_path / 'live.sqlite3')

    def fill(id, side, price, seconds):
        return Execution('test', 'paper', 'ESZ6', side, 1, price, after(seconds), execution_id=id, point_value=50), {}
    journal.ingest([fill('a', 'buy', 5000, -60)])
    assert journal.trades()[0].status == 'open' and after_exit.line(journal, journal.trades()[0], 'points') is None
    journal.ingest([fill('b', 'sell', 5002, 0), fill('c', 'buy', 5001, 120), fill('d', 'sell', 5003, 180)])
    journal.ingest_prices(prints([(1, 5002), (900, 5003)], 'motivewave-ticks'))
    first, second = sorted(journal.trades(), key=lambda t: t.opened)
    assert after_exit.line(journal, first, 'points') == f'{OPENING} went 1.00 pts further your way and did not reach breakeven.'
    assert after_exit.line(journal, dataclasses.replace(first, flags=[EXIT_MISSING]), 'points') is None
    merged = merging.create(journal, [first.id, second.id])
    view = merging.ReviewJournal(journal)
    merged = next(t for t in view.trades() if t.id == merged.id)
    assert merged.merge_leg_ids and after_exit.line(view, merged, 'points') is None
