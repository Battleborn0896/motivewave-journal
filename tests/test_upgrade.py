"""The schema step that gives a 0.3.0 journal the platform's position ids, on invented fills only."""
import json
import sqlite3

import pytest

from mwjournal import Journal, store
from mwjournal.importers import apply_preview, preview_bridge, preview_csv
from mwjournal.matching import match
from mwjournal.metrics import excursions
from mwjournal.model import ENTRY_MISSING, EXIT_MISSING, NO_FILL_PRICE
from mwjournal.sync import SyncWorker
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
    assert rows(path, 'PRAGMA user_version') == [(3,)] and len(after) == 12
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
    assert (note['from'], note['to'], note['executions_rewritten'], note['backup']) == (2, 3, 12, str(backup))
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
    assert rows(new.path, 'PRAGMA user_version') == [(3,)]


def test_openers_racing_on_an_old_journal_upgrade_it_once_and_none_of_them_fails(tmp_path):
    import threading
    path, _ = journal_0_3_0(tmp_path / 'home')
    before = rows(path, 'SELECT key, payload, raw FROM executions ORDER BY key')
    opened, start = [], threading.Barrier(6)

    def open_it():
        start.wait()
        try:
            opened.append(len(Journal(path).trades()))
        except Exception as exc:                           # reported by the assertion below
            opened.append(exc)
    threads = [threading.Thread(target=open_it) for _ in range(6)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert opened == [7] * 6 and rows(path, 'PRAGMA user_version') == [(3,)]
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

    def cut_short(db):
        upgrade(db)                                        # every row is rewritten, then the process dies
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
    assert rows(path, 'PRAGMA user_version') == [(3,)] and journal.executions()[0].native_position_id == 'p0'
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


def test_watcher_measures_the_regrouped_trades_again_and_measurements_of_unchanged_trades_survive(tmp_path):
    path, _ = journal_0_3_0(tmp_path / 'home')
    # 0.3.0 had measured the trade that is the same before and after: the last round trip, p5.
    unchanged = Journal(tmp_path / 'scratch.sqlite3')
    apply_preview(unchanged, preview_bridge(lines(unlabelled(history()[9:11])), dataset='live'))
    kept = unchanged.trades()[0]
    unchanged.record_metric(kept, {'source': 'platform:reported', 'precision': 'platform-reported', 'mae': 60, 'mfe': 20,
                                   'basis': 'last', 'observations': 0, 'path': [], 'notes': []})
    stored, = rows(unchanged.path, 'SELECT trade_id, basis, source, payload FROM metrics')
    db = sqlite3.connect(path)
    with db:
        db.execute('INSERT INTO metrics VALUES(?,?,?,?)', stored)
    db.close()
    journal = Journal(path)
    assert journal.recalculation_due()
    round_trip, last = journal.trades()[1], journal.trades()[5]
    assert last.id == kept.id and journal.best_metric(last)['mae'] == 60          # a report no price can rebuild
    assert journal.metric_options(round_trip) == []                              # 0.3.0 never saw this trade
    SyncWorker(journal).poll()                                                   # nothing new in the file
    measured = journal.best_metric(round_trip)
    bars = journal.prices('NQZ6', 'motivewave-bars:1m', start=round_trip.opened, end=round_trip.closed)
    assert measured == excursions(round_trip, bars, 'motivewave-bars:1m')
    assert (measured['mae'], measured['mfe'], measured['precision']) == (100, 240, 'estimated-bars')
    assert not journal.recalculation_due() and journal.best_metric(last)['precision'] == 'platform-reported'
    assert all(journal.metric_options(t) == [] for t in journal.trades() if not t.complete)


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
    assert rows(path, 'PRAGMA user_version') == [(3,)] and len(backups(tmp_path)) == 1
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
