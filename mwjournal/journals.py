"""Named local journals with independent reviews and an explicitly scoped capture mirror.

The study always writes the original Live/Replay journal. A named journal mirrors only selected
provider/account pairs, never consumes a bridge independently, and never writes back to capture.
Archiving hides a journal; its database and media remain recoverable. Paths are UUID-owned.
"""
from __future__ import annotations

import json
import re
import sqlite3
import uuid
from pathlib import Path

from .model import product
from .store import Journal, ImportConflict, encode
from .workspace import label

KEY = 'journal_registry'


def registry(capture):
    with capture.connect() as db:
        row = db.execute('SELECT value FROM journal_meta WHERE key=?', (KEY,)).fetchone()
        return json.loads(row[0]) if row else {'journals': {}, 'account_aliases': {}}


def edit(capture, operation):
    with capture.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        row = db.execute('SELECT value FROM journal_meta WHERE key=?', (KEY,)).fetchone()
        data = json.loads(row[0]) if row else {'journals': {}, 'account_aliases': {}}
        result = operation(data)
        db.execute('INSERT OR REPLACE INTO journal_meta VALUES(?,?)', (KEY, encode(data)))
        return result


def journal_path(capture, id):
    if not isinstance(id, str) or not re.fullmatch(r'[0-9a-f]{32}', id):
        raise ValueError('Choose a known journal identifier.')
    path = capture.path.parent / 'journals' / id / 'trades.sqlite3'
    if path.parent.resolve().parent != (capture.path.parent / 'journals').resolve():
        raise ValueError('The journal folder is not owned by this capture journal.')
    return path


def open_named(capture, id, *, refresh=True):
    definition = registry(capture)['journals'].get(id)
    if not definition:
        raise ValueError('Unknown journal.')
    path = journal_path(capture, id)
    if not path.is_file():
        raise ValueError('The named journal database is missing. Restore a backup; no empty replacement was created.')
    journal = Journal(path, dataset=capture.dataset)
    journal.ui_scope = capture.dataset + '-' + id
    journal.capture_journal = capture
    journal.journal_name = definition['name']
    journal.allowed_media_roots = [journal.path.parent / 'media', capture.path.parent / 'media']
    parent = definition.get('clone_of')
    ancestors = set()
    registered = registry(capture)['journals']
    while parent and parent not in ancestors and parent in registered:
        ancestors.add(parent)
        journal.allowed_media_roots.append(journal_path(capture, parent).parent / 'media')
        parent = registered[parent].get('clone_of')
    if refresh and not definition['archived']:
        mirror(capture, journal, definition['scopes'])
        journal.reconcile_drafts()
    return journal


def create(capture, name, scopes, *, clone=None):
    name = label(name)
    scopes = sorted({tuple(s) for s in scopes})
    if len(scopes) > 100 or any(len(s) != 2 or any(not isinstance(v, str) or not v.strip() for v in s) for s in scopes):
        raise ValueError('Choose explicit provider/account pairs.')
    current = registry(capture)
    if any(d['name'].casefold() == name.casefold() for d in current['journals'].values()):
        raise ValueError('That journal name already exists, including archived journals.')
    source = open_named(capture, clone) if clone else None
    id = uuid.uuid4().hex
    path = journal_path(capture, id)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=False)
    if source:
        # A fresh destination only. No database or WAL is overwritten.
        path.write_bytes(source.backup())
        path.chmod(0o600)
    journal = Journal(path, dataset=capture.dataset)
    if not source:
        # Reuse definitions/display preferences, but not another journal's review outcomes or runs.
        with capture.connect() as src, journal.connect() as dst:
            for key in ('tag_library', 'tag_catalog', 'display_settings'):
                row = src.execute('SELECT value FROM journal_meta WHERE key=?', (key,)).fetchone()
                if row:
                    dst.execute('INSERT OR REPLACE INTO journal_meta VALUES(?,?)', (key, row[0]))
    mirror(capture, journal, scopes)
    def register(state):
        if any(d['name'].casefold() == name.casefold() for d in state['journals'].values()):
            raise ValueError('Another window created that name. The unregistered database was preserved for recovery.')
        state['journals'][id] = {'id': id, 'name': name, 'scopes': [list(s) for s in scopes],
                                 'archived': False, 'clone_of': clone}
    edit(capture, register)
    return id


def rename(capture, id, name):
    name = label(name)
    def apply(state):
        if id not in state['journals']:
            raise ValueError('Unknown journal.')
        if any(key != id and d['name'].casefold() == name.casefold() for key, d in state['journals'].items()):
            raise ValueError('That journal name already exists.')
        state['journals'][id]['name'] = name
    edit(capture, apply)


def archive(capture, id, archived=True):
    def apply(state):
        if id not in state['journals']:
            raise ValueError('Unknown journal.')
        state['journals'][id]['archived'] = bool(archived)
    edit(capture, apply)


def account_alias(capture, provider, account, name):
    key = encode([provider, account])
    name = label(name)
    edit(capture, lambda state: state['account_aliases'].update({key: name}))


def alias(capture, scope):
    return registry(capture)['account_aliases'].get(encode(list(scope)), scope[1])


def mirror(capture, target, scopes):
    """Copy authoritative capture data in transactions; preserve independent annotations/settings.

    Raw fill conflicts abort rather than overwrite a different execution. Source label upgrades
    are allowed just as Journal.ingest allows them. SQL parameters never come from path text.
    Named journals are materialized databases so their ordinary SQLite export remains complete.
    """
    if capture.dataset != target.dataset or capture.path.resolve() == target.path.resolve():
        raise ValueError('Named journal mirrors require distinct files in the same environment.')
    clauses = ['(json_extract(payload,\'$.provider\')=? AND json_extract(payload,\'$.account\')=?)' for _ in scopes]
    where = ' OR '.join(clauses) or '1'
    params = [v for scope in scopes for v in scope]
    with capture.connect() as db:
        stored = Journal._distinct(db, 'symbol')       # every symbol the capture holds prices under
    with target.connect() as db:
        db.execute('ATTACH DATABASE ? AS capture', (str(capture.path),))
        db.execute('BEGIN')
        db.execute('CREATE TEMP TABLE selected_fills AS SELECT key FROM capture.executions WHERE ' + where, params)
        conflicts = db.execute("SELECT held.key FROM main.executions held JOIN capture.executions incoming ON held.key=incoming.key "
                               "JOIN selected_fills chosen ON chosen.key=incoming.key WHERE "
                               "json_remove(held.payload,'$.quality','$.native_position_id','$.native_entry') != "
                               "json_remove(incoming.payload,'$.quality','$.native_position_id','$.native_entry') LIMIT 1").fetchone()
        if conflicts:
            raise ImportConflict('A named-journal execution conflicts with the capture source. Nothing was overwritten; reconcile it first.')
        db.execute('INSERT INTO main.executions(key,payload,raw,imported) SELECT incoming.key,incoming.payload,incoming.raw,incoming.imported '
                   'FROM capture.executions incoming JOIN selected_fills chosen ON incoming.key=chosen.key WHERE 1 ORDER BY incoming.rowid '
                   'ON CONFLICT(key) DO UPDATE SET payload=excluded.payload, raw=excluded.raw')
        db.execute("CREATE TEMP TABLE selected_symbols AS SELECT DISTINCT json_extract(payload,'$.symbol') symbol FROM main.executions")
        # A trade's prices are those of its product, whatever symbol they were recorded under (model.trade_prices).
        traded = {product(row[0]) for row in db.execute('SELECT symbol FROM selected_symbols') if row[0]}
        db.execute('CREATE TEMP TABLE price_symbols (symbol TEXT PRIMARY KEY)')
        db.executemany('INSERT INTO price_symbols VALUES(?)', [(symbol,) for symbol in stored if product(symbol) in traded])
        for table in ('prices', 'instruments'):
            # Price rows are shared market observations, not account positions.
            selection = 'price_symbols' if table == 'prices' else 'selected_symbols'
            keys = 'key,symbol,source,ts,payload' if table == 'prices' else 'symbol,point_value'
            update = ('payload=excluded.payload' if table == 'prices' else 'point_value=excluded.point_value')
            difference = ('main.prices.payload != excluded.payload' if table == 'prices' else 'main.instruments.point_value != excluded.point_value')
            conflict_key = 'key' if table == 'prices' else 'symbol'
            db.execute(f'INSERT INTO main.{table}({keys}) SELECT ' + ','.join('incoming.' + key for key in keys.split(',')) +
                       f' FROM capture.{table} incoming JOIN {selection} selected ON incoming.symbol=selected.symbol WHERE 1 '
                       f'ON CONFLICT({conflict_key}) DO UPDATE SET {update} WHERE {difference}')
        db.execute('INSERT OR REPLACE INTO main.capture_events SELECT incoming.* FROM capture.capture_events incoming '
                   'WHERE incoming.source IN (SELECT DISTINCT source FROM main.prices)')
        scopes_in_target = db.execute("SELECT DISTINCT json_extract(payload,'$.provider'),json_extract(payload,'$.account') FROM main.executions").fetchall()
        for provider, account in scopes_in_target:
            db.execute('INSERT OR REPLACE INTO main.protection SELECT incoming.* FROM capture.protection incoming '
                       'JOIN selected_symbols selected ON incoming.symbol=selected.symbol WHERE incoming.provider=? AND incoming.account=?',
                       (provider, account))
    ids = [trade.id for trade in target.trades()]
    if ids:
        with target.connect() as db:
            db.execute('ATTACH DATABASE ? AS capture', (str(capture.path),))
            db.execute('BEGIN')
            db.execute('CREATE TEMP TABLE selected_trades (id TEXT PRIMARY KEY)')
            db.executemany('INSERT INTO selected_trades VALUES(?)', [(id,) for id in ids])
            db.execute('INSERT OR REPLACE INTO main.metrics SELECT incoming.* FROM capture.metrics incoming '
                       'JOIN selected_trades selected ON incoming.trade_id=selected.id')
            # The path stays a reference to original local captures. allowed_media_roots is set by
            # open_named, not trusted from the copied payload. Manual named-journal captures stay local.
            db.execute('INSERT OR IGNORE INTO main.media SELECT incoming.* FROM capture.media incoming '
                       'JOIN selected_trades selected ON incoming.trade_id=selected.id')
