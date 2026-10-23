"""Trade chart and recorder contract tests, using paper fixtures only."""
import datetime as dt
import json
from pathlib import Path

import pytest

from mwjournal import Execution, Journal, PriceSample
from mwjournal.chart import chart_window, closing_pairs, minute_candles, protection_segments, trade_chart
from mwjournal.importers import apply_preview, preview_bridge, preview_csv
from mwjournal.metrics import excursions
from mwjournal.matching import match
from mwjournal.model import ENTRY_MISSING, ProtectionEvent, instant


def round_trip(journal):
    journal.ingest([(Execution('motivewave', 'paper', 'ESZ6', 'buy', 2, 5000,
                              '2026-10-07T14:00:23.250Z', execution_id='entry', commission=4), {}),
                    (Execution('motivewave', 'paper', 'ESZ6', 'sell', 1, 5002,
                              '2026-10-07T14:01:37Z', execution_id='partial', commission=2), {}),
                    (Execution('motivewave', 'paper', 'ESZ6', 'sell', 1, 5003,
                              '2026-10-07T14:03:11Z', execution_id='exit', commission=2), {})])
    return journal.trades()[0]


def bar(minute=0, *, source='motivewave-bars:1m', **kwargs):
    return PriceSample('ESZ6', f'2026-10-07T14:{minute:02d}:00Z', source, 5004, 4999, 60,
                       open=5000, close=5002, volume=123, **kwargs)


def seed_preview(path):
    """Reusable, clearly labeled visual-QA data. `path` is a test's own MWJ_HOME, never the user's."""
    journal = Journal(Path(path) / 'trades.sqlite3')
    trade = round_trip(journal)
    journal.annotate(trade.id, {'tags': ['PAPER FIXTURE', 'breakout'], 'planned_stop': 4998, 'planned_target': 5005, 'initial_risk': 200,
                               'notes': 'Synthetic fixture, not an actual account trade.'})
    start = instant('2026-10-07T13:45:00Z')
    samples = []
    for i in range(40):
        open_ = 4999 + i * .1 + (i % 4) * .2
        close = open_ + (.5 if i % 3 else -.35)
        samples.append(PriceSample('ESZ6', (start + dt.timedelta(minutes=i)).isoformat(),
                                   'motivewave-bars:1m', max(open_, close) + .6, min(open_, close) - .6,
                                   60, open=open_, close=close, volume=100 + i * 17))
    journal.ingest_prices(samples)
    journal.record_metric(trade, excursions(trade, samples, samples[0].source))
    return journal


def seed_ticks(journal):
    """The prints the study recorded around seed_preview's trade, which MAE / MFE are measured from (its candles
    only draw the chart, an estimate that never measures a trade). Long 2 from 5000, 1 out at 5002, the other at
    5003: MAE 1 point (4999 with 2 held, $100); MFE 2.5 points (5002.5 with 2 held, $250), which beats the 3.5
    points the one contract left saw at 5003.5 ($175). The 2 points banked by the partial exit are not counted.
    Two prints after the exit are for the runner."""
    trade = journal.trades()[0]
    prints = [PriceSample('ESZ6', f'2026-10-07T14:{at}Z', 'motivewave-ticks', price, price)
              for at, price in (('00:40', 4999), ('01:10', 5002.5), ('02:00', 5001), ('02:30', 4999.5),
                                ('03:00', 5003.5), ('03:30', 5004), ('05:00', 5002))]
    journal.ingest_prices(prints)
    journal.record_metric(trade, excursions(trade, prints, 'motivewave-ticks'))
    return journal


def test_ohlcv_survives_the_bridge_and_sqlite_and_a_revised_candle_replaces_it(tmp_path):
    event = {'type': 'bar', 'provider': 'motivewave-bars:1m', 'symbol': 'ESZ6',
             'ts': '2026-10-07T14:00:00Z', 'resolution_seconds': 60,
             'open': 5000, 'high': 5004, 'low': 4999, 'close': 5002, 'volume': 123}
    journal = Journal(tmp_path / 'journal.sqlite3')
    assert apply_preview(journal, preview_bridge(json.dumps(event)))['prices'] == 1
    assert journal.prices('ESZ6', 'motivewave-bars:1m')[0] == bar()
    revised = preview_bridge(json.dumps({**event, 'close': 5003, 'volume': 125}))
    assert apply_preview(journal, revised)['prices'] == 1
    assert len(journal.prices('ESZ6', 'motivewave-bars:1m')) == 1
    assert journal.prices('ESZ6', 'motivewave-bars:1m')[0].close == 5003


def test_seed_preview_fixture_is_the_journal_the_app_opens(tmp_path, monkeypatch):
    monkeypatch.setenv('MWJ_HOME', str(tmp_path))
    seeded = seed_preview(tmp_path)
    journal = Journal()
    assert journal.path == seeded.path
    assert [m['precision'] for m in journal.metric_options(journal.trades()[0])] == ['estimated-bars']


@pytest.mark.parametrize('fields', [{'open': 5005, 'close': 5002}, {'open': 5000},
                                   {'volume': -1}, {'open': float('nan'), 'close': 5002}])
def test_invalid_candles_are_rejected(fields):
    with pytest.raises(ValueError):
        PriceSample('ESZ6', '2026-10-07T14:00:00Z', 'test', 5004, 4999, 60, **fields)


def test_highlow_only_and_coarse_data_cannot_invent_one_minute_candles():
    samples = [PriceSample('ESZ6', '2026-10-07T14:00:00Z', 'test', 5004, 4999, 60),
               PriceSample('ESZ6', '2026-10-07T14:05:00Z', 'test', 5004, 4999, 300, open=5000, close=5002)]
    data, notes = minute_candles(samples, 'ESZ6', 'test')
    assert not data and any('cannot draw' in n for n in notes)


def test_ticks_aggregate_in_time_order_without_inventing_missing_minutes():
    samples = [PriceSample('ESZ6', f'2026-10-07T14:{m:02d}:{s:02d}Z', 'ticks', p, p, volume=v)
               for m, s, p, v in [(0, 45, 5001, 3), (0, 10, 5000, 2), (0, 20, 4998, 4), (2, 10, 5002, 5)]]
    data, notes = minute_candles(samples, 'ESZ6', 'ticks')
    assert len(data) == 2
    assert data[0] == {'time': int(instant(samples[1].ts).timestamp() // 60) * 60,
                       'open': 5000, 'high': 5001, 'low': 4998, 'close': 5001, 'volume': 9}
    assert any('incomplete' in n for n in notes) and any('Missing minutes' in n for n in notes)


def test_fill_overlays_use_actual_prices_and_utc_fractional_seconds(tmp_path):
    journal = Journal(tmp_path / 'journal.sqlite3')
    trade = round_trip(journal)
    payload = trade_chart(trade, [bar(0), bar(1), bar(3)], 'motivewave-bars:1m', 4998)
    fills = payload['overlays']['fills']
    assert [f['price'] for f in fills] == [5000, 5002, 5003]
    assert [(f['side'], f['qty']) for f in fills] == [('buy', 2), ('sell', 1), ('sell', 1)]   # the chart colours by side
    assert [f['label'] for f in fills] == ['Entry · 2 @ 5000', 'Exit · 1 @ 5002', 'Exit · 1 @ 5003']
    # Scaled out: the 2-lot entry is joined to each exit, one contract each.
    assert payload['overlays']['pairs'] == [{'entry': 0, 'exit': 1, 'qty': 1, 'points': 2},
                                            {'entry': 0, 'exit': 2, 'qty': 1, 'points': 3}]
    assert fills[0]['time'] == instant(trade.opened).timestamp()
    assert fills[0]['time'] % 60 == 23.25
    data = payload['series']['data']
    assert len(data) == 4 and data[2] == {'time': data[1]['time'] + 60}
    assert payload['overlays']['levels'] == [{'from': instant(trade.opened).timestamp(), 'to': instant(trade.closed).timestamp(),
                                              'price': 4998, 'label': 'Stop · R', 'color': '#F05B70', 'style': 'solid',
                                              'full': True}]            # drawn across the whole chart


def execution(id, side, qty, price, second):
    return Execution('motivewave', 'paper', 'ESZ6', side, qty, price, f'2026-10-07T14:{second // 60:02d}:{second % 60:02d}Z',
                     execution_id=id), {}


def test_entries_and_exits_are_paired_first_in_first_out_as_a_platform_joins_them(tmp_path):
    journal = Journal(tmp_path / 'journal.sqlite3')
    # Scaled in: two buys, a 3-lot then a 1-lot, out in two sells of 2. The first sell takes 2 of the oldest entry;
    # the second takes its last one and the later buy, one pair each.
    journal.ingest([execution('a', 'buy', 3, 5000, 10), execution('b', 'buy', 1, 4996, 40),
                    execution('c', 'sell', 2, 5004, 70), execution('d', 'sell', 2, 4998, 100)])
    pairs = trade_chart(journal.trades()[0], [], 'none')['overlays']['pairs']
    assert pairs == [{'entry': 0, 'exit': 2, 'qty': 2, 'points': 4}, {'entry': 0, 'exit': 3, 'qty': 1, 'points': -2},
                     {'entry': 1, 'exit': 3, 'qty': 1, 'points': 2}]


def test_a_reversal_joins_each_trade_to_its_own_part_of_the_reversing_fill(tmp_path):
    """Long 1, sell 2 (out of the long and into a short), buy 1 back: two trades, each with one pair, a short's
    points counted the short way."""
    journal = Journal(tmp_path / 'journal.sqlite3')
    journal.ingest([execution('long', 'buy', 1, 5000, 10), execution('reverse', 'sell', 2, 5002, 70),
                    execution('cover', 'buy', 1, 5003, 130)])
    long, short = sorted(journal.trades(), key=lambda t: t.opened)
    for trade, sides, points in ((long, ['buy', 'sell'], 2), (short, ['sell', 'buy'], -1)):
        overlays = trade_chart(trade, [], 'none')['overlays']
        assert [(f['side'], f['qty']) for f in overlays['fills']] == [(sides[0], 1), (sides[1], 1)]
        assert overlays['pairs'] == [{'entry': 0, 'exit': 1, 'qty': 1, 'points': points}]


def test_an_exit_without_its_entry_or_a_fill_without_a_price_is_joined_to_nothing():
    def leg(role, side, qty, price):
        return {'role': role, 'side': side, 'qty': qty, 'price': price}
    # Nothing is open before the first exit; the first entry has no price, yet it is still the one the next exit
    # closes; the last contract is still open.
    fills = [leg('exit', 'sell', 1, 5001), leg('entry', 'buy', 1, 0), leg('entry', 'buy', 2, 4999),
             leg('exit', 'sell', 2, 5000)]
    assert closing_pairs(fills) == [{'entry': 2, 'exit': 3, 'qty': 1, 'points': 1}]


def test_a_position_whose_entry_the_platform_lost_has_no_entry_exit_lines():
    """Kept of one platform position: buy 1 at 100, sell 1 at 104, sell 2 at 98, so 2 or more bought earlier are
    gone. First in first out, the first sell closed one of those and the kept buy went out at 98, a loss: a line
    from the buy to the first sell would show a win that never happened. What each exit closed is not known."""
    executions = [Execution('motivewave-native:sim', 'paper', 'NQZ6', side, qty, price, f'2026-10-07T14:0{minute}:00Z',
                            execution_id=id, native_position_id='P1', native_entry=entry)
                  for id, side, qty, price, minute, entry in (('b', 'buy', 1, 100, 1, True),
                                                              ('s1', 'sell', 1, 104, 2, False),
                                                              ('s2', 'sell', 2, 98, 3, False))]
    trade, = match(executions)
    assert ENTRY_MISSING in trade.flags and len(trade.fills) == 3
    overlays = trade_chart(trade, [], 'none')['overlays']
    assert len(overlays['fills']) == 3 and overlays['pairs'] == []


def test_dst_repeated_clock_hour_stays_two_distinct_utc_minutes():
    samples = [PriceSample('ESZ6', t, 'test', 5004, 4999, 60, open=5000, close=5002)
               for t in ('2026-11-01T01:30:00-04:00', '2026-11-01T01:30:00-05:00')]
    data, _ = minute_candles(samples, 'ESZ6', 'test')
    assert data[1]['time'] - data[0]['time'] == 3600


def test_protection_order_changes_cancellations_and_strict_trade_link(tmp_path):
    journal = Journal(tmp_path / 'journal.sqlite3')
    trade = round_trip(journal)
    events = [ProtectionEvent('motivewave', 'paper', 'ESZ6', 'stop-1', stamp, 'stop', state, price,
                               entry_execution_id='entry') for stamp, state, price in
              [('2026-10-07T13:59:59Z', 'working', 4998),
               ('2026-10-07T14:01:00Z', 'working', 5000),
               ('2026-10-07T14:02:00Z', 'cancelled', None)]]
    other = ProtectionEvent('motivewave', 'another-account', 'ESZ6', 'stop-1', events[0].ts,
                            'stop', 'working', 4900, trade_id=trade.id)
    assert journal.ingest_protection(events + [other]) == 4
    assert journal.ingest_protection(events) == 0
    linked = journal.protection_events(trade)
    assert linked == events
    # Without a stop for R, each stop order is drawn dashed from the moment the stop settled, ten seconds in.
    segments = protection_segments(trade, linked)
    assert [(s['price'], s['style']) for s in segments] == [(4998, 'dashed'), (5000, 'dashed')]
    assert segments[0]['from'] == instant(trade.opened).timestamp() + 10
    assert segments[-1]['to'] == instant('2026-10-07T14:02:00Z').timestamp()
    # The stop R uses is solid across the trade; the order at that price is not drawn twice.
    segments = protection_segments(trade, linked, 4998)
    assert [(s['price'], s['style']) for s in segments] == [(4998, 'solid'), (5000, 'dashed')]
    assert segments[0]['from'] == instant(trade.opened).timestamp()


def test_unlinked_protective_order_is_an_import_error():
    event = {'type': 'protection', 'provider': 'motivewave', 'account': 'paper', 'symbol': 'ESZ6',
             'order_id': 's', 'ts': '2026-10-07T14:00:00Z', 'role': 'stop', 'state': 'working', 'price': 4998}
    assert preview_bridge(json.dumps(event)).errors
    event['entry_execution_id'] = 'entry'
    preview = preview_bridge(json.dumps(event))
    assert not preview.errors and preview.count == 1


def test_motivewave_style_execution_csv_supports_separate_date_and_time():
    preview = preview_csv('Account,Instrument,Side,Quantity,Avg Price,Date,Time,Trade ID,Commission\n'
                          'paper,ESZ6,Buy,1,5000,2026-10-07,10:00:23,entry,2\n', provider='motivewave')
    assert not preview.errors
    fill = preview.fills[0][0]
    assert fill.ts == '2026-10-07T14:00:23+00:00' and fill.execution_id == 'entry'


def test_a_time_of_day_takes_the_rows_date_whatever_the_columns_are_called():
    # Mapped by hand under Column Mapping: neither column has a name the importer knows.
    text = 'Acct,Sym,B/S,Filled,Px,Trade Date,Exec Time\npaper,ESZ6,Buy,1,5000,2026-10-07,{}\n'
    mapping = {'account': 'Acct', 'symbol': 'Sym', 'side': 'B/S', 'qty': 'Filled', 'price': 'Px',
               'date': 'Trade Date', 'ts': 'Exec Time'}
    for time, utc in (('10:00:23', '14:00:23'), ('10:00', '14:00:00'), ('1:05:09 PM', '17:05:09'),
                      ('10:00:23.250', '14:00:23.250000')):
        preview = preview_csv(text.format(time), provider='broker', mapping=mapping)
        assert not preview.errors and preview.fills[0][0].ts == f'2026-10-07T{utc}+00:00', (time, preview.errors)
    # A full timestamp in the Time column is used as it is, beside any Date column.
    preview = preview_csv(text.format('2026-10-07 10:00:23'), provider='broker', mapping=mapping)
    assert not preview.errors and preview.fills[0][0].ts == '2026-10-07T14:00:23+00:00'


def test_chart_window_is_bounded_for_long_open_positions(tmp_path):
    trade = round_trip(Journal(tmp_path / 'journal.sqlite3'))
    trade.closed = None
    start, end = chart_window(trade, 30, now=instant('2027-10-07T14:00:00Z'))
    assert (end - start).total_seconds() == 10000 * 60
    # An open replay trade's chart ends at its replay clock: the replay's later minutes are stored already.
    start, end = chart_window(trade, 30, now=instant(trade.opened) + dt.timedelta(minutes=5))
    assert end == instant(trade.opened) + dt.timedelta(minutes=5)


def test_historical_capture_gap_uses_price_window_not_export_wall_clock(tmp_path):
    journal = Journal(tmp_path / 'journal.sqlite3')
    trade = round_trip(journal)
    gap = {'type': 'gap', 'provider': 'motivewave-bars:1m', 'ts': '2026-10-08T18:00:00Z',
           'start_ts': '2026-10-07T13:59:00Z', 'end_ts': '2026-10-07T14:02:00Z', 'dropped': 3}
    assert apply_preview(journal, preview_bridge(json.dumps(gap)))['gaps'] == 1
    assert journal.capture_gaps('motivewave-bars:1m', start=trade.opened, end=trade.closed)
    assert not journal.capture_gaps('motivewave-bars:1m', start='2026-10-08T00:00:00Z')
