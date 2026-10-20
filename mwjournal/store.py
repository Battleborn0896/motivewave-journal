"""Transactional SQLite storage in the user's own data folder; live and replay are separate files."""

from __future__ import annotations

import datetime as dt
import json
import os
import sqlite3
import tempfile
from contextlib import contextmanager
from dataclasses import asdict
from pathlib import Path

from . import paths
from .matching import match
from .model import (NATIVE, Execution, PriceSample, ProtectionEvent, digest, finite, heartbeat_report, instant,
                    product, timestamp, trade_prices)
from .scope import event_mode, allows_source
from .workspace import Workspace, read as read_workspace

SCHEMA_VERSION = 3
# The rules trades are measured by. Raise it when they change: the watcher then measures every trade of a
# journal measured under older rules once more, from its stored prices, and records this number in it.
# 1: a trade's prices are those of its product (model.trade_prices).
MEASUREMENT_RULES = 1
# What a writer may say differently about a fill it already sent, without it being another fill.
LABELS = ('quality', *NATIVE)
SCHEMA = '''
CREATE TABLE IF NOT EXISTS executions (
 key TEXT PRIMARY KEY, payload TEXT NOT NULL, raw TEXT NOT NULL, imported TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS instruments (symbol TEXT PRIMARY KEY, point_value REAL NOT NULL);
CREATE TABLE IF NOT EXISTS annotations (trade_id TEXT PRIMARY KEY, payload TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS metrics (
 trade_id TEXT NOT NULL, basis TEXT NOT NULL, source TEXT NOT NULL, payload TEXT NOT NULL,
 PRIMARY KEY (trade_id, basis, source)
);
CREATE TABLE IF NOT EXISTS prices (
 key TEXT PRIMARY KEY, symbol TEXT NOT NULL, source TEXT NOT NULL, ts TEXT NOT NULL, payload TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS prices_window ON prices(symbol, source, ts);
CREATE INDEX IF NOT EXISTS prices_bars ON prices(symbol, source, ts)
 WHERE json_extract(payload, '$.resolution_seconds') > 0;
CREATE TABLE IF NOT EXISTS capture_events (
 key TEXT PRIMARY KEY, source TEXT NOT NULL, ts TEXT NOT NULL, payload TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS capture_window ON capture_events(source,ts);
CREATE TABLE IF NOT EXISTS sessions (key TEXT PRIMARY KEY, payload TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS journal_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS media (id TEXT PRIMARY KEY, trade_id TEXT NOT NULL, payload TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS media_trade ON media(trade_id);
CREATE TABLE IF NOT EXISTS profiles (name TEXT PRIMARY KEY, payload TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS sync_status (name TEXT PRIMARY KEY, payload TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS bridge_offsets (
 name TEXT PRIMARY KEY, identity TEXT NOT NULL, position INTEGER NOT NULL, checkpoint BLOB NOT NULL
);
CREATE TABLE IF NOT EXISTS protection (
 key TEXT PRIMARY KEY, provider TEXT NOT NULL, account TEXT NOT NULL, symbol TEXT NOT NULL,
 ts TEXT NOT NULL, payload TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS protection_trade ON protection(provider,account,symbol,ts);
'''


def encode(value):
    return json.dumps(value, sort_keys=True, allow_nan=False)


def _upgrade(db) -> dict:
    """Schema 3, inside the caller's transaction, and harmless to run twice.

    Fills stored by schema 2 lack the platform's position id and entry flag, although the raw event
    kept beside each one has them. They are copied over, so the journal holds what importing the
    same bridge file today would store and reading that file again is neither a conflict nor a
    second fill. A capture warning that schema 2 recorded as a failure stops being one.
    """
    rewritten = cleared = 0
    for row in db.execute('SELECT key, payload, raw FROM executions').fetchall():
        try:
            stored, raw = json.loads(row['payload']), json.loads(row['raw'])
            if not isinstance(raw, dict) or all(raw.get(name) in (None, '') for name in NATIVE):
                continue
            fill = Execution(**{**stored, **{name: raw[name] for name in NATIVE if name in raw}})
        except (ValueError, TypeError):
            continue                               # a row this version cannot read stays exactly as it is
        payload = encode(fill.payload())
        if fill.key == row['key'] and payload != row['payload']:
            db.execute('UPDATE executions SET payload=? WHERE key=?', (payload, row['key']))
            rewritten += 1
    for row in db.execute('SELECT name, payload FROM sync_status').fetchall():
        try:
            status = json.loads(row['payload'])
            heartbeat = status.get('heartbeat')
        except (ValueError, AttributeError):
            continue
        error, warnings = heartbeat_report(heartbeat)
        if error is None and warnings and status.get('error') == heartbeat.get('detail'):
            del status['error']
            status.update(status='synced', warnings=warnings)
            db.execute('UPDATE sync_status SET payload=? WHERE name=?', (encode(status), row['name']))
            cleared += 1
    if rewritten:
        # The trades are grouped differently now; the watcher measures them again from stored prices.
        db.execute("DELETE FROM journal_meta WHERE key='measurement_rules'")
    return {'executions_rewritten': rewritten, 'alarms_cleared': cleared}


class ImportConflict(ValueError):
    pass


class Journal(Workspace):
    def __init__(self, path: Path | None = None, *, dataset='live'):
        if dataset not in ('live', 'replay'):
            raise ValueError('Journal dataset must be live or replay.')
        self.dataset = dataset
        personal_folder = path is None
        if path is None:
            path = paths.database(dataset)
        self.path = Path(path)
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        newer = ('This journal was written by a newer version of MotiveWave Journal. '
                 'Update the app to open it.')
        with self.connect() as db:
            found = db.execute('PRAGMA user_version').fetchone()[0]
            if found > SCHEMA_VERSION:
                raise ValueError(newer)
            existing = db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='executions'").fetchone()
            backup = None
            if existing:
                self._check_environment(db)        # the other environment's file is refused before it is touched
                if found < SCHEMA_VERSION:
                    backup = self._backup_before_upgrade(db, found)
            db.execute('PRAGMA journal_mode=WAL')
            db.executescript(SCHEMA)
            # One writer at a time decides the schema step. A second process waits here, then finds it done.
            db.execute('BEGIN IMMEDIATE')
            self._check_environment(db)
            db.execute("INSERT OR IGNORE INTO journal_meta VALUES('dataset',?)", (dataset,))
            if not existing:                       # a new journal: nothing in it was measured under older rules
                db.execute("INSERT OR IGNORE INTO journal_meta VALUES('measurement_rules',?)", (str(MEASUREMENT_RULES),))
            version = db.execute('PRAGMA user_version').fetchone()[0]
            if version > SCHEMA_VERSION:
                raise ValueError(newer)
            if version < SCHEMA_VERSION:
                if existing and (backup is None or version != found):
                    # Never rewrite a journal that holds data without a finished copy of exactly that journal.
                    raise ValueError('This journal changed while it was being backed up for an upgrade. Open it again.')
                # The rewrite and the new version number commit together or not at all, so an
                # interrupted upgrade leaves the journal as it was and simply runs again.
                note = _upgrade(db)
                if backup is not None:
                    note = {**note, 'from': version, 'to': SCHEMA_VERSION, 'backup': str(backup),
                            'at': dt.datetime.now(dt.timezone.utc).isoformat()}
                    db.execute("INSERT OR REPLACE INTO journal_meta VALUES('schema_upgrade',?)", (encode(note),))
                db.execute('PRAGMA user_version = %d' % SCHEMA_VERSION)
        if os.name != 'nt':
            try:
                self.path.chmod(0o600)
                # The default data folder is ours to lock, and the study may have made it first with
                # ordinary permissions. A folder chosen with MWJ_HOME is the user's to share or restrict.
                if personal_folder and self.path.parent != paths.chosen_home():
                    self.path.parent.chmod(0o700)
            except OSError:
                pass  # Filesystem permissions vary; Windows uses the user's profile ACLs.

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=15)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def _check_environment(self, db):
        try:
            stored = db.execute("SELECT value FROM journal_meta WHERE key='dataset'").fetchone()
        except sqlite3.OperationalError:           # a file from before the journal recorded its environment
            stored = None
        if stored and stored[0] != self.dataset:
            raise ValueError('This database belongs to the other trading environment.')
        if not stored and self.dataset == 'replay' and db.execute('SELECT 1 FROM executions LIMIT 1').fetchone():
            raise ValueError('An existing live journal cannot be relabelled as replay.')

    def _backup_before_upgrade(self, db, version: int) -> Path | None:
        """SQLite's online backup of the journal as an older version left it, complete before the
        first change and kept beside it: live and replay each get their own, in their own folder."""
        name = f'{self.path.stem}.schema-{version}-backup-' + dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%M%SZ')
        handle, partial = tempfile.mkstemp(dir=self.path.parent, prefix=name + '.', suffix='.partial')
        os.close(handle)                           # private to this user, and this attempt's alone
        partial = Path(partial)
        try:
            copy = sqlite3.connect(partial)
            try:
                db.backup(copy)
                copied = copy.execute('PRAGMA user_version').fetchone()[0]
            finally:
                copy.close()
            if copied != version:
                return None                        # another process upgraded it meanwhile and holds the real backup
            target, n = self.path.with_name(name + self.path.suffix), 1
            while target.exists():
                n += 1
                target = self.path.with_name(f'{name}-{n}{self.path.suffix}')
            os.replace(partial, target)            # a copy that was cut short never carries a backup's name
            return target
        finally:
            partial.unlink(missing_ok=True)

    def upgrade_note(self) -> dict | None:
        """What the last schema upgrade of this journal did and where its backup is, or None."""
        with self.connect() as db:
            row = db.execute("SELECT value FROM journal_meta WHERE key='schema_upgrade'").fetchone()
        return json.loads(row[0]) if row else None

    def recalculation_due(self) -> bool:
        """True while this journal's trades were measured under older MEASUREMENT_RULES than this version's
        (or regrouped by a schema upgrade), until the watcher has measured them all again."""
        with self.connect() as db:
            row = db.execute("SELECT value FROM journal_meta WHERE key='measurement_rules'").fetchone()
        return row is None or int(row[0]) < MEASUREMENT_RULES

    def recalculation_done(self):
        """Every trade has been measured under this version's rules."""
        with self.connect() as db:
            db.execute("INSERT OR REPLACE INTO journal_meta VALUES('measurement_rules',?)", (str(MEASUREMENT_RULES),))
            db.execute("DELETE FROM journal_meta WHERE key='recalculate'")    # what 0.4.4 and earlier set instead

    @staticmethod
    def _insert(db, fill: Execution, raw: dict) -> bool:
        data = fill.payload()
        payload = encode(data)
        previous = db.execute('SELECT payload FROM executions WHERE key=?', (fill.key,)).fetchone()
        if previous is None:
            db.execute('INSERT INTO executions(key,payload,raw) VALUES(?,?,?)', (fill.key, payload, encode(raw)))
            return True
        if previous['payload'] != payload:
            held = json.loads(previous['payload'])
            if {k: v for k, v in held.items() if k not in LABELS} != {k: v for k, v in data.items() if k not in LABELS}:
                raise ImportConflict('An execution ID already exists with different values. '
                                     'Reconcile the source export before importing a correction.')
            # The same fill, described again: the newer wording replaces the old, and a position id
            # the journal already holds is not lost to a line that does not carry one.
            merged = encode({**data, **{k: held[k] for k in NATIVE if k in held and k not in data}})
            if merged != previous['payload']:
                db.execute('UPDATE executions SET payload=? WHERE key=?', (merged, fill.key))
        return False

    @contextmanager
    def _write(self, db=None):
        if db is not None:
            yield db
        else:
            with self.connect() as connection:
                yield connection

    def ingest(self, fills: list[tuple[Execution, dict]], *, db=None) -> int:
        for fill, raw in fills:
            if ('mode' in raw or fill.provider.startswith('motivewave-native:')) and event_mode(
                    {**raw, 'provider': fill.provider}) != self.dataset:
                raise ValueError('Execution belongs to the other trading environment.')
        with self._write(db) as db:
            return sum(self._insert(db, fill, raw) for fill, raw in fills)

    def executions(self) -> list[Execution]:
        with self.connect() as db:
            return [Execution(**json.loads(r['payload'])) for r in
                    db.execute('SELECT payload FROM executions ORDER BY rowid')]

    def execution_count(self):
        with self.connect() as db:
            return db.execute('SELECT count(*) FROM executions').fetchone()[0]

    def media_cursor(self):
        with self.connect() as db:
            return db.execute('SELECT coalesce(max(rowid),0) FROM executions').fetchone()[0]

    def media_fills(self, after: int):
        with self.connect() as db:
            return [(r['rowid'], Execution(**json.loads(r['payload'])), json.loads(r['raw'])) for r in
                    db.execute('SELECT rowid,payload,raw FROM executions WHERE rowid>? ORDER BY rowid', (after,))]

    def record_media(self, identity: str, trade_id: str, data: dict):
        with self.connect() as db:
            db.execute('INSERT OR REPLACE INTO media VALUES(?,?,?)', (identity, trade_id, encode(data)))

    def trade_media(self, trade_id: str):
        with self.connect() as db:
            return [json.loads(r[0]) for r in db.execute(
                'SELECT payload FROM media WHERE trade_id=? ORDER BY rowid', (trade_id,))]

    def media_settings(self):
        with self.connect() as db:
            row = db.execute("SELECT value FROM journal_meta WHERE key='media_settings'").fetchone()
            return json.loads(row[0]) if row else {}

    def save_media_settings(self, data: dict):
        # Preferences only. Recording is deliberately NOT automatically re-armed on app restart.
        with self.connect() as db:
            db.execute("INSERT OR REPLACE INTO journal_meta VALUES('media_settings',?)", (encode(data),))

    def trades(self):
        with self.connect() as db:
            instruments = {r['symbol']: r['point_value'] for r in db.execute('SELECT * FROM instruments')}
        return match(self.executions(), instruments)

    def detached_reviews(self) -> dict:
        """Saved work whose trade id matches no trade now, by kind. A trade is named after its first
        fill, so this happens when fills are regrouped and that fill no longer starts a trade.
        Nothing is deleted: the work is still stored and returns if a trade starts there again."""
        known = {trade.id for trade in self.trades()}
        with self.connect() as db:
            found = {'annotations': [r[0] for r in db.execute('SELECT trade_id FROM annotations ORDER BY rowid')],
                     'media': [r[0] for r in db.execute('SELECT trade_id FROM media ORDER BY rowid')]}
            state = read_workspace(db)
        for name, kind in (('checklist_runs', 'runs'), ('checkins', 'checkins'), ('drafts', 'drafts')):
            found[name] = sorted(item['trade_id'] for item in state[kind].values() if item.get('trade_id'))
        return {name: [id for id in dict.fromkeys(ids) if id not in known] for name, ids in found.items()}

    def set_instrument(self, symbol: str, point_value: float):
        with self.connect() as db:
            db.execute('INSERT OR REPLACE INTO instruments VALUES(?,?)',
                       (symbol.strip().upper(), finite(point_value, 'Point value', True)))

    def annotate(self, trade_id: str, data: dict):
        tags = sorted({str(t).strip() for t in data.get('tags', []) if str(t).strip()})
        data = {**data, 'tags': tags}
        risk = data.get('initial_risk')
        if risk is not None:
            data['initial_risk'] = finite(risk, 'Initial risk', True)
        for key in ('planned_stop', 'planned_target'):
            if data.get(key) is not None:
                data[key] = finite(data[key], key.replace('_', ' ').title())
        with self.connect() as db:
            db.execute('INSERT OR REPLACE INTO annotations VALUES(?,?)', (trade_id, encode(data)))

    def annotations(self) -> dict:
        from . import tagging
        with self.connect() as db:
            state = tagging.library(db)
            result = {}
            for row in db.execute('SELECT * FROM annotations'):
                annotation = tagging.project(state, json.loads(row['payload']))
                annotation.pop('tag_ids', None)  # stable IDs live in storage; preserve the public review shape
                result[row['trade_id']] = annotation
            return result

    def update_annotation(self, trade_id: str, changes: dict, *, add_tags=(), remove_tags=()):
        """Atomic partial edits for autosave: a tag click cannot overwrite notes or another group's tags."""
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            previous = db.execute('SELECT payload FROM annotations WHERE trade_id=?', (trade_id,)).fetchone()
            from . import tagging
            state = tagging.library(db)
            data = {**tagging.project(state, json.loads(previous[0]) if previous else {}), **changes}
            tags = {str(t).strip() for t in data.get('tags', []) if str(t).strip()}
            tags.difference_update(str(t).strip() for t in remove_tags)
            tags.update(str(t).strip() for t in add_tags if str(t).strip())
            data['tags'] = sorted(tags)
            tagging.ensure_names(state, tags)
            incoming = tagging.ids(state, add_tags)
            for group, settings in state['groups'].items():
                choices = [id for id in incoming if state['tags'][id]['group'] == group]
                if settings['exclusive'] and choices:
                    if len(choices) > 1:
                        raise ValueError('Copied tags contain multiple choices in an exclusive group.')
                    tags = {name for name in tags if state['tags'][next(iter(tagging.ids(state, [name])))]['group'] != group}
                    tags.update(tagging.names(state, choices))
            data['tags'] = sorted(tags)
            data['tag_ids'] = sorted(tagging.ids(state, tags))
            tagging.save(db, state)
            if data.get('initial_risk') is not None:
                data['initial_risk'] = finite(data['initial_risk'], 'Initial risk', True)
            for name in ('planned_stop', 'planned_target'):
                if data.get(name) is not None:
                    data[name] = finite(data[name], name.replace('_', ' ').title())
            db.execute('INSERT OR REPLACE INTO annotations VALUES(?,?)', (trade_id, encode(data)))
        return data

    def tag_catalog(self):
        return {t['name']: t['group'] for t in self.tag_library()['tags'].values() if not t['retired']}

    def tag_library(self):
        from . import tagging
        with self.connect() as db:
            return tagging.library(db)

    def add_tag(self, name: str, group: str):
        """Create a reusable tag; no existing trade is retagged or deleted."""
        from . import tagging
        name, group = name.strip(), group.strip()
        if not name or not group or len(name) > 64 or len(group) > 40:
            raise ValueError('Give the tag a name (up to 64 characters) and group (up to 40).')
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            state = tagging.library(db)
            existing = tagging.ids(state, [name])
            if existing and state['tags'][next(iter(existing))]['group'] != group:
                raise ValueError('That tag already belongs to another group. Use a different name.')
            if existing and state['tags'][next(iter(existing))]['name'] != name:
                raise ValueError('That name is a historical alias; rename the existing tag instead.')
            tagging.ensure_names(state, [name], group)
            tagging.save(db, state)

    def add_tag_group(self, name: str):
        """Create an empty reusable tag category without inventing a placeholder tag."""
        from . import tagging
        name = str(name).strip()
        if not name or len(name) > 40:
            raise ValueError('Give the category a name, up to 40 characters.')
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            state = tagging.library(db)
            if name in state['groups']:
                raise ValueError('That category already exists.')
            state['groups'][name] = {'exclusive': False, 'order': len(state['groups'])}
            tagging.save(db, state)

    def delete_tag(self, id):
        """Remove a tag nothing uses. A used tag is refused: retire it, so its history keeps its name."""
        from . import tagging
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            state = tagging.library(db)
            if id not in state['tags']:
                raise ValueError('Unknown tag.')
            uses = tagging.tag_uses(db, state, id)
            if uses:
                raise ValueError(f"{state['tags'][id]['name']} {' and '.join(uses)}. "
                                 'Retire it instead: it leaves the tag panel and keeps its history.')
            tagging.save(db, tagging.delete_tag(state, id))

    def delete_tag_group(self, group):
        """Remove an empty category that nothing names."""
        from . import tagging
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            state = tagging.library(db)
            if group not in state['groups']:
                raise ValueError('Unknown category.')
            uses = tagging.group_uses(db, state, group)
            if uses:
                raise ValueError(f"{group} {' and '.join(uses)}. Move or delete its tags first.")
            tagging.save(db, tagging.delete_group(state, group))

    def edit_tag(self, id, **changes):
        from . import tagging
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            tagging.save(db, tagging.edit_tag(tagging.library(db), id, **changes))

    def edit_tag_group(self, group, *, name=None, exclusive=None, move=0):
        from . import tagging
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            state = tagging.library(db)
            if group not in state['groups']:
                raise ValueError('Unknown group.')
            if exclusive is not None:
                state['groups'][group]['exclusive'] = bool(exclusive)
            ordered = sorted(state['groups'], key=lambda g: state['groups'][g]['order'])
            position = ordered.index(group)
            target = max(0, min(len(ordered) - 1, position + move))
            ordered.insert(target, ordered.pop(position))
            for index, label in enumerate(ordered):
                state['groups'][label]['order'] = index
            if name is not None and name.strip() != group:
                name = name.strip()
                if not name or len(name) > 40 or name in state['groups']:
                    raise ValueError('Use a unique group name, 1–40 characters.')
                state['groups'][name] = state['groups'].pop(group)
                for tag in state['tags'].values():
                    if tag['group'] == group:
                        tag['group'] = name
                for book in state['playbooks'].values():
                    if group in book['rules']:
                        book['rules'][name] = book['rules'].pop(group)
            tagging.save(db, state)

    def move_tag(self, id, offset):
        from . import tagging
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            state = tagging.library(db)
            tag = state['tags'][id]
            ordered = sorted((t for t in state['tags'].values() if t['group'] == tag['group']), key=lambda t: t['order'])
            position = next(i for i, t in enumerate(ordered) if t['id'] == id)
            ordered.insert(max(0, min(len(ordered) - 1, position + offset)), ordered.pop(position))
            for index, item in enumerate(ordered):
                item['order'] = index
            tagging.save(db, state)

    def set_trade_tag_group(self, trade, group, chosen):
        from . import tagging
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            state = tagging.library(db)
            row = db.execute('SELECT payload FROM annotations WHERE trade_id=?', (trade.id,)).fetchone()
            old = json.loads(row[0]) if row else {}
            session = db.execute('SELECT payload FROM sessions WHERE key=?', (tagging.session_key(state, trade.opened),)).fetchone()
            data = tagging.set_group(state, old, group, chosen, json.loads(session[0]) if session else {})
            db.execute('INSERT OR REPLACE INTO annotations VALUES(?,?)', (trade.id, encode(data)))

    def set_day_tag_group(self, date, group, chosen):
        from . import tagging
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            state = tagging.library(db)
            key = f"{dt.date.fromisoformat(str(date))}:{state['session_timezone']}"
            row = db.execute('SELECT payload FROM sessions WHERE key=?', (key,)).fetchone()
            old = json.loads(row[0]) if row else {}
            if group not in state['groups'] or len(tagging.ids(state, chosen)) != len(set(chosen)):
                raise ValueError('Choose known day tags from a known group.')
            chosen = tagging.ids(state, chosen)
            if any(state['tags'][id]['group'] != group or state['tags'][id]['retired']
                   or 'day' not in state['tags'][id]['levels'] for id in chosen):
                raise ValueError('Choose active day tags from this group.')
            if state['groups'][group]['exclusive'] and len(chosen) > 1:
                raise ValueError('This group permits one choice.')
            other = {id for id in tagging.inherited(state, old) if state['tags'][id]['group'] != group}
            data = {**old, 'date': str(date), 'timezone': state['session_timezone'],
                    'day_tag_ids': sorted(other | chosen)}
            db.execute('INSERT OR REPLACE INTO sessions VALUES(?,?)', (key, encode(data)))

    def save_playbook(self, name, rules, *, id=None, retired=False):
        from . import tagging
        name = name.strip()
        if not name or len(name) > 64:
            raise ValueError('Give this playbook a name (up to 64 characters).')
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            state = tagging.library(db)
            if id is not None and id not in state['playbooks']:
                raise ValueError('Unknown playbook.')
            if any(group not in state['groups'] or len(tagging.ids(state, tags)) != len(set(tags))
                   for group, tags in rules.items()):
                raise ValueError('Choose known tags from known groups.')
            normalized = {group: sorted(tagging.ids(state, tags), key=lambda i: state['tags'][i]['order'])
                          for group, tags in rules.items() if tags}
            if not normalized or any(not values or any(state['tags'][i]['group'] != group for i in values)
                                     for group, values in normalized.items()):
                raise ValueError('Choose at least one valid tag per participating group.')
            if any(p['name'] == name and key != id for key, p in state['playbooks'].items()):
                raise ValueError('That playbook name already exists.')
            id = id or tagging.identity('playbook:' + name)
            state['playbooks'][id] = {'id': id, 'name': name, 'rules': normalized, 'retired': bool(retired)}
            tagging.save(db, state)
        return id

    def apply_playbook(self, trade, id):
        from . import tagging
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            state = tagging.library(db)
            if id not in state['playbooks'] or state['playbooks'][id].get('retired'):
                raise ValueError('Choose an active playbook.')
            row = db.execute('SELECT payload FROM annotations WHERE trade_id=?', (trade.id,)).fetchone()
            data = json.loads(row[0]) if row else {}
            row = db.execute('SELECT payload FROM sessions WHERE key=?', (tagging.session_key(state, trade.opened),)).fetchone()
            session = json.loads(row[0]) if row else {}
            for group, choices in state['playbooks'][id]['rules'].items():
                active = [i for i in choices if not state['tags'][i]['retired'] and 'trade' in state['tags'][i]['levels']]
                if not active:
                    raise ValueError('A playbook group has no active choices.')
                if state['groups'][group]['exclusive']:
                    current = tagging.effective(state, data, session)
                    active = [i for i in active if i in current][:1] or active[:1]
                data = tagging.set_group(state, data, group, active, session)
            db.execute('INSERT OR REPLACE INTO annotations VALUES(?,?)', (trade.id, encode(data)))

    def set_tag_timezone(self, timezone):
        from zoneinfo import ZoneInfo
        from . import tagging
        ZoneInfo(timezone)
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            state = tagging.library(db)
            state['session_timezone'] = timezone
            tagging.save(db, state)

    def update_session(self, key, changes):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT payload FROM sessions WHERE key=?', (key,)).fetchone()
            data = {**(json.loads(row[0]) if row else {}), **changes}
            db.execute('INSERT OR REPLACE INTO sessions VALUES(?,?)', (key, encode(data)))


    def display_settings(self):
        with self.connect() as db:
            stored = db.execute("SELECT value FROM journal_meta WHERE key='display_settings'").fetchone()
        return {'excursion_unit': 'currency', 'excursion_policy': 'ticks-only', 'tick_sizes': {},
                'dashboard_sections': ['Overview', 'Compare', 'MAE / MFE', 'Runners'],
                **(json.loads(stored[0]) if stored else {})}

    def set_excursion_policy(self, policy):
        if policy not in ('ticks-only', 'best-available'):
            raise ValueError('Unknown accuracy policy.')
        self._update_display_settings({'excursion_policy': policy})

    def set_dashboard_sections(self, sections):
        allowed = {'Overview', 'Compare', 'MAE / MFE', 'Runners'}
        if not sections or len(set(sections)) != len(sections) or not set(sections) <= allowed:
            raise ValueError('Choose at least one unique dashboard section.')
        self._update_display_settings({'dashboard_sections': list(sections)})

    def set_excursion_unit(self, unit):
        from .units import UNITS
        if unit not in UNITS:
            raise ValueError('Unknown excursion display unit.')
        self._update_display_settings({'excursion_unit': unit})

    def set_tick_size(self, symbol, value):
        symbol = str(symbol or '').strip().upper()
        if not symbol:
            raise ValueError('An exact instrument symbol is required.')
        value = finite(value, 'Tick size', True)
        self._update_display_settings({}, tick=(symbol, value))

    def _update_display_settings(self, changes, *, tick=None):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            stored = db.execute("SELECT value FROM journal_meta WHERE key='display_settings'").fetchone()
            settings = {**(json.loads(stored[0]) if stored else {}), **changes}
            if tick is not None:
                settings['tick_sizes'] = {**settings.get('tick_sizes', {}), tick[0]: tick[1]}
            db.execute("INSERT OR REPLACE INTO journal_meta VALUES('display_settings',?)", (encode(settings),))

    def record_metric(self, trade, metric: dict):
        if not trade.complete:
            return                                 # no excursion is kept for a position the fills only partly show
        gaps = self.capture_gaps(metric['source'], start=trade.opened, end=trade.closed, symbol=trade.symbol)
        if gaps:
            note = 'Capture bridge reported dropped ticks/bars; missing prices can understate MAE/MFE.'
            metric = {**metric, 'notes': list(dict.fromkeys([*metric.get('notes', []), note]))}
        with self.connect() as db:
            basis = metric.get('basis', 'last')
            # Last-traded marks and executable bid/ask marks are kept side by side.
            db.execute('INSERT OR REPLACE INTO metrics VALUES(?,?,?,?)',
                       (trade.id, trade.basis, encode([metric['source'], basis]), encode(metric)))

    def metric_options(self, trade) -> list[dict]:
        if not trade.complete:
            return []
        with self.connect() as db:
            return [json.loads(r['payload']) for r in db.execute(
                'SELECT payload FROM metrics WHERE trade_id=? AND basis=? ORDER BY source', (trade.id, trade.basis))]

    def best_metric(self, trade):
        options = self.metric_options(trade)
        rank = {'platform-reported': 4, 'observed-ticks': 3, 'estimated-bars': 2,
                'replay-simulated': 1, 'proxy-estimate': 1, 'pending': 0}
        return max(options, key=lambda m: (rank.get(m.get('precision'), 0), m.get('basis', 'last') == 'last',
                                           m.get('observations', 0))) if options else None

    def ingest_prices(self, samples: list[PriceSample], *, db=None) -> int:
        for sample in samples:
            if sample.source.startswith('motivewave-') and event_mode({'provider': sample.source}) != self.dataset:
                raise ValueError('MotiveWave prices belong to the other trading environment.')
        with self._write(db) as db:
            count = 0
            for sample in samples:
                data = asdict(sample)
                # A revised bar replaces its previous version, preserving one source/time slot.
                tick_identity = {k: v for k, v in data.items() if k not in ('open', 'close', 'volume')}
                if sample.volume is not None:
                    tick_identity['volume'] = sample.volume
                key = digest([sample.symbol, sample.source, sample.ts, sample.resolution_seconds]
                             if sample.resolution_seconds else tick_identity)
                before = db.total_changes
                db.execute('INSERT INTO prices VALUES(?,?,?,?,?) ON CONFLICT(key) DO UPDATE SET payload=excluded.payload '
                           'WHERE prices.payload != excluded.payload',
                           (key, sample.symbol, sample.source, sample.ts, encode(data)))
                count += db.total_changes - before
            return count

    @staticmethod
    def _distinct(db, column: str, where: str = '', args=()) -> list[str]:
        """The distinct values of an indexed column of prices, in order, one index step each instead of
        a read of the millions of rows behind them. `where` fixes the index columns before `column`."""
        found, value = [], ''
        while True:
            value = db.execute(f'SELECT min({column}) FROM prices WHERE {where}{column}>?', (*args, value)).fetchone()[0]
            if value is None:
                return found
            found.append(value)

    def _labels(self, db, symbol: str) -> list[str]:
        """Every symbol prices of this symbol's product are stored under: a chart's label, the contract
        months. A handful, so the price queries below can name them and keep using the index."""
        return [label for label in self._distinct(db, 'symbol') if product(label) == product(symbol)]

    def prices(self, symbol: str, source: str, *, start: str | None = None, end: str | None = None) -> list[PriceSample]:
        """The prices of one source that measure a trade in `symbol`, as model.trade_prices chooses them."""
        exact = symbol.strip().upper()
        with self.connect() as db:
            labels = self._labels(db, exact)
            if not labels:
                return []
            within = 'symbol IN (' + ','.join('?' * len(labels)) + ') AND source=?'
            filters, args = [within], [*labels, source]
            if start:
                # The hot tick query starts at entry, not a day earlier. A separate partial index
                # picks up overlapping bars without scanning yesterday's entire tick tape.
                filters.append('ts>=?')
                args.append(timestamp(start))
            if end:
                filters.append('ts<=?')
                args.append(timestamp(end))
            query = 'SELECT symbol,payload,ts FROM prices WHERE ' + ' AND '.join(filters)
            if start:
                query += (' UNION ALL SELECT symbol,payload,ts FROM prices INDEXED BY prices_bars WHERE ' + within +
                          " AND json_extract(payload, '$.resolution_seconds') > 0 AND ts>=? AND ts<? "
                          "AND julianday(ts) + json_extract(payload, '$.resolution_seconds') / 86400.0 > julianday(?)")
                args.extend([*labels, source, (instant(start) - dt.timedelta(days=1)).isoformat(),
                             timestamp(start), timestamp(start)])
            rows = db.execute(query + ' ORDER BY ts, symbol', args).fetchall()
        if any(row['symbol'] == exact for row in rows):
            rows = [row for row in rows if row['symbol'] == exact]   # trade_prices' choice, before any payload is read
        return trade_prices([PriceSample(**json.loads(row['payload'])) for row in rows], exact, source)

    def latest_price(self, symbol: str, source: str, *, since=None) -> PriceSample | None:
        """The newest price of one source that measures a trade in `symbol`, since `since`: the contract's
        own when it has one, as model.trade_prices chooses. One indexed step per label, however long the tape."""
        exact = symbol.strip().upper()
        with self.connect() as db:
            found = [row for label in self._labels(db, exact) for row in db.execute(
                'SELECT symbol,payload,ts FROM prices WHERE symbol=? AND source=? AND ts>=? ORDER BY ts DESC LIMIT 1',
                (label, source, timestamp(since) if since else ''))]
        if any(row['symbol'] == exact for row in found):
            found = [row for row in found if row['symbol'] == exact]
        return PriceSample(**json.loads(max(found, key=lambda row: row['ts'])['payload'])) if found else None

    def price_sources(self, symbol: str, trade=None) -> list[str]:
        """Every source holding prices of this symbol's product."""
        with self.connect() as db:
            sources = {source for label in self._labels(db, symbol)
                       for source in self._distinct(db, 'source', 'symbol=? AND ', (label,))}
        return [s for s in sorted(sources) if trade is None or allows_source(trade, s)]

    def recent_contracts(self, *, now: dt.datetime | None = None, within=dt.timedelta(hours=1)) -> list[str]:
        """The exact contracts the study recorded prices or fills for recently, traded ones first.

        Only what the study wrote counts: 'motivewave-' prices and 'motivewave-native:' fills, never an
        import. Live times are wall-clock times, so live means within `within` of now. Replay times are the
        replay's own clock and a rewound run starts over, so replay means the run the study wrote last, within
        `within` of that run's newest price or fill. Prices are found through the (symbol, source, ts) index
        one contract at a time, so a journal of millions of ticks answers as fast as an empty one."""
        replay = self.dataset == 'replay'
        with self.connect() as db:
            run = None
            if replay:
                row = db.execute("SELECT source FROM prices WHERE source LIKE 'motivewave-replay:%' "
                                 'ORDER BY rowid DESC LIMIT 1').fetchone()
                if row:
                    run = row[0][len('motivewave-replay:'):].split(':', 1)[0]
                else:
                    row = db.execute("SELECT json_extract(payload, '$.provider') FROM executions "
                                     "WHERE json_extract(payload, '$.provider') LIKE 'motivewave-native:%:replay:%' "
                                     'ORDER BY rowid DESC LIMIT 1').fetchone()
                    run = row[0].rsplit(':replay:', 1)[1] if row else None
                if not run:
                    return []
            priced, last = {}, ''
            for _ in range(500):                       # one indexed step per contract, never a scan
                symbol = db.execute('SELECT min(symbol) FROM prices WHERE symbol>?', (last,)).fetchone()[0]
                if symbol is None:
                    break
                last, source = symbol, ''
                while True:
                    source = db.execute('SELECT min(source) FROM prices WHERE symbol=? AND source>?',
                                        (symbol, source)).fetchone()[0]
                    if source is None:
                        break
                    ours = (source.startswith(f'motivewave-replay:{run}:') if replay
                            else source.startswith('motivewave-') and not source.startswith('motivewave-replay:')
                            and source != 'motivewave-ticks:history')       # a backfill of a past window
                    if ours:
                        newest = db.execute('SELECT max(ts) FROM prices WHERE symbol=? AND source=?',
                                            (symbol, source)).fetchone()[0]
                        if newest and newest > priced.get(symbol, ''):
                            priced[symbol] = newest
            filled = {}
            for symbol, provider, ts in db.execute(
                    "SELECT json_extract(payload, '$.symbol'), json_extract(payload, '$.provider'), "
                    "json_extract(payload, '$.ts') FROM executions "
                    "WHERE json_extract(payload, '$.provider') LIKE 'motivewave-native:%'"):
                if (provider.endswith(f':replay:{run}') if replay else ':replay:' not in provider) and ts:
                    filled[symbol] = max(filled.get(symbol, ''), timestamp(ts))
        stamps = {symbol: instant(ts) for symbol, ts in priced.items()}
        for symbol, ts in filled.items():
            stamps[symbol] = max(stamps.get(symbol, instant(ts)), instant(ts))
        if not stamps:
            return []
        clock = max(stamps.values()) if replay else (now or dt.datetime.now(dt.timezone.utc))
        recent = {symbol for symbol, at in stamps.items() if at >= clock - within}
        traded = sorted((s for s in recent if s in filled), key=lambda s: filled[s], reverse=True)
        return traded + sorted(recent - set(traded))

    def trade_clock(self, trade):
        """Open replay positions advance on recorded replay time, never today's wall clock: the latest
        price of the trade's product in its own run."""
        if trade.closed or ':replay:' not in trade.provider:
            return None
        run = trade.provider.rsplit(':replay:', 1)[1]
        prefix = 'motivewave-replay:' + run + ':'
        with self.connect() as db:
            # Every source that starts with the prefix: the run's ticks, simulated prints and history.
            latest = [db.execute('SELECT max(ts) FROM prices WHERE symbol=? AND source>=? AND source<?',
                                 (label, prefix, prefix + '\U0010ffff')).fetchone()[0]
                      for label in self._labels(db, trade.symbol)]
        stamps = [instant(f['ts']) for f in trade.fills]
        stamps.extend(instant(stamp) for stamp in latest if stamp)
        return max(stamps) + dt.timedelta(milliseconds=1)

    def ingest_protection(self, events: list[ProtectionEvent], *, db=None) -> int:
        for event in events:
            if event.provider.startswith('motivewave-native:') and event_mode({'provider': event.provider}) != self.dataset:
                raise ValueError('Protective order belongs to the other trading environment.')
        with self._write(db) as db:
            count = 0
            for event in events:
                data = asdict(event)
                before = db.total_changes
                db.execute('INSERT OR IGNORE INTO protection VALUES(?,?,?,?,?,?)',
                           (digest(data), event.provider, event.account, event.symbol, event.ts, encode(data)))
                count += db.total_changes - before
            return count

    def protection_events(self, trade) -> list[ProtectionEvent]:
        with self.connect() as db:
            events = [ProtectionEvent(**json.loads(r['payload'])) for r in db.execute(
                'SELECT payload FROM protection WHERE provider=? AND account=? AND symbol=? ORDER BY ts,rowid',
                (trade.provider, trade.account, trade.symbol))]
        entry_ids = {f['execution_id'] for f in trade.fills if f['role'] == 'entry' and f['execution_id']}
        return [e for e in events if e.trade_id == trade.id or
                (not e.trade_id and e.entry_execution_id in entry_ids)]

    def ingest_gaps(self, events: list[dict], *, db=None) -> int:
        with self._write(db) as db:
            count = 0
            for event in events:
                before = db.total_changes
                db.execute('INSERT OR IGNORE INTO capture_events VALUES(?,?,?,?)',
                           (digest(event), event['provider'], event.get('ts', ''), encode(event)))
                count += db.total_changes - before
            return count

    def capture_gaps(self, source: str | None = None, *, start=None, end=None, symbol=None) -> list[dict]:
        """Recorded gaps, optionally of one source and overlapping one window. With `symbol`, a gap
        that names another product is left out; a gap that names none concerns every instrument."""
        filters, args = [], []
        if source:
            filters.append('source=?')
            args.append(source)
        window = []
        if start:
            window.append("coalesce(json_extract(payload, '$.end_ts'), ts)>=?")
            args.append(timestamp(start))
        if end:
            window.append("coalesce(json_extract(payload, '$.start_ts'), ts)<=?")
            args.append(timestamp(end))
        if window:
            filters.append("(ts='' OR (" + ' AND '.join(window) + '))')
        with self.connect() as db:
            gaps = [json.loads(r['payload']) for r in db.execute(
                'SELECT payload FROM capture_events' + (' WHERE ' + ' AND '.join(filters) if filters else '')
                + ' ORDER BY ts', args)]
        return [gap for gap in gaps if not symbol or gap.get('symbol') is None
                or product(str(gap['symbol'])) == product(str(symbol))]

    def put(self, table: str, key: str, data: dict):
        if table not in ('sessions', 'profiles', 'sync_status'):
            raise ValueError('Unsupported journal table.')
        with self.connect() as db:
            db.execute(f'INSERT OR REPLACE INTO {table} VALUES(?,?)', (key, encode(data)))

    def get(self, table: str) -> dict:
        if table not in ('sessions', 'profiles', 'sync_status'):
            raise ValueError('Unsupported journal table.')
        with self.connect() as db:
            return {r[0]: json.loads(r[1]) for r in db.execute(f'SELECT * FROM {table}')}

    def remove_profile(self, name: str):
        with self.connect() as db:
            # Its last status and read position go too, or a removed connection keeps reporting.
            for table in ('profiles', 'sync_status', 'bridge_offsets'):
                db.execute(f'DELETE FROM {table} WHERE name=?', (name,))

    def clear_status(self, name: str):
        with self.connect() as db:
            db.execute('DELETE FROM sync_status WHERE name=?', (name,))

    def bridge_offset(self, name: str):
        """(file identity, bytes consumed, the bytes just before that point), or None to start over."""
        with self.connect() as db:
            row = db.execute('SELECT identity, position, checkpoint FROM bridge_offsets WHERE name=?',
                             (name,)).fetchone()
        # SQLite columns hold any type. A damaged row means reading the file again, which is safe.
        if row is None or not (isinstance(row[0], str) and type(row[1]) is int and isinstance(row[2], bytes)):
            return None
        return row[0], row[1], row[2]

    def set_bridge_offset(self, name: str, identity: str, position: int, checkpoint: bytes):
        with self.connect() as db:
            db.execute('INSERT OR REPLACE INTO bridge_offsets VALUES(?,?,?,?)',
                       (name, identity, position, checkpoint))

    def backup(self) -> bytes:
        """Use SQLite backup so an active watcher cannot leave the exported DB inconsistent."""
        copy = sqlite3.connect(':memory:')
        try:
            with self.connect() as db:
                db.backup(copy)
            return copy.serialize()
        finally:
            copy.close()
