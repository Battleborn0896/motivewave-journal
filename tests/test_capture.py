"""Native bridge protocol fixtures only: no brokerage requests or real trading records."""
import datetime as dt
import json
from types import SimpleNamespace

import pytest

from mwjournal import Journal
from mwjournal.chart import trade_chart
from mwjournal.importers import apply_preview, preview_bridge
from mwjournal.metrics import excursions
from mwjournal.sync import SyncWorker


def lines(events):
    return ''.join(json.dumps(e) + '\n' for e in events)


def fill(id, side='buy', price=5000, *, mode='replay', run='run-a', qty=1, ts='2022-01-03T14:30:00Z'):
    return {'version': 1, 'type': 'execution', 'mode': mode,
            'provider': 'motivewave-native:workspace:' + ('replay:' + run if mode == 'replay' else 'live'),
            'account': 'REPLAY-1' if mode == 'replay' else 'paper', 'symbol': 'ESZ6', 'execution_id': id,
            'side': side, 'qty': qty, 'price': price, 'ts': ts, 'point_value': 50,
            'commission': 2, 'commission_known': False, 'currency': 'USD',
            'quality': 'MotiveWave internal capture; fees may be estimated'}


def tick(price, run='run-a', suffix='ticks', ts='2022-01-03T14:30:30Z'):
    return {'type': 'tick', 'mode': 'replay', 'provider': f'motivewave-replay:{run}:{suffix}',
            'symbol': 'ESZ6', 'price': price, 'ts': ts}


def test_environments_have_separate_databases_and_reject_wrong_streams(tmp_path, monkeypatch):
    monkeypatch.setenv('MWJ_HOME', str(tmp_path))
    live, replay = Journal(), Journal(dataset='replay')
    assert live.path == tmp_path / 'trades.sqlite3' and replay.path == tmp_path / 'replay' / 'trades.sqlite3'
    assert preview_bridge(lines([fill('a')]), dataset='live').errors
    parsed = preview_bridge(lines([fill('a'), fill('b', 'sell', 5002, ts='2022-01-03T14:32:00Z')]), dataset='replay')
    apply_preview(replay, parsed)
    assert len(replay.trades()) == 1 and not live.trades()
    with pytest.raises(ValueError, match='other trading environment'):
        apply_preview(live, parsed)
    with pytest.raises(ValueError, match='other trading environment'):
        apply_preview(live, preview_bridge(lines([fill('a')])))
    with pytest.raises(ValueError, match='other trading environment'):
        Journal(replay.path)
    assert Journal(replay.path, dataset='replay').executions()[0].execution_id == 'a'


def test_native_watcher_deduplicates_restart_and_matches_only_own_replay_prices(tmp_path):
    journal = Journal(tmp_path / 'replay.db', dataset='replay')
    path = tmp_path / 'motivewave.replay.ndjson'
    path.write_text(lines([fill('a'), tick(4999), tick(5003, ts='2022-01-03T14:31:30Z'),
                           tick(5100, run='another-run'),
                           fill('b', 'sell', 5002, ts='2022-01-03T14:32:00Z')]))
    journal.put('profiles', 'native', {'mode': 'bridge', 'path': str(path), 'enabled': True})
    SyncWorker(journal).poll()
    assert len(journal.trades()) == 1
    trade = journal.trades()[0]
    assert journal.price_sources(trade.symbol, trade) == ['motivewave-replay:run-a:ticks']
    metric = journal.best_metric(trade)
    assert metric['mae'] == 50 and metric['mfe'] == 150 and metric['precision'] == 'observed-ticks'
    assert 'fees not provided' not in trade.flags   # MotiveWave's commission is the fee
    assert 'MotiveWave internal capture; fees may be estimated' not in trade.flags
    # Simulate study reload: same identity, not a new provider/run.
    with path.open('a') as fh:
        fh.write(lines([fill('a'), fill('b', 'sell', 5002, ts='2022-01-03T14:32:00Z')]))
    SyncWorker(journal).poll()
    assert len(journal.executions()) == 2
    foreign = excursions(trade, journal.prices('ESZ6', 'motivewave-replay:another-run:ticks'),
                         'motivewave-replay:another-run:ticks')
    assert foreign['precision'] == 'pending' and foreign['mfe'] is None


def test_generated_replay_ticks_are_labelled_simulated_in_metrics_and_chart(tmp_path):
    journal = Journal(tmp_path / 'replay.db', dataset='replay')
    apply_preview(journal, preview_bridge(lines([fill('a'), tick(5003, suffix='simulated'),
                      fill('b', 'sell', 5002, ts='2022-01-03T14:32:00Z')]), dataset='replay'))
    trade = journal.trades()[0]
    source = journal.price_sources('ESZ6', trade)[0]
    prices = journal.prices('ESZ6', source)
    metric = excursions(trade, prices, source)
    assert metric['precision'] == 'replay-simulated'
    assert any('synthetic' in n for n in metric['notes'])
    chart = trade_chart(trade, prices, source)
    assert chart['series']['data'] and any('Generated/unknown' in n for n in chart['notes'])


def test_the_replayed_days_minute_bars_draw_every_replay_trades_chart_and_never_measure_them(tmp_path):
    """Study 0.3.7 writes MotiveWave's 1-minute bars of the days a replay plays, the same for every run: a trade of
    any run on those days is drawn from them, as platform candles, MAE / MFE still wait for ticks, and they move
    neither an open replay trade's clock nor what Setup calls recording."""
    from mwjournal.scope import PROVENANCE, REPLAY_BARS, provenance
    from views.trades import default_source

    def bar(minute, low, high):
        return {'version': 1, 'type': 'bar', 'mode': 'replay', 'provider': REPLAY_BARS,
                'symbol': 'ESZ6', 'ts': f'2022-01-03T14:{minute:02d}:00Z', 'resolution_seconds': 60,
                'open': low, 'high': high, 'low': low, 'close': high, 'proxy': False}

    journal = Journal(tmp_path / 'replay.db', dataset='replay')
    apply_preview(journal, preview_bridge(lines([
        bar(28, 4995, 4998), bar(29, 4997, 5001), bar(30, 4996, 5004), bar(31, 4999, 5006), bar(50, 5010, 5012),
        fill('a'), tick(5003), tick(5002, ts='2022-01-03T14:32:00Z'), fill('b', 'sell', 5002, ts='2022-01-03T14:32:00Z'),
        fill('c', run='run-b', ts='2022-01-03T14:29:00Z'), fill('d', 'sell', 5004, run='run-b', ts='2022-01-03T14:31:00Z'),
        fill('e', run='run-c', ts='2022-01-03T14:30:30Z')]), dataset='replay'))
    first, other, still_open = sorted(journal.trades(), key=lambda t: t.provider)
    assert journal.price_sources('ESZ6', first) == [REPLAY_BARS, 'motivewave-replay:run-a:ticks']
    assert journal.price_sources('ESZ6', other) == [REPLAY_BARS]                      # another run, the same day's bars
    assert default_source(journal.price_sources('ESZ6', other)) == REPLAY_BARS
    assert PROVENANCE[provenance(REPLAY_BARS)] == 'Platform candles'
    chart = trade_chart(other, journal.prices('ESZ6', REPLAY_BARS), REPLAY_BARS)
    assert chart['series']['data'] and not any('no corresponding candle' in n for n in chart['notes'])
    # The exit's minute has no bar yet when the replay's history goes only to its time: the run's own prints draw
    # it, as live prints draw a live chart's minute in progress.
    drawn = trade_chart(first, journal.prices('ESZ6', REPLAY_BARS), REPLAY_BARS,
                        prints=journal.prices('ESZ6', 'motivewave-replay:run-a:ticks'),
                        prints_source='motivewave-replay:run-a:ticks')
    exit_minute = int(dt.datetime(2022, 1, 3, 14, 32, tzinfo=dt.timezone.utc).timestamp())
    assert any(c['time'] == exit_minute for c in drawn['series']['data'])
    assert excursions(other, journal.prices('ESZ6', REPLAY_BARS), REPLAY_BARS)['precision'] != 'observed-ticks'
    assert journal.best_metric(other) is None or journal.best_metric(other)['precision'] == 'pending'
    # The day's later bars move neither an open replay trade's clock nor what is recording.
    assert journal.trade_clock(still_open).isoformat() < '2022-01-03T14:50'
    assert journal.recent_contracts() == ['ESZ6']


def test_an_unknown_event_type_is_skipped_and_counted_never_fatal(tmp_path):
    """A newer study can write events this app does not read yet (study 0.4's 'position' lines, say): they are
    skipped and counted, and the fills, stops and heartbeat around them still arrive."""
    events = [fill('a', mode='live', ts='2026-10-07T14:00:00Z'),
              {'version': 1, 'type': 'position', 'mode': 'live', 'provider': 'motivewave-native:workspace:live',
               'account': 'paper', 'symbol': 'ESZ6', 'fills': ['a'], 'mae_points': 1.25},
              {'version': 1, 'type': 'something-newer', 'mode': 'replay'},           # even in the other file's mode
              {'version': 1, 'type': 'protection', 'mode': 'live', 'provider': 'motivewave-native:workspace:live',
               'account': 'paper', 'symbol': 'ESZ6', 'order_id': 's1', 'entry_execution_id': 'a',
               'ts': '2026-10-07T14:00:01Z', 'role': 'stop', 'state': 'working', 'price': 4996},
              fill('b', 'sell', 5002, mode='live', ts='2026-10-07T14:02:00Z'),
              {'version': 1, 'type': 'heartbeat', 'mode': 'live', 'provider': 'motivewave-native',
               'ts': '2026-10-07T14:02:01Z', 'status': 'connected', 'detail': 'ok'}]
    parsed = preview_bridge(lines(events), dataset='live')
    assert not parsed.errors and parsed.unknown == 2
    assert len(parsed.fills) == 2 and len(parsed.protection) == 1 and len(parsed.heartbeats) == 1
    journal = Journal(tmp_path / 'trades.sqlite3')
    file = tmp_path / 'motivewave.ndjson'
    file.write_text(lines(events[:3]), encoding='utf-8', newline='')
    journal.put('profiles', 'study', {'path': str(file), 'mode': 'bridge', 'enabled': True})
    worker = SyncWorker(journal)
    worker.poll()
    with file.open('a', encoding='utf-8', newline='') as fh:
        fh.write(lines(events[3:]))
    worker.poll()
    status = journal.get('sync_status')['study']
    assert status['status'] == 'synced' and 'error' not in status
    assert status['unknown_events'] == 2                  # counted across reads of the same file
    trade, = journal.trades()
    assert trade.closed and [e.order_id for e in journal.protection_events(trade)] == ['s1']


def test_connector_waiting_error_heartbeat_and_recovery_are_visible(tmp_path):
    journal = Journal(tmp_path / 'live.db')
    path = tmp_path / 'motivewave.ndjson'
    journal.put('profiles', 'native', {'mode': 'bridge', 'path': str(path), 'enabled': True})
    worker = SyncWorker(journal)
    worker.poll()
    assert journal.get('sync_status')['native']['status'] == 'waiting for study'
    heartbeat = {'type': 'heartbeat', 'mode': 'live', 'provider': 'motivewave-native',
                 'ts': '2026-10-08T14:00:00Z', 'status': 'error', 'detail': 'Unsupported MotiveWave version'}
    path.write_text(lines([heartbeat]))
    worker.poll()
    assert journal.get('sync_status')['native']['error'] == heartbeat['detail']
    with path.open('a') as fh:
        fh.write(lines([{**heartbeat, 'status': 'connected', 'detail': 'Read-only capture',
                         'connector': 'motivewave-journal-study', 'connector_version': '0.1.0'}]))
    worker.poll()
    status = journal.get('sync_status')['native']
    assert not status.get('error') and status['heartbeat']['status'] == 'connected'
    # Optional fields added by a newer study travel with the heartbeat; older studies simply omit them.
    assert status['heartbeat']['connector'] == 'motivewave-journal-study'
    assert status['heartbeat']['connector_version'] == '0.1.0'


def test_only_a_newline_ends_an_event_and_windows_line_endings_parse():
    beat = {'type': 'heartbeat', 'mode': 'replay', 'provider': 'motivewave-native', 'status': 'error',
            'ts': '2022-01-03T14:30:00Z', 'detail': 'first\u2028second\u0085third'}
    text = json.dumps(beat, ensure_ascii=False) + '\r\n' + json.dumps(fill('a')) + '\r\n'
    parsed = preview_bridge(text, dataset='replay')
    assert not parsed.errors and len(parsed.fills) == 1
    assert parsed.heartbeats[0]['detail'] == beat['detail']
    assert len(preview_bridge(text + '{"type":"exec', dataset='replay', allow_partial_tail=True).fills) == 1


def test_legacy_live_bridge_stays_compatible_and_unlabelled_stream_cannot_enter_replay():
    old = {k: v for k, v in fill('a', mode='live').items() if k != 'mode'}
    assert not preview_bridge(lines([old]), dataset='live').errors
    assert preview_bridge(lines([old]), dataset='replay').errors
    assert preview_bridge(lines([{**tick(5001), 'mode': 'live'}]), dataset='live').errors


def test_linked_scale_in_protection_is_visible_without_guessing(tmp_path):
    journal = Journal(tmp_path / 'replay.db', dataset='replay')
    second = fill('scale-in', qty=1, price=5001, ts='2022-01-03T14:30:10Z')
    order = {'type': 'protection', 'mode': 'replay', 'provider': second['provider'],
             'account': 'REPLAY-1', 'symbol': 'ESZ6', 'entry_execution_id': 'scale-in',
             'order_id': 'stop', 'role': 'stop', 'state': 'working', 'price': 4998, 'ts': second['ts']}
    apply_preview(journal, preview_bridge(lines([fill('a'), second, order]), dataset='replay'))
    assert journal.protection_events(journal.trades()[0])[0].order_id == 'stop'


def test_open_replay_uses_its_recorded_clock_not_wall_time(tmp_path):
    journal = Journal(tmp_path / 'replay.db', dataset='replay')
    apply_preview(journal, preview_bridge(lines([fill('a'), tick(5001),
                           tick(5100, run='other', ts='2025-01-03T14:30:30Z')]), dataset='replay'))
    trade = journal.trades()[0]
    clock = journal.trade_clock(trade)
    assert clock.isoformat() == '2022-01-03T14:30:30.001000+00:00'
    metric = excursions(trade, journal.prices('ESZ6', 'motivewave-replay:run-a:ticks'),
                        'motivewave-replay:run-a:ticks', now=clock)
    assert metric['through'].startswith('2022-01-03') and metric['mfe'] == 50
    chart = trade_chart(trade, [], 'motivewave-replay:run-a:ticks', 4998, now=clock)
    assert chart['overlays']['levels'][0]['to'] == clock.timestamp()


def beat(status='connected', detail='Experimental read-only account capture; 12 fills read', second=0, **more):
    return {'version': 1, 'type': 'heartbeat', 'mode': 'live', 'provider': 'motivewave-native', 'workspace': 'workspace',
            'ts': f'2026-10-08T14:00:{second:02d}Z', 'status': status, 'detail': detail,
            'connector': 'motivewave-journal-study', 'connector_version': '0.3.1', **more}


def test_heartbeat_warnings_travel_with_a_working_capture_instead_of_raising_an_alarm(tmp_path):
    journal = Journal(tmp_path / 'live.db')
    path = tmp_path / 'motivewave.ndjson'
    journal.put('profiles', 'native', {'mode': 'bridge', 'path': str(path), 'enabled': True})
    worker = SyncWorker(journal)

    def after(event):
        with path.open('a') as fh:
            fh.write(lines([event]))
        worker.poll()
        return journal.get('sync_status')['native']
    warning = '3 fills skipped: instrument unavailable; 2 fills with an assumed currency'
    status = after(beat(warnings=warning))
    assert status['status'] == 'synced' and status['warnings'] == warning and 'error' not in status
    assert status['heartbeat']['warnings'] == warning and status['heartbeat']['connector_version'] == '0.3.1'
    # Study 0.1 and 0.2 said the same thing as an error. It is the same warning, not a failure.
    partial = 'Some execution accounts unavailable; capture is partial.'
    status = after(beat('error', partial, 10))
    assert status['status'] == 'synced' and status['warnings'] == partial and 'error' not in status
    both = partial + '; Some instrument multipliers unavailable; capture is partial.'
    assert after(beat('error', both, 20))['warnings'] == both
    # Anything else a study calls an error still needs attention, also beside a partial-capture sentence.
    for second, detail in ((30, 'Native MotiveWave adapter unavailable: NoSuchMethodException'),
                           (40, partial + '; 2 native execution records changed; reconciliation required (not duplicated).'),
                           (50, 'capture is partial.')):
        status = after(beat('error', detail, second))
        assert status['status'] == 'needs attention' and status['error'] == detail and 'warnings' not in status
    # A heartbeat with nothing to warn about clears the warning; one of an unexpected type is not a failure.
    status = after(beat(second=51))
    assert status['status'] == 'synced' and 'warnings' not in status and 'error' not in status
    status = after(beat(second=52, warnings={'fills skipped': 3}))
    assert status['status'] == 'synced' and 'warnings' not in status
    # The warning stays while other lines arrive without a new heartbeat.
    after(beat(second=53, warnings=warning))
    status = after(fill('a', mode='live', ts='2026-10-08T14:01:00Z'))
    assert status['status'] == 'synced' and status['warnings'] == warning and status['executions'] == 1


def print_at(second, price, provider):
    stamp = f'2026-10-07T14:{second // 60:02d}:{second % 60:02d}Z'
    return {'version': 1, 'type': 'tick', 'mode': 'live', 'provider': provider, 'symbol': 'ESZ6',
            'ts': stamp, 'price': price, 'volume': 2, 'proxy': False}


def test_history_backfill_ticks_are_observed_ticks_with_their_own_label_and_are_never_added_to_live_ticks(tmp_path):
    from mwjournal.post_exit import best_post_exit
    from mwjournal.scope import PROVENANCE, provenance
    journal = Journal(tmp_path / 'live.db')
    path = tmp_path / 'motivewave.ndjson'
    journal.put('profiles', 'native', {'mode': 'bridge', 'path': str(path), 'enabled': True})
    entry = fill('a', mode='live', ts='2026-10-07T14:00:00Z')
    exit = fill('b', 'sell', 5002, mode='live', ts='2026-10-07T14:02:00Z')
    # The study was started mid-trade: it recorded two prints live, and later loaded the whole
    # holding window (and a little after it) from the platform's tick history. Two prints are in both.
    live = [print_at(70, 5003, 'motivewave-ticks'), print_at(100, 5001, 'motivewave-ticks')]
    backfill = [print_at(second, price, 'motivewave-ticks:history') for second, price in
                ((1, 5000), (20, 4998), (70, 5003), (100, 5001), (121, 5002), (180, 5006), (419, 5004))]
    path.write_text(lines([entry, *live, exit, *backfill]))
    SyncWorker(journal).poll()
    trade, = journal.trades()
    assert journal.price_sources('ESZ6', trade) == ['motivewave-ticks', 'motivewave-ticks:history']
    recorded, history = journal.metric_options(trade)
    assert [m['source'] for m in (recorded, history)] == ['motivewave-ticks', 'motivewave-ticks:history']
    assert [m['precision'] for m in (recorded, history)] == ['observed-ticks', 'observed-ticks']
    assert [m['provenance'] for m in (recorded, history)] == ['recorded-live', 'history-backfill']
    assert [PROVENANCE[provenance(m['source'])] for m in (recorded, history)] == ['Recorded live', 'History backfill']
    # Each stream is measured on its own: four prints inside the holding window, never two plus four.
    assert (recorded['observations'], history['observations']) == (2, 4)
    assert (recorded['mae'], recorded['mfe']) == (0, 150) and (history['mae'], history['mfe']) == (100, 150)
    assert any('History backfill' in note for note in history['notes'])
    assert not any('History backfill' in note for note in recorded['notes'])
    best = journal.best_metric(trade)                     # equal quality: the stream that saw more of the trade
    assert best['source'] == 'motivewave-ticks:history' and best['precision'] == 'observed-ticks'
    # Only recorded ticks measure, and backfilled prints are recorded ticks, for the runner view too.
    runner = best_post_exit(journal, trade, minutes=5)
    assert (runner['source'], runner['precision'], runner['provenance']) == (
        'motivewave-ticks:history', 'observed-ticks', 'history-backfill')
    assert runner['extra_mfe'] == 200 and runner['observations'] == 3 and runner['coverage'] == 'window-observed'
    # The chart draws one stream at a time and says which; volume is that stream's, not the sum.
    charts = {source: trade_chart(trade, journal.prices('ESZ6', source), source)
              for source in journal.price_sources('ESZ6', trade)}
    assert charts['motivewave-ticks']['provenance'] == 'recorded-live'
    assert charts['motivewave-ticks:history']['provenance'] == 'history-backfill'
    assert sum(b.get('volume', 0) for b in charts['motivewave-ticks']['series']['data']) == 4
    assert sum(b.get('volume', 0) for b in charts['motivewave-ticks:history']['series']['data']) == 14
    assert any('history backfill' in note for note in charts['motivewave-ticks:history']['notes'])
    assert not any('history backfill' in note for note in charts['motivewave-ticks']['notes'])
    assert len(charts['motivewave-ticks:history']['overlays']['fills']) == 2
    # Reading the file again adds nothing to either stream.
    journal.set_bridge_offset('native', 'another-file', 0, b'')
    SyncWorker(journal).poll()
    assert [len(journal.prices('ESZ6', s)) for s in journal.price_sources('ESZ6', trade)] == [2, 7]
    assert journal.best_metric(trade) == best
    # It is a live-environment source: the replay journal refuses it like the other live streams.
    replay = Journal(tmp_path / 'replay.db', dataset='replay')
    assert preview_bridge(lines(backfill), dataset='replay').errors
    with pytest.raises(ValueError, match='other trading environment'):
        apply_preview(replay, preview_bridge(lines(backfill)))


def test_replay_history_ticks_are_real_ticks_of_their_own_run_and_never_of_another_or_of_live(tmp_path):
    """motivewave-replay:<run>:history (study 0.3.3): real prints of a replay trade's holding window, loaded
    from MotiveWave's history once it was flat. Observed ticks of that run only, never simulated."""
    from common import metric_options
    from mwjournal.post_exit import best_post_exit
    from mwjournal.scope import PROVENANCE, allows_source, is_history, is_simulated, provenance
    journal = Journal(tmp_path / 'replay.db', dataset='replay')
    path = tmp_path / 'motivewave.replay.ndjson'
    journal.put('profiles', 'native', {'mode': 'bridge', 'path': str(path), 'enabled': True})
    history = [tick(price, suffix='history', ts=f'2022-01-03T{stamp}Z') for stamp, price in
               (('14:29:57', 4990), ('14:30:20', 4997), ('14:31:10', 5006), ('14:32:03', 5010))]
    # The same moments in another run (the replay was rewound and replayed): never this trade's prices.
    other_run = [tick(4900, run='run-b', suffix='history', ts='2022-01-03T14:31:00Z'),
                 tick(5100, run='run-b', suffix='history', ts='2022-01-03T14:31:30Z')]
    path.write_text(lines([fill('a'), tick(4999, suffix='simulated'), *history, *other_run,
                           fill('b', 'sell', 5002, ts='2022-01-03T14:32:00Z')]), encoding='utf-8', newline='')
    SyncWorker(journal).poll()
    trade, = journal.trades()
    source = 'motivewave-replay:run-a:history'
    assert journal.price_sources('ESZ6', trade) == [source, 'motivewave-replay:run-a:simulated']
    assert not allows_source(trade, 'motivewave-replay:run-b:history') and allows_source(trade, source)
    assert is_history(source) and not is_simulated(source) and provenance(source) == 'replay-history'
    assert PROVENANCE['replay-history'] == 'Backfilled from MotiveWave history'
    measured, simulated = journal.metric_options(trade)
    assert measured['source'] == source and simulated['source'] == 'motivewave-replay:run-a:simulated'
    # Real ticks: observed precision, both for currency and for points, never the simulated label.
    assert (measured['precision'], measured['point_precision'], measured['provenance']) == (
        'observed-ticks', 'observed-ticks', 'replay-history')
    assert (measured['mae'], measured['mfe'], measured['observations']) == (150, 300, 2)
    assert (measured['mae_points'], measured['mfe_points']) == (3, 6)
    assert any('History backfill' in note for note in measured['notes'])
    assert not any('synthetic' in note or 'Replay generated' in note for note in measured['notes'])
    assert simulated['precision'] == 'replay-simulated' and simulated['provenance'] == 'replay-simulated'
    # Ranked above the simulated replay prints, and the one measurement shown: only recorded ticks measure.
    assert journal.best_metric(trade)['source'] == source
    assert [m['source'] for m in metric_options(journal, trade)] == [source]
    # The chart draws it as real prints loaded afterwards, with this run's prices only.
    chart = trade_chart(trade, journal.prices('ESZ6', source), source)
    assert chart['provenance'] == 'replay-history'
    assert any('history backfill' in note for note in chart['notes'])
    assert not any('Generated/unknown replay ticks' in note for note in chart['notes'])
    lows = [bar['low'] for bar in chart['series']['data'] if 'low' in bar]
    assert min(lows) == 4990 and 4900 not in lows
    runner = best_post_exit(journal, trade, minutes=5)
    assert runner['precision'] == 'observed-ticks' and runner['provenance'] == 'replay-history'
    # A trade of the other run is measured only with that run's prices.
    other = SimpleNamespace(provider='motivewave-native:workspace:replay:run-b')
    assert allows_source(other, 'motivewave-replay:run-b:history') and not allows_source(other, source)
    # It is a replay stream: a live trade never uses it, and the live journal refuses it outright.
    live_trade = SimpleNamespace(provider='motivewave-native:workspace:live')
    assert not allows_source(live_trade, source)
    assert preview_bridge(lines(history), dataset='live').errors
    with pytest.raises(ValueError, match='other trading environment'):
        apply_preview(Journal(tmp_path / 'live.db'), preview_bridge(lines(history)))
    # Reading the file again adds nothing.
    journal.set_bridge_offset('native', 'another-file', 0, b'')
    SyncWorker(journal).poll()
    assert len(journal.prices('ESZ6', source)) == 4 and journal.best_metric(trade)['source'] == source


def test_gap_in_one_tick_stream_or_one_instrument_does_not_mark_another(tmp_path):
    journal = Journal(tmp_path / 'live.db')
    events = [fill('a', mode='live', ts='2026-10-07T14:00:00Z'),
              print_at(30, 5001, 'motivewave-ticks'), print_at(30, 5001, 'motivewave-ticks:history'),
              fill('b', 'sell', 5002, mode='live', ts='2026-10-07T14:02:00Z'),
              # The backfill of another contract was cut short; the live recording of this one dropped prints.
              {'type': 'gap', 'provider': 'motivewave-ticks:history', 'symbol': 'NQZ6', 'dropped': 9,
               'start_ts': '2026-10-07T14:00:00Z', 'end_ts': '2026-10-07T14:05:00Z', 'ts': '2026-10-07T14:05:00Z'},
              {'type': 'gap', 'provider': 'motivewave-ticks', 'dropped': 3, 'ts': '2026-10-07T14:01:00Z'}]
    path = tmp_path / 'motivewave.ndjson'
    path.write_text(lines(events))
    journal.put('profiles', 'native', {'mode': 'bridge', 'path': str(path), 'enabled': True})
    SyncWorker(journal).poll()
    trade, = journal.trades()
    recorded, history = journal.metric_options(trade)
    assert any('dropped ticks' in note for note in recorded['notes'])
    assert not any('dropped ticks' in note for note in history['notes'])
    window = {'start': trade.opened, 'end': trade.closed}
    assert journal.capture_gaps('motivewave-ticks:history', **window)                       # every instrument
    assert not journal.capture_gaps('motivewave-ticks:history', symbol='ESZ6', **window)
    assert journal.capture_gaps('motivewave-ticks:history', symbol='nqz6', **window)[0]['dropped'] == 9
    assert journal.capture_gaps('motivewave-ticks', symbol='ESZ6', **window)[0]['dropped'] == 3   # names no instrument


def test_motivewave_fees_are_the_fee_even_at_zero_and_its_source_label_is_not_a_warning():
    # Commission rates are set in MotiveWave; what it reports is the fee. Only real problems stay flagged.
    from mwjournal import Execution
    from mwjournal.matching import match
    plain = 'MotiveWave internal capture; fees may be estimated'

    def native(id, side, minute, quality=plain):
        return Execution('motivewave-native:workspace:live', 'paper', 'ESZ6', side, 1, 5000 + minute,
                         f'2022-01-03T14:3{minute}:00Z', execution_id=id, commission=0,
                         commission_known=False, point_value=50, quality=quality)

    trade, = match([native('a', 'buy', 0), native('b', 'sell', 1, plain + '; account currency assumed from the instrument')])
    assert trade.commission == 0 and trade.net == 50
    assert trade.flags == ['account currency assumed from the instrument']

    def imported(id, side, minute):
        return Execution('csv', 'paper', 'ESZ6', side, 1, 5000, f'2022-01-03T14:3{minute}:00Z',
                         execution_id=id, commission_known=False)

    other, = match([imported('c', 'buy', 0), imported('d', 'sell', 1)])
    assert 'fees not provided' in other.flags   # an import without a fee column still says so
