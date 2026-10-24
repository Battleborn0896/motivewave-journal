"""One file that backs up the whole journal, and putting it back.

A backup is a zip of the data folder's databases, Live and Replay together:

    backup.json                            {app, version, schema, created}
    live.sqlite3                           <home>/trades.sqlite3
    replay.sqlite3                         <home>/replay/trades.sqlite3
    journals/<id>/trades.sqlite3           a named Live journal of 0.4.17 or earlier, archived ones too
    replay/journals/<id>/trades.sqlite3    a named Replay journal of 0.4.17 or earlier

A named journal is in a backup only while its folder is in the data folder: until this version first opens its
environment's journal, which backs them up, folds each into it as a view and removes their folders (journals.fold).
A backup that holds them is restored as it was taken, and they are folded at the next open.

Each database is SQLite's own online snapshot, taken on a plain connection: consistent while the watcher
writes, and never through Journal(), so a migration that runs while a Journal opens can back up first.
Restoring copies each database into the existing file with the same backup API, so no file is replaced
under SQLite's WAL. Nothing here stops the watchers: the app does that around restore().
"""

from __future__ import annotations

import datetime as dt
import json
import os
import re
import shutil
import sqlite3
import tempfile
import zipfile
import zlib
from contextlib import closing, contextmanager
from pathlib import Path

from . import paths, store

MANIFEST = 'backup.json'
SLOTS = {'live.sqlite3': 'live', 'replay.sqlite3': 'replay'}                  # member: its environment
NAMED = re.compile(r'(replay/)?journals/[0-9a-f]{32}/trades\.sqlite3')
NOT_A_BACKUP = 'This file is not a MotiveWave Journal backup.'
NEWER = 'This backup was made by a newer version of MotiveWave Journal, so update the app before restoring it.'
DAMAGED = 'This backup is damaged, so it cannot be restored.'


def file_name(day: dt.date | None = None) -> str:
    return f'{paths.APP_NAME} backup {(day or dt.date.today()).isoformat()}.zip'


def _home(home) -> Path:
    return Path(home) if home is not None else paths.data_home()


def _dataset(member: str) -> str:
    """replay.sqlite3 and replay/journals/... are Replay; the rest are Live."""
    return 'replay' if member.startswith('replay') else 'live'


def _place(home: Path, member: str) -> Path:
    """Where a member lives in the data folder: the environments where paths.database puts them, a named journal
    under the same relative path."""
    if member in SLOTS:
        return home / paths.database(SLOTS[member]).relative_to(paths.data_home())
    return home / member


def _databases(home: Path) -> dict[str, Path]:
    """Member name → file for every database the data folder holds; an environment's file may not exist yet."""
    found = {member: _place(home, member) for member in SLOTS}
    for path in sorted([*home.glob('journals/*/trades.sqlite3'), *home.glob('replay/journals/*/trades.sqlite3')]):
        member = path.relative_to(home).as_posix()
        if NAMED.fullmatch(member):
            found[member] = path
    return found


def _copy(source: Path, target: Path) -> None:
    """SQLite's online backup of one database into another, page by page: never the whole file in memory."""
    with closing(sqlite3.connect(source, timeout=15)) as reader, closing(sqlite3.connect(target, timeout=15)) as writer:
        reader.backup(writer)


def write(target, home=None) -> Path:
    """Backs up every journal in `home` (the data folder by default) to one zip and returns its path. `target`
    is the zip, or a folder to put it in under file_name(). The zip is built beside the target and renamed into
    place, so a backup cut short never carries the name."""
    home, target = _home(home), Path(target)
    if target.is_dir():
        target = target / file_name()
    target.parent.mkdir(parents=True, exist_ok=True)
    manifest = {'app': paths.APP_NAME, 'version': paths.VERSION, 'schema': store.SCHEMA_VERSION,
                'created': dt.datetime.now(dt.timezone.utc).isoformat()}
    handle, partial = tempfile.mkstemp(dir=target.parent, prefix=f'.{target.stem}.', suffix='.partial')
    os.close(handle)
    partial = Path(partial)
    try:
        with (tempfile.TemporaryDirectory(dir=target.parent, prefix='.backup-') as scratch,
              zipfile.ZipFile(partial, 'w', zipfile.ZIP_DEFLATED) as archive):
            archive.writestr(MANIFEST, json.dumps(manifest, indent=2))
            for member, source in _databases(home).items():
                if not source.is_file():           # an environment never opened: an empty journal, so both are there
                    source = store.Journal(Path(scratch) / 'empty' / member, dataset=_dataset(member)).path
                snapshot = Path(scratch) / 'snapshot.sqlite3'
                _copy(source, snapshot)
                with closing(sqlite3.connect(snapshot)) as db:
                    db.execute('PRAGMA journal_mode=DELETE')    # one file on its own, without WAL, wherever it is opened
                archive.write(snapshot, member)
                snapshot.unlink()
        os.replace(partial, target)
        return target
    finally:
        partial.unlink(missing_ok=True)


def _check(path: Path, dataset: str) -> None:
    """A database member is a sound journal of this environment that this version can open."""
    try:
        with closing(sqlite3.connect(path)) as db:
            sound = db.execute('PRAGMA quick_check').fetchone()[0] == 'ok'
            version = db.execute('PRAGMA user_version').fetchone()[0]
            try:
                stored = db.execute("SELECT value FROM journal_meta WHERE key='dataset'").fetchone()
            except sqlite3.OperationalError:       # SQLite, but not a journal
                stored = None
    except sqlite3.DatabaseError:                  # not SQLite at all, or cut short
        raise ValueError(DAMAGED) from None
    if version > store.SCHEMA_VERSION:
        raise ValueError(NEWER)
    if not sound:
        raise ValueError(DAMAGED)
    if stored is None or stored[0] != dataset:
        raise ValueError(NOT_A_BACKUP)


@contextmanager
def _unpacked(file):
    """The backup's manifest and its databases, checked, as copies in a temporary folder: {member: copy}. Only
    the fixed member names are read, each into a path made here, so no name in the zip can point elsewhere."""
    try:
        archive = zipfile.ZipFile(file)
    except zipfile.BadZipFile:
        raise ValueError(NOT_A_BACKUP) from None
    with archive, tempfile.TemporaryDirectory(prefix='mwj-backup-') as scratch:
        names = archive.namelist()
        if (len(set(names)) != len(names) or MANIFEST not in names or not set(SLOTS) <= set(names)
                or any(name != MANIFEST and name not in SLOTS and not NAMED.fullmatch(name) for name in names)):
            raise ValueError(NOT_A_BACKUP)
        try:
            manifest = json.loads(archive.read(MANIFEST))
            created = dt.datetime.fromisoformat(manifest['created'])
            schema = manifest['schema']
        # RuntimeError: a zip with a password; NotImplementedError: one packed in a way Python cannot unpack.
        except (ValueError, TypeError, KeyError, zipfile.BadZipFile, RuntimeError, NotImplementedError):
            raise ValueError(NOT_A_BACKUP) from None
        if manifest.get('app') != paths.APP_NAME or type(schema) is not int or created.tzinfo is None:
            raise ValueError(NOT_A_BACKUP)
        if schema > store.SCHEMA_VERSION:
            raise ValueError(NEWER)
        copies = {}
        for number, member in enumerate(name for name in names if name != MANIFEST):
            copy = Path(scratch) / str(number) / 'trades.sqlite3'
            copy.parent.mkdir()
            try:
                with archive.open(member) as source, copy.open('wb') as out:
                    shutil.copyfileobj(source, out)
            except (zipfile.BadZipFile, zlib.error, EOFError):
                raise ValueError(DAMAGED) from None
            _check(copy, _dataset(member))
            copies[member] = copy
        yield {**manifest, 'created': created}, copies


def read(file) -> dict:
    """What a backup holds, after checking every database in it: {'created': aware datetime, 'version': the app
    that made it, 'trades': {'live': n, 'replay': n}}, each count taken by a Journal opened on a temporary copy.
    `file` is a path or an open binary file. Raises ValueError with one plain sentence when it cannot be restored."""
    with _unpacked(file) as (manifest, copies):
        try:
            trades = {dataset: len(store.Journal(copies[member], dataset=dataset).trades())
                      for member, dataset in SLOTS.items()}
        except sqlite3.Error:
            raise ValueError(DAMAGED) from None
    return {'created': manifest['created'], 'version': manifest.get('version'), 'trades': trades}


def before(home, step: str) -> Path:
    """Where the backup taken before `step` goes: <home>/backups/before-<step>-<UTC time>.zip, or -2, -3 and on after
    the time when one was taken in the same second, so none is ever written over."""
    stamp = dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    target, n = Path(home) / 'backups' / f'before-{step}-{stamp}.zip', 1
    while target.exists():
        n += 1
        target = target.with_name(f'before-{step}-{stamp}-{n}.zip')
    return target


def restore(file, home=None) -> dict:
    """Puts a backup back in `home` (the data folder by default): read()'s summary, with 'previous', the zip the
    journal as it was is saved to first (before(home, 'restore')). Each database is copied into its existing file.
    Each environment is then opened once, Replay with the Live journal it shares settings with, so a backup from an
    older version is brought up to date: its named journals become views. The caller stops the watchers first."""
    home = _home(home)
    summary = read(file)
    previous = write(before(home, 'restore'), home)
    with _unpacked(file) as (_, copies):
        for member, copy in copies.items():
            target = _place(home, member)
            target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            _copy(copy, target)
    live = store.Journal(_place(home, 'live.sqlite3'))
    store.Journal(_place(home, 'replay.sqlite3'), dataset='replay', shared=live)
    return {**summary, 'previous': previous}
