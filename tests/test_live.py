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


def test_a_new_fill_measures_only_the_trade_whose_fills_it_changed(tmp_path, monkeypatch):
    journal, bridge = watched(tmp_path)
    write(bridge, [fill('buy', 'a-in', 20000, '2026-10-07T14:00:00Z'), tick(19990, '2026-10-07T14:00:01Z'),
                   fill('sell', 'a-out', 20010, '2026-10-07T14:00:02Z'),
                   fill('buy', 'b-in', 20010, '2026-10-07T14:01:00Z'), tick(20020, '2026-10-07T14:01:01Z')])
    worker = SyncWorker(journal)
    worker.poll()
    closed, opened = journal.trades()
    before = journal.metric_options(closed)
    measured = []
    original = Journal.measure
    monkeypatch.setattr(Journal, 'measure', lambda self, trade, source, **options: (
        measured.append(trade.id), original(self, trade, source, **options))[1])
    # A second contract into the open trade, in the same account and contract as the closed one: only the open
    # trade is measured, from entry, as its fills now are.
    write(bridge, [fill('buy', 'b-add', 20020, '2026-10-07T14:01:02Z')])
    worker.poll()
    assert measured == [opened.id]
    _, scaled = journal.trades()
    assert scaled.basis != opened.basis and journal.best_metric(scaled)['observations'] == 1
    assert journal.metric_options(closed) == before
    # The fill that closes it: the same, and the closed trade's measurement is still the one stored at first.
    measured.clear()
    write(bridge, [fill('sell', 'b-out', 20030, '2026-10-07T14:01:03Z'), fill('sell', 'b-out2', 20030, '2026-10-07T14:01:03Z')])
    worker.poll()
    assert measured == [opened.id] and journal.trades()[1].closed
    assert journal.metric_options(closed) == before
    # After an upgrade every trade is measured again, once.
    measured.clear()
    worker.rebuild = True
    worker.poll()
    assert sorted(measured) == sorted([closed.id, opened.id]) and not worker.rebuild


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



def named_with_open_trade(tmp_path):
    capture, bridge = watched(tmp_path)
    write(bridge, [fill('buy', 'in', 20000, '2026-10-07T14:00:00Z'), tick(19995, '2026-10-07T14:00:01Z')])
    worker = SyncWorker(capture)
    worker.poll()
    id = journals.create(capture, 'Main', [('motivewave-native:ws:live', 'SIM')])
    return capture, bridge, worker, id


def test_a_second_window_on_the_named_journal_makes_follow_wait_not_fail(tmp_path):
    import sqlite3
    import threading
    capture, bridge, worker, id = named_with_open_trade(tmp_path)
    named = journals.open_named(capture, id)
    trade, = named.trades()
    write(bridge, [tick(20030, '2026-10-07T14:00:02Z')])
    worker.poll()
    other = sqlite3.connect(named.path, timeout=15)
    other.execute('BEGIN IMMEDIATE')                      # the other window, mid-write
    failed = []
    thread = threading.Thread(target=lambda: _follow(named, trade, failed))
    thread.start()
    thread.join(0.3)
    assert thread.is_alive()                             # waiting its turn
    other.rollback()
    other.close()
    thread.join(10)
    assert not failed and named.best_metric(trade)['observations'] == 2


def _follow(named, trade, failed):
    try:
        journals.follow(named, trade)
    except Exception as exc:                             # noqa: BLE001 - the test reports any failure
        failed.append(exc)


def test_follow_writes_nothing_when_nothing_is_new(tmp_path):
    import sqlite3
    capture, bridge, worker, id = named_with_open_trade(tmp_path)
    named = journals.open_named(capture, id)
    trade, = named.trades()
    journals.follow(named, trade)
    watcher = sqlite3.connect(named.path)
    version = watcher.execute('PRAGMA data_version').fetchone()[0]
    journals.follow(named, trade)
    assert watcher.execute('PRAGMA data_version').fetchone()[0] == version
    watcher.close()


def test_prices_a_whole_page_copies_are_measured_on_that_page_and_by_follow(tmp_path):
    capture, bridge, worker, id = named_with_open_trade(tmp_path)
    named = journals.open_named(capture, id)
    named.ingest([(Execution('motivewave-native:ws:live', 'SIM', 'NQZ6', 'buy', 1, 20010, '2026-10-07T14:00:01.5Z',
                             execution_id='by-hand'), {})])
    by_hand, = named.trades()
    journals.follow(named, by_hand)                       # measured here: the capture does not hold this trade
    assert named.best_metric(by_hand)['observations'] == 1
    # A print the capture stores late, with a time before the measurement's newest: the whole page copies it.
    write(bridge, [tick(19970, '2026-10-07T14:00:03Z')])
    worker.poll()
    named = journals.open_named(capture, id)             # its mirror copies the print and measures from entry
    by_hand, = named.trades()
    assert named.best_metric(by_hand)['observations'] == 2 and named.best_metric(by_hand)['mae_points'] == 35
    write(bridge, [tick(19960, '2026-10-07T14:00:04Z')])
    worker.poll()
    journals.follow(named, by_hand)                       # and the next print by follow, carried on
    assert named.best_metric(by_hand)['observations'] == 3 and named.best_metric(by_hand)['mae_points'] == 45


def test_a_named_journals_own_trades_are_measured_again_once_under_new_rules(tmp_path):
    """MEASUREMENT_RULES 2 reaches named journals too, which have no watcher of their own: a trade only the named
    journal holds (here closed by a fill added by hand) is measured again from its stored prints, and the bid / ask
    rows 0.4.7 kept go, once the capture journal has been measured again too."""
    capture, bridge, worker, id = named_with_open_trade(tmp_path)
    named = journals.open_named(capture, id)
    named.ingest([(Execution('motivewave-native:ws:live', 'SIM', 'NQZ6', 'sell', 1, 20010, '2026-10-07T14:00:01.5Z',
                             execution_id='by-hand', point_value=20), {})])
    named = journals.open_named(capture, id)
    own, = named.trades()
    assert own.closed and not capture.metric_options(own)
    fresh = named.best_metric(own)
    assert (fresh['mae'], fresh['mae_points']) == (100, 5)    # 19995 against 20000, 1 contract of $20 a point
    # As 0.4.7 left both journals: measured under rules 1, a stale number and bid / ask rows.
    stale = {**fresh, 'mae': 999.0}
    named.record_metric(own, stale)
    named.record_metric(own, {**stale, 'basis': 'liquidation'})
    with named.connect() as db:                           # a bid / ask row the mirror copied from the capture
        db.execute('INSERT INTO metrics VALUES(?,?,?,?)', (own.id, 'capture-basis', json.dumps(['motivewave-ticks', 'liquidation']),
                                                           json.dumps({**stale, 'basis': 'liquidation'})))
    for journal in (named, capture):
        with journal.connect() as db:
            db.execute("UPDATE journal_meta SET value='1' WHERE key='measurement_rules'")
    named = journals.open_named(capture, id)
    assert [(m['basis'], m['mae']) for m in named.metric_options(own)] == [('last', 100)]
    assert named.recalculation_due()                      # not done until the capture is: its mirror may still copy
    SyncWorker(Journal(capture.path)).recalculate()
    assert not capture.recalculation_due()
    named = journals.open_named(capture, id)
    assert not named.recalculation_due()
    with named.connect() as db:
        assert db.execute("SELECT count(*) FROM metrics WHERE json_extract(source, '$[1]')='liquidation'").fetchone()[0] == 0
    named.record_metric(own, stale)                       # once only: a closed trade is not measured again
    journals.open_named(capture, id)
    assert named.best_metric(own)['mae'] == 999


def test_a_named_journal_copying_does_not_hold_up_the_watcher(tmp_path):
    import sqlite3
    capture, bridge, worker, id = named_with_open_trade(tmp_path)
    named = journals.open_named(capture, id)
    with named.connect() as db:                           # what follow and mirror do, held open
        db.execute('ATTACH DATABASE ? AS capture', (str(capture.path),))
        journals.lock_named(db)
        db.execute('SELECT count(*) FROM capture.prices').fetchone()
        watcher = sqlite3.connect(capture.path, timeout=0.5)
        watcher.execute('BEGIN IMMEDIATE')                # the capture stays writable
        watcher.rollback()
        watcher.close()
        other = sqlite3.connect(named.path, timeout=0.2)
        try:
            other.execute('BEGIN IMMEDIATE')              # the named journal is held
            raise AssertionError('the named journal was not locked')
        except sqlite3.OperationalError:
            pass
        finally:
            other.close()


def test_measuring_from_entry_leaves_no_measurement_it_did_not_make(tmp_path):
    journal = Journal(tmp_path / 'trades.sqlite3')
    journal.ingest([(Execution('t', 'a', 'NQZ6', 'buy', 1, 20000, '2026-10-07T14:00:00Z', execution_id='in'), {})])
    journal.ingest_prices([PriceSample('NQ', '2026-10-07T14:00:01Z', 'motivewave-ticks', 19995, 19995,
                                       bid=19994.75, ask=19995.25)])
    trade, = journal.trades()
    # A bid/ask measurement stored by 0.4.7 and earlier: measuring again from entry leaves only what it made.
    journal.record_metric(trade, {'source': 'motivewave-ticks', 'basis': 'liquidation', 'precision': 'observed-ticks',
                                  'mae': 5, 'mfe': 0, 'path': [], 'notes': []})
    journal.measure(trade, 'motivewave-ticks')
    assert [m['basis'] for m in journal.metric_options(trade)] == ['last']
    journal.ingest_prices([PriceSample('NQZ6', '2026-10-07T14:00:02Z', 'motivewave-ticks', 19990, 19990)])
    journal.measure(trade, 'motivewave-ticks')
    assert [m['basis'] for m in journal.metric_options(trade)] == ['last']
