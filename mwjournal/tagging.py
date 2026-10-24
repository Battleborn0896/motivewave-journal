"""Stable local tag identities, safe editing, inherited day tags and playbook rules.

Legacy annotation strings remain on disk. Aliases resolve them after a rename; no fill or
historical annotation is destructively rewritten. New edits also retain stable tag IDs.
"""
from __future__ import annotations

import copy
import hashlib
import json
import re
import sqlite3
from zoneinfo import ZoneInfo

from .model import instant
from .tags import DAY, LEGACY_CATALOG, STARTER_VERSION, STARTERS

COLOURS = {group: colour for group, colour, _ in STARTERS}     # the colour of each category the app offers


def identity(name):
    return hashlib.sha256(name.encode()).hexdigest()[:24]


def starter_library():
    """Setup, Execution and Mindset as ordinary trade tags and Day as day tags, marked as offered so they are
    offered once."""
    state = {'version': 1, 'tags': {}, 'groups': {}, 'playbooks': {}, 'starters': STARTER_VERSION}
    for order, (group, colour, names) in enumerate(STARTERS):
        state['groups'][group] = {'exclusive': False, 'order': order}
        for name in names:
            id = identity(name)
            state['tags'][id] = {'id': id, 'name': name, 'aliases': [name], 'group': group, 'retired': False,
                                 'levels': ['day'] if group == DAY[0] else ['trade'], 'color': colour,
                                 'order': len(state['tags'])}
    return state


def upgrade_library(db, *, needed: bool) -> tuple[dict, dict]:
    """The schema 4 upgrade of a journal's tag library, inside the caller's transaction (store._schema_4): the library
    loses the time zone it kept for day tags, gains the Day category and its tags (tags.DAY), and is stored. A tag
    that is or once was named as a Day tag, in any case, is used as it is and made available on days. A journal that
    has never had a tag is left to be offered the starters, Day among them, at its first read (library), unless its
    library is `needed` now. Returns (the library, each Day tag's id by its name)."""
    if not needed and _never_tagged(db) and db.execute(
            "SELECT 1 FROM journal_meta WHERE key IN ('tag_library', 'tag_catalog')").fetchone() is None:
        return {'tags': {}, 'groups': {}}, {}
    state = library(db)
    state.pop('session_timezone', None)
    group, colour, names = DAY
    found = {}
    for name in names:
        id = next((id for id, tag in state['tags'].items()
                   if name.casefold() in {alias.casefold() for alias in tag['aliases']}), None)
        if id is None:
            id = identity(name)
            state['groups'].setdefault(group, {'exclusive': False, 'order': len(state['groups'])})
            state['tags'][id] = {'id': id, 'name': name, 'aliases': [name], 'group': group, 'retired': False,
                                 'levels': ['day'], 'color': colour, 'order': len(state['tags'])}
        elif 'day' not in state['tags'][id]['levels']:
            state['tags'][id]['levels'] = [*state['tags'][id]['levels'], 'day']
        found[name] = id
    save(db, state)
    return state, found


def _never_tagged(db):
    """No tag was ever chosen or referred to here: no tagged review or day, and none of the old
    built-in suggestions named in a rating, filter, checklist or draft."""
    for (payload,) in db.execute('SELECT payload FROM annotations'):
        if any(json.loads(payload).get(name) for name in ('tags', 'tag_ids', 'day_tag_exclusions')):
            return False
    for (payload,) in db.execute('SELECT payload FROM sessions'):
        if json.loads(payload).get('day_tag_ids'):
            return False
    legacy = [identity(name) for name in LEGACY_CATALOG]
    return not any(id in value for (value,) in db.execute("SELECT value FROM journal_meta WHERE key != 'tag_library'")
                   for id in legacy)


def _offer_starters(db):
    """Store the starter library for a journal that has never had one, once. Inside the write lock
    the question is asked again, so two windows opening the same new journal store one library."""
    began = not db.in_transaction
    try:
        if began:
            db.execute('BEGIN IMMEDIATE')
        if (db.execute("SELECT 1 FROM journal_meta WHERE key IN ('tag_library', 'tag_catalog')").fetchone() is None
                and _never_tagged(db)):
            db.execute("INSERT INTO journal_meta VALUES('tag_library',?)",
                       (json.dumps(starter_library(), sort_keys=True, allow_nan=False),))
        if began:
            db.commit()
    except sqlite3.OperationalError:
        if began and db.in_transaction:
            db.rollback()
        return json.dumps(starter_library())      # a read-only copy: show them without storing them
    row = db.execute("SELECT value FROM journal_meta WHERE key='tag_library'").fetchone()
    return row[0] if row else None


def library(db):
    saved = db.execute("SELECT value FROM journal_meta WHERE key='tag_library'").fetchone()
    legacy = db.execute("SELECT value FROM journal_meta WHERE key='tag_catalog'").fetchone()
    if saved is None and legacy is None and _never_tagged(db):
        # A brand-new journal opens on the starter categories. They are stored at once, with the
        # record that they were offered, so a journal whose tags were all deleted stays empty.
        stored = _offer_starters(db)
        saved = (stored,) if stored else None
    state = json.loads(saved[0]) if saved else {'version': 1, 'tags': {}, 'groups': {}, 'playbooks': {}}
    # A library from 0.4.0 or earlier always holds the hidden suggestions of that version, as it
    # did then, except any the trader has since deleted. A library that was offered the starters
    # holds only what is stored.
    removed = set(state.get('removed', []))
    catalog = {} if state.get('starters') else {n: g for n, g in LEGACY_CATALOG.items() if n not in removed}
    catalog.update(json.loads(legacy[0]) if legacy else {})
    for row in db.execute('SELECT payload FROM annotations'):
        for name in json.loads(row[0]).get('tags', []):
            catalog.setdefault(name, 'Other tags')
    aliases = {name for tag in state['tags'].values() for name in tag['aliases']}
    for name, group in catalog.items():
        if name in aliases:
            continue
        id = identity(name)
        state['tags'][id] = {'id': id, 'name': name, 'aliases': [name], 'group': group,
                             'retired': False, 'levels': ['trade'], 'color': '#65CA98',
                             'order': len(state['tags'])}
        aliases.add(name)
    for tag in state['tags'].values():
        group = tag['group']
        state['groups'].setdefault(group, {'exclusive': False, 'order': len(state['groups'])})
    return state


def _plural(count, word):
    return f'{count} {word}' + ('' if count == 1 else 's')


def _settings(db):
    """The stored text of every setting that can name a tag or a category: each journal_meta entry but the tag
    library. The workspace's rating systems of 0.4.16 and earlier stay stored but are read nowhere, so they are left
    out: a tag only they name can be deleted."""
    for key, value in db.execute("SELECT key, value FROM journal_meta WHERE key != 'tag_library'"):
        if key == 'workspace':
            value = json.dumps({name: part for name, part in json.loads(value).items() if name != 'ratings'})
        yield value


def tag_uses(db, state, id):
    """Why a tag cannot be deleted, as phrases ('is on 3 trades'); empty when nothing uses it. A
    used tag is retired instead, so no review, day, playbook, filter, checklist or draft is
    left pointing at nothing."""
    names = set(state['tags'][id]['aliases'])
    trades = days = 0
    for (payload,) in db.execute('SELECT payload FROM annotations'):
        data = json.loads(payload)
        trades += bool(id in data.get('tag_ids', []) or id in data.get('day_tag_exclusions', [])
                       or names & set(data.get('tags', [])))
    for (payload,) in db.execute('SELECT payload FROM sessions'):
        days += id in json.loads(payload).get('day_tag_ids', [])
    uses = ['is on ' + ' and '.join(_plural(n, word) for n, word in ((trades, 'trade'), (days, 'day')) if n)] \
        if trades or days else []
    if any(id in choices for book in state['playbooks'].values() for choices in book['rules'].values()):
        uses.append('is part of a playbook')
    quoted = [json.dumps(name) for name in names]
    if any(id in value or any(q in value for q in quoted) for value in _settings(db)):
        uses.append('is named in a filter, a checklist or Tag My Next Trade')
    return uses


def group_uses(db, state, group):
    """Why a category cannot be deleted, as phrases ('holds 4 tags'); empty when nothing uses it."""
    held = sum(tag['group'] == group for tag in state['tags'].values())
    uses = ['holds ' + _plural(held, 'tag')] if held else []
    if any(group in book['rules'] for book in state['playbooks'].values()):
        uses.append('is part of a playbook')
    quoted = json.dumps(group)
    if any(quoted in value for value in _settings(db)):
        uses.append('is named in a timer or checklist')
    return uses


def delete_tag(state, id):
    state = copy.deepcopy(state)
    tag = state['tags'].pop(id)
    if not state.get('starters'):
        # An old built-in suggestion would otherwise come back with the next read.
        state['removed'] = sorted(set(state.get('removed', [])) | {n for n in tag['aliases'] if n in LEGACY_CATALOG})
    return state


def delete_group(state, group):
    state = copy.deepcopy(state)
    del state['groups'][group]
    for index, name in enumerate(sorted(state['groups'], key=lambda g: state['groups'][g]['order'])):
        state['groups'][name]['order'] = index
    return state


def save(db, state):
    db.execute("INSERT OR REPLACE INTO journal_meta VALUES('tag_library',?)",
               (json.dumps(state, sort_keys=True, allow_nan=False),))


def ids(state, values):
    aliases = {name: id for id, tag in state['tags'].items() for name in tag['aliases']}
    return {value if value in state['tags'] else aliases[value] for value in values
            if value in state['tags'] or value in aliases}


def ensure_names(state, values, group='Other tags'):
    # A new tag takes its category's colour, and is chosen where its tags are (on trades or on days), when all of
    # them agree: Mindset stays Mindset-coloured, and a tag made in Day is a day tag.
    colours = {tag['color'] for tag in state['tags'].values() if tag['group'] == group}
    colour = colours.pop() if len(colours) == 1 else '#65CA98'
    levels = {tuple(tag['levels']) for tag in state['tags'].values() if tag['group'] == group}
    levels = list(levels.pop()) if len(levels) == 1 else ['trade']
    added = False
    for name in values:
        if ids(state, [name]):
            continue
        id = identity(name)
        state['tags'][id] = {'id': id, 'name': name, 'aliases': [name], 'group': group,
                             'retired': False, 'levels': list(levels), 'color': colour,
                             'order': len(state['tags'])}
        added = True
    if added:
        # Only a category that now holds a new tag. Saving a note, or clicking a tag that already
        # exists, must not leave an empty 'Other tags' card in the tag panel.
        state['groups'].setdefault(group, {'exclusive': False, 'order': len(state['groups'])})


def file_under(state, review, name, group):
    """`review` with the tag `name` added to its tags, as update_annotation adds one, for an upgrade that turns an
    old answer into a tag. The tag whose name or a former name is `name`, in any case, is used wherever it is, retired
    or not; else it is made in `group`, in the colour the app gives that category (tags.STARTERS) when it has no tags
    yet. Nothing is removed, even in a one-choice category: the tag panel shows the conflict, and a click resolves
    it. `state` gains any new tag; the caller saves it."""
    folded = name.casefold()
    id = next(iter(ids(state, [name])), None) or next(
        (id for id, tag in state['tags'].items() if folded in {alias.casefold() for alias in tag['aliases']}), None)
    if id is None:
        empty = not any(tag['group'] == group for tag in state['tags'].values())
        ensure_names(state, [name], group)
        id = next(iter(ids(state, [name])))
        if empty and group in COLOURS:
            state['tags'][id]['color'] = COLOURS[group]
    chosen = selected(state, review) | {id}
    return {**review, 'tags': names(state, chosen), 'tag_ids': sorted(chosen)}


def scored(state) -> bool:
    """Whether any tag has a score (Settings → Tags). Until one does, Tag score is not offered anywhere: not on a
    trade, in Filters, as a dashboard widget or measure, nor to a script."""
    return any(tag.get('score') for tag in state['tags'].values())


def selected(state, annotation):
    return ids(state, annotation.get('tag_ids', annotation.get('tags', [])))


def names(state, values):
    return sorted(state['tags'][id]['name'] for id in set(values) if id in state['tags'])


def project(state, annotation):
    chosen = selected(state, annotation)
    return {**annotation, 'tags': names(state, chosen), 'tag_ids': sorted(chosen)}


def ordered_groups(state, *, level='trade', include_retired=False):
    groups = {}
    for group in sorted(state['groups'], key=lambda g: state['groups'][g]['order']):
        tags = sorted((t for t in state['tags'].values() if t['group'] == group
                       and level in t['levels'] and (include_retired or not t['retired'])), key=lambda t: t['order'])
        if tags:
            groups[group] = tags
    return groups


def day_key(opened, zone) -> str:
    """The trading day of a trade opened at `opened`: its date in `zone`, the journal's Time Zone, where a day starts
    at midnight. 'YYYY-MM-DD', the key of that day's tags and review in the sessions table."""
    return instant(opened).astimezone(ZoneInfo(zone)).date().isoformat()


def inherited(state, session):
    return ids(state, session.get('day_tag_ids', []))


def effective(state, annotation, session):
    direct = selected(state, annotation)
    day = inherited(state, session) - set(annotation.get('day_tag_exclusions', []))
    # A trade-specific choice takes precedence in an exclusive group; historical conflicts
    # are not erased when someone enables exclusivity later.
    for id in direct:
        group = state['tags'][id]['group']
        if state['groups'][group]['exclusive']:
            day = {d for d in day if state['tags'][d]['group'] != group}
    return direct | day


def set_group(state, annotation, group, chosen, session):
    if group not in state['groups'] or len(ids(state, chosen)) != len(set(chosen)):
        raise ValueError('Choose known tags from a known group.')
    chosen = ids(state, chosen)
    if any(state['tags'][id]['group'] != group or state['tags'][id]['retired']
           or 'trade' not in state['tags'][id]['levels'] for id in chosen):
        raise ValueError('Choose active tags from this group.')
    if state['groups'][group]['exclusive'] and len(chosen) > 1:
        raise ValueError('This group permits one choice.')
    group_ids = {id for id, t in state['tags'].items() if t['group'] == group}
    direct = selected(state, annotation) - group_ids | chosen
    excluded = set(annotation.get('day_tag_exclusions', [])) - group_ids
    excluded |= inherited(state, session) & group_ids - chosen
    return {**annotation, 'tag_ids': sorted(direct), 'tags': names(state, direct),
            'day_tag_exclusions': sorted(excluded)}


def edit_tag(state, id, **changes):
    state = copy.deepcopy(state)
    if id not in state['tags']:
        raise ValueError('Unknown tag.')
    tag = state['tags'][id]
    name = str(changes.get('name', tag['name'])).strip()
    group = str(changes.get('group', tag['group'])).strip()
    if not name or len(name) > 64 or not group or len(group) > 40:
        raise ValueError('Tag names need 1–64 characters; group names need 1–40.')
    if any(name in other['aliases'] for key, other in state['tags'].items() if key != id):
        raise ValueError('That name or historical alias belongs to another tag.')
    color = changes.get('color', tag['color'])
    if not re.fullmatch(r'#[0-9a-fA-F]{6}', color):
        raise ValueError('Use a six-digit hex color.')
    levels = changes.get('levels', tag['levels'])
    if not isinstance(levels, list) or not set(levels) <= {'trade', 'day'}:
        raise ValueError('Unknown tag level.')
    tag.update(name=name, group=group, color=color, levels=levels,
               retired=bool(changes.get('retired', tag['retired'])))
    from .model import finite
    tag['score'] = finite(changes.get('score', tag.get('score', 0)), 'Tag score')
    if name not in tag['aliases']:
        tag['aliases'].append(name)
    state['groups'].setdefault(group, {'exclusive': False, 'order': len(state['groups'])})
    return state


def matches(state, chosen, playbook):
    """OR among alternatives inside a group; AND between participating groups."""
    chosen = set(chosen)
    rules = playbook.get('rules', {})
    return bool(rules) and all(bool(chosen & set(alternatives)) for alternatives in rules.values())


def matched_playbooks(state, chosen):
    return [p['name'] for p in state['playbooks'].values() if not p.get('retired') and matches(state, chosen, p)]
