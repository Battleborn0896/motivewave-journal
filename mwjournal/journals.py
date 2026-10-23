"""Named local journals with independent reviews and an explicitly scoped capture mirror.

The study always writes the original Live/Replay journal. A named journal mirrors only selected
provider/account pairs, never consumes a bridge independently, and never writes back to capture.
Archiving hides a journal; its database and media remain recoverable. Paths are UUID-owned.
"""
from __future__ import annotations

import datetime as dt
import json
import re
import sqlite3
import uuid
from pathlib import Path

from .model import instant, product, timestamp
from .scope import allows_source
from .store import Journal, ImportConflict, drop_copies, encode, minute
from .workspace import label

KEY = 'journal_registry'
RECENT = dt.timedelta(minutes=3)       # the study writes the last two minutes' candles again as each minute closes
CONTEXT = dt.timedelta(minutes=120)    # the widest chart context before entry (Trades → Options)


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
        measure_own(capture, journal)
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


def lock_named(db):
    """Start a transaction holding the named journal's write lock and nothing on the attached capture journal.
    BEGIN IMMEDIATE would take the capture's write lock too and hold up the watcher; a deferred transaction
    that reads first can fail at once if another window writes in between. Writing to main first waits its
    turn at the busy timeout, on the named journal alone."""
    db.execute('BEGIN')
    db.execute('DELETE FROM main.prices WHERE 0')


def follow(journal, trade):
    """Bring a named journal's copy of an open trade up to date, as the watcher keeps the capture journal: the
    prices, gaps and orders the capture holds for it since, then its measurement. An open trade's one-second
    redraw calls this; the rest of a named journal is brought up to date when the whole page is drawn
    (open_named). A trade the capture journal measures as this journal holds it takes that measurement; one
    changed in this journal alone (a fill added by hand, its own multiplier) is measured here. Nothing to do
    for a capture journal, which the watcher keeps."""
    capture = getattr(journal, 'capture_journal', None)
    if capture is None or trade.status != 'open':
        return
    with capture.connect() as db:
        stored = {label: [s for s in Journal._distinct(db, 'source', 'symbol=? AND ', (label,)) if allows_source(trade, s)]
                  for label in capture._labels(db, trade.symbol)}
    sources = sorted({source for found in stored.values() for source in found})
    with journal.connect() as db:
        db.execute('ATTACH DATABASE ? AS capture', (str(capture.path),))
        lock_named(db)
        since = {}
        for label, found in stored.items():
            for source in found:
                held = db.execute('SELECT ts, payload FROM main.prices WHERE symbol=? AND source=? ORDER BY ts DESC LIMIT 1',
                                  (label, source)).fetchone()
                # From the newest price held: a print is never written again, but the study writes the last two
                # minutes' candles again as each minute closes. A print, from the start of its minute: the capture
                # journal may have packed that minute since, and the packed row is stored at the minute's start.
                # Nothing held yet: from the chart's widest context.
                if held is None:
                    start = timestamp(instant(trade.opened) - CONTEXT)
                elif json.loads(held['payload']).get('resolution_seconds'):
                    start = timestamp(instant(held['ts']) - RECENT)
                else:
                    start = minute(held['ts'])
                copied = db.execute('INSERT INTO main.prices(key,symbol,source,ts,payload) SELECT key,symbol,source,ts,payload '
                                    'FROM capture.prices WHERE symbol=? AND source=? AND ts>=? AND true '
                                    'ON CONFLICT(key) DO UPDATE SET payload=excluded.payload '
                                    "WHERE main.prices.payload != excluded.payload RETURNING ts, "
                                    "iif(json_extract(payload, '$.packed') IS NOT NULL, key, NULL)",
                                    (label, source, start)).fetchall()
                drop_copies(db, [row[1] for row in copied if row[1]])        # as mirror does
                if copied:
                    first = instant(min(row[0] for row in copied))
                    since[source] = min(since.get(source, first), first)
        if sources:
            db.execute('INSERT OR IGNORE INTO main.capture_events SELECT * FROM capture.capture_events WHERE source IN ('
                       + ','.join('?' * len(sources)) + ') AND ts>=?', (*sources, timestamp(trade.opened)))
        db.execute('INSERT OR IGNORE INTO main.protection SELECT * FROM capture.protection '
                   'WHERE provider=? AND account=? AND symbol=? AND ts>=?',
                   (trade.provider, trade.account, trade.symbol, timestamp(trade.opened)))
        measured = db.execute('SELECT count(*) FROM capture.metrics WHERE trade_id=? AND basis=?',
                              (trade.id, trade.basis)).fetchone()[0]
        if measured:
            db.execute('INSERT INTO main.metrics SELECT * FROM capture.metrics WHERE trade_id=? AND basis=? AND true '
                       'ON CONFLICT(trade_id, basis, source) DO UPDATE SET payload=excluded.payload '
                       'WHERE main.metrics.payload != excluded.payload', (trade.id, trade.basis))
    if not measured:
        # Measured here: from the earliest price this call copied, so measure carries on from where the
        # measurement stopped, or measures from entry when a copied price is older (or nothing was measured
        # yet). Prices the whole page's mirror copied are measured by measure_own, on that page.
        held = journal.metric_options(trade)
        for source in journal.price_sources(trade.symbol, trade):
            stops = [m.get('last_ts') for m in held if m['source'] == source]
            if not (stops and all(stops)):
                journal.measure(trade, source)
            elif source in since:
                journal.measure(trade, source, since=since[source])


def measure_own(capture, journal):
    """Measure the trades this journal holds differently from the capture journal (a fill added by hand, its own
    multiplier), which the capture's watcher does not measure: an open one from entry, as the mirror may have
    copied prices older than its measurement; a closed one once, when it has no measurement yet, and once more
    when this journal was measured under older rules (store.MEASUREMENT_RULES). It counts as measured under
    these rules only once the capture journal is, so nothing the mirror copies is left from the older ones."""
    again = journal.recalculation_due()
    with capture.connect() as db:
        captured = {tuple(row) for row in db.execute('SELECT DISTINCT trade_id, basis FROM metrics')}
    with journal.connect() as db:
        measured = {tuple(row) for row in db.execute('SELECT DISTINCT trade_id, basis FROM metrics')}
        priced = {product(label) for label in Journal._distinct(db, 'symbol')}
    for trade in journal.trades():
        own = trade.complete and (trade.id, trade.basis) not in captured and product(trade.symbol) in priced
        if own and (again or trade.status == 'open' or (trade.id, trade.basis) not in measured):
            for source in journal.price_sources(trade.symbol, trade):
                journal.measure(trade, source)
    if again and not capture.recalculation_due():
        journal.recalculation_done()


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
        lock_named(db)
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
        # Price rows are shared market observations, not account positions. They come as the capture journal stores
        # them: a packed minute (Journal.pack_prices) is a row like any other, and the copies of its prints this
        # journal holds as rows go as it comes (drop_copies). A named journal packs nothing of its own.
        copied = db.execute('INSERT INTO main.prices(key,symbol,source,ts,payload) SELECT incoming.key,incoming.symbol,'
                            'incoming.source,incoming.ts,incoming.payload FROM capture.prices incoming JOIN price_symbols '
                            'selected ON incoming.symbol=selected.symbol WHERE 1 ON CONFLICT(key) DO UPDATE SET '
                            'payload=excluded.payload WHERE main.prices.payload != excluded.payload '
                            "RETURNING iif(json_extract(payload, '$.packed') IS NOT NULL, key, NULL)").fetchall()
        drop_copies(db, [row[0] for row in copied if row[0]])
        db.execute('INSERT INTO main.instruments(symbol,point_value) SELECT incoming.symbol,incoming.point_value '
                   'FROM capture.instruments incoming JOIN selected_symbols selected ON incoming.symbol=selected.symbol '
                   'WHERE 1 ON CONFLICT(symbol) DO UPDATE SET point_value=excluded.point_value '
                   'WHERE main.instruments.point_value != excluded.point_value')
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
            # Only rows that changed: an unchanged row keeps its rowid, which common.rows reads to know whether the
            # trade list must be built again.
            db.execute('INSERT INTO main.metrics SELECT incoming.* FROM capture.metrics incoming '
                       'JOIN selected_trades selected ON incoming.trade_id=selected.id WHERE true '
                       'ON CONFLICT(trade_id, basis, source) DO UPDATE SET payload=excluded.payload '
                       'WHERE main.metrics.payload != excluded.payload')
            # The path stays a reference to original local captures. allowed_media_roots is set by
            # open_named, not trusted from the copied payload. Manual named-journal captures stay local.
            db.execute('INSERT OR IGNORE INTO main.media SELECT incoming.* FROM capture.media incoming '
                       'JOIN selected_trades selected ON incoming.trade_id=selected.id')
