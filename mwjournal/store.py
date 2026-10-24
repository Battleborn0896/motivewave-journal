"""Transactional SQLite storage in the user's own data folder; live and replay are separate files, and the Live
file also keeps the tags and settings both use (Journal.shared)."""

from __future__ import annotations

import base64
import datetime as dt
import json
import os
import shutil
import sqlite3
import tempfile
import zlib
from collections import Counter
from contextlib import contextmanager
from dataclasses import asdict
from pathlib import Path

from . import humanize, paths, tagging
from .matching import match
from .metrics import carry_on, measurements
from .model import (NATIVE, Execution, PriceSample, ProtectionEvent, digest, finite, heartbeat_report, instant,
                    product, timestamp, trade_prices)
from .risk import stop_for_risk
from .scope import event_mode, allows_source
from .tags import DAY
from .workspace import (MAX_QUESTIONS, SHARED, Workspace, checklist_questions, follow, question,
                        read as read_workspace, store_checklist, time_zone, write as write_workspace)

SCHEMA_VERSION = 4
# The one connection a journal reads, the study's bridge file (sync.py): its row in 'profiles' and 'sync_status'.
DEFAULT_CONNECTION = 'MotiveWave'
# The rules trades are measured by. Raise it when they change: the watcher then measures every trade of a
# journal measured under older rules once more, from its stored prices, and records this number in it.
# 1: a trade's prices are those of its product (model.trade_prices).
# 2: dollar MAE / MFE are the moves of the contracts held (banked profit no longer counts, metrics.py), and
#    the bid / ask ('liquidation') measurement is gone: its stored rows are removed when this pass ends.
# 3: points come from the same moment as the dollars (metrics.extremes), and the platform's own MAE / MFE reports
#    are gone: their stored rows are removed when this pass ends.
MEASUREMENT_RULES = 3
# What the pass for these rules says once at the top of Settings (Journal.recalculation_done).
MEASURED_AGAIN = ('MAE / MFE were measured again from your recorded ticks, so dollars, points, ticks and R now '
                  'show the same moment.')
# The prints a trade uses: from 2 minutes before its first fill to 120 minutes after its last, which covers the 15
# minutes after its exit (post_exit.AFTER_EXIT_MINUTES) with room to spare. Never make it shorter than the 30 minutes
# after each exit the study loads, or those prints would be thrown away. An open trade's window runs on to now,
# unless it is incomplete: nothing measures one, so its window ends as a closed one's would. Prints outside every
# trade's window are removed once (Journal._trim_prices): MotiveWave keeps that history itself, and charts draw its
# candles.
PRINTS_BEFORE, PRINTS_AFTER = dt.timedelta(minutes=2), dt.timedelta(minutes=120)
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


def megabytes(size: int) -> str:
    return f'{size / 1e6:,.0f} MB' if size >= 1e6 else 'under 1 MB'


# A print as ingest_prices stores it: these keys, one price (high == low), no open or close. Only the study's prints
# ('motivewave-' sources) of this shape are packed (Journal.pack_prices); bars, imported prices and anything else
# stay rows as they were stored.
PRINT_KEYS = frozenset(('ask', 'bid', 'close', 'high', 'low', 'open', 'proxy', 'resolution_seconds', 'source', 'symbol',
                        'ts', 'volume'))
MICROSECOND = dt.timedelta(microseconds=1)


def plain_print(data: dict) -> bool:
    return (data.keys() == PRINT_KEYS and data['resolution_seconds'] == 0 and data['high'] == data['low']
            and data['open'] is None and data['close'] is None)


def minute(ts: str) -> str:
    """The UTC minute a stored time falls in, written as stored times are."""
    return instant(ts).replace(second=0, microsecond=0).isoformat()


def packed_key(symbol: str, source: str, start: str) -> str:
    """The key of the packed row of one symbol, source and minute."""
    return digest([symbol, source, start, 'packed'])


def identity(data: dict) -> tuple:
    """What makes a print of one symbol and source the same print: what its key digest says (ingest_prices)."""
    return data['ts'], data['high'], data['bid'], data['ask'], data['proxy'], data['volume']


def pack(symbol: str, source: str, start: str, prints: list) -> dict | None:
    """The packed row of the prints of one symbol, source and minute, given as (rowid, payload) in the order
    prices() reads them. Each print's time is kept in microseconds from the minute, its prices in steps of the
    smallest power of ten that makes them whole, and the rowid it was stored under, which orders prints sharing a
    time; each as the change from the print before, compressed: about 5 bytes a print instead of about 475 as a row.
    The row stays JSON: the prices_bars index reads every payload as JSON. None when a price needs more than ten
    decimals."""
    quoted = [data[name] for _, data in prints for name in ('high', 'bid', 'ask') if data[name] is not None]
    scale = next((10 ** n for n in range(11) if all(round(p * 10 ** n) / 10 ** n == p for p in quoted)), None)
    if scale is None:
        return None
    base, columns, before = instant(start), [[] for _ in range(7)], (0, 0, 0)
    for stored, data in prints:
        at, price = (instant(data['ts']) - base) // MICROSECOND, round(data['high'] * scale)
        for column, value in zip(columns, (
                at - before[0], price - before[1], data['volume'],
                None if data['bid'] is None else round(data['bid'] * scale) - price,
                None if data['ask'] is None else round(data['ask'] * scale) - price,
                int(data['proxy']), stored - before[2])):
            column.append(value)
        before = at, price, stored
    blob = zlib.compress(json.dumps([scale, *columns], separators=(',', ':')).encode(), 9)
    return {'symbol': symbol, 'source': source, 'ts': start, 'resolution_seconds': 0, 'prints': len(prints),
            'last': prints[-1][1]['ts'], 'packed': base64.b64encode(blob).decode('ascii')}


def unpack(data: dict) -> list:
    """The prints a packed row holds, as (rowid, payload) exactly as they were stored, in the order stored."""
    scale, times, prices, volumes, bids, asks, proxies, rowids = json.loads(
        zlib.decompress(base64.b64decode(data['packed'])))
    base, symbol, source = instant(data['ts']), data['symbol'], data['source']
    at = price = stored = 0
    prints = []
    for step, move, volume, bid, ask, proxy, row in zip(times, prices, volumes, bids, asks, proxies, rowids):
        at, price, stored = at + step, price + move, stored + row
        last = price / scale
        prints.append((stored, {'ask': None if ask is None else (price + ask) / scale,
                                'bid': None if bid is None else (price + bid) / scale, 'close': None, 'high': last,
                                'low': last, 'open': None, 'proxy': bool(proxy), 'resolution_seconds': 0.0,
                                'source': source, 'symbol': symbol, 'ts': (base + at * MICROSECOND).isoformat(),
                                'volume': volume}))
    return prints


def _schema_3(db) -> dict:
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


# The three boxes the Daily Report Card had until schema 4, and the Day tag (tags.DAY) each ticked one became.
DAY_BOXES = dict(zip(('planned', 'risk_limits', 'stops'), DAY[2]))


def _schema_4(db) -> dict:
    """Schema 4, inside the caller's transaction, and harmless to run twice: one time zone, the journal's own.

    Until schema 4 a trading day had a time zone of its own. Day tags followed the tag library's (New York unless
    changed) and the Daily Report Card saved each review under the zone chosen on it, so a day was stored as
    'date:zone'. Now a day is its date in the journal's Time Zone (workspace.time_zone), stored as 'date', and a
    journal with no Time Zone saved is given this computer's, so its days never move with the computer. Every day
    moves to its date: the days of one date in different zones become one. Their day tags join, a one-choice
    category keeping the choice of the day in the Time Zone; of the review, that day's boxes win, empty ones are
    filled from the others, and different notes are joined with a blank line. A trade near midnight can so fall on
    the neighbouring date and gain or lose that date's day tags. The tag library loses its own zone and gains the
    Day category (tagging.upgrade_library), and each ticked box of the old card becomes its Day tag. A stored day
    rating stays: it is the day's Rating."""
    workspace = read_workspace(db)
    if not humanize.valid_zone(workspace['preferences'].get('display_timezone')):
        workspace['preferences']['display_timezone'] = humanize.local_zone()
        write_workspace(db, workspace)
    zone = time_zone(db)
    days = {}                                      # date: [(not in the Time Zone, its zone, its key, the day)]
    for key, payload in db.execute('SELECT key, payload FROM sessions').fetchall():
        date, _, held = key.partition(':')
        days.setdefault(date, []).append((held not in ('', zone), held, key, json.loads(payload)))
    ticked = any(day.get(box) for found in days.values() for *_, day in found for box in DAY_BOXES)
    library, day_tags = tagging.upgrade_library(db, needed=ticked)
    moved = 0
    for date, found in days.items():
        found.sort(key=lambda held: held[:2])      # the day in the Time Zone first
        merged, chosen, notes = {}, [], []
        for *_, day in found:
            tags = [*day.get('day_tag_ids', []), *(day_tags[name] for box, name in DAY_BOXES.items() if day.get(box))]
            # A one-choice category an earlier day of this date chose in keeps that choice. Within one day nothing
            # is dropped: conflicting choices made before the category became one-choice stay, as they do on a trade.
            taken = {library['tags'].get(id, {}).get('group') for id in chosen}
            for id in tags:
                group = library['tags'].get(id, {}).get('group')
                if id not in chosen and not (group in taken and library['groups'].get(group, {}).get('exclusive')):
                    chosen.append(id)
            if isinstance(day.get('notes'), str) and day['notes'].strip() and day['notes'].strip() not in notes:
                notes.append(day['notes'].strip())
            for field, value in day.items():
                if field not in ('day_tag_ids', 'notes', 'timezone', *DAY_BOXES) and merged.get(field) in (None, ''):
                    merged[field] = value
        if chosen or any('day_tag_ids' in day for *_, day in found):
            merged['day_tag_ids'] = sorted(chosen)
        if notes or any('notes' in day for *_, day in found):
            merged['notes'] = '\n\n'.join(notes)
        if [key for *_, key, _ in found] != [date] or found[0][3] != merged:
            db.executemany('DELETE FROM sessions WHERE key=?', [(key,) for *_, key, _ in found])
            db.execute('INSERT INTO sessions VALUES(?,?)', (date, encode(merged)))
            moved += 1
    return {'days': {'zone': zone, 'moved': moved,
                     'note': f'Days now follow your Time Zone ({zone}); day reviews moved to their dates and the day '
                             'checkboxes became Day tags.'}}


# Each schema step by the version it brings a journal to, run in this order inside one transaction (Journal).
UPGRADES = {3: _schema_3, 4: _schema_4}


def _upgrade(db, version: int) -> dict:
    """Brings a journal stored under schema `version` to SCHEMA_VERSION, every step it lacks in order, inside the
    caller's transaction, and returns what the steps did for the upgrade note."""
    note = {}
    for reaches, step in sorted(UPGRADES.items()):
        if version < reaches:
            note.update(step(db))
    return note


def _stops_from_risks(db) -> int:
    """Once per journal, when it is first opened by 0.4.7. R now comes from a trade's stop (risk.py), no
    longer from a dollar risk typed under Notes. A review that saved such a risk and no stop is given the
    stop that puts exactly that risk on the position held when the stop settled, so its R stays what it
    was, and clearing that stop later returns to the captured stop order. Nothing is removed: the dollar
    figure stays stored beside it, no longer used, so this needs no backup. A review whose trade cannot say its
    position (missing fills or multiplier, or a trade no longer there) is left as it was."""
    legacy = {}
    for row in db.execute('SELECT trade_id, payload FROM annotations').fetchall():
        review = json.loads(row['payload'])
        if review.get('initial_risk') and review.get('planned_stop') is None:
            legacy[row['trade_id']] = review
    converted = 0
    if legacy:
        instruments = {r['symbol']: r['point_value'] for r in db.execute('SELECT * FROM instruments')}
        fills = [Execution(**json.loads(r['payload'])) for r in db.execute('SELECT payload FROM executions ORDER BY rowid')]
        trades = {t.id: t for t in match(fills, instruments)}
        for trade_id, review in legacy.items():
            stop = stop_for_risk(trades[trade_id], review['initial_risk']) if trade_id in trades else None
            if stop is not None:
                db.execute('UPDATE annotations SET payload=? WHERE trade_id=?',
                           (encode({**review, 'planned_stop': stop}), trade_id))
                converted += 1
    db.execute("INSERT OR REPLACE INTO journal_meta VALUES('stops_from_risks',?)", (str(converted),))
    return converted


# The Execution tag each answer to the 'Followed my plan' box of 0.4.16 and earlier became (_adherence_to_tags).
ADHERENCE_TAGS = {'Yes': 'Followed Plan', 'No': 'Broke Plan'}


def _reviews_to_tags(db, flag: str, group: str, tag_for) -> int:
    """Once per journal, inside the caller's transaction: each review that `tag_for(review)` names a tag for (None
    for no tag) gains that tag, made in `group` when no tag has that name (tagging.file_under). The old answer stays
    stored beside it, no longer read, so this needs no backup. Returns how many reviews gained a tag, also stored
    under `flag` in journal_meta, the record that this ran."""
    found = {}
    for row in db.execute('SELECT trade_id, payload FROM annotations ORDER BY rowid').fetchall():
        review = json.loads(row['payload'])
        if name := tag_for(review):
            found[row['trade_id']] = review, name
    if found:
        state = tagging.library(db)
        for trade_id, (review, name) in found.items():
            db.execute('UPDATE annotations SET payload=? WHERE trade_id=?',
                       (encode(tagging.file_under(state, review, name, group)), trade_id))
        tagging.save(db, state)
    db.execute('INSERT OR REPLACE INTO journal_meta VALUES(?,?)', (flag, str(len(found))))
    return len(found)


def _typed_setup(review) -> str | None:
    """What was typed in the Setup box of 0.4.16 and earlier, as a tag name (at most 64 characters, one space between
    words, so 'Opening  Range' is the tag Opening Range), or None."""
    text = review.get('setup')
    return ' '.join(text.split())[:64].strip() if isinstance(text, str) and text.strip() else None


def _setups_to_tags(db) -> int:
    """Each typed setup becomes a Setup tag (_reviews_to_tags). Spellings that differ only in case or spaces are
    one tag, named as it was typed most often."""
    def spelling(text):
        return ''.join(text.split()).casefold()
    typed = {}
    for (payload,) in db.execute('SELECT payload FROM annotations ORDER BY rowid').fetchall():
        if text := _typed_setup(json.loads(payload)):
            typed.setdefault(spelling(text), Counter())[text] += 1
    return _reviews_to_tags(db, 'setups_to_tags', 'Setup',
                            lambda review: (text := _typed_setup(review)) and typed[spelling(text)].most_common(1)[0][0])


def _adherence_to_tags(db) -> int:
    """Each 'Followed my plan' answer becomes an Execution tag, Yes Followed Plan and No Broke Plan
    (_reviews_to_tags); Unreviewed becomes nothing."""
    return _reviews_to_tags(db, 'adherence_to_tags', 'Execution',
                            lambda review: ADHERENCE_TAGS.get(str(review.get('adherence'))))


def _answers_to_tags(db) -> None:
    """The typed setups, then the 'Followed my plan' answers, become tags, each once per journal, inside the caller's
    transaction, and the upgrade note says how many under 'answers'."""
    moved = {}
    for flag, step in (('setups_to_tags', _setups_to_tags), ('adherence_to_tags', _adherence_to_tags)):
        if not db.execute('SELECT 1 FROM journal_meta WHERE key=?', (flag,)).fetchone():
            moved[flag] = step(db)
    words = [f'{what} on {n} trade' + ('' if n == 1 else 's') for what, n in (
        ('Typed setups became Setup tags', moved.get('setups_to_tags')),
        ('Followed my plan answers became Execution tags', moved.get('adherence_to_tags'))) if n]
    if words:
        _add_note(db, 'answers', {**moved, 'note': '; '.join(words) + '.'})


def _prints_away_from_trades(db) -> int:
    """Removes the prints the study recorded outside every trade's window (PRINTS_BEFORE, PRINTS_AFTER) and returns
    how many, inside the caller's transaction. Only the study's prints go: its candles, imported prices and every
    print inside a window stay. MAE / MFE and post-exit read prints inside a window only, so no measurement changes."""
    instruments = {r['symbol']: r['point_value'] for r in db.execute('SELECT * FROM instruments')}
    fills = [Execution(**json.loads(r['payload'])) for r in db.execute('SELECT payload FROM executions ORDER BY rowid')]
    windows = []                                   # [start, end] in time order, overlaps joined; end None runs on
    for trade in sorted(match(fills, instruments), key=lambda t: instant(t.opened)):
        start = instant(trade.opened) - PRINTS_BEFORE
        # An incomplete trade is never measured. A fill the platform gave no price leaves one open for good, months
        # on: running on to now, its window would keep every print recorded since.
        last = trade.closed or (trade.fills[-1]['ts'] if trade.incomplete else None)
        end = instant(last) + PRINTS_AFTER if last else None
        if windows and (windows[-1][1] is None or start <= windows[-1][1]):
            windows[-1][1] = None if windows[-1][1] is None or end is None else max(windows[-1][1], end)
        else:
            windows.append([start, end])
    db.execute('CREATE TEMP TABLE kept_windows (start TEXT PRIMARY KEY, until TEXT)')
    db.executemany('INSERT INTO kept_windows VALUES(?,?)',
                   [(timestamp(start), timestamp(end) if end else None) for start, end in windows])
    # What stays is set aside and copied back: twice as quick as deleting most of a long tape row by row, and each
    # row keeps its rowid, the order prices sharing a time were stored in (prices()). Stored times are UTC ISO
    # text, so they compare as text in time order: a print is inside a window when the last window starting at or
    # before it has not ended by then, one index step per print. A packed minute (pack_prices) is stored at the
    # minute's start, so it is never judged by that time: it stays.
    db.execute('CREATE TEMP TABLE kept_prices AS SELECT rowid AS stored, key, symbol, source, ts, payload FROM prices '
               "WHERE source NOT GLOB 'motivewave-*' OR coalesce((SELECT until IS NULL OR until >= prices.ts "
               'FROM kept_windows WHERE start <= prices.ts ORDER BY start DESC LIMIT 1), 0) '
               "OR json_extract(payload, '$.resolution_seconds') IS NOT 0 OR json_extract(payload, '$.packed') IS NOT NULL")
    removed = (db.execute('SELECT count(*) FROM prices').fetchone()[0]
               - db.execute('SELECT count(*) FROM kept_prices').fetchone()[0])
    if removed:
        db.execute('DELETE FROM prices')
        db.execute('INSERT INTO prices(rowid, key, symbol, source, ts, payload) '
                   'SELECT stored, key, symbol, source, ts, payload FROM kept_prices ORDER BY stored')
    db.execute('DROP TABLE kept_prices')
    db.execute('DROP TABLE kept_windows')
    return removed


def _one_connection(db) -> None:
    """Once per journal, inside the caller's transaction. In 0.4.16 and earlier a journal could also watch CSV files
    and other bridge files. Now it reads one file, the study's (sync.ensure_default_connection, which also points it
    at the file study 0.3.10 always writes): every other connection goes, with its last status and read position.
    Fills and prices already read stay. Nothing stored is rewritten, so this needs no backup; the upgrade note says
    what was removed under 'connections'."""
    removed = [name for (name,) in db.execute('SELECT name FROM profiles WHERE name<>?', (DEFAULT_CONNECTION,))]
    for table in ('profiles', 'sync_status', 'bridge_offsets'):
        db.executemany(f'DELETE FROM {table} WHERE name=?', [(name,) for name in removed])
    db.execute("INSERT OR REPLACE INTO journal_meta VALUES('one_connection',?)", (str(len(removed)),))
    if removed:
        one = len(removed) == 1
        note = (f"The journal now reads only the study's own file; {len(removed)} other watched "
                f'file{"" if one else "s"} ({", ".join(removed)}) {"was" if one else "were"} removed, and the trades '
                f'read from {"it" if one else "them"} stay.')
        _add_note(db, 'connections', {'removed': removed, 'note': note})


def _fixed_stats_tabs(db) -> None:
    """Once per journal, inside the caller's transaction. Until 0.4.17 Customize on Stats chose which tabs it showed,
    in which order; now it always shows all of them, in one order. The saved choice stays stored, unread, so this
    needs no backup; when it hid or moved a tab, the upgrade note says so under 'stats_tabs'. Every tab in order was
    saved under the names 0.4.17 and earlier used, where After Exit was Runners."""
    row = db.execute("SELECT value FROM journal_meta WHERE key='display_settings'").fetchone()
    chosen = (json.loads(row[0]) if row else {}).get('dashboard_sections')
    changed = chosen is not None and chosen != ['Overview', 'Compare', 'MAE / MFE', 'Runners']
    if changed:
        _add_note(db, 'stats_tabs', {'note': 'Stats now always shows all of its tabs: Overview, Compare, MAE / MFE, '
                                             'After Exit and Dashboards.'})
    db.execute("INSERT OR REPLACE INTO journal_meta VALUES('fixed_stats_tabs',?)", (str(int(changed)),))


def _walked(graph) -> dict:
    """`graph` with its questions in the order a walk from its start question meets them, a question's first answer
    followed before its second, and without the questions no walk reaches."""
    nodes = {node['id']: node for node in graph['nodes']}
    order = []

    def visit(id):
        if id is not None and id not in order:
            order.append(id)
            for answer in nodes[id].get('next', [])[:2]:
                visit(follow(nodes, answer)[2])
    visit(follow(nodes, graph['start'])[2])
    return {'start': order[0] if order else graph['start'],
            'nodes': [*(nodes[id] for id in order), *(node for node in graph['nodes'] if node['kind'] != 'question')]}


def _checklists_as_lists(db) -> None:
    """Once per journal, inside the caller's transaction. Until 0.4.17 a checklist was a canvas of question, answer,
    pass and counter nodes; now it is a list of yes/no questions (workspace.question). Each checklist becomes the list
    a walk from its start question meets (_walked, workspace.checklist_questions): a question's first answer is its
    Yes and its second its No, a third answer and an answer's own wording go, and the pass and counter nodes after an
    answer fold into it. Its previous graph stays in its backups and every run keeps its own copy, so this needs no
    backup. A hidden checklist shows again. A checklist only ever run for the session, from the menu above the trade
    list, runs before the session, on the Daily Report Card; every other one on each trade. A session run is given the
    day it started in the Time Zone, so it is that day's. A checklist already a list is left as it is."""
    state, zone = read_workspace(db), time_zone(db)
    old = [key for key, definition in state['checklists'].items() if 'runs_on' not in definition]
    # Read only when needed: a journal's first read of its tag library decides whether it is offered the starters.
    library = tagging.library(db) if old else None
    converted = session = hidden = 0
    for key in old:
        definition = state['checklists'][key]
        runs = [run for run in state['runs'].values() if run.get('checklist_id') == key]
        runs_on = 'session' if runs and not any(run.get('trade_id') for run in runs) else 'trade'
        walked = _walked(definition['graph'])
        questions = (checklist_questions(walked) if any(node['kind'] == 'question' for node in walked['nodes'])
                     else [question(next(node['text'] for node in walked['nodes'] if node['id'] == walked['start']))])
        questions = questions[:MAX_QUESTIONS]
        for asked in questions:
            for answer in ('yes', 'no'):
                branch = asked[answer]
                branch['tag_ids'] = [id for id in branch['tag_ids'] if id in library['tags']]
                if type(branch['go']) is int and branch['go'] > len(questions):
                    branch['go'] = 'finish'
        store_checklist(state, library, key, definition['name'], questions, runs_on)
        converted += 1
        session += runs_on == 'session'
        hidden += bool(definition.get('hidden'))
    dated = [run for run in state['runs'].values() if not run.get('trade_id') and not run.get('day')]
    for run in dated:
        run['day'] = tagging.day_key(run['started'], zone)
    if old or dated:
        write_workspace(db, state)
    db.execute("INSERT OR REPLACE INTO journal_meta VALUES('checklists_as_lists',?)", (str(converted),))
    if converted:
        words = ['Checklists are now lists of yes/no questions',
                 *(['a checklist you ran for the session is now on the Daily Report Card'] if session else []),
                 *(['hidden checklists show again, and Delete removes one'] if hidden else [])]
        _add_note(db, 'checklists', {'converted': converted, 'note': '; '.join(words) + '.'})


def _drafts_without_windows(db) -> None:
    """Once per journal, inside the caller's transaction. Until 0.4.17 a pre-trade draft had a name and waited for a
    trade inside a time window, and one that more than one trade or draft could match was 'ambiguous'; now Tag My
    Next Trade goes on the next trade on its account and contract, whenever it comes (Workspace.reconcile_drafts). A
    draft waiting, ambiguous or needing review whose window has ended is cancelled. One still inside its window waits
    for its trade under the new rule, the one starting last on each account and contract, as arming one now replaces
    the one waiting there. Each takes its window's start as the time it was armed, which orders the Session menu's
    list. Its name, start and end stay stored, unread, so this needs no backup."""
    state = read_workspace(db)
    before = encode(state['drafts'])
    drafts = [draft for draft in state['drafts'].values() if 'end' in draft]       # saved with a window
    now = dt.datetime.now(dt.timezone.utc)
    cancelled = 0
    for draft in drafts:
        draft.setdefault('at', draft['start'])
        if draft['state'] in ('armed', 'ambiguous', 'needs review') and instant(draft['end']) <= now:
            draft['state'] = 'cancelled'
            cancelled += 1
        elif draft['state'] == 'ambiguous':
            draft['state'] = 'armed'
    waiting = {}
    armed = [draft for draft in state['drafts'].values() if draft['state'] == 'armed']
    for draft in sorted(armed, key=lambda d: instant(d['at'])):
        scope = (draft['provider'], draft['account'], draft['symbol'])
        if scope in waiting:
            waiting[scope]['state'] = 'cancelled'
            cancelled += 1
        waiting[scope] = draft
    if encode(state['drafts']) != before:
        write_workspace(db, state)
        ended = (f'; {cancelled} that had not found their trade were cancelled' if cancelled > 1 else
                 '; one that had not found its trade was cancelled' if cancelled else '')
        _add_note(db, 'drafts', {'cancelled': cancelled,
                                 'note': 'Pre-trade drafts are now Tag My Next Trade, in the Session menu on Trades, '
                                         'and go on your next trade on that contract with no time window'
                                         + ended + '.'})
    db.execute("INSERT OR REPLACE INTO journal_meta VALUES('drafts_without_windows',?)", (str(cancelled),))


# What a journal keeps in journal_meta of the settings shared from it (Journal.shared), besides the workspace's
# shared parts (workspace.SHARED): the tag library with its playbooks, the tag catalog of 0.4.0 and earlier, and MAE /
# MFE Units with the tick sizes.
SHARED_KEYS = ('tag_library', 'tag_catalog', 'display_settings')
# What the move to one set of settings for Live and Replay says once, on Settings in Live (Journal._share_settings).
SHARED_NOTE = ('Live and Replay now share one set of tags, playbooks, filters, dashboards, checklists, Time Zone and '
               'MAE / MFE Units, and each keeps its own trades and reviews')


def _own_settings(db) -> tuple[bool, bool]:
    """(whether journal `db` holds settings of its own: a tag library, the tag catalog, display settings or a shared
    part of its workspace, empty or not; whether it holds one worth a backup before it goes: any of those but its
    preferences, which every journal has had since schema 4 and which give way to the shared journal's)."""
    stored = db.execute('SELECT 1 FROM journal_meta WHERE key IN (?,?,?)', SHARED_KEYS).fetchone() is not None
    row = db.execute("SELECT value FROM journal_meta WHERE key='workspace'").fetchone()
    state = json.loads(row[0]) if row else {}
    return (stored or any(part in state for part in SHARED),
            stored or any(state.get(part) for part in SHARED if part != 'preferences'))


def _free_name(name: str, rule: dict, held: dict, word: str) -> str | None:
    """Where the saved filter `name` of another journal goes among the filters `held`: its own name, else 'name (word)',
    'name (word 2)' and on, the first that is free or already holds `rule` (None: nothing to save)."""
    for number in range(1000):
        suffix = '' if number == 0 else f' ({word})' if number == 1 else f' ({word} {number})'
        candidate = name[:80 - len(suffix)] + suffix
        if candidate not in held:
            return candidate
        if held[candidate] == rule:
            return None
    raise ValueError(f'The saved filter {name} has no free name.')


def merge_settings(db, other, word: str = 'Replay') -> tuple[dict, dict]:
    """Joins the settings journal `other` holds of its own into those journal `db` keeps, and stores them in `db`,
    inside both callers' transactions; `other` is only read. What `db` keeps wins:

    - tags, categories and playbooks as tagging.merge joins them, with every tag name on either journal's reviews
      kept in the library (tagging.held), and without the starters `db` deleted that nothing in `other` uses;
    - dashboards and checklists, whose ids are their own, are added after `db`'s, each tag and playbook id in them
      as it became;
    - a saved filter goes under its own name, or under '<name> (<word>)' when `db` has another filter of that name;
    - preferences (the Time Zone, the check-in timer's) and MAE / MFE Units stay those of `db`, any `db` has never
      chosen taken from `other`, and the tick sizes of both are kept.

    Returns ({id: the id it became} for each tag and playbook of `other` that went into one of `db`'s, {filter name:
    the name it is saved under} for each filter saved under another name). Harmless to run again: what it added
    once is found and not added twice."""
    ours, theirs = tagging.held(db), tagging.held(other)
    folds = {}
    if theirs is not None:
        if ours is None:
            ours = theirs
        else:
            # A starter `db` deleted stays deleted, unless `other` uses it.
            unused = {id for id in theirs['tags'] if id in tagging.GIVEN and not tagging.tag_uses(other, theirs, id)}
            ours, folds = tagging.merge(ours, theirs, unused)
    if ours is not None:
        row = db.execute("SELECT value FROM journal_meta WHERE key='tag_library'").fetchone()
        if not row or json.loads(row[0]) != ours:
            tagging.save(db, ours)
    kept, given = read_workspace(db), read_workspace(other)
    before, renamed = encode(kept), {}
    for part in ('dashboards', 'checklists'):
        order = max((item.get('order', 0) for item in kept[part].values()), default=-1)
        for id, item in sorted(given[part].items(), key=lambda pair: pair[1].get('order', 0)):
            if id not in kept[part]:
                order += 1
                kept[part][id] = {**tagging.refold(item, folds), 'order': order}
    for name, rule in given['filters'].items():
        rule = tagging.refold(rule, folds)
        target = _free_name(name, rule, kept['filters'], word)
        if target is not None:
            kept['filters'][target] = rule
            if target != name:
                renamed[name] = target
    kept['preferences'] = {**given['preferences'], **kept['preferences']}
    if encode(kept) != before:
        write_workspace(db, kept)
    def units(each):
        row = each.execute("SELECT value FROM journal_meta WHERE key='display_settings'").fetchone()
        return json.loads(row[0]) if row else {}
    mine, sent = units(db), units(other)
    joined = {**sent, **mine, 'tick_sizes': {**sent.get('tick_sizes', {}), **mine.get('tick_sizes', {})}}
    if sent and joined != mine:
        db.execute("INSERT OR REPLACE INTO journal_meta VALUES('display_settings',?)", (encode(joined),))
    return folds, renamed


def drop_settings(db, folds: dict) -> None:
    """After merge_settings, journal `db` keeps no settings of its own, inside the caller's transaction: each id that
    went into another (`folds`) becomes that id wherever `db`'s own rows name it (its reviews, days, checklist runs,
    check-ins and drafts), and its copies go: tag library, tag catalog, display settings and the shared parts of its
    workspace. Harmless to run again."""
    if folds:
        for table, key in (('annotations', 'trade_id'), ('sessions', 'key')):
            for name, payload in db.execute(f'SELECT {key}, payload FROM {table}').fetchall():
                data = json.loads(payload)
                if (moved := tagging.refold(data, folds)) != data:
                    db.execute(f'UPDATE {table} SET payload=? WHERE {key}=?', (encode(moved), name))
    row = db.execute("SELECT value FROM journal_meta WHERE key='workspace'").fetchone()
    if row:
        state = json.loads(row[0])
        own = tagging.refold({part: value for part, value in state.items() if part not in SHARED}, folds)
        if own != state:
            write_workspace(db, own)
    db.execute('DELETE FROM journal_meta WHERE key IN (?,?,?)', SHARED_KEYS)


def _add_note(db, name: str, data: dict):
    """Records what a one-time step did in the upgrade note (Journal.upgrade_note) under `name`, inside the caller's
    transaction."""
    row = db.execute("SELECT value FROM journal_meta WHERE key='schema_upgrade'").fetchone()
    note = {**(json.loads(row[0]) if row else {}), name: {**data, 'at': dt.datetime.now(dt.timezone.utc).isoformat()}}
    db.execute("INSERT OR REPLACE INTO journal_meta VALUES('schema_upgrade',?)", (encode(note),))


class ImportConflict(ValueError):
    pass


class Journal(Workspace):
    """One environment's journal. `shared` is the journal keeping the tags, playbooks, filters, dashboards, checklists,
    preferences and Units it uses: itself for Live and for a journal opened by its path, the Live journal for the Replay
    journal of the data folder (made first when only Replay is used), and the journal given as shared= (a Live one).
    A Replay journal first opened with a shared one gives its own settings to it (merge_settings)."""

    def __init__(self, path: Path | None = None, *, dataset='live', shared: Journal | None = None):
        from . import journals
        if dataset not in ('live', 'replay'):
            raise ValueError('Journal dataset must be live or replay.')
        self.dataset = dataset
        personal_folder = path is None
        if path is None:
            path = paths.database(dataset)
            if dataset == 'replay' and shared is None:
                shared = Journal()
        self.path = Path(path)
        if shared is not None and shared.dataset != 'live':
            raise ValueError('A journal shares the settings of a Live journal.')
        self.shared = self if shared is None or Path(shared.path).resolve() == self.path.resolve() else shared
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
                db.execute("INSERT OR IGNORE INTO journal_meta VALUES('prices_trimmed','0')")    # nor recorded all day
            version = db.execute('PRAGMA user_version').fetchone()[0]
            if version > SCHEMA_VERSION:
                raise ValueError(newer)
            if version < SCHEMA_VERSION:
                if existing and (backup is None or version != found):
                    # Never rewrite a journal that holds data without a finished copy of exactly that journal.
                    raise ValueError('This journal changed while it was being backed up for an upgrade. Open it again.')
                # The rewrite and the new version number commit together or not at all, so an
                # interrupted upgrade leaves the journal as it was and simply runs again. A new journal takes the
                # steps too, on nothing: schema 4 saves this computer's time zone in it.
                note = _upgrade(db, version)
                if backup is not None:
                    # Added to the note: a one-time step's own note (each a dict, such as 'packed') stays, and an
                    # older schema upgrade's figures give way to this one's.
                    row = db.execute("SELECT value FROM journal_meta WHERE key='schema_upgrade'").fetchone()
                    note = {**{name: value for name, value in (json.loads(row[0]) if row else {}).items()
                               if isinstance(value, dict)},
                            **note, 'from': version, 'to': SCHEMA_VERSION, 'backup': str(backup),
                            'at': dt.datetime.now(dt.timezone.utc).isoformat()}
                    db.execute("INSERT OR REPLACE INTO journal_meta VALUES('schema_upgrade',?)", (encode(note),))
                db.execute('PRAGMA user_version = %d' % SCHEMA_VERSION)
            if not db.execute("SELECT 1 FROM journal_meta WHERE key='stops_from_risks'").fetchone():
                _stops_from_risks(db)
            _answers_to_tags(db)
            if not db.execute("SELECT 1 FROM journal_meta WHERE key='one_connection'").fetchone():
                _one_connection(db)
            if not db.execute("SELECT 1 FROM journal_meta WHERE key='fixed_stats_tabs'").fetchone():
                _fixed_stats_tabs(db)
            if not db.execute("SELECT 1 FROM journal_meta WHERE key='checklists_as_lists'").fetchone():
                _checklists_as_lists(db)
            if not db.execute("SELECT 1 FROM journal_meta WHERE key='drafts_without_windows'").fetchone():
                _drafts_without_windows(db)
            if self.shared is not self:            # last: every step above runs on this journal's own settings
                self._share_settings(db, existed=bool(existing))
            # Named journals of 0.4.17 and earlier become views, after this transaction (journals.fold); a registry
            # without one has nothing to fold, and says so here.
            fold = not db.execute('SELECT 1 FROM journal_meta WHERE key=?', (journals.FLAG,)).fetchone()
            if fold and not journals.named(db):
                db.execute('INSERT INTO journal_meta VALUES(?,?)', (journals.FLAG, '0'))
                fold = False
            trim = not db.execute("SELECT 1 FROM journal_meta WHERE key='prices_trimmed'").fetchone()
        if fold:
            journals.fold(self)
        if trim:
            self._trim_prices()
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

    def _share_settings(self, db, *, existed: bool) -> None:
        """Until 0.4.17 Live and Replay each kept their own tags, playbooks, filters, dashboards, checklists,
        preferences and Units. Inside this journal's write lock, on `db`, and then the shared journal's: this journal's
        own settings join the shared journal's (merge_settings), stored there first, and then go from here
        (drop_settings). The first time (journal_meta 'shared_settings' in the shared journal), when this journal
        existed with settings of its own worth keeping (_own_settings), the data folder is backed up first
        (backup.write, to <home>/backups/before-shared-settings-<UTC time>.zip) and the shared journal's upgrade note
        says so once. Two files cannot commit as one, so every step is harmless to run again: one cut short is
        finished at the next open, as long as this journal holds anything of its own."""
        holds, keeps = _own_settings(db)
        with self.shared.connect() as shared:
            flag = "SELECT 1 FROM journal_meta WHERE key='shared_settings'"
            if not holds and shared.execute(flag).fetchone():
                return
            shared.execute('BEGIN IMMEDIATE')
            first = not shared.execute(flag).fetchone()
            copy = None
            if first and existed and keeps:
                from . import backup
                home = self.shared.path.parent
                copy = backup.write(backup.before(home, 'shared-settings'), home)
            folds, renamed = merge_settings(shared, db)
            if first:
                shared.execute("INSERT INTO journal_meta VALUES('shared_settings',?)",
                               (encode({'folded': len(folds), 'renamed': renamed}),))
                if copy is not None:
                    names = '; '.join(f"Replay's filter {old} is now {new}" for old, new in renamed.items())
                    _add_note(shared, 'shared_settings', {'backup': str(copy), 'renamed': renamed,
                                                          'note': SHARED_NOTE + ('; ' + names if names else '') + '.'})
        drop_settings(db, folds)

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
            # The finished copy takes the first free name in one step, a hard link, which fails on a name already
            # taken instead of replacing that file: openers backing up in the same second never touch one name (Windows
            # refuses to replace a file another opener or a virus scanner has open), and a copy that was cut short
            # never carries a backup's name.
            n = 1
            while True:
                target = self.path.with_name(name + (f'-{n}' if n > 1 else '') + self.path.suffix)
                try:
                    os.link(partial, target)
                    return target
                except FileExistsError:
                    n += 1
        finally:
            partial.unlink(missing_ok=True)

    def _trim_prices(self):
        """Once per journal, when 0.4.14 first opens it. Until then the study recorded every print of its charts all
        day, about 475 bytes each in the journal: one day of NQ took over 300 MB. The prints away from every trade
        are removed (_prints_away_from_trades) in one transaction with the record that this ran, so an interruption
        leaves the journal as it was, to be trimmed at the next open. Then the file is compacted (_compact) so it
        shrinks on disk, and the upgrade note says in plain words what was freed."""
        try:
            size = sum(path.stat().st_size for path in (self.path, Path(f'{self.path}-wal')) if path.exists())
            if shutil.disk_usage(self.path.parent).free < size:
                return                             # the deletion is logged before it commits: next open, with room
            with self.connect() as db:
                # 64 MB of cache rather than 2: the copied-back prints land all over their indexes, and on a
                # day of NQ this takes a third off the few seconds others wait for the journal.
                db.execute('PRAGMA cache_size=-65536')
                db.execute('BEGIN IMMEDIATE')
                if db.execute("SELECT 1 FROM journal_meta WHERE key='prices_trimmed'").fetchone():
                    return                         # another opener has just done it
                page = db.execute('PRAGMA page_size').fetchone()[0]
                before = db.execute('PRAGMA page_count').fetchone()[0] * page
                removed = _prints_away_from_trades(db)
                db.execute("INSERT INTO journal_meta VALUES('prices_trimmed',?)", (str(removed),))
        except (sqlite3.OperationalError, OSError):
            return                                 # busy or out of room: as it was, trimmed at the next open
        if not removed:
            return
        try:
            compacted, freed, _ = self._compact(before)
            words = (f'Freed {megabytes(freed)} of price data recorded away from your trades.' if compacted else
                     f'Removed {megabytes(freed)} of price data recorded away from your trades. The file keeps its size, '
                     'as it was in use or the disk was nearly full; new prices fill that space first.')
            self._note('prices', {'removed': removed, 'freed': freed, 'compacted': compacted, 'note': words})
        except (sqlite3.OperationalError, OSError):
            pass                                   # the prints are gone either way; only the note is missing

    def _compact(self, before: int) -> tuple[bool, int, int]:
        """Gives the space freed inside the file back to the disk: SQLite writes what is kept into a new file
        (VACUUM), so this needs that much free disk twice, and it waits for another writer no longer than any write
        does. From then on the file gives back what a pass frees as it goes (auto_vacuum=INCREMENTAL, pack_prices).
        Returns (compacted, bytes freed since the file took `before`, bytes it takes now); when it cannot run, the
        file keeps its size and new prices fill the space freed inside it first."""
        with self.connect() as db:
            page = db.execute('PRAGMA page_size').fetchone()[0]
            kept = (db.execute('PRAGMA page_count').fetchone()[0] - db.execute('PRAGMA freelist_count').fetchone()[0]) * page
            compacted = shutil.disk_usage(self.path.parent).free >= 2 * kept
            if compacted:
                try:
                    db.execute('PRAGMA auto_vacuum=INCREMENTAL')
                    db.execute('VACUUM')
                    db.execute('PRAGMA wal_checkpoint(TRUNCATE)').fetchall()
                except sqlite3.OperationalError:
                    compacted = False              # another writer held on, or the disk filled: left as it was
            after = db.execute('PRAGMA page_count').fetchone()[0] * page if compacted else kept
        return compacted, max(before - after, 0), after

    def _note(self, name: str, data: dict):
        """Records what a one-time step did, in the upgrade note (upgrade_note) under `name`."""
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            _add_note(db, name, data)

    def upgrade_note(self) -> dict | None:
        """What upgrading this journal did, or None: the last schema upgrade (from, to, its backup); under 'days',
        the move to one time zone (_schema_4); under 'prices', the prints recorded away from trades that were removed
        once (_trim_prices); under 'packed', the first packing of the recorded ticks (pack_prices); under
        'answers', the typed setups and 'Followed my plan' answers that became tags (_answers_to_tags); under
        'connections', the watched files that were removed (_one_connection); under 'stats_tabs', a saved choice of
        Stats tabs that is no longer used (_fixed_stats_tabs); under 'checklists', the checklists that became lists of
        yes/no questions (_checklists_as_lists); under 'drafts', the pre-trade drafts that lost their time window and
        those cancelled with it (_drafts_without_windows); under 'shared_settings', in the Live journal, Replay's
        settings joining Live's and the backup taken first (_share_settings); under 'journal_views', named journals
        becoming views, the fills only they held and the backup taken first (journals.fold); and under
        'measurement_rules_3', every trade measured again under MEASUREMENT_RULES 3 (recalculation_done). Each
        one-time step has a note in plain words, which Settings shows once (unseen_notes)."""
        with self.connect() as db:
            row = db.execute("SELECT value FROM journal_meta WHERE key='schema_upgrade'").fetchone()
        return json.loads(row[0]) if row else None

    def unseen_notes(self) -> dict[str, str]:
        """The plain sentence of each one-time step (upgrade_note) the trader has not seen yet, by step, oldest
        first. Settings shows them in one line until its OK marks them seen (notes_seen)."""
        with self.connect() as db:
            row = db.execute("SELECT value FROM journal_meta WHERE key='notes_seen'").fetchone()
        seen = set(json.loads(row[0])) if row else set()
        steps = sorted((str(step.get('at', '')), name, step['note'])
                       for name, step in (self.upgrade_note() or {}).items()
                       if isinstance(step, dict) and isinstance(step.get('note'), str) and name not in seen)
        return {name: note for _, name, note in steps}

    def notes_seen(self, names) -> None:
        """Marks these steps' notes seen, so Settings no longer shows them."""
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute("SELECT value FROM journal_meta WHERE key='notes_seen'").fetchone()
            seen = sorted({*(json.loads(row[0]) if row else []), *names})
            db.execute("INSERT OR REPLACE INTO journal_meta VALUES('notes_seen',?)", (encode(seen),))

    def pack_prices(self) -> int:
        """Packs the study's prints stored before the previous pass into one row per symbol, source and minute
        (pack), and returns how many prints it packed. prices() unpacks them, so every reader gets exactly the prints
        it got before, in the same order: every print is kept, in about 1% of the space. The watcher runs it as it
        starts and then every hour (sync.py), so a print is packed one to two hours after it was stored, whatever
        trade is open: no trade decides anything here.

        Which prints: journal_meta 'prices_packed' holds two rowids, from and mark. A pass packs the prints stored
        after `from` up to `mark` (a rowid range, never a read of the whole table), then moves from to mark and mark
        to the newest rowid. The first pass has no marks and packs every print. Minutes are packed in batches that
        commit on their own, so a pass cut short leaves each minute either packed or as it was, and the next pass,
        from the same marks, finishes it. A packed minute takes the rowid of its newest print, so the newest rowid
        still names the newest price stored (recent_contracts).

        The first pass then gives the freed space back to the disk once (_compact) and says so in the upgrade note
        ('packed'); every later pass gives back what it freed (incremental_vacuum)."""
        with self.connect() as db:
            row = db.execute("SELECT value FROM journal_meta WHERE key='prices_packed'").fetchone()
            marks = json.loads(row[0]) if row else None
            low, high = ((marks['from'], marks['mark']) if marks else
                         (0, db.execute('SELECT coalesce(max(rowid), 0) FROM prices').fetchone()[0]))
            before = db.execute('PRAGMA page_count').fetchone()[0] * db.execute('PRAGMA page_size').fetchone()[0]
            groups = {}
            for rowid, symbol, source, ts in db.execute(
                    "SELECT rowid, symbol, source, ts FROM prices WHERE rowid>? AND rowid<=? AND source GLOB 'motivewave-*' "
                    "AND json_extract(payload, '$.resolution_seconds')=0 AND json_extract(payload, '$.packed') IS NULL",
                    (low, high)):
                groups.setdefault((symbol, source, minute(ts)), []).append(rowid)
        packed = 0
        ordered = sorted(groups.items(), key=lambda group: max(group[1]))
        for first in range(0, len(ordered), 100):
            with self.connect() as db:
                db.execute('BEGIN IMMEDIATE')
                for (symbol, source, start), rowids in ordered[first:first + 100]:
                    packed += self._pack_minute(db, symbol, source, start, set(rowids))
        with self.connect() as db:
            newest = db.execute('SELECT coalesce(max(rowid), 0) FROM prices').fetchone()[0]
            db.execute("INSERT OR REPLACE INTO journal_meta VALUES('prices_packed',?)",
                       (encode({'from': high, 'mark': newest}),))
        if marks is not None:
            with self.connect() as db:
                db.execute('PRAGMA incremental_vacuum').fetchall()      # unread, it gives nothing back
            return packed
        try:
            compacted, freed, after = self._compact(before)
            if packed:
                words = (f'Packed the recorded ticks: the journal went from {megabytes(before)} to {megabytes(after)}, '
                         'and every tick is kept.' if compacted else
                         'Packed the recorded ticks, and every tick is kept. The file keeps its size, as it was in use '
                         'or the disk was nearly full; new prices fill that space first.')
                self._note('packed', {'prints': packed, 'freed': freed, 'compacted': compacted, 'note': words})
        except (sqlite3.OperationalError, OSError):
            pass                                   # packed either way; the file keeps its size and has no note
        return packed

    @staticmethod
    def _pack_minute(db, symbol: str, source: str, start: str, rowids: set) -> int:
        """Packs the prints stored under `rowids` of one symbol, source and minute into that minute's packed row,
        inside the caller's transaction, and returns how many it took. A print the packed row already holds (read
        twice) only goes. The packed row must unpack to exactly the prints it replaces, rowids and stored text alike,
        or the minute stays as it is."""
        key = packed_key(symbol, source, start)
        held = db.execute('SELECT rowid, payload FROM prices WHERE key=?', (key,)).fetchone()
        prints = [(stored, data, encode(data)) for stored, data in unpack(json.loads(held[1]))] if held else []
        known, taken = {identity(data) for _, data, _ in prints}, []
        until = (instant(start) + dt.timedelta(minutes=1)).isoformat()
        for rowid, text in db.execute('SELECT rowid, payload FROM prices WHERE symbol=? AND source=? AND ts>=? AND ts<?',
                                      (symbol, source, start, until)):
            data = json.loads(text) if rowid in rowids else None
            if data is not None and plain_print(data) and (data['symbol'], data['source']) == (symbol, source):
                taken.append(rowid)
                if identity(data) not in known:
                    known.add(identity(data))
                    prints.append((rowid, data, text))
        if not taken:
            return 0
        prints.sort(key=lambda found: (found[1]['ts'], found[0]))
        payload = pack(symbol, source, start, [(stored, data) for stored, data, _ in prints])
        if payload is None or [(stored, encode(data)) for stored, data in unpack(payload)] != [
                (stored, text) for stored, _, text in prints]:
            return 0
        db.executemany('DELETE FROM prices WHERE rowid=?', [(rowid,) for rowid in taken])
        stored = max(taken + ([held[0]] if held else []))
        if held:
            db.execute('UPDATE prices SET rowid=?, payload=? WHERE key=?', (stored, encode(payload), key))
        else:
            db.execute('INSERT INTO prices(rowid, key, symbol, source, ts, payload) VALUES(?,?,?,?,?,?)',
                       (stored, key, symbol, source, start, encode(payload)))
        return len(taken)

    def recalculation_due(self) -> bool:
        """True while this journal's trades were measured under older MEASUREMENT_RULES than this version's
        (or regrouped by a schema upgrade), until the watcher has measured them all again."""
        with self.connect() as db:
            row = db.execute("SELECT value FROM journal_meta WHERE key='measurement_rules'").fetchone()
        return row is None or int(row[0]) < MEASUREMENT_RULES

    def recalculation_done(self):
        """Every trade has been measured under this version's rules. Bid / ask measurements and the platform's
        own reports, which these rules no longer keep, go with it, including those of trades no longer in the
        journal, and the upgrade note says so once (MEASURED_AGAIN). Safe to run twice: the second finds nothing
        due and nothing to remove."""
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute("SELECT value FROM journal_meta WHERE key='measurement_rules'").fetchone()
            if row is not None and int(row[0]) >= MEASUREMENT_RULES:
                return
            db.execute("DELETE FROM metrics WHERE json_extract(source, '$[1]')='liquidation'")
            removed = db.execute("DELETE FROM metrics WHERE json_extract(payload, '$.precision')='platform-reported'"
                                 ).rowcount
            db.execute("INSERT OR REPLACE INTO journal_meta VALUES('measurement_rules',?)", (str(MEASUREMENT_RULES),))
            db.execute("DELETE FROM journal_meta WHERE key='recalculate'")    # what 0.4.4 and earlier set instead
            # A journal with nothing measured, and no report removed, has nothing to be told.
            if removed or db.execute('SELECT 1 FROM metrics LIMIT 1').fetchone():
                _add_note(db, f'measurement_rules_{MEASUREMENT_RULES}', {'reports_removed': removed,
                                                                          'note': MEASURED_AGAIN})

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
                # Counts the fills described again (fills_revision): the importer reports them, and the
                # watcher's fills_version sees a rewrite of the same length by it.
                db.execute("INSERT INTO journal_meta VALUES('fills_revision','1') ON CONFLICT(key) "
                           'DO UPDATE SET value=CAST(value AS INTEGER)+1')
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

    @staticmethod
    def fills_revision(db) -> int:
        """How many times a fill already held has been described again (_insert)."""
        row = db.execute("SELECT value FROM journal_meta WHERE key='fills_revision'").fetchone()
        return int(row[0]) if row else 0

    def fills_version(self) -> tuple:
        """Changes whenever trades() could: a fill added or described again, or a multiplier set."""
        with self.connect() as db:
            return tuple(db.execute('SELECT (SELECT count(*) FROM executions), (SELECT total(length(payload)) FROM '
                                    "executions), (SELECT value FROM journal_meta WHERE key='fills_revision'), "
                                    "(SELECT group_concat(symbol || '=' || point_value) FROM instruments)").fetchone())

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

    def reattach_reviews(self, trades) -> int:
        """Keep reviews with their trades when fills are regrouped. A trade is named after its first fill
        (matching.start), so an upgrade or a fill described again can leave a review under a name no trade has.
        Its pictures move to the trade that now holds the fill it was named after, and so does its review when
        that trade has none of its own; otherwise the review stays stored where it was (detached_reviews).
        Returns how many reviews moved. `trades` are this journal's trades as just built."""
        known = {trade.id for trade in trades}
        with self.connect() as db:
            detached = {row[0] for row in db.execute('SELECT trade_id FROM annotations UNION SELECT trade_id FROM media')}
        detached -= known
        if not detached:
            return 0
        holder = {fill['key']: trade.id for trade in trades for fill in trade.fills}
        moves = {}
        for fill in self.executions():
            name = digest([(fill.provider, fill.account, fill.symbol, fill.currency), fill.key, fill.side])[:24]
            if name in detached and fill.key in holder:
                moves[name] = holder[fill.key]
        moved = 0
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            for old, new in moves.items():
                db.execute('UPDATE media SET trade_id=? WHERE trade_id=?', (new, old))
                moved += db.execute('UPDATE annotations SET trade_id=? WHERE trade_id=? AND NOT EXISTS '
                                    '(SELECT 1 FROM annotations WHERE trade_id=?)', (new, old, new)).rowcount
        return moved

    def set_instrument(self, symbol: str, point_value: float):
        with self.connect() as db:
            db.execute('INSERT OR REPLACE INTO instruments VALUES(?,?)',
                       (symbol.strip().upper(), finite(point_value, 'Point value', True)))

    def annotate(self, trade_id: str, data: dict):
        tags = sorted({str(t).strip() for t in data.get('tags', []) if str(t).strip()})
        data = {**data, 'tags': tags}
        # R reads only planned_stop (risk.py). initial_risk and planned_target are what older versions saved:
        # kept and still checked, never used (_stops_from_risks turned each initial_risk into a stop once).
        risk = data.get('initial_risk')
        if risk is not None:
            data['initial_risk'] = finite(risk, 'Initial risk', True)
        for key in ('planned_stop', 'planned_target'):
            if data.get(key) is not None:
                data[key] = finite(data[key], key.replace('_', ' ').title())
        with self.connect() as db:
            db.execute('INSERT OR REPLACE INTO annotations VALUES(?,?)', (trade_id, encode(data)))
        if tags:
            # A review that names its tags only by name: each name no tag has is a tag in Other tags, as
            # update_annotation makes one. Asked after the review is stored, so a first review is no new journal.
            with self._settings(write=True) as db:
                state = self._library(db)
                if tagging.ensure_names(state, tags):
                    tagging.save(db, state)

    def annotations(self) -> dict:
        state = self.tag_library()
        with self.connect() as db:
            result = {}
            for row in db.execute('SELECT * FROM annotations'):
                annotation = tagging.project(state, json.loads(row['payload']))
                annotation.pop('tag_ids', None)  # stable IDs live in storage; preserve the public review shape
                result[row['trade_id']] = annotation
            return result

    def update_annotation(self, trade_id: str, changes: dict, *, add_tags=(), remove_tags=()):
        """Atomic partial edits for autosave: a tag click cannot overwrite notes or another group's tags. A new tag
        name becomes a tag of the shared library, saved before the review."""
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            previous = db.execute('SELECT payload FROM annotations WHERE trade_id=?', (trade_id,)).fetchone()
            with self._settings(db, write=True) as shared:
                state = self._library(shared)
                data = {**tagging.project(state, json.loads(previous[0]) if previous else {}), **changes}
                tags = {str(t).strip() for t in data.get('tags', []) if str(t).strip()}
                tags.difference_update(str(t).strip() for t in remove_tags)
                tags.update(str(t).strip() for t in add_tags if str(t).strip())
                data['tags'] = sorted(tags)
                added = tagging.ensure_names(state, tags)
                incoming = tagging.ids(state, add_tags)
                for group, settings in state['groups'].items():
                    choices = [id for id in incoming if state['tags'][id]['group'] == group]
                    if settings['exclusive'] and choices:
                        if len(choices) > 1:
                            raise ValueError('Copied tags contain multiple choices in an exclusive group.')
                        tags = {name for name in tags
                                if state['tags'][next(iter(tagging.ids(state, [name])))]['group'] != group}
                        tags.update(tagging.names(state, choices))
                data['tags'] = sorted(tags)
                data['tag_ids'] = sorted(tagging.ids(state, tags))
                if added:
                    tagging.save(shared, state)
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
        """The one tag library of Live and Replay, kept with the shared settings (shared)."""
        with self._settings() as db:
            return self._library(db)

    def add_tag(self, name: str, group: str):
        """Create a reusable tag; no existing trade is retagged or deleted."""
        name, group = name.strip(), group.strip()
        if not name or not group or len(name) > 64 or len(group) > 40:
            raise ValueError('Give the tag a name (up to 64 characters) and group (up to 40).')
        with self._settings(write=True) as db:
            state = self._library(db)
            existing = tagging.ids(state, [name])
            if existing and state['tags'][next(iter(existing))]['group'] != group:
                raise ValueError('That tag already belongs to another group. Use a different name.')
            if existing and state['tags'][next(iter(existing))]['name'] != name:
                raise ValueError('That name is a historical alias; rename the existing tag instead.')
            tagging.ensure_names(state, [name], group)
            tagging.save(db, state)

    def add_tag_group(self, name: str):
        """Create an empty reusable tag category without inventing a placeholder tag."""
        name = str(name).strip()
        if not name or len(name) > 40:
            raise ValueError('Give the category a name, up to 40 characters.')
        with self._settings(write=True) as db:
            state = self._library(db)
            if name in state['groups']:
                raise ValueError('That category already exists.')
            state['groups'][name] = {'exclusive': False, 'order': len(state['groups'])}
            tagging.save(db, state)

    def delete_tag(self, id):
        """Remove a tag nothing uses, in Live or Replay. A used tag is refused: retire it, so its history keeps its
        name."""
        with self._settings(write=True) as db:
            state = self._library(db)
            if id not in state['tags']:
                raise ValueError('Unknown tag.')
            uses = tagging.tag_uses(db, state, id, self._sharing())
            if uses:
                raise ValueError(f"{state['tags'][id]['name']} {' and '.join(uses)}. "
                                 'Retire it instead: it leaves the tag panel and keeps its history.')
            tagging.save(db, tagging.delete_tag(state, id))

    def delete_tag_group(self, group):
        """Remove an empty category that nothing names, in Live or Replay."""
        with self._settings(write=True) as db:
            state = self._library(db)
            if group not in state['groups']:
                raise ValueError('Unknown category.')
            uses = tagging.group_uses(db, state, group, self._sharing())
            if uses:
                raise ValueError(f"{group} {' and '.join(uses)}. Move or delete its tags first.")
            tagging.save(db, tagging.delete_group(state, group))

    def edit_tag(self, id, **changes):
        with self._settings(write=True) as db:
            tagging.save(db, tagging.edit_tag(self._library(db), id, **changes))

    def edit_tag_group(self, group, *, name=None, exclusive=None, move=0):
        with self._settings(write=True) as db:
            state = self._library(db)
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
        with self._settings(write=True) as db:
            state = self._library(db)
            tag = state['tags'][id]
            ordered = sorted((t for t in state['tags'].values() if t['group'] == tag['group']), key=lambda t: t['order'])
            position = next(i for i, t in enumerate(ordered) if t['id'] == id)
            ordered.insert(max(0, min(len(ordered) - 1, position + offset)), ordered.pop(position))
            for index, item in enumerate(ordered):
                item['order'] = index
            tagging.save(db, state)

    def _tag_rows(self, db) -> tuple[dict, str]:
        """(the tag library, the Time Zone), read from the shared settings while this journal's write lock is held on
        `db`: what a change to a trade's or a day's tags here is checked against."""
        with self._settings(db) as shared:
            return self._library(shared), time_zone(shared)

    def set_trade_tag_group(self, trade, group, chosen):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            state, zone = self._tag_rows(db)
            row = db.execute('SELECT payload FROM annotations WHERE trade_id=?', (trade.id,)).fetchone()
            old = json.loads(row[0]) if row else {}
            session = db.execute('SELECT payload FROM sessions WHERE key=?',
                                 (tagging.day_key(trade.opened, zone),)).fetchone()
            data = tagging.set_group(state, old, group, chosen, json.loads(session[0]) if session else {})
            db.execute('INSERT OR REPLACE INTO annotations VALUES(?,?)', (trade.id, encode(data)))

    def set_day_tag_group(self, date, group, chosen):
        """Chooses a day's tags in one category. The day is a date in the journal's Time Zone (tagging.day_key)."""
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            state, _ = self._tag_rows(db)
            key = dt.date.fromisoformat(str(date)).isoformat()
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
            data = {**old, 'date': key, 'day_tag_ids': sorted(other | chosen)}
            db.execute('INSERT OR REPLACE INTO sessions VALUES(?,?)', (key, encode(data)))

    def save_playbook(self, name, rules, *, id=None, retired=False, color=None):
        """`color` is the playbook's own colour, six-digit hex; None keeps the one it has, if any."""
        import re
        name = name.strip()
        if not name or len(name) > 64:
            raise ValueError('Give this playbook a name (up to 64 characters).')
        with self._settings(write=True) as db:
            state = self._library(db)
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
            color = state['playbooks'].get(id, {}).get('color') if color is None else color
            if color is not None and not re.fullmatch(r'#[0-9a-fA-F]{6}', str(color)):
                raise ValueError('Use a six-digit hex color.')
            state['playbooks'][id] = {'id': id, 'name': name, 'rules': normalized, 'retired': bool(retired),
                                      **({'color': color} if color else {})}
            tagging.save(db, state)
        return id

    def apply_playbook(self, trade, id):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            state, zone = self._tag_rows(db)
            if id not in state['playbooks'] or state['playbooks'][id].get('retired'):
                raise ValueError('Choose an active playbook.')
            row = db.execute('SELECT payload FROM annotations WHERE trade_id=?', (trade.id,)).fetchone()
            data = json.loads(row[0]) if row else {}
            row = db.execute('SELECT payload FROM sessions WHERE key=?',
                             (tagging.day_key(trade.opened, zone),)).fetchone()
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

    def add_day_tags(self, date, tags) -> list[str]:
        """Adds `tags` (names or ids) that are available on days to a day's tags, as a checklist's Apply Collected Tags
        does, and returns the names added. In a one-choice category the new tag replaces the day's."""
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            state, _ = self._tag_rows(db)
            key = dt.date.fromisoformat(str(date)).isoformat()
            row = db.execute('SELECT payload FROM sessions WHERE key=?', (key,)).fetchone()
            old = json.loads(row[0]) if row else {}
            incoming = sorted(id for id in tagging.ids(state, tags)
                              if not state['tags'][id]['retired'] and 'day' in state['tags'][id]['levels'])
            chosen = set(tagging.inherited(state, old))
            for group in {state['tags'][id]['group'] for id in incoming}:
                choices = [id for id in incoming if state['tags'][id]['group'] == group]
                if state['groups'][group]['exclusive']:
                    if len(choices) > 1:
                        raise ValueError(f'{group} takes one tag, and this checklist collected {len(choices)}.')
                    chosen = {id for id in chosen if state['tags'][id]['group'] != group}
                chosen.update(choices)
            db.execute('INSERT OR REPLACE INTO sessions VALUES(?,?)',
                       (key, encode({**old, 'date': key, 'day_tag_ids': sorted(chosen)})))
        return tagging.names(state, incoming)

    def update_session(self, key, changes):
        """Saves fields of one day's review (its Rating, its note), keyed by the day's date 'YYYY-MM-DD'."""
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT payload FROM sessions WHERE key=?', (key,)).fetchone()
            data = {**(json.loads(row[0]) if row else {}), **changes}
            db.execute('INSERT OR REPLACE INTO sessions VALUES(?,?)', (key, encode(data)))

    def display_settings(self):
        """MAE / MFE Units and the tick sizes, one choice for Live and Replay (shared)."""
        with self._settings() as db:
            stored = db.execute("SELECT value FROM journal_meta WHERE key='display_settings'").fetchone()
        settings = {'excursion_unit': 'currency', 'tick_sizes': {}, **(json.loads(stored[0]) if stored else {})}
        settings.pop('excursion_policy', None)     # MAE / MFE Accuracy, until 0.4.7: only recorded ticks measure now
        settings.pop('dashboard_sections', None)   # the Stats tabs chosen until 0.4.17: Stats shows them all now
        return settings

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
        with self._settings(write=True) as db:
            stored = db.execute("SELECT value FROM journal_meta WHERE key='display_settings'").fetchone()
            settings = {**(json.loads(stored[0]) if stored else {}), **changes}
            if tick is not None:
                settings['tick_sizes'] = {**settings.get('tick_sizes', {}), tick[0]: tick[1]}
            db.execute("INSERT OR REPLACE INTO journal_meta VALUES('display_settings',?)", (encode(settings),))

    def record_metric(self, trade, metric: dict):
        """Stores a trade's measurement from one source. A source the study reported a gap in still has its
        measurement stored: the app shows none from it (app/common.py metric_options)."""
        if not trade.complete:
            return                                 # no excursion is kept for a position the fills only partly show
        with self.connect() as db:
            db.execute('INSERT OR REPLACE INTO metrics VALUES(?,?,?,?)',
                       (trade.id, trade.basis, encode([metric['source'], metric.get('basis', 'last')]), encode(metric)))

    def measure(self, trade, source: str, *, since=None):
        """Measure a trade from one source and record it. `since` is the earliest time among the prices stored
        since it was last measured: when they all come at or after where an open trade's measurement from
        prints stopped, it is carried on from there (metrics.carry_on), the same result without reading the
        tape from entry again. Otherwise, or when it cannot be carried on exactly, it is measured from entry."""
        clock = self.trade_clock(trade)
        options = {'now': clock} if clock else {}
        if since is not None and not trade.closed:
            previous = [m for m in self.metric_options(trade) if m['source'] == source]
            stops = [m.get('last_ts') for m in previous]
            if previous and all(stops) and since >= instant(max(stops)):
                carried = carry_on(trade, previous, self.prices(trade.symbol, source, start=min(stops)), **options)
                if carried is not None:
                    for metric in carried:
                        self.record_metric(trade, metric)
                    return
        samples = self.prices(trade.symbol, source, start=trade.opened, end=trade.closed)
        measured = measurements(trade, samples, source, **options)
        with self.connect() as db:
            # The rows this source holds for the trade are exactly these: a bid/ask measurement whose prices the
            # contract's own have since replaced does not stay behind.
            kept = [encode([source, m.get('basis', 'last')]) for m in measured]
            db.execute('DELETE FROM metrics WHERE trade_id=? AND basis=? AND json_extract(source, \'$[0]\')=? AND source NOT IN ('
                       + ','.join('?' * len(kept)) + ')', (trade.id, trade.basis, source, *kept))
        for metric in measured:
            self.record_metric(trade, metric)

    def measured(self) -> set[tuple[str, str, str]]:
        """(trade id, basis, source) of every stored measurement, read from the table's key alone."""
        with self.connect() as db:
            return {(row[0], row[1], json.loads(row[2])[0])
                    for row in db.execute('SELECT trade_id, basis, source FROM metrics')}

    def metric_options(self, trade) -> list[dict]:
        if not trade.complete:
            return []
        with self.connect() as db:
            return [json.loads(r['payload']) for r in db.execute(
                'SELECT payload FROM metrics WHERE trade_id=? AND basis=? ORDER BY source', (trade.id, trade.basis))]

    def ingest_prices(self, samples: list[PriceSample], *, db=None) -> int:
        for sample in samples:
            if sample.source.startswith('motivewave-') and event_mode({'provider': sample.source}) != self.dataset:
                raise ValueError('MotiveWave prices belong to the other trading environment.')
        with self._write(db) as db:
            count, packed = 0, {}                  # (symbol, source, minute): the prints its packed row holds
            for sample in samples:
                data = asdict(sample)
                if sample.source.startswith('motivewave-') and not sample.resolution_seconds:
                    # A print its minute's packed row already holds (pack_prices) is not stored again: a bridge
                    # file read again, or history sent again, adds nothing.
                    slot = (sample.symbol, sample.source, minute(sample.ts))
                    if slot not in packed:
                        row = db.execute('SELECT payload FROM prices WHERE key=?', (packed_key(*slot),)).fetchone()
                        packed[slot] = {identity(held) for _, held in unpack(json.loads(row[0]))} if row else set()
                    if identity(data) in packed[slot]:
                        continue
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
        """The prices of one source that measure a trade in `symbol`, as model.trade_prices chooses them. A packed
        minute (pack_prices) gives the prints it holds, exactly as they were stored."""
        exact = symbol.strip().upper()
        with self.connect() as db:
            labels = self._labels(db, exact)
            if not labels:
                return []
            within = 'symbol IN (' + ','.join('?' * len(labels)) + ') AND source=?'
            columns = "rowid AS stored,symbol,payload,ts,json_extract(payload, '$.packed') IS NOT NULL AS packed"
            filters, args = [within], [*labels, source]
            if start:
                # The hot tick query starts at entry, not a day earlier. A separate partial index
                # picks up overlapping bars without scanning yesterday's entire tick tape.
                filters.append('ts>=?')
                args.append(timestamp(start))
            if end:
                filters.append('ts<=?')
                args.append(timestamp(end))
            query = f'SELECT {columns} FROM prices WHERE ' + ' AND '.join(filters)
            if start:
                query += (f' UNION ALL SELECT {columns} FROM prices INDEXED BY prices_bars WHERE ' + within +
                          " AND json_extract(payload, '$.resolution_seconds') > 0 AND ts>=? AND ts<? "
                          "AND julianday(ts) + json_extract(payload, '$.resolution_seconds') / 86400.0 > julianday(?)")
                args.extend([*labels, source, (instant(start) - dt.timedelta(days=1)).isoformat(),
                             timestamp(start), timestamp(start)])
                # The packed minute that holds `start` is stored at the minute's start, before it.
                query += (f' UNION ALL SELECT {columns} FROM prices WHERE ' + within +
                          " AND ts>=? AND ts<? AND json_extract(payload, '$.packed') IS NOT NULL")
                args.extend([*labels, source, minute(timestamp(start)), timestamp(start)])
            # Prices sharing a time stay in the order they were stored, the tape's, whatever their label:
            # the last of them is a candle's close and an open trade's last price, and metrics.carry_on
            # counts on it.
            rows = db.execute(query + ' ORDER BY ts, stored', args).fetchall()
        # (time, rowid stored under, symbol, payload): a packed print keeps the rowid it was stored under.
        found = [(row['ts'], row['stored'], row['symbol'], row['payload']) for row in rows if not row['packed']]
        if len(found) < len(rows):
            low, high = timestamp(start) if start else '', timestamp(end) if end else None
            for row in rows:
                if row['packed']:
                    found.extend((data['ts'], stored, row['symbol'], data) for stored, data in unpack(json.loads(row['payload']))
                                 if data['ts'] >= low and (high is None or data['ts'] <= high))
            found.sort(key=lambda price: price[:2])
        if any(price[2] == exact for price in found):
            found = [price for price in found if price[2] == exact]  # trade_prices' choice, before a stored payload is read
        return trade_prices([PriceSample.stored(data if isinstance(data, dict) else json.loads(data))
                             for _, _, _, data in found], exact, source)

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
                # The replayed days' bars belong to no run (scope.REPLAY_BARS) and are not recording.
                row = db.execute("SELECT source FROM prices WHERE source LIKE 'motivewave-replay:%' "
                                 "AND source != 'motivewave-replay:bars' ORDER BY rowid DESC LIMIT 1").fetchone()
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
                        newest = self._newest(db, symbol, source)
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
        """Open replay positions advance on recorded replay time, never today's wall clock (replay_clock)."""
        return None if trade.closed else self.replay_clock(trade)

    def replay_clock(self, trade):
        """The time a replay trade's run has played to: the latest price of the trade's product in its own run, or
        just after its last fill. None for a live trade, whose clock is the wall clock."""
        if ':replay:' not in trade.provider:
            return None
        run = trade.provider.rsplit(':replay:', 1)[1]
        prefix = 'motivewave-replay:' + run + ':'
        with self.connect() as db:
            # Every source that starts with the prefix: the run's ticks, simulated prints and history. One
            # index step per source; a range over sources would read every price of the run, every second.
            latest = [self._newest(db, label, source) for label in self._labels(db, trade.symbol)
                      for source in self._distinct(db, 'source', 'symbol=? AND ', (label,)) if source.startswith(prefix)]
        stamps = [instant(f['ts']) for f in trade.fills]
        stamps.extend(instant(stamp) for stamp in latest if stamp)
        return max(stamps) + dt.timedelta(milliseconds=1)

    @staticmethod
    def _newest(db, symbol: str, source: str) -> str | None:
        """The time of the newest price of one symbol and source, two index steps: a packed minute is stored at the
        minute's start, and says when its newest print was."""
        newest = db.execute('SELECT max(ts) FROM prices WHERE symbol=? AND source=?', (symbol, source)).fetchone()[0]
        if newest is None:
            return None
        row = db.execute("SELECT json_extract(payload, '$.last') FROM prices WHERE key=?",
                         (packed_key(symbol, source, minute(newest)),)).fetchone()
        return max(newest, row[0]) if row and row[0] else newest

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
        return self.protection_by_trade([trade])[trade.id]

    def protection_by_trade(self, trades) -> dict[str, list[ProtectionEvent]]:
        """Each trade's linked protective orders in time order: those naming the trade, or naming a fill
        that opened or added to it. One read per account and contract, however many trades it has."""
        result, scopes = {t.id: [] for t in trades}, {}
        for trade in trades:
            scopes.setdefault((trade.provider, trade.account, trade.symbol), []).append(trade)
        with self.connect() as db:
            for scope, group in scopes.items():
                ids, entries = {t.id for t in group}, {}
                for trade in group:
                    for fill in trade.fills:
                        if fill['role'] == 'entry' and fill['execution_id']:
                            entries.setdefault(fill['execution_id'], set()).add(trade.id)
                for row in db.execute('SELECT payload FROM protection WHERE provider=? AND account=? AND symbol=? '
                                      'ORDER BY ts,rowid', scope):
                    # Another trade's order is skipped before it is built into an event (which checks it all
                    # again), so reading one trade's orders stays quick in a journal of thousands.
                    data = json.loads(row['payload'])
                    linked = data.get('trade_id')
                    owners = ({linked} & ids) if linked else entries.get(data.get('entry_execution_id'), ())
                    if owners:
                        event = ProtectionEvent(**data)
                        for owner in owners:
                            result[owner].append(event)
        return result

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
        """A complete copy of the journal as one consistent snapshot, so an active watcher cannot leave it
        inconsistent. SQLite writes it straight to a private temporary file beside the journal (VACUUM INTO),
        which is read once and removed, rather than building a second database in memory."""
        with tempfile.TemporaryDirectory(dir=self.path.parent, prefix='.backup-') as folder:
            copy = Path(folder) / self.path.name
            with self.connect() as db:
                db.execute('VACUUM INTO ?', (str(copy),))
            return copy.read_bytes()
