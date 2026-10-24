"""Back Up Journal and Restore From Backup: one zip of every journal, checked in full before anything is put back."""
import datetime as dt
import json
import sqlite3
import zipfile
from contextlib import closing
from types import SimpleNamespace

import pytest

import runtime
from mwjournal import Execution, Journal, backup, journals, paths, store
from test_app import backup_controls, open_app, press, said
from test_chart import seed_preview


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    monkeypatch.setenv('MWJ_HOME', str(tmp_path))
    yield tmp_path
    runtime.stop_all()


def trade(journal, name, day=7):
    """One ES round trip on its own day."""
    journal.ingest([(Execution('test', 'paper', 'ESZ6', side, 1, price, f'2026-10-{day:02d}T14:0{minute}:00Z',
                               execution_id=f'{name}-{side}', point_value=50), {})
                    for side, price, minute in (('buy', 5000, 0), ('sell', 5002, 5))])


def held(dataset):
    journal = Journal(dataset=dataset)
    return journal.executions(), journal.annotations()


def members(saved) -> dict:
    with zipfile.ZipFile(saved) as archive:
        return {name: archive.read(name) for name in archive.namelist()}


def zipped(path, contents: dict):
    with zipfile.ZipFile(path, 'w') as archive:
        for name, data in contents.items():
            archive.writestr(name, data)
    return path


def test_a_backup_puts_live_and_replay_back_and_saves_the_journal_it_replaces(home):
    live, replay = Journal(), Journal(dataset='replay')
    trade(live, 'live-1')
    trade(replay, 'replay-1')
    live.annotate(live.trades()[0].id, {'notes': 'As it was.'})
    (home / 'Backups').mkdir()
    saved = backup.write(home / 'Backups')
    assert saved == home / 'Backups' / backup.file_name() and list((home / 'Backups').iterdir()) == [saved]
    manifest = json.loads(members(saved)['backup.json'])
    assert sorted(members(saved)) == ['backup.json', 'live.sqlite3', 'replay.sqlite3']
    assert (manifest['app'], manifest['version'], manifest['schema']) == (paths.APP_NAME, paths.VERSION,
                                                                          store.SCHEMA_VERSION)
    before = {dataset: held(dataset) for dataset in paths.DATASETS}
    files = {dataset: paths.database(dataset).stat().st_ino for dataset in paths.DATASETS}
    trade(live, 'live-2', day=8)
    trade(replay, 'replay-2', day=8)
    live.annotate(live.trades()[0].id, {'notes': 'Changed.'})

    summary = backup.read(saved)
    assert summary['trades'] == {'live': 1, 'replay': 1} and summary['version'] == paths.VERSION
    result = backup.restore(saved)
    assert {dataset: held(dataset) for dataset in paths.DATASETS} == before
    assert {dataset: paths.database(dataset).stat().st_ino for dataset in paths.DATASETS} == files   # copied into
    assert result['trades'] == summary['trades']
    previous = result['previous']
    assert previous.parent == home / 'backups' and previous.name.startswith('before-restore-')
    assert backup.read(previous)['trades'] == {'live': 2, 'replay': 2}             # the journal it replaced


class OneSecond(dt.datetime):
    """A clock that stays on one second, so two restores share their time."""
    @classmethod
    def now(cls, tz=None):
        return dt.datetime(2026, 10, 23, 17, 0, 0, tzinfo=tz)


def test_restoring_the_copy_saved_before_a_restore_undoes_it_even_within_the_same_second(home, monkeypatch):
    monkeypatch.setattr(backup, 'dt', SimpleNamespace(datetime=OneSecond, timezone=dt.timezone, date=dt.date))
    live = Journal()
    trade(live, 'live-1')
    saved = backup.write(home / 'backup.zip')
    trade(live, 'live-2', day=8)
    before = held('live')
    first = backup.restore(saved)
    assert len(Journal().trades()) == 1
    undone = backup.restore(first['previous'])
    assert held('live') == before and undone['trades']['live'] == 2
    assert sorted(p.name for p in (home / 'backups').iterdir()) == ['before-restore-20261023T170000Z-2.zip',
                                                                    'before-restore-20261023T170000Z.zip']
    assert backup.read(first['previous'])['trades']['live'] == 2                  # never written over


def refused(path, message):
    with pytest.raises(ValueError) as raised:
        backup.read(path)
    assert str(raised.value) == message
    with pytest.raises(ValueError):
        backup.restore(path)


def test_a_file_that_is_not_a_sound_backup_of_this_version_is_refused_and_changes_nothing(home):
    trade(Journal(), 'live-1')
    good = members(backup.write(home / 'good.zip'))
    before = held('live')
    swapped = zipped(home / 'swapped.zip', {**good, 'replay.sqlite3': good['live.sqlite3']})
    refused(swapped, backup.NOT_A_BACKUP)                     # a live journal in the replay slot
    stray = zipped(home / 'stray.zip', {**good, '../trades.sqlite3': good['live.sqlite3']})
    refused(stray, backup.NOT_A_BACKUP)                       # a member that would land outside the folder
    assert not (home.parent / 'trades.sqlite3').exists()
    refused(zipped(home / 'extra.zip', {**good, 'notes.txt': b'hello'}), backup.NOT_A_BACKUP)
    refused(zipped(home / 'empty.zip', {}), backup.NOT_A_BACKUP)
    (home / 'text.zip').write_text('not a zip')
    refused(home / 'text.zip', backup.NOT_A_BACKUP)
    locked = bytearray(zipped(home / 'locked.zip', good).read_bytes())
    locked[locked.find(b'PK\x01\x02') + 8] |= 1                 # backup.json marked as behind a password
    (home / 'locked.zip').write_bytes(locked)
    refused(home / 'locked.zip', backup.NOT_A_BACKUP)
    refused(zipped(home / 'damaged.zip', {**good, 'live.sqlite3': b'x' * 4096}), backup.DAMAGED)

    newer_manifest = {**json.loads(good['backup.json']), 'schema': store.SCHEMA_VERSION + 1}
    refused(zipped(home / 'newer.zip', {**good, 'backup.json': json.dumps(newer_manifest)}), backup.NEWER)
    newer = home / 'newer.sqlite3'
    newer.write_bytes(good['live.sqlite3'])
    with closing(sqlite3.connect(newer)) as db:
        db.execute('PRAGMA user_version = %d' % (store.SCHEMA_VERSION + 1))
    refused(zipped(home / 'newer-journal.zip', {**good, 'live.sqlite3': newer.read_bytes()}), backup.NEWER)

    assert held('live') == before and not (home / 'backups').exists()


def test_a_backup_from_another_data_folder_reads_this_ones_study_file(home, monkeypatch):
    """There is no path to type: a journal restored from another computer reads the file the study writes here."""
    from mwjournal.sync import ensure_default_connection
    monkeypatch.setenv('MWJ_HOME', str(home / 'other computer'))
    for dataset in paths.DATASETS:
        ensure_default_connection(Journal(dataset=dataset))
    saved = backup.write(home / 'other computer')
    monkeypatch.setenv('MWJ_HOME', str(home / 'this computer'))
    backup.restore(saved)
    for dataset in paths.DATASETS:                     # what the app does once the backup is back (runtime.start)
        journal = Journal(dataset=dataset)
        ensure_default_connection(journal)
        assert journal.get('profiles')['MotiveWave']['path'] == str(paths.bridge_file(dataset))
        assert str(home / 'this computer') in str(paths.bridge_file(dataset))


def test_a_backup_from_0_4_17_with_named_journals_is_restored_and_they_become_views_again(home):
    from test_journals import old_named
    env = seed_preview(home)
    named, id = old_named(env, 'Practice', [('motivewave', 'paper')])
    _, archived = old_named(env, 'Old', [('motivewave', 'paper')], archived=True)
    trade, = named.trades()
    named.annotate(trade.id, {'notes': 'Practice review.'})
    saved = backup.write(home / 'backup.zip')                    # as 0.4.17 took it: with both named journals
    assert {f'journals/{id}/trades.sqlite3', f'journals/{archived}/trades.sqlite3'} <= set(members(saved))
    assert backup.read(saved)['trades'] == {'live': 1, 'replay': 0}
    journal = Journal()                                           # opened by this version: folded, folders gone
    assert set(journals.registry(journal)['views']) == {id} and not (home / 'journals').exists()
    assert 'journals/' not in ' '.join(members(backup.write(home / 'after.zip')))
    journal.update_annotation(trade.id, {'notes': 'Changed.'})
    backup.restore(saved)
    # Put back as it was taken, then opened: the named journals become views again, from the reviews of then.
    restored = Journal()
    assert set(journals.registry(restored)['views']) == {id} and not (home / 'journals').exists()
    assert restored.annotations()[trade.id]['notes'] == ('Synthetic fixture, not an actual account trade.\n\n'
                                                         'Practice review.')
    assert len(list((home / 'backups').glob('before-journal-views-*.zip'))) == 2


def test_an_environment_never_opened_is_backed_up_as_an_empty_journal(home):
    trade(Journal(), 'live-1')
    assert not paths.database('replay').exists()
    saved = backup.write(home / 'backup.zip')
    assert not paths.database('replay').exists()             # backing up creates nothing in the data folder
    assert backup.read(saved)['trades'] == {'live': 1, 'replay': 0}
    backup.restore(saved)
    assert Journal(dataset='replay').trades() == [] and len(Journal().trades()) == 1


def test_export_and_backup_backs_up_and_restores_in_three_controls(home):
    journal = seed_preview(home)
    saved = backup.write(home / 'backup.zip')
    trade(journal, 'later', day=9)
    app = open_app('Export')
    assert backup_controls(app) and not any(b.key == 'restore-apply' for b in app.button)
    app.file_uploader[0].set_value((saved.name, saved.read_bytes(), 'application/zip')).run()
    assert not app.exception
    line = next(m.value for m in app.main.markdown if m.value.startswith('Backup of '))
    assert line.endswith(' · 1 live trade, 0 replay trades')
    press(app, 'Restore This Backup')
    assert said(app.success, 'Restored. Your previous journal was saved to ')
    assert len(Journal().trades()) == 1 and len(list((home / 'backups').glob('before-restore-*.zip'))) == 1
    assert not app.file_uploader[0].value and not any(b.key == 'restore-apply' for b in app.button)
    app.file_uploader[0].set_value(('text.zip', b'not a zip', 'application/zip')).run()
    assert said(app.error, backup.NOT_A_BACKUP) and not any(b.key == 'restore-apply' for b in app.button)
