"""The schema steps, on invented fills only: 3 gives a 0.3.0 journal the platform's position ids, 4 gives every
journal one time zone, its own, for its days."""
import json
import sqlite3
import traceback

import pytest

import common
from mwjournal import Execution, Journal, humanize, paths, store, tagging
from mwjournal.importers import apply_preview, preview_bridge, preview_csv
from mwjournal.matching import match
from mwjournal.metrics import excursions
from mwjournal.model import ENTRY_MISSING, EXIT_MISSING, NO_FILL_PRICE
from mwjournal.sync import SyncWorker
from mwjournal.tags import DAY, STARTERS
from test_chart import round_trip
from test_packing import tape
from test_positions import fills, history, lines, unlabelled

PARTIAL = 'Some execution accounts unavailable; capture is partial.'
BEAT = {'version': 1, 'type': 'heartbeat', 'mode': 'live', 'provider': 'motivewave-native', 'workspace': 'workspace',
        'ts': '2026-10-08T09:00:10Z', 'status': 'error', 'detail': PARTIAL,
        'connector': 'motivewave-journal-study', 'connector_version': '0.2.0'}


def bridge_text():
    """A bridge file as study 0.2 left it: every retained fill written again by a second start of the
    study, candles but no ticks, and a heartbeat that calls skipped fills an error."""
    bars = [{'version': 1, 'type': 'bar', 'provider': 'motivewave-bars:1m', 'symbol': 'NQZ6', 'proxy': False,
             'ts': f'2026-09-21T{13 + (55 + m) // 60}:{(55 + m) % 60:02d}:00Z', 'resolution_seconds': 60,
             'open': 20000, 'high': 20012, 'low': 19995, 'close': 20005, 'volume': 10} for m in range(20)]
    again = [{**event, 'observed_at': '2026-10-09T09:00:00Z'} for event in history()]
    return lines([*history(), BEAT, *bars, *again, {**BEAT, 'ts': '2026-10-09T09:00:10Z'}])


def rows(path, query):
    db = sqlite3.connect(path)
    try:
        return db.execute(query).fetchall()
    finally:
        db.close()


def as_schema_2(path):
    """Put a journal file back as 0.3.0 stored it: fills without the platform's position fields (the raw
    event kept beside each still has them), the capture warning held as a failure, no measurement rules
    recorded, version 2."""
    db = sqlite3.connect(path)
    with db:
        for key, payload in db.execute('SELECT key, payload FROM executions').fetchall():
            old = {k: v for k, v in json.loads(payload).items() if k not in ('native_position_id', 'native_entry')}
            db.execute('UPDATE executions SET payload=? WHERE key=?', (json.dumps(old, sort_keys=True), key))
        for name, payload in db.execute('SELECT name, payload FROM sync_status').fetchall():
            status = json.loads(payload)
            if status.pop('warnings', None):
                status.update(status='needs attention', error=status['heartbeat']['detail'])
            status.pop('relabelled', None)
            db.execute('UPDATE sync_status SET payload=? WHERE name=?', (json.dumps(status, sort_keys=True), name))
        db.execute("DELETE FROM journal_meta WHERE key IN ('schema_upgrade', 'recalculate', 'measurement_rules')")
        db.execute('DELETE FROM metrics')
        db.execute('PRAGMA user_version=2')
    db.close()


def journal_0_3_0(folder, *, dataset='live'):
    """(database, bridge file) of a journal that 0.3.0 filled from that bridge file and then closed."""
    folder.mkdir(parents=True, exist_ok=True)
    bridge = folder / 'motivewave.ndjson'
    bridge.write_text(bridge_text() if dataset == 'live' else lines([{**BEAT, 'mode': 'replay'}]))
    path = folder / 'trades.sqlite3'
    journal = Journal(path, dataset=dataset)
    journal.put('profiles', 'MotiveWave', {'path': str(bridge), 'mode': 'bridge', 'enabled': True})
    SyncWorker(journal).poll()
    as_schema_2(path)
    return path, bridge


def backups(folder):
    return sorted(p for p in folder.iterdir() if 'backup' in p.name and p.suffix == '.sqlite3')


def test_schema_2_journal_is_backed_up_then_reads_its_position_fields_from_the_stored_raw_events(tmp_path):
    path, bridge = journal_0_3_0(tmp_path / 'home')
    before = rows(path, 'SELECT key, payload, raw FROM executions ORDER BY key')
    assert len(before) == 12 and not any('native_position_id' in payload or 'native_entry' in payload for _, payload, _ in before)
    assert all('native_position_id' in raw and 'native_entry' in raw for _, _, raw in before)
    assert rows(path, 'SELECT count(*) FROM prices') == [(20,)]
    journal = Journal(path)
    # One complete copy of the journal as it was, beside it, before anything changed.
    backup, = backups(path.parent)
    assert backup.name.startswith('trades.schema-2-backup-') and not list(path.parent.glob('*.partial*'))
    assert rows(backup, 'SELECT key, payload, raw FROM executions ORDER BY key') == before
    assert rows(backup, 'PRAGMA user_version') == [(2,)] and rows(backup, 'SELECT count(*) FROM prices') == [(20,)]
    after = rows(path, 'SELECT key, payload, raw FROM executions ORDER BY key')
    assert rows(path, 'PRAGMA user_version') == [(store.SCHEMA_VERSION,)] and len(after) == 12
    assert [(key, raw) for key, _, raw in after] == [(key, raw) for key, _, raw in before]
    # The journal now holds exactly what importing the same file today stores.
    fresh = Journal(tmp_path / 'fresh.sqlite3')
    apply_preview(fresh, preview_bridge(bridge.read_text(), dataset='live'))
    assert [payload for _, payload, _ in after] == [p for (p,) in rows(fresh.path, 'SELECT payload FROM executions ORDER BY key')]
    assert journal.executions() == fresh.executions() and journal.executions()[0].native_position_id == 'p0'
    trades = journal.trades()
    assert [(t.id, t.status, t.net) for t in trades] == [(t.id, t.status, t.net) for t in fresh.trades()]
    assert [t.incomplete for t in trades] == [[ENTRY_MISSING], [], [], [NO_FILL_PRICE], [EXIT_MISSING], [], []]
    note = journal.upgrade_note()
    assert (note['from'], note['to'], note['executions_rewritten'], note['backup']) == (2, 4, 12, str(backup))
    # Reading the whole bridge file again is neither a conflict nor a second fill...
    result = apply_preview(journal, preview_bridge(bridge.read_text(), dataset='live'))
    assert (result['executions'], result['relabelled'], result['duplicates'], result['prices']) == (0, 0, 24, 0)
    # ...also for the watcher, when its stored position is lost and it starts from the first byte.
    journal.set_bridge_offset('MotiveWave', 'another-file', 0, b'')
    SyncWorker(journal).poll()
    status = journal.get('sync_status')['MotiveWave']
    assert status['status'] == 'synced' and 'error' not in status and status['executions'] == 0
    assert rows(path, 'SELECT key, payload, raw FROM executions ORDER BY key') == after


def test_upgrade_runs_once_and_a_new_or_current_journal_takes_no_backup(tmp_path):
    path, _ = journal_0_3_0(tmp_path / 'home')
    first = Journal(path)
    snapshot = rows(path, 'SELECT key, payload FROM executions ORDER BY key'), first.upgrade_note()
    for _ in range(2):
        again = Journal(path)
        assert (rows(path, 'SELECT key, payload FROM executions ORDER BY key'), again.upgrade_note()) == snapshot
    assert len(backups(path.parent)) == 1
    new = Journal(tmp_path / 'new' / 'trades.sqlite3')
    Journal(new.path)
    assert not backups(new.path.parent) and new.upgrade_note() is None and not new.recalculation_due()
    assert rows(new.path, 'PRAGMA user_version') == [(store.SCHEMA_VERSION,)]


def test_openers_racing_on_an_old_journal_upgrade_it_once_and_none_of_them_fails(tmp_path):
    import threading
    path, _ = journal_0_3_0(tmp_path / 'home')
    before = rows(path, 'SELECT key, payload, raw FROM executions ORDER BY key')
    opened, start = [], threading.Barrier(6)

    def open_it():
        start.wait()
        try:
            opened.append(len(Journal(path).trades()))
        except Exception as exc:                           # reported by the assertion below, with where it was raised
            opened.append(''.join(traceback.format_exception(exc)))
    threads = [threading.Thread(target=open_it) for _ in range(6)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert opened == [7] * 6 and rows(path, 'PRAGMA user_version') == [(store.SCHEMA_VERSION,)]
    assert Journal(path).upgrade_note()['executions_rewritten'] == 12
    # Whoever copied it, every backup is the journal as 0.3.0 left it, and no unfinished copy remains.
    assert backups(path.parent) and not list(path.parent.glob('*.partial*'))
    for backup in backups(path.parent):
        assert rows(backup, 'PRAGMA user_version') == [(2,)]
        assert rows(backup, 'SELECT key, payload, raw FROM executions ORDER BY key') == before


def test_interrupted_upgrade_leaves_the_journal_as_it_was_and_completes_on_the_next_open(tmp_path, monkeypatch):
    path, _ = journal_0_3_0(tmp_path / 'home')
    before = [rows(path, 'SELECT key, payload, raw FROM executions ORDER BY key'),
              rows(path, 'SELECT name, payload FROM sync_status'), rows(path, 'SELECT key, value FROM journal_meta ORDER BY key')]
    upgrade = store._upgrade

    def cut_short(db, version):
        upgrade(db, version)                               # every row is rewritten, then the process dies
        raise KeyboardInterrupt
    monkeypatch.setattr(store, '_upgrade', cut_short)
    with pytest.raises(KeyboardInterrupt):
        Journal(path)
    assert rows(path, 'PRAGMA user_version') == [(2,)]
    assert before == [rows(path, 'SELECT key, payload, raw FROM executions ORDER BY key'),
                      rows(path, 'SELECT name, payload FROM sync_status'),
                      rows(path, 'SELECT key, value FROM journal_meta ORDER BY key')]
    monkeypatch.setattr(store, '_upgrade', upgrade)
    journal = Journal(path)
    assert rows(path, 'PRAGMA user_version') == [(store.SCHEMA_VERSION,)]
    assert journal.executions()[0].native_position_id == 'p0'
    assert journal.upgrade_note()['executions_rewritten'] == 12
    # Each attempt backed the untouched journal up first; both copies are the journal as 0.3.0 left it.
    for backup in backups(path.parent):
        assert rows(backup, 'SELECT key, payload, raw FROM executions ORDER BY key') == before[0]
        assert rows(backup, 'PRAGMA user_version') == [(2,)]


def test_no_backup_means_no_upgrade(tmp_path, monkeypatch):
    path, _ = journal_0_3_0(tmp_path / 'home')
    before = rows(path, 'SELECT key, payload, raw FROM executions ORDER BY key')
    full = sqlite3.OperationalError('database or disk is full')

    def fails(self, db, version):
        raise full
    monkeypatch.setattr(Journal, '_backup_before_upgrade', fails)
    with pytest.raises(sqlite3.OperationalError):
        Journal(path)
    # A copy that turned out not to be of this journal as it is counts as no copy at all.
    monkeypatch.setattr(Journal, '_backup_before_upgrade', lambda self, db, version: None)
    with pytest.raises(ValueError, match='Open it again'):
        Journal(path)
    assert rows(path, 'PRAGMA user_version') == [(2,)] and not backups(path.parent)
    assert rows(path, 'SELECT key, payload, raw FROM executions ORDER BY key') == before
    monkeypatch.undo()
    assert Journal(path).executions()[0].native_position_id == 'p0' and len(backups(path.parent)) == 1


def test_live_and_replay_are_upgraded_and_backed_up_apart_and_the_wrong_file_is_refused_untouched(tmp_path, monkeypatch):
    home = tmp_path / 'home'
    monkeypatch.setenv('MWJ_HOME', str(home))
    live, _ = journal_0_3_0(home)
    replay, _ = journal_0_3_0(home / 'replay', dataset='replay')
    assert not backups(home) and not backups(home / 'replay')
    with pytest.raises(ValueError, match='other trading environment'):
        Journal(replay)                                    # the replay file opened as the live journal
    with pytest.raises(ValueError, match='other trading environment'):
        Journal(live, dataset='replay')
    assert rows(live, 'PRAGMA user_version') == rows(replay, 'PRAGMA user_version') == [(2,)]
    assert not backups(home) and not backups(home / 'replay')
    assert Journal().path == live and len(Journal().trades()) == 7
    assert len(backups(home)) == 1 and not backups(home / 'replay') and rows(replay, 'PRAGMA user_version') == [(2,)]
    journal = Journal(dataset='replay')
    assert journal.path == replay and not journal.executions()
    assert len(backups(home)) == 1 and len(backups(home / 'replay')) == 1
    assert rows(backups(home / 'replay')[0], "SELECT value FROM journal_meta WHERE key='dataset'") == [('replay',)]
    # The replay journal had the same false alarm and loses it the same way.
    status = journal.get('sync_status')['MotiveWave']
    assert status['status'] == 'synced' and status['warnings'] == PARTIAL and 'error' not in status


def test_upgrade_turns_the_stored_partial_capture_alarm_into_a_warning_and_keeps_real_failures(tmp_path):
    path, _ = journal_0_3_0(tmp_path / 'home')
    broken = {'status': 'needs attention', 'checked': '2026-10-09T09:00:00+00:00',
              'error': 'Native MotiveWave adapter unavailable: NoSuchMethodException',
              'heartbeat': {**BEAT, 'detail': 'Native MotiveWave adapter unavailable: NoSuchMethodException'}}
    unread = {'status': 'needs attention', 'checked': '2026-10-09T09:00:00+00:00', 'error': 'The watched file does not exist.'}
    # The file could not be read after the last heartbeat: that failure is not the heartbeat's.
    later = {'status': 'needs attention', 'checked': '2026-10-09T09:00:00+00:00', 'error': 'Line 3: Unknown event type.',
             'heartbeat': BEAT}
    db = sqlite3.connect(path)
    with db:
        for name, status in (('broken', broken), ('unread', unread), ('later', later)):
            db.execute('INSERT INTO sync_status VALUES(?,?)', (name, json.dumps(status)))
    db.close()
    assert json.loads(rows(path, "SELECT payload FROM sync_status WHERE name='MotiveWave'")[0][0])['error'] == PARTIAL
    journal = Journal(path)
    statuses = journal.get('sync_status')
    study = statuses['MotiveWave']
    assert study['status'] == 'synced' and study['warnings'] == PARTIAL and 'error' not in study
    assert study['heartbeat']['detail'] == PARTIAL and study['executions'] == 12       # the rest of it is untouched
    assert (statuses['broken'], statuses['unread'], statuses['later']) == (broken, unread, later)
    assert journal.upgrade_note()['alarms_cleared'] == 1


def test_reviews_stay_on_trades_that_begin_with_the_same_fill_and_the_rest_are_reported_not_deleted(tmp_path):
    path, _ = journal_0_3_0(tmp_path / 'home')
    false_short, swallowed = match(fills(unlabelled(history())))[:2]      # as 0.3.0 paired these fills
    assert [f['execution_id'] for f in swallowed.fills][0] == 'a2'
    db = sqlite3.connect(path)
    with db:
        db.execute('INSERT INTO annotations VALUES(?,?)', (false_short.id, json.dumps({'notes': 'kept', 'tags': []})))
        db.execute('INSERT INTO annotations VALUES(?,?)', (swallowed.id, json.dumps({'notes': 'adrift', 'tags': []})))
        db.execute('INSERT INTO media VALUES(?,?,?)', ('m1', false_short.id, json.dumps({'kind': 'screenshot'})))
        db.execute('INSERT INTO media VALUES(?,?,?)', ('m2', swallowed.id, json.dumps({'kind': 'screenshot'})))
    db.close()
    journal = Journal(path)
    lone_exit = journal.trades()[0]
    assert lone_exit.id == false_short.id and lone_exit.incomplete == [ENTRY_MISSING]
    assert journal.annotations()[lone_exit.id]['notes'] == 'kept' and len(journal.trade_media(lone_exit.id)) == 1
    assert journal.detached_reviews() == {'annotations': [swallowed.id], 'media': [swallowed.id],
                                          'checklist_runs': [], 'checkins': [], 'drafts': []}
    assert journal.annotations()[swallowed.id]['notes'] == 'adrift' and len(journal.trade_media(swallowed.id)) == 1


def test_watcher_measures_every_trade_again_once_and_the_platforms_own_reports_go(tmp_path):
    """MEASUREMENT_RULES 3: the regrouped trades and the unchanged ones are measured again from their stored
    prices, the MAE / MFE a platform reported (which no price can rebuild, and which 0.3.0 kept) are removed, and
    Settings says so once. A second pass finds nothing to do."""
    path, _ = journal_0_3_0(tmp_path / 'home')
    # 0.3.0 had a platform's report for the trade that is the same before and after: the last round trip, p5.
    unchanged = Journal(tmp_path / 'scratch.sqlite3')
    apply_preview(unchanged, preview_bridge(lines(unlabelled(history()[9:11])), dataset='live'))
    kept = unchanged.trades()[0]
    unchanged.record_metric(kept, {'source': 'platform:reported', 'precision': 'platform-reported', 'mae': 60, 'mfe': 20,
                                   'basis': 'last', 'observations': 0, 'path': [], 'notes': []})
    stored, = rows(unchanged.path, 'SELECT trade_id, basis, source, payload FROM metrics')
    db = sqlite3.connect(path)
    with db:
        db.execute('INSERT INTO metrics VALUES(?,?,?,?)', stored)
        db.execute('INSERT INTO metrics VALUES(?,?,?,?)', ('gone', 'old-basis', *stored[2:]))   # a trade long gone
    db.close()
    journal = Journal(path)
    assert journal.recalculation_due()
    round_trip, last = journal.trades()[1], journal.trades()[5]
    assert last.id == kept.id and [m['precision'] for m in journal.metric_options(last)] == ['platform-reported']
    assert journal.metric_options(round_trip) == []                              # 0.3.0 never saw this trade
    SyncWorker(journal).poll()                                                   # nothing new in the file
    measured, = journal.metric_options(round_trip)
    bars = journal.prices('NQZ6', 'motivewave-bars:1m', start=round_trip.opened, end=round_trip.closed)
    assert measured == excursions(round_trip, bars, 'motivewave-bars:1m')
    assert (measured['mae'], measured['mfe'], measured['precision']) == (100, 240, 'estimated-bars')
    assert not journal.recalculation_due()
    assert rows(path, "SELECT count(*) FROM metrics WHERE json_extract(payload, '$.precision')='platform-reported'") == [(0,)]
    assert 'platform-reported' not in [m['precision'] for m in journal.metric_options(last)]
    assert all(journal.metric_options(t) == [] for t in journal.trades() if not t.complete)
    note = journal.upgrade_note()['measurement_rules_3']
    assert note['reports_removed'] == 2 and journal.unseen_notes()['measurement_rules_3'] == store.MEASURED_AGAIN
    # Once only: a report stored again stays, and the note is the same one.
    journal.record_metric(last, json.loads(stored[3]))
    journal.recalculation_done()
    SyncWorker(Journal(path)).poll()
    assert 'platform-reported' in [m['precision'] for m in journal.metric_options(last)]
    assert journal.upgrade_note()['measurement_rules_3'] == note


def test_a_journal_with_nothing_measured_is_told_nothing_about_measuring_again(tmp_path):
    journal = Journal(tmp_path / 'replay.sqlite3', dataset='replay')       # the Replay journal of a live-only trader
    with journal.connect() as db:
        db.execute("UPDATE journal_meta SET value='2' WHERE key='measurement_rules'")
    assert journal.recalculation_due()
    SyncWorker(journal).recalculate()
    assert not journal.recalculation_due() and journal.upgrade_note() is None and journal.unseen_notes() == {}


def test_points_stored_under_rules_2_become_the_points_of_the_dollars_moment_after_one_pass(tmp_path):
    """Long 2 at 100, a dip to 97, sell 1 at 102, the runner dips to 96: rules 2 stored the runner's 4 points beside
    the $300 of the dip with 2 held. The pass measures it again from its stored ticks: 3 points, the same moment."""
    from test_core import fill, tick
    journal = Journal(tmp_path / 'trades.db')
    journal.ingest([(fill('a', 'buy', 2, 100), {}), (fill('b', 'sell', 1, 102, 1), {}), (fill('c', 'sell', 1, 101, 2), {})])
    journal.ingest_prices([tick(0, 97), tick(1, 96)])
    trade, = journal.trades()
    old = {'source': 'test-prices', 'basis': 'last', 'precision': 'observed-ticks', 'mae': 300., 'mfe': 200.,
           'mae_points': 4., 'mfe_points': 2., 'path': [], 'observations': 2}
    journal.record_metric(trade, old)
    with journal.connect() as db:
        db.execute("UPDATE journal_meta SET value='2' WHERE key='measurement_rules'")
    SyncWorker(Journal(journal.path)).recalculate()
    metric, = journal.metric_options(trade)
    assert (metric['mae'], metric['mae_points'], metric['mfe'], metric['mfe_points']) == (300, 3, 200, 2)
    assert not journal.recalculation_due() and 'measurement_rules_3' in journal.unseen_notes()


def test_fills_that_never_had_position_fields_are_left_byte_for_byte(tmp_path):
    path = tmp_path / 'trades.sqlite3'
    journal = Journal(path)
    apply_preview(journal, preview_csv('symbol,side,qty,price,ts,id\nESZ6,buy,1,5000,2026-10-07T14:00:00Z,a\n'
                                       'ESZ6,sell,1,5002,2026-10-07T14:01:00Z,b\n'))
    apply_preview(journal, preview_bridge(lines(unlabelled(history()[1:3])), dataset='live'))
    journal.ingest([(fills(unlabelled(history()[9:10]))[0], {})])
    trades = [(t.id, t.net, t.basis) for t in journal.trades()]
    as_schema_2(path)
    before = rows(path, 'SELECT key, payload, raw FROM executions ORDER BY key')
    upgraded = Journal(path)
    assert rows(path, 'SELECT key, payload, raw FROM executions ORDER BY key') == before
    assert rows(path, 'PRAGMA user_version') == [(store.SCHEMA_VERSION,)] and len(backups(tmp_path)) == 1
    assert upgraded.upgrade_note()['executions_rewritten'] == 0
    assert upgraded.recalculation_due()          # nothing regrouped, but measured under older rules: once more
    assert [(t.id, t.net, t.basis) for t in upgraded.trades()] == trades


def test_the_watcher_moves_a_review_left_behind_to_the_trade_that_now_holds_its_fill(tmp_path):
    path, _ = journal_0_3_0(tmp_path / 'home')
    false_short, swallowed = match(fills(unlabelled(history())))[:2]
    holder = next(t for t in Journal(path).trades() if 'a2' in [f['execution_id'] for f in t.fills])
    assert holder.id != swallowed.id
    db = sqlite3.connect(path)
    with db:
        db.execute('INSERT INTO annotations VALUES(?,?)', (swallowed.id, json.dumps({'notes': 'adrift', 'tags': []})))
        db.execute('INSERT INTO media VALUES(?,?,?)', ('m2', swallowed.id, json.dumps({'kind': 'screenshot'})))
    db.close()
    journal = Journal(path)
    SyncWorker(journal).trades()
    assert journal.annotations()[holder.id]['notes'] == 'adrift' and len(journal.trade_media(holder.id)) == 1
    assert swallowed.id not in journal.annotations()
    assert not any(journal.detached_reviews().values())


def test_a_review_left_behind_never_replaces_the_review_its_fill_now_has(tmp_path):
    path, _ = journal_0_3_0(tmp_path / 'home')
    false_short, swallowed = match(fills(unlabelled(history())))[:2]
    holder = next(t for t in Journal(path).trades() if 'a2' in [f['execution_id'] for f in t.fills])
    db = sqlite3.connect(path)
    with db:
        db.execute('INSERT INTO annotations VALUES(?,?)', (holder.id, json.dumps({'notes': 'its own', 'tags': []})))
        db.execute('INSERT INTO annotations VALUES(?,?)', (swallowed.id, json.dumps({'notes': 'adrift', 'tags': []})))
        db.execute('INSERT INTO media VALUES(?,?,?)', ('m2', swallowed.id, json.dumps({'kind': 'screenshot'})))
    db.close()
    journal = Journal(path)
    SyncWorker(journal).trades()
    assert journal.annotations()[holder.id]['notes'] == 'its own' and len(journal.trade_media(holder.id)) == 1
    assert journal.detached_reviews()['annotations'] == [swallowed.id]       # still stored, still reported


# ---------------------------------------------------------------- schema 4: one time zone

NY, LA = 'America/New_York', 'America/Los_Angeles'
PATIENT, FOMO = tagging.identity('Patient'), tagging.identity('FOMO')
PLAN, LIMITS, STOP = (tagging.identity(name) for name in DAY[2])


def library_0_4_16():
    """The tag library as 0.4.16 stored it: the starters without the Day category, the day-tag time zone (New York),
    Mindset one choice at a time, and Patient and FOMO also on days."""
    state = tagging.starter_library()
    del state['groups']['Day']
    state['tags'] = {id: tag for id, tag in state['tags'].items() if tag['group'] != 'Day'}
    state['groups']['Mindset']['exclusive'] = True
    for id in (PATIENT, FOMO):
        state['tags'][id]['levels'] = ['trade', 'day']
    return {**state, 'session_timezone': NY}


def as_schema_3(path, days, *, zone=LA, library=None):
    """Put a journal back as 0.4.16 stored its days: each under 'date:zone', with the Daily Report Card's three boxes;
    a tag library that keeps the day-tag zone (library_0_4_16); the Time Zone `zone`, None for never chosen."""
    db = sqlite3.connect(path)
    with db:
        db.execute('DELETE FROM sessions')
        db.executemany('INSERT INTO sessions VALUES(?,?)', [(key, json.dumps(day)) for key, day in days.items()])
        db.execute("INSERT OR REPLACE INTO journal_meta VALUES('tag_library',?)",
                   (json.dumps(library or library_0_4_16()),))
        db.execute("INSERT OR REPLACE INTO journal_meta VALUES('workspace',?)",
                   (json.dumps({'preferences': {'display_timezone': zone} if zone else {}}),))
        db.execute('PRAGMA user_version=3')
    db.close()


def near_midnight(journal):
    """A trade at 10 PM on Oct 7 in Los Angeles, already 1 AM on Oct 8 in New York."""
    journal.ingest([(Execution('motivewave', 'paper', 'ESZ6', side, 1, price, ts, execution_id=id), {})
                    for side, price, ts, id in (('buy', 5000, '2026-10-08T05:00:00Z', 'late-in'),
                                                ('sell', 5001, '2026-10-08T05:02:00Z', 'late-out'))])
    return next(t for t in journal.trades() if t.opened.startswith('2026-10-08'))


def test_a_schema_3_journal_keeps_one_day_per_date_in_its_time_zone_and_its_ticked_boxes_become_day_tags(tmp_path):
    path = tmp_path / 'trades.sqlite3'
    journal = Journal(path)
    morning, late = round_trip(journal), near_midnight(journal)        # 7 AM and 10 PM on Oct 7 in Los Angeles
    journal.ingest_prices(tape(4, minutes=6))
    assert journal.pack_prices()
    prices = rows(path, 'SELECT rowid, key, symbol, source, ts, payload FROM prices ORDER BY rowid')
    assert any('"packed"' in row[-1] for row in prices)
    # Day tags in New York (the day-tag zone), and a review of Oct 7 in each zone, as 0.4.16 kept them.
    as_schema_3(path, {
        f'2026-10-07:{NY}': {'date': '2026-10-07', 'timezone': NY, 'day_tag_ids': [PATIENT], 'planned': True,
                             'risk_limits': False, 'stops': False, 'rating': 2, 'notes': 'Waited for the open.'},
        f'2026-10-07:{LA}': {'date': '2026-10-07', 'timezone': LA, 'day_tag_ids': [FOMO], 'stops': True, 'rating': 4,
                             'notes': 'Chased the first push.'},
        f'2026-10-08:{NY}': {'date': '2026-10-08', 'timezone': NY, 'day_tag_ids': [PATIENT, FOMO], 'planned': False,
                             'notes': ''}})
    before = rows(path, 'SELECT key, payload FROM sessions ORDER BY key')
    upgraded = Journal(path)
    backup, = backups(tmp_path)
    assert backup.name.startswith('trades.schema-3-backup-')
    assert rows(backup, 'SELECT key, payload FROM sessions ORDER BY key') == before
    # One row per date. In Mindset, one choice at a time, the Time Zone's FOMO wins; the Settings-zone review wins and
    # the other fills its blanks; the two notes are joined; ticked boxes are Day tags; unticked ones leave nothing.
    # Oct 8's Patient and FOMO, chosen before Mindset became one choice, both stay: only another day's choice yields.
    assert upgraded.get('sessions') == {
        '2026-10-07': {'date': '2026-10-07', 'day_tag_ids': sorted([FOMO, PLAN, STOP]), 'rating': 4,
                       'notes': 'Chased the first push.\n\nWaited for the open.'},
        '2026-10-08': {'date': '2026-10-08', 'day_tag_ids': sorted([PATIENT, FOMO]), 'notes': ''}}
    library = upgraded.tag_library()
    assert 'session_timezone' not in library and not library.get('removed')
    assert [(t['name'], t['levels']) for t in tagging.ordered_groups(library, level='day')['Day']] == [
        (name, ['day']) for name in DAY[2]]
    assert {t['name'] for t in library['tags'].values()} == {*(n for _, _, names in STARTERS for n in names)}
    # Both trades are on Oct 7 in Los Angeles: the late one moved off New York's Oct 8 and its Patient.
    tags = {row['id']: row['tags'] for row in common.rows_for(upgraded)}
    assert tags[morning.id] == tags[late.id] == ['FOMO', 'Had a Plan', 'Respected My Stop']
    note = upgraded.upgrade_note()
    assert (note['from'], note['to'], note['days']['note']) == (
        3, 4, f'Days now follow your Time Zone ({LA}); day reviews moved to their dates and the day checkboxes became '
              'Day tags.')
    assert 'packed' in note                                               # the note of the first packing stays
    assert rows(path, 'SELECT rowid, key, symbol, source, ts, payload FROM prices ORDER BY rowid') == prices


def test_a_schema_2_journal_takes_both_steps_and_gets_this_computers_time_zone(tmp_path, monkeypatch):
    monkeypatch.setattr(humanize, 'local_zone', lambda: 'Europe/Berlin')
    path, _ = journal_0_3_0(tmp_path / 'home')
    db = sqlite3.connect(path)
    with db:
        db.execute("DELETE FROM journal_meta WHERE key IN ('workspace', 'tag_library')")     # nothing chosen yet
        db.execute('INSERT INTO sessions VALUES(?,?)', (f'2026-09-21:{NY}', json.dumps(
            {'date': '2026-09-21', 'timezone': NY, 'risk_limits': True, 'notes': 'Small size.'})))
    db.close()
    assert rows(path, 'PRAGMA user_version') == [(2,)]
    journal = Journal(path)
    assert rows(path, 'PRAGMA user_version') == [(4,)] and journal.executions()[0].native_position_id == 'p0'
    assert journal.time_zone() == 'Europe/Berlin'                         # saved: it stays if the computer travels
    assert journal.get('sessions') == {'2026-09-21': {'date': '2026-09-21', 'day_tag_ids': [LIMITS],
                                                      'notes': 'Small size.'}}
    assert journal.tag_library()['tags'][LIMITS]['group'] == 'Day'
    note = journal.upgrade_note()
    assert (note['from'], note['to'], note['executions_rewritten'], note['days']['zone']) == (2, 4, 12, 'Europe/Berlin')
    monkeypatch.setattr(humanize, 'local_zone', lambda: 'Asia/Tokyo')
    assert Journal(path).time_zone() == 'Europe/Berlin'


def test_opening_an_upgraded_journal_again_changes_nothing_and_the_step_is_harmless_twice(tmp_path):
    path = tmp_path / 'trades.sqlite3'
    round_trip(Journal(path))
    library = library_0_4_16()
    library['tags'][tagging.identity('Moved Stop')]['aliases'].append('respected my stop')   # once named so
    as_schema_3(path, {f'2026-10-07:{NY}': {'date': '2026-10-07', 'timezone': NY, 'stops': True, 'planned': True}},
                library=library)
    Journal(path)

    def everything():
        return [rows(path, f'SELECT * FROM {table} ORDER BY 1') for table in ('sessions', 'journal_meta')]
    after = everything()
    # A tag once named Respected My Stop, in another case, is that Day tag: no second one is made.
    stop, again = tagging.identity('Moved Stop'), Journal(path)
    assert again.get('sessions')['2026-10-07']['day_tag_ids'] == sorted([PLAN, stop])
    assert again.tag_library()['tags'][stop]['levels'] == ['trade', 'day'] and STOP not in again.tag_library()['tags']
    assert everything() == after and len(backups(tmp_path)) == 1
    db = sqlite3.connect(path)
    db.row_factory = sqlite3.Row
    with db:
        store._upgrade(db, 3)                                             # the step itself, once more
    db.close()
    assert everything() == after


# ---------------------------------------------------------------- typed setups and 'Followed my plan' become tags

SETUP, EXECUTION = (colour for group, colour, _ in STARTERS if group in ('Setup', 'Execution'))


def as_0_4_16(path, reviews, *, library=None):
    """Put reviews back as 0.4.16 stored them, a typed setup and a 'Followed my plan' answer in any of them, with the
    starter library (or `library`), never moved to tags."""
    db = sqlite3.connect(path)
    with db:
        db.executemany('INSERT OR REPLACE INTO annotations VALUES(?,?)',
                       [(id, json.dumps(review)) for id, review in reviews.items()])
        db.execute("INSERT OR REPLACE INTO journal_meta VALUES('tag_library',?)",
                   (json.dumps(library or tagging.starter_library()),))
        db.execute("DELETE FROM journal_meta WHERE key IN ('setups_to_tags', 'adherence_to_tags')")
    db.close()


def stored(path, id):
    """A review exactly as stored."""
    return json.loads(rows(path, f"SELECT payload FROM annotations WHERE trade_id='{id}'")[0][0])


def meta(path, key):
    found = rows(path, f"SELECT value FROM journal_meta WHERE key='{key}'")
    return found[0][0] if found else None


def test_typed_setups_become_one_setup_tag_per_spelling_named_as_typed_most_often(tmp_path):
    path = tmp_path / 'trades.sqlite3'
    Journal(path)
    as_0_4_16(path, {'a': {'setup': 'ORB break', 'notes': 'Kept'}, 'b': {'setup': '  ORB break '},
                     'c': {'setup': 'orb  Break', 'tags': ['Calm']}, 'd': {'setup': '   '}, 'e': {'notes': 'No setup'},
                     'f': {'setup': 'Opening drive ' + 'x' * 60}, 'g': {'setup': 'pullback'}})
    journal = Journal(path)
    library = journal.tag_library()
    orb, = (id for id, tag in library['tags'].items() if tag['name'].casefold() == 'orb break')
    long, = (id for id, tag in library['tags'].items() if tag['name'].startswith('Opening drive'))
    assert library['tags'][orb]['name'] == 'ORB break' and library['tags'][orb]['group'] == 'Setup'
    assert library['tags'][orb]['color'] == SETUP and library['tags'][orb]['levels'] == ['trade']
    assert library['tags'][long]['name'] == ('Opening drive ' + 'x' * 60)[:64]
    reviews = journal.annotations()
    assert [reviews[id]['tags'] for id in 'abcfg'] == [['ORB break'], ['ORB break'], ['Calm', 'ORB break'],
                                                      [library['tags'][long]['name']], ['Pullback']]
    assert reviews['a']['notes'] == 'Kept' and not reviews['d']['tags'] and not reviews['e']['tags']
    assert stored(path, 'b')['setup'] == '  ORB break '                   # left stored, no longer read
    assert stored(path, 'g')['tag_ids'] == [tagging.identity('Pullback')]  # the starter tag, not a second one
    assert (meta(path, 'setups_to_tags'), meta(path, 'adherence_to_tags')) == ('5', '0')
    assert journal.upgrade_note()['answers']['note'] == 'Typed setups became Setup tags on 5 trades.'
    assert not backups(tmp_path)                                          # nothing rewritten that a backup must keep


def test_a_typed_setup_reuses_a_tag_of_that_name_in_any_case_category_or_former_name_even_retired(tmp_path):
    path = tmp_path / 'trades.sqlite3'
    Journal(path)
    library = tagging.starter_library()
    hesitant, range_ = tagging.identity('Hesitant'), tagging.identity('Range')
    library['tags'][hesitant]['retired'] = True
    library = tagging.edit_tag(library, range_, name='Range day')        # Range is now a former name
    as_0_4_16(path, {'a': {'setup': 'patient'}, 'b': {'setup': 'HESITANT'}, 'c': {'setup': 'range'},
                     'd': {'setup': ' range   DAY '}}, library=library)
    journal = Journal(path)
    reviews = journal.annotations()
    assert [reviews[id]['tags'] for id in 'abcd'] == [['Patient'], ['Hesitant'], ['Range day'], ['Range day']]
    after = journal.tag_library()
    assert len(after['tags']) == len(library['tags'])                     # no tag made
    assert after['tags'][PATIENT]['group'] == 'Mindset' and after['tags'][hesitant]['retired']


def test_a_typed_setup_is_added_beside_a_one_choice_setup_already_chosen(tmp_path):
    path = tmp_path / 'trades.sqlite3'
    journal = Journal(path)
    trade = round_trip(journal)
    library = tagging.starter_library()
    library['groups']['Setup']['exclusive'] = True
    as_0_4_16(path, {trade.id: {'tags': ['Pullback'], 'tag_ids': [tagging.identity('Pullback')], 'setup': 'Breakout'}},
              library=library)
    journal = Journal(path)
    assert journal.annotations()[trade.id]['tags'] == ['Breakout', 'Pullback']    # nothing removed: both stay
    assert next(r for r in common.rows_for(journal) if r['id'] == trade.id)['tags'] == ['Breakout', 'Pullback']
    journal.set_trade_tag_group(trade, 'Setup', ['Breakout'])           # one click on the panel resolves it
    assert journal.annotations()[trade.id]['tags'] == ['Breakout']


def test_followed_my_plan_yes_and_no_become_execution_tags(tmp_path):
    path = tmp_path / 'trades.sqlite3'
    Journal(path)
    library = tagging.starter_library()
    followed = tagging.identity('Followed Plan')
    library = tagging.edit_tag(library, followed, name='Followed plan')   # matched in any case
    as_0_4_16(path, {'a': {'adherence': 'Yes'}, 'b': {'adherence': 'No', 'tags': ['Calm']},
                     'c': {'adherence': 'Unreviewed'}, 'd': {'adherence': 'No', 'setup': 'Fade'}}, library=library)
    journal = Journal(path)
    reviews, library = journal.annotations(), journal.tag_library()
    assert [reviews[id]['tags'] for id in 'abcd'] == [['Followed plan'], ['Broke Plan', 'Calm'], [],
                                                     ['Broke Plan', 'Fade']]
    broke = library['tags'][tagging.identity('Broke Plan')]
    assert (broke['group'], broke['color'], broke['levels']) == ('Execution', EXECUTION, ['trade'])
    assert (meta(path, 'setups_to_tags'), meta(path, 'adherence_to_tags')) == ('1', '3')
    assert journal.upgrade_note()['answers']['note'] == (
        'Typed setups became Setup tags on 1 trade; Followed my plan answers became Execution tags on 3 trades.')


def test_a_missing_execution_category_is_made_in_its_own_colour(tmp_path):
    path = tmp_path / 'trades.sqlite3'
    Journal(path)
    library = tagging.starter_library()
    library['tags'] = {id: tag for id, tag in library['tags'].items() if tag['group'] != 'Execution'}
    del library['groups']['Execution']
    as_0_4_16(path, {'a': {'adherence': 'Yes'}}, library=library)
    after = Journal(path).tag_library()
    tag = after['tags'][tagging.identity('Followed Plan')]
    assert (tag['group'], tag['color']) == ('Execution', EXECUTION) and 'Execution' in after['groups']


def test_a_replay_journal_moves_its_own_answers(tmp_path):
    path = tmp_path / 'replay.sqlite3'
    Journal(path, dataset='replay')
    as_0_4_16(path, {'a': {'setup': 'Fade', 'adherence': 'Yes'}})
    journal = Journal(path, dataset='replay')
    assert journal.annotations()['a']['tags'] == ['Fade', 'Followed Plan']
    assert journal.tag_library()['tags'][tagging.identity('Fade')]['group'] == 'Setup'


def test_each_move_runs_once_and_a_new_journal_has_nothing_to_move(tmp_path):
    path = tmp_path / 'trades.sqlite3'
    Journal(path)
    as_0_4_16(path, {'a': {'setup': 'Fade', 'adherence': 'No'}})
    Journal(path)

    def everything():
        return [rows(path, f'SELECT * FROM {table} ORDER BY 1') for table in ('annotations', 'journal_meta')]
    after = everything()
    Journal(path)
    assert everything() == after
    db = sqlite3.connect(path)
    with db:                                       # an answer stored again in a field no version shows any more
        db.execute("INSERT INTO annotations VALUES('b', ?)", (json.dumps({'setup': 'Late', 'adherence': 'Yes'}),))
    db.close()
    journal = Journal(path)
    assert not journal.annotations()['b']['tags'] and 'Late' not in journal.tag_catalog()
    assert (meta(path, 'setups_to_tags'), meta(path, 'adherence_to_tags')) == ('1', '1')
    new = Journal(tmp_path / 'new.sqlite3')
    assert (meta(new.path, 'setups_to_tags'), meta(new.path, 'adherence_to_tags')) == ('0', '0')
    assert new.upgrade_note() is None and meta(new.path, 'tag_library') is None   # the starters still come at first read


# ---------------------------------------------------------------- one connection: the study's file alone

def as_0_4_16_connections(journal):
    """Put a journal back as 0.4.16 left it: free to watch other files, its study connection pointed anywhere."""
    with journal.connect() as db:
        db.execute("DELETE FROM journal_meta WHERE key='one_connection'")


def test_the_migration_removes_a_watched_csv_and_keeps_the_default_connection(tmp_path, monkeypatch):
    home = tmp_path / 'home'
    monkeypatch.setenv('MWJ_HOME', str(home))
    journal = Journal()
    csv = tmp_path / 'fills.csv'
    csv.write_text('symbol,side,qty,price,ts,id\nESZ6,buy,1,5000,2026-10-07T14:00:00Z,a\n'
                   'ESZ6,sell,1,5002,2026-10-07T14:01:00Z,b\n')
    apply_preview(journal, preview_csv(csv.read_text()))                   # what the watched CSV brought in
    journal.put('profiles', 'Broker export', {'path': str(csv), 'mode': 'executions', 'enabled': True,
                                              'timezone': 'America/New_York', 'provider': 'csv', 'mapping': None})
    study = {'path': str(paths.bridge_file()), 'mode': 'bridge', 'enabled': True}
    journal.put('profiles', 'MotiveWave', study)
    for name in ('Broker export', 'MotiveWave', 'worker'):
        journal.put('sync_status', name, {'status': 'synced', 'checked': '2026-10-09T09:00:00+00:00'})
    for name in ('Broker export', 'MotiveWave'):
        journal.set_bridge_offset(name, '1', 2, b'}\n')
    as_0_4_16_connections(journal)
    opened = Journal()
    assert opened.get('profiles') == {'MotiveWave': study}
    # What the other file said, and how far it was read, no longer apply; the study's connection and the reader's own
    # status stay as they were.
    assert sorted(opened.get('sync_status')) == ['MotiveWave', 'worker']
    assert opened.bridge_offset('Broker export') is None and opened.bridge_offset('MotiveWave') == ('1', 2, b'}\n')
    assert len(opened.trades()) == 1 and opened.trades()[0].provider == 'csv'      # the trades it read stay
    note = opened.upgrade_note()['connections']
    assert note['note'] == ("The journal now reads only the study's own file; 1 other watched file (Broker export) "
                            'was removed, and the trades read from it stay.')
    assert opened.unseen_notes() == {'connections': note['note']}
    assert not backups(home)                                                    # nothing rewritten: no backup
    # Twice: nothing left to remove, and the note stays as it was.
    as_0_4_16_connections(opened)
    again = Journal()
    assert again.get('profiles') == opened.get('profiles') and again.upgrade_note()['connections'] == note
    assert meta(again.path, 'one_connection') == '0'
    # A journal reading nothing but the study's file has nothing to say.
    replay = Journal(dataset='replay')
    replay.put('profiles', 'MotiveWave', {'path': str(paths.bridge_file('replay')), 'mode': 'bridge', 'enabled': True})
    as_0_4_16_connections(replay)
    assert Journal(dataset='replay').upgrade_note() is None


# ---------------------------------------------------------------- Stats always shows every tab

def as_0_4_17_tabs(journal, tabs):
    """The Stats tabs Customize saved until 0.4.17, and a journal that has not taken the step yet."""
    with journal.connect() as db:
        db.execute("INSERT OR REPLACE INTO journal_meta VALUES('display_settings', ?)",
                   (json.dumps({'excursion_unit': 'r', 'dashboard_sections': tabs}),))
        db.execute("DELETE FROM journal_meta WHERE key='fixed_stats_tabs'")


def test_a_saved_choice_of_stats_tabs_is_left_unread_and_said_once_with_no_backup(tmp_path):
    path = tmp_path / 'trades.sqlite3'
    as_0_4_17_tabs(Journal(path), ['Runners', 'Overview'])
    opened = Journal(path)
    assert opened.display_settings() == {'excursion_unit': 'r', 'tick_sizes': {}}        # the tabs are not read
    note = 'Stats now always shows all of its tabs: Overview, Compare, MAE / MFE, After Exit and Dashboards.'
    assert opened.unseen_notes() == {'stats_tabs': note}
    assert not backups(tmp_path) and meta(path, 'fixed_stats_tabs') == '1'
    with opened.connect() as db:                   # stored as it was, unread
        stored = json.loads(db.execute("SELECT value FROM journal_meta WHERE key='display_settings'").fetchone()[0])
    assert stored['dashboard_sections'] == ['Runners', 'Overview']
    # Twice: the same note, once.
    with opened.connect() as db:
        db.execute("DELETE FROM journal_meta WHERE key='fixed_stats_tabs'")
    again = Journal(path)
    assert again.unseen_notes() == {'stats_tabs': note} and meta(path, 'fixed_stats_tabs') == '1'
    # Every tab already shown in this order, as 0.4.17 named them, or no choice saved: nothing to say.
    every = tmp_path / 'every.sqlite3'
    as_0_4_17_tabs(Journal(every), ['Overview', 'Compare', 'MAE / MFE', 'Runners'])
    assert Journal(every).upgrade_note() is None and meta(every, 'fixed_stats_tabs') == '0'
    assert Journal(tmp_path / 'new.sqlite3').upgrade_note() is None
