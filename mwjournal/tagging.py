"""Stable local tag identities, safe editing, inherited day tags and playbook rules.

Legacy annotation strings remain on disk. Aliases resolve them after a rename; no fill or
historical annotation is destructively rewritten. New edits also retain stable tag IDs.
"""
from __future__ import annotations

import copy
import hashlib
import json
import re
from zoneinfo import ZoneInfo

from .model import instant
from .tags import DEFAULT_CATALOG


def identity(name):
    return hashlib.sha256(name.encode()).hexdigest()[:24]


def library(db):
    saved = db.execute("SELECT value FROM journal_meta WHERE key='tag_library'").fetchone()
    state = json.loads(saved[0]) if saved else {'version': 1, 'tags': {}, 'groups': {}, 'playbooks': {},
                                               'session_timezone': 'America/New_York'}
    legacy = db.execute("SELECT value FROM journal_meta WHERE key='tag_catalog'").fetchone()
    catalog = {**DEFAULT_CATALOG, **(json.loads(legacy[0]) if legacy else {})}
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


def save(db, state):
    db.execute("INSERT OR REPLACE INTO journal_meta VALUES('tag_library',?)",
               (json.dumps(state, sort_keys=True, allow_nan=False),))


def ids(state, values):
    aliases = {name: id for id, tag in state['tags'].items() for name in tag['aliases']}
    return {value if value in state['tags'] else aliases[value] for value in values
            if value in state['tags'] or value in aliases}


def ensure_names(state, values, group='Other tags'):
    for name in values:
        if ids(state, [name]):
            continue
        id = identity(name)
        state['tags'][id] = {'id': id, 'name': name, 'aliases': [name], 'group': group,
                             'retired': False, 'levels': ['trade'], 'color': '#65CA98',
                             'order': len(state['tags'])}
    state['groups'].setdefault(group, {'exclusive': False, 'order': len(state['groups'])})


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


def session_key(state, opened):
    return f"{instant(opened).astimezone(ZoneInfo(state['session_timezone'])).date()}:{state['session_timezone']}"


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
