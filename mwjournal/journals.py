"""Journal views: each a name and the accounts it shows, over the one journal of its environment.

A view shows its accounts' trades with the reviews, tags and settings of All accounts: choosing one in the sidebar
filters the trades before any saved filter, and making, renaming or deleting one changes nothing stored about a
trade. Each environment keeps its views, and the names its accounts are shown by, in its own journal (journal_meta
'journal_registry'), as Live and Replay hold different accounts.

Until 0.4.17 a named journal was a database of its own, journals/<id>/trades.sqlite3 beside its environment's, that
copied the trades of chosen accounts and kept its own reviews and settings. The first time this version opens an
environment's journal, each one is folded into it and becomes a view (fold).
"""
from __future__ import annotations

import json
import re
import shutil
import sqlite3
import uuid
from pathlib import Path

from . import merging, tagging
from .model import Execution
from .store import Journal, _add_note, encode, merge_settings, one_waiting
from .workspace import OWN, label, read as read_workspace

KEY = 'journal_registry'
ALL = 'All accounts'             # the Journal menu's first choice: every trade, no view
FLAG = 'journal_views'           # in an environment's journal: its named journals of 0.4.17 or earlier were folded
PENDING = 'journal_views_pending'    # while a fold is under way: its backup and what is done so far


def _stored(db) -> dict:
    """The registry stored in journal `db`: {'views': {id: {'id', 'name', 'accounts': [[provider, account], ...]}},
    'account_aliases': {'["provider", "account"]': name}}. Until it is folded, a registry of 0.4.17 or earlier also
    lists its named journals under 'journals'."""
    row = db.execute('SELECT value FROM journal_meta WHERE key=?', (KEY,)).fetchone()
    return {'views': {}, 'account_aliases': {}, **(json.loads(row[0]) if row else {})}


def registry(journal) -> dict:
    """The registry (_stored), its views in the order of their names, as the Journal menu lists them: stored by id,
    they would come in no order a trader could follow."""
    with journal.connect() as db:
        state = _stored(db)
    state['views'] = dict(sorted(state['views'].items(), key=lambda item: (item[1]['name'].casefold(), item[0])))
    return state


def edit(journal, operation):
    """Runs `operation(registry)` inside the journal's write lock and saves what it changes."""
    with journal.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        state = _stored(db)
        result = operation(state)
        db.execute('INSERT OR REPLACE INTO journal_meta VALUES(?,?)', (KEY, encode(state)))
        return result


def _free(state, name, id=None) -> None:
    if name.casefold() == ALL.casefold() or any(
            key != id and view['name'].casefold() == name.casefold() for key, view in state['views'].items()):
        raise ValueError('The Journal menu already has that name.')


def create(journal, name, accounts) -> str:
    """Makes a view of `accounts`, [provider, account] pairs (at least one), named `name`, and returns its id."""
    name = label(name)
    accounts = sorted({tuple(account) for account in accounts})
    if not accounts or len(accounts) > 100 or any(
            len(account) != 2 or any(not isinstance(v, str) or not v.strip() for v in account) for account in accounts):
        raise ValueError('Choose at least one account.')
    id = uuid.uuid4().hex

    def add(state):
        _free(state, name)
        state['views'][id] = {'id': id, 'name': name, 'accounts': [list(account) for account in accounts]}
    edit(journal, add)
    return id


def rename(journal, id, name) -> None:
    name = label(name)

    def apply(state):
        if id not in state['views']:
            raise ValueError('Unknown view.')
        _free(state, name, id)
        state['views'][id]['name'] = name
    edit(journal, apply)


def delete(journal, id) -> None:
    """Removes a view and nothing else: its trades and their reviews stay in All accounts."""
    def apply(state):
        if id not in state['views']:
            raise ValueError('Unknown view.')
        del state['views'][id]
    edit(journal, apply)


def account_alias(journal, provider, account, name):
    key = encode([provider, account])
    name = label(name)
    edit(journal, lambda state: state['account_aliases'].update({key: name}))


def alias(journal, scope):
    return registry(journal)['account_aliases'].get(encode(list(scope)), scope[1])


# ---------------------------------------------------------------- named journals of 0.4.17 and earlier become views

def named(db) -> bool:
    """Whether the registry in journal `db` still lists named journals to fold."""
    return bool(_stored(db).get('journals'))


def _pending(db) -> dict:
    row = db.execute('SELECT value FROM journal_meta WHERE key=?', (PENDING,)).fetchone()
    return json.loads(row[0]) if row else {}


def _progress(db, *, fills=0, folded=None) -> None:
    """Adds to the record of a fold under way, inside the caller's transaction: fills added, a named journal done."""
    pending = _pending(db)
    pending['fills'] = pending.get('fills', 0) + fills
    pending['folded'] = [*pending.get('folded', []), *([folded] if folded else [])]
    db.execute('INSERT OR REPLACE INTO journal_meta VALUES(?,?)', (PENDING, encode(pending)))


def _made(path: Path) -> float:
    """When a named journal's database was made, as the file system keeps it: their order is the order they were
    made in."""
    found = path.stat()
    return getattr(found, 'st_birthtime', found.st_mtime)


def fold(journal) -> None:
    """Once per environment: each named journal of 0.4.17 or earlier becomes a view of its accounts over `journal`,
    the environment's journal, which holds its trades, reviews and settings from then on. Journal calls it after its
    own steps, outside its write lock, while the registry still lists named journals; the record that it ran
    (journal_meta 'journal_views') is set with the views, and the upgrade note says it once.

    The data folder is backed up first, named journals included (backup.write, to <home>/backups/
    before-journal-views-<UTC time>.zip), and the zip is read back (backup.read): one that does not read back is
    removed, nothing changes, and the open fails saying why. Then each named journal, archived ones too, in the order
    they were made, is folded in (_fold_one), and only then are their folders removed. Two files cannot commit as one,
    so every step is harmless to run again: the backup and what is done (journal_meta 'journal_views_pending') are
    recorded as it goes, and a fold cut short, or failing, carries on at the next open from the same backup instead of
    writing another."""
    from . import backup
    folder = journal.path.parent / 'journals'
    with journal.connect() as db:
        if db.execute('SELECT 1 FROM journal_meta WHERE key=?', (FLAG,)).fetchone():
            return
        old = _stored(db).get('journals', {})
        pending = _pending(db)
    found = [(id, folder / id / 'trades.sqlite3') for id in old if re.fullmatch(r'[0-9a-f]{32}', id)]
    found = sorted(((id, path) for id, path in found if path.is_file()), key=lambda item: (_made(item[1]), item[0]))
    if found and not (pending.get('backup') and Path(pending['backup']).is_file()):
        home, made = journal.shared.path.parent, None
        try:
            made = backup.write(backup.before(home, 'journal-views'), home)
            backup.read(made)
        except (ValueError, OSError, sqlite3.Error) as exc:
            if made is not None:
                made.unlink(missing_ok=True)
            raise ValueError('Your named journals could not be backed up before they become views, so nothing '
                             f'changed. {exc}') from None
        with journal.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            pending = _pending(db)
            if db.execute('SELECT 1 FROM journal_meta WHERE key=?', (FLAG,)).fetchone():
                made.unlink()
                return                             # another window folded them while this one backed up
            if pending.get('backup') not in (None, str(made)) and Path(pending['backup']).is_file():
                made.unlink()                      # another window backed up first
            else:
                # A record whose backup was deleted keeps what it says is done: that is in the journal already.
                pending = {'fills': 0, 'folded': [], **pending, 'backup': str(made)}
                db.execute('INSERT OR REPLACE INTO journal_meta VALUES(?,?)', (PENDING, encode(pending)))
    accounts = {}
    for id, path in found:
        if not path.is_file():
            continue                               # another window folded it and removed its folder meanwhile
        try:
            each = Journal(path, dataset=journal.dataset)
            accounts[id] = {(fill.provider, fill.account) for fill in each.executions()}
            if id not in pending.get('folded', []):
                _fold_one(journal, each, id, old[id]['name'])
        except (ValueError, OSError, sqlite3.Error) as exc:
            raise ValueError(f"The named journal {old[id]['name']} could not become a view ({exc}). Its folder "
                             f'{path.parent} is kept: move it out of the data folder to open the journal without '
                             'it.') from None
    with journal.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        if db.execute('SELECT 1 FROM journal_meta WHERE key=?', (FLAG,)).fetchone():
            return                                 # another window finished it
        state, pending = _stored(db), _pending(db)
        views, listed = state['views'], False
        for id, definition in state.pop('journals', {}).items():
            chosen = sorted({*(tuple(scope) for scope in definition.get('scopes', [])), *accounts.get(id, ())})
            if chosen and not definition.get('archived'):
                name = definition['name']
                if name.casefold() == ALL.casefold():
                    name += ' (view)'              # a name 0.4.17 allowed, which the Journal menu's first choice has
                views[id] = {'id': id, 'name': name, 'accounts': [list(each) for each in chosen]}
                listed = True
        db.execute('INSERT OR REPLACE INTO journal_meta VALUES(?,?)', (KEY, encode(state)))
        db.execute('DELETE FROM journal_meta WHERE key=?', (PENDING,))
        db.execute('INSERT INTO journal_meta VALUES(?,?)', (FLAG, str(len(found))))
        fills = pending.get('fills', 0)
        # Archived journals, and those without an account, are no view: then there is no Journal menu to point to.
        note = (('Named journals are now views, chosen under Journal in the sidebar, that show their accounts with '
                 'the same reviews, tags and settings as All accounts' if listed else
                 "Your named journals' reviews, tags and settings joined All accounts")
                + (f'; {fills} fill{"" if fills == 1 else "s"} entered only in a named journal now count under All '
                   'accounts too' if fills else '') + '.')
        _add_note(db, FLAG, {'backup': pending.get('backup'), 'fills': fills, 'note': note})
    for _, path in found:
        shutil.rmtree(path.parent, ignore_errors=True)
    try:
        folder.rmdir()
    except OSError:
        pass                                       # not there, or holds something else


def _fold_one(journal, other, id, name) -> None:
    """Folds named journal `other` (a Journal of its own database) into `journal`, `other` only read:

    a. the fills only `other` holds (added by hand or imported there) are added, so its view still shows them and
       All accounts counts them too; reviews follow fills that regrouped trades (Journal.reattach_reviews), and the
       trades they are in are measured from the prices `journal` holds;
    b. its multipliers, for contracts `journal` has none for;
    c. its own settings join the shared ones (store.merge_settings): its tags, categories and playbooks, dashboards,
       checklists and saved filters, a filter whose name All accounts uses for another being saved as
       '<name> (<journal name>)'; every tag and playbook id of its rows becomes the one it went into (tagging.refold);
    d. a trade's review is copied where All accounts has none, and joined where it has one (_joined);
    e. so is a day's review and its day tags;
    f. a merged trade of its, when no merged trade of All accounts holds any of its trades;
    g. its checklist runs, check-ins and Tag My Next Trade, which still waits for a trade neither journal held, one
       on each account and contract (store.one_waiting).

    Each step is harmless to run again. The record that `other` is done is set with the last."""
    with journal.connect() as db:
        held = {key for (key,) in db.execute('SELECT key FROM executions')}
    with other.connect() as db:
        own = [(Execution(**json.loads(payload)), json.loads(raw)) for key, payload, raw in
               db.execute('SELECT key, payload, raw FROM executions ORDER BY rowid') if key not in held]
        multipliers = [tuple(row) for row in db.execute('SELECT symbol, point_value FROM instruments')]
    with journal.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        added = journal.ingest(own, db=db) if own else 0
        db.executemany('INSERT OR IGNORE INTO instruments VALUES(?,?)', multipliers)
        _progress(db, fills=added)
    trades = journal.trades()
    journal.reattach_reviews(trades)
    keys = {fill.key for fill, _ in own}
    for trade in trades:
        if trade.complete and any(fill['key'] in keys for fill in trade.fills):
            for source in journal.price_sources(trade.symbol, trade):
                journal.measure(trade, source)
    with journal.connect() as db, other.connect() as theirs:
        db.execute('BEGIN IMMEDIATE')
        with journal._settings(db, write=True) as shared:
            folds, _ = merge_settings(shared, theirs, name)
            library = journal._library(shared)
        for table, key, field in (('annotations', 'trade_id', 'tag_ids'), ('sessions', 'key', 'day_tag_ids')):
            for held_as, payload in theirs.execute(f'SELECT {key}, payload FROM {table} ORDER BY rowid').fetchall():
                given = tagging.refold(json.loads(payload), folds)
                row = db.execute(f'SELECT payload FROM {table} WHERE {key}=?', (held_as,)).fetchone()
                kept = json.loads(row[0]) if row else None
                data = given if kept is None else _joined(kept, given, library, field)
                if data != kept:
                    db.execute(f'INSERT OR REPLACE INTO {table} VALUES(?,?)', (held_as, encode(data)))
        ours = _merges(db)
        before = dict(ours)
        taken = {leg for merge in ours.values() if merge['active'] for leg in merge['leg_ids']}
        for merge_id, merge in _merges(theirs).items():
            if merge['active'] and merge_id not in ours and not taken & set(merge['leg_ids']):
                ours[merge_id] = merge
                taken.update(merge['leg_ids'])
        if ours != before:
            db.execute('INSERT OR REPLACE INTO journal_meta VALUES(?,?)', (merging.KEY, json.dumps(ours, allow_nan=False)))
        state, given = read_workspace(db), read_workspace(theirs)
        before = encode(state)
        for part in OWN:
            for item_id, item in given[part].items():
                state[part].setdefault(item_id, tagging.refold(item, folds))
        # Tag My Next Trade waits for a trade the journal did not hold when it was armed. Every trade it holds now was
        # traded before, the ones only `other` held included, so none is the next one; and a contract keeps one.
        for draft in state['drafts'].values():
            if draft['state'] == 'armed':
                draft['existing_trades'] = sorted({*draft.get('existing_trades', []), *(trade.id for trade in trades)})
        one_waiting(state['drafts'])
        if encode(state) != before:
            journal._write_own(db, state)
        _progress(db, folded=id)


def _joined(kept: dict, given: dict, library: dict, field: str) -> dict:
    """The review of a trade or a day in All accounts, `kept`, with a named journal's of the same one, `given`, joined
    in. Kept's Rating, Stop and every other answer stay; one kept left empty is taken from given. The tags of both are
    kept (`field`: tag_ids on a trade, day_tag_ids on a day), except given's in a one-choice category kept already
    chose in. Each paragraph of given's note that is not one of kept's (spaces and line breaks aside) is added after a
    blank line: clones of one journal hold copies of each other's notes, which are never said twice. A paragraph is
    matched whole, never inside another one: 'FOMO' is not already said by 'No FOMO today'. A note kept left empty is
    given's, as it was typed."""
    def empty(value):
        return value is None or value == [] or (isinstance(value, str) and not value.strip())
    joined = {**kept, **{name: value for name, value in given.items() if empty(kept.get(name))}}
    chosen = tagging.selected if field == 'tag_ids' else tagging.inherited
    mine = chosen(library, kept)
    taken = {library['tags'][id]['group'] for id in mine}
    tags = mine | {id for id in chosen(library, given) if not (library['tags'][id]['group'] in taken
                                                      and library['groups'][library['tags'][id]['group']]['exclusive'])}
    if tags or field in kept or field in given:
        joined[field] = sorted(tags)
        if field == 'tag_ids':
            joined['tags'] = tagging.names(library, tags)
    notes = str(kept.get('notes') or '')
    if notes.strip():
        said, added = {' '.join(part.split()) for part in re.split(r'\n\s*\n', notes)}, []
        for part in re.split(r'\n\s*\n', str(given.get('notes') or '')):
            if part.strip() and ' '.join(part.split()) not in said:
                said.add(' '.join(part.split()))
                added.append(part.strip())
        if added:
            joined['notes'] = '\n\n'.join([notes.rstrip(), *added])
    return joined


def _merges(db) -> dict:
    """The merged trades stored in journal `db` (merging.definitions)."""
    row = db.execute('SELECT value FROM journal_meta WHERE key=?', (merging.KEY,)).fetchone()
    return json.loads(row[0]) if row else {}
