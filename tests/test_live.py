"""An open trade follows the market: the watcher measures it as prices arrive, its result is shown at the
newest price it was measured with, and its chart draws the minute in progress from the study's prints before
the platform's candle for that minute exists. All of it stays cheap enough to do every second."""
import dataclasses
import json
import types

from mwjournal import Execution, Journal, PriceSample
from mwjournal import journals
from mwjournal.chart import trade_chart
from mwjournal.matching import match
from mwjournal.metrics import excursions
from mwjournal import sync
from mwjournal.sync import POLL_SECONDS, SyncWorker


def fill(side, id, price, ts, symbol='NQZ6', account='SIM'):
    return {'version': 1, 'type': 'execution', 'mode': 'live', 'provider': 'motivewave-native:ws:live',
            'account': account, 'symbol': symbol, 'side': side, 'qty': 1, 'price': price, 'ts': ts,
            'execution_id': id, 'order_id': 'o-' + id, 'commission': 2.0, 'point_value': 20}


def tick(price, ts, symbol='NQ'):
    return {'version': 1, 'type': 'tick', 'mode': 'live', 'provider': 'motivewave-ticks', 'symbol': symbol,
            'price': price, 'ts': ts, 'proxy': False}


def write(path, events):
    with path.open('a', encoding='utf-8', newline='') as fh:
        fh.write(''.join(json.dumps(event) + '\n' for event in events))


def watched(tmp_path):
    journal = Journal(tmp_path / 'trades.sqlite3')
    bridge = tmp_path / 'motivewave.ndjson'
    journal.put('profiles', 'native', {'mode': 'bridge', 'path': str(bridge), 'enabled': True})
    return journal, bridge


def test_an_open_trade_is_measured_again_as_prices_arrive(tmp_path):
    journal, bridge = watched(tmp_path)
    write(bridge, [fill('buy', 'in', 20000, '2026-10-07T14:00:00Z'), tick(19995, '2026-10-07T14:00:01Z')])
    worker = SyncWorker(journal)
    worker.poll()
    trade, = journal.trades()
    metric = journal.best_metric(trade)
    assert not trade.closed and metric['mae_points'] == 5
    # The newest price measured, which the open result is shown at: -$100 and the $2 entry fee.
    assert (metric['last_price'], metric['last_ts']) == (19995, '2026-10-07T14:00:01+00:00')
    assert trade.net_at(metric['last_price']) == -102
    # The next poll measures it again: a measurement this small leaves no wait.
    write(bridge, [tick(20030, '2026-10-07T14:00:02Z')])
    worker.poll()
    metric = journal.best_metric(trade)
    assert (metric['mae_points'], metric['mfe_points'], metric['observations'], metric['last_price']) == (5, 30, 2, 20030)
    assert POLL_SECONDS <= 1


def test_new_prices_alone_wait_as_long_as_the_last_measurement_took_and_fills_never_wait(tmp_path, monkeypatch):
    journal, bridge = watched(tmp_path)
    write(bridge, [fill('buy', 'in', 20000, '2026-10-07T14:00:00Z'), tick(19995, '2026-10-07T14:00:01Z')])
    worker = SyncWorker(journal)
    worker.poll()
    trade, = journal.trades()
    clock = [100.0]
    monkeypatch.setattr(sync.time, 'monotonic', lambda: clock[0])
    worker.measure_after = 103.0                      # as if the last measurement had taken three seconds
    write(bridge, [tick(19990, '2026-10-07T14:00:02Z')])
    worker.poll()
    assert journal.best_metric(trade)['observations'] == 1            # read, not yet measured
    clock[0] = 103.5
    worker.poll()
    assert journal.best_metric(trade)['observations'] == 2            # measured, with the price that waited
    worker.measure_after = 200.0
    write(bridge, [fill('sell', 'out', 19990, '2026-10-07T14:00:03Z')])
    worker.poll()
    trade, = journal.trades()
    assert trade.closed and journal.best_metric(trade)['observations'] == 2
    assert worker.measure_after == 0                  # a pass for fills holds back no later prices


def test_the_watcher_builds_its_trades_again_only_when_fills_or_multipliers_change(tmp_path, monkeypatch):
    journal, bridge = watched(tmp_path)
    write(bridge, [fill('buy', 'in', 20000, '2026-10-07T14:00:00Z')])
    worker = SyncWorker(journal)
    worker.poll()
    built = []
    original = Journal.trades
    monkeypatch.setattr(Journal, 'trades', lambda self: built.append(1) or original(self))
    for second in range(1, 4):
        write(bridge, [tick(20000 + second, f'2026-10-07T14:00:0{second}Z')])
        worker.poll()
    assert built == [] and journal.best_metric(original(journal)[0])['observations'] == 3
    journal.set_instrument('NQZ6', 20)                # set in the app, which the watcher is not told about
    write(bridge, [tick(20009, '2026-10-07T14:00:05Z')])
    worker.poll()
    assert len(built) == 1
    write(bridge, [fill('sell', 'out', 20009, '2026-10-07T14:00:06Z')])
    worker.poll()
    assert len(built) == 2 and original(journal)[0].closed
    # A fill described again in the app, to a payload of the same length, is a change too.
    out = next(e for e in journal.executions() if e.execution_id == 'out')
    journal.ingest([(dataclasses.replace(out, quality='aaaa'), {})])
    worker.poll()
    journal.ingest([(dataclasses.replace(out, quality='bbbb'), {})])
    worker.poll()
    assert len(built) == 4


def test_the_result_of_an_open_trade_at_a_price():
    long, = match([Execution('t', 'a', 'NQZ6', 'buy', 2, 20000, '2026-10-07T14:00:00Z', commission=4,
                             execution_id='l')], {'NQZ6': 20})
    assert long.net == -4 and long.net_at(20010) == -4 + 10 * 2 * 20 and long.net_at(None) is None
    short, = match([Execution('t', 'a', 'NQZ6', 'sell', 1, 20000, '2026-10-07T14:00:00Z', execution_id='s')],
                   {'NQZ6': 20})
    assert short.net_at(20010) == -200 and short.net_at(19990) == 200
    assert PriceSample('NQ', '2026-10-07T14:00:00Z', 's', 5, 5).last == 5
    bar = dict(symbol='NQ', ts='2026-10-07T14:00:00Z', source='b', high=9, low=1, resolution_seconds=60)
    assert PriceSample(**bar, open=2, close=7).last == 7 and PriceSample(**bar).last is None


def test_the_newest_measured_price_is_the_newest_by_time_whatever_order_prices_come_in():
    trade, = match([Execution('t', 'a', 'NQZ6', 'buy', 1, 20000, '2026-10-07T14:00:00Z', execution_id='in')],
                   {'NQZ6': 20})
    prices = [PriceSample('NQ', f'2026-10-07T14:00:0{s}Z', 'ticks', p, p) for s, p in [(3, 20003), (1, 20001), (2, 20002)]]
    metric = excursions(trade, prices, 'ticks', now=trade_time('2026-10-07T14:00:09Z'))
    assert metric['last_price'] == 20003 and metric['last_ts'] == '2026-10-07T14:00:03+00:00'
    # Prints sharing the newest time (a sweep): the last one on the tape, as the chart's close is.
    sweep = [PriceSample('NQ', '2026-10-07T14:00:04Z', 'ticks', p, p) for p in (20004, 20004.25, 20004.5)]
    assert excursions(trade, prices + sweep, 'ticks', now=trade_time('2026-10-07T14:00:09Z'))['last_price'] == 20004.5
    chart = trade_chart(trade, prices + sweep, 'ticks')
    assert chart['series']['data'][-1]['close'] == 20004.5


def trade_time(text):
    import datetime as dt
    return dt.datetime.fromisoformat(text.replace('Z', '+00:00'))


def test_prices_read_back_are_the_prices_stored(tmp_path):
    journal = Journal(tmp_path / 'trades.sqlite3')
    stored = [PriceSample('NQ', '2026-10-07T14:00:01.25Z', 'motivewave-ticks', 20000.25, 20000.25, bid=20000,
                          ask=20000.5, volume=3),
              PriceSample('NQ', '2026-10-07T14:00:00Z', 'motivewave-bars:1m', 20010, 19990, 60, open=19995,
                          close=20005, volume=1200, proxy=True)]
    journal.ingest_prices(stored)
    read = journal.prices('NQZ6', 'motivewave-ticks') + journal.prices('NQZ6', 'motivewave-bars:1m')
    assert read == stored and all(type(s) is PriceSample for s in read)
    assert [dataclasses.asdict(s) for s in read] == [dataclasses.asdict(s) for s in stored]


def test_a_named_journals_open_trade_is_brought_up_to_date_at_each_redraw(tmp_path):
    capture, bridge = watched(tmp_path)
    write(bridge, [fill('buy', 'in', 20000, '2026-10-07T14:00:00Z'), tick(19995, '2026-10-07T14:00:01Z')])
    worker = SyncWorker(capture)
    worker.poll()
    id = journals.create(capture, 'Main', [('motivewave-native:ws:live', 'SIM')])
    named = journals.open_named(capture, id)
    named.set_excursion_unit('points')
    trade, = named.trades()
    # The watcher keeps writing to the capture journal; the named copy is behind until it is followed.
    write(bridge, [tick(20030, '2026-10-07T14:00:02Z'),
                   {'version': 1, 'type': 'gap', 'provider': 'motivewave-ticks', 'symbol': 'NQ', 'dropped': 1,
                    'start_ts': '2026-10-07T14:00:02Z', 'end_ts': '2026-10-07T14:00:02Z', 'ts': '2026-10-07T14:00:02Z'}])
    worker.poll()
    assert named.best_metric(trade)['observations'] == 1 and len(named.prices('NQZ6', 'motivewave-ticks')) == 1
    journals.follow(named, trade)
    assert named.best_metric(trade) == capture.best_metric(trade)
    assert named.best_metric(trade)['observations'] == 2 and len(named.prices('NQZ6', 'motivewave-ticks')) == 2
    assert named.capture_gaps('motivewave-ticks') == capture.capture_gaps('motivewave-ticks')
    # Its own settings stay its own; following again with nothing new changes nothing.
    assert named.display_settings()['excursion_unit'] == 'points' != capture.display_settings()['excursion_unit']
    journals.follow(named, trade)
    assert named.best_metric(trade)['observations'] == 2
    # A capture journal and a closed trade: nothing to do.
    journals.follow(capture, trade)
    journals.follow(named, dataclasses.replace(trade, closed='2026-10-07T14:00:05+00:00'))
    # A trade changed in the named journal alone (a fill added by hand) is measured there as prices arrive.
    named.ingest([(Execution('motivewave-native:ws:live', 'SIM', 'NQZ6', 'buy', 1, 20010, '2026-10-07T14:00:01.5Z',
                             execution_id='by-hand'), {})])
    by_hand, = named.trades()
    assert by_hand.id == trade.id and by_hand.basis != trade.basis and not capture.metric_options(by_hand)
    write(bridge, [tick(19980, '2026-10-07T14:00:03Z')])
    worker.poll()
    journals.follow(named, by_hand)
    measured = named.best_metric(by_hand)                  # measured from entry the first time
    assert measured['observations'] == 3 and measured['last_price'] == 19980
    assert measured['mae_points'] == 25                    # from the average of 20000 and 20010
    write(bridge, [tick(19970, '2026-10-07T14:00:04Z')])
    worker.poll()
    journals.follow(named, by_hand)
    assert named.best_metric(by_hand)['mae_points'] == 35 and named.best_metric(by_hand)['observations'] == 4


def test_an_open_trades_chart_reads_only_the_prices_after_its_kept_minutes(tmp_path, monkeypatch):
    from views import trades as view
    journal = Journal(tmp_path / 'trades.sqlite3')
    ticks = lambda *pairs: [PriceSample('NQ', ts, 'motivewave-ticks', p, p) for ts, p in pairs]
    journal.ingest_prices(ticks(('2026-10-07T14:00:10Z', 1), ('2026-10-07T14:01:10Z', 2), ('2026-10-07T14:02:05Z', 3)))
    journal.ingest([(Execution('t', 'a', 'NQZ6', 'buy', 1, 1, '2026-10-07T14:00:00Z', execution_id='in'), {})])
    trade, = journal.trades()
    monkeypatch.setattr(view, 'st', types.SimpleNamespace(session_state={}))
    monkeypatch.setattr(view.time, 'time', lambda: 60 * 1000)
    window = trade_time('2026-10-07T13:45:00Z'), trade_time('2026-10-07T14:20:00Z')
    reads = []
    original = Journal.prices
    monkeypatch.setattr(Journal, 'prices', lambda self, *a, **k: reads.append(k.get('start')) or original(self, *a, **k))
    first = view.window_prices(journal, trade, 'motivewave-ticks', *window)
    assert [s.high for s in first] == [1, 2, 3]
    # Kept: everything before the newest minute (14:02). Each later draw reads from there on only.
    journal.ingest_prices(ticks(('2026-10-07T14:02:30Z', 4)))
    reads.clear()
    assert [s.high for s in view.window_prices(journal, trade, 'motivewave-ticks', *window)] == [1, 2, 3, 4]
    assert reads == ['2026-10-07T14:02:00+00:00']
    # A replay racing ahead: the newest minute moves with the prices, not the clock on the wall.
    journal.ingest_prices(ticks(('2026-10-07T14:05:01Z', 5)))
    view.window_prices(journal, trade, 'motivewave-ticks', *window)
    reads.clear()
    assert [s.high for s in view.window_prices(journal, trade, 'motivewave-ticks', *window)] == [1, 2, 3, 4, 5]
    assert reads == ['2026-10-07T14:05:00+00:00']
    # A minute later everything is read again once.
    monkeypatch.setattr(view.time, 'time', lambda: 60 * 1001)
    reads.clear()
    assert [s.high for s in view.window_prices(journal, trade, 'motivewave-ticks', *window)] == [1, 2, 3, 4, 5]
    assert reads[0] == window[0].isoformat()
    # The contract's own prices appearing replace the continuous chart's in the kept minutes too.
    journal.ingest_prices([PriceSample('NQZ6', '2026-10-07T14:05:30Z', 'motivewave-ticks', 9, 9)])
    assert [s.high for s in view.window_prices(journal, trade, 'motivewave-ticks', *window)] == [9]
    # A closed trade reads everything, and nothing is kept for it.
    closed = dataclasses.replace(trade, closed='2026-10-07T14:06:00+00:00')
    assert closed.status == 'closed' and view.window_prices(journal, closed, 'motivewave-ticks', *window)
    assert view.KEPT not in view.st.session_state


def test_the_minute_in_progress_is_drawn_from_prints_after_the_last_platform_candle():
    trade, = match(
        [Execution('t', 'a', 'NQZ6', 'buy', 1, 20000, '2026-10-07T14:00:30Z', execution_id='in')], {'NQZ6': 20})
    bar = lambda minute, o, h, l, c: PriceSample('NQ', f'2026-10-07T14:0{minute}:00Z', 'motivewave-bars:1m', h, l,
                                                 resolution_seconds=60, open=o, close=c)
    bars = [bar(0, 19990, 20010, 19980, 20000), bar(1, 20000, 20020, 19995, 20015)]
    prints = [PriceSample('NQ', ts, 'motivewave-ticks', p, p) for ts, p in [
        ('2026-10-07T14:01:30Z', 1), ('2026-10-07T14:02:05Z', 20016), ('2026-10-07T14:02:20Z', 20030),
        ('2026-10-07T14:02:40Z', 20012)]]
    chart = trade_chart(trade, bars, 'motivewave-bars:1m', prints=prints, prints_source='motivewave-ticks')
    data = chart['series']['data']
    # Its own candle where the platform has one (the stray print inside it is not used), then the prints.
    assert [d['time'] % 3600 for d in data] == [0, 60, 120]
    assert data[1]['close'] == 20015 and data[1]['low'] == 19995
    assert {k: data[2][k] for k in ('open', 'high', 'low', 'close')} == {
        'open': 20016, 'high': 20030, 'low': 20012, 'close': 20012}
    assert any('the one in progress' in note for note in chart['notes'])
    # When the platform's candle for that minute arrives, it replaces the prints.
    chart = trade_chart(trade, bars + [bar(2, 20016, 20031, 20011, 20020)], 'motivewave-bars:1m',
                        prints=prints, prints_source='motivewave-ticks')
    assert chart['series']['data'][-1]['high'] == 20031 and not any('in progress' in n for n in chart['notes'])
    # Without prints the chart is the platform's candles alone.
    assert trade_chart(trade, bars, 'motivewave-bars:1m')['series']['data'] == data[:2]


def test_cancelling_one_of_two_overlapping_drafts_lets_the_other_attach_at_the_next_poll(tmp_path):
    journal, bridge = watched(tmp_path)
    ids = [journal.save_draft(f'Plan {i}', provider='motivewave-native:ws:live', account='SIM', symbol='NQZ6',
                              start='2026-10-07T14:00:00Z', end='2026-10-07T14:05:00Z', notes=f'note {i}')
           for i in range(2)]
    write(bridge, [fill('buy', 'in', 20000, '2026-10-07T14:00:10Z')])
    worker = SyncWorker(journal)
    worker.poll()
    assert {journal.workspace()['drafts'][id]['state'] for id in ids} == {'ambiguous'}
    journal.cancel_draft(ids[0])
    write(bridge, [tick(20001, '2026-10-07T14:00:11Z')])        # prices only: no fill changes
    worker.poll()
    assert journal.workspace()['drafts'][ids[1]]['state'] == 'attached'


def test_a_draft_waiting_for_its_trade_is_not_written_again_at_every_poll(tmp_path, monkeypatch):
    from mwjournal import workspace
    journal, bridge = watched(tmp_path)
    id = journal.save_draft('Plan', provider='motivewave-native:ws:live', account='SIM', symbol='NQZ6',
                            start='2026-10-07T15:00:00Z', end='2026-10-07T15:05:00Z')
    write(bridge, [fill('buy', 'in', 20000, '2026-10-07T14:00:10Z')])         # outside its window
    worker = SyncWorker(journal)
    written = []
    original = workspace.write
    monkeypatch.setattr(workspace, 'write', lambda db, state: written.append(1) or original(db, state))
    for second in range(3):
        write(bridge, [tick(20001 + second, f'2026-10-07T14:00:1{second}Z')])
        worker.poll()
    assert journal.workspace()['drafts'][id]['state'] == 'armed' and written == []

