"""Local journal workflows. No broker, network, screen capture or order-entry access.

Definitions live in journal_meta; runs retain a snapshot so an edited checklist cannot rewrite
past answers. Every partial mutation takes the SQLite write lock and preserves other workflows.
"""
from __future__ import annotations

import copy
import datetime as dt
import json
import re
import uuid

from . import tagging
from .model import finite, instant, timestamp

WIDGETS = ('Summary', 'Equity', 'Drawdown', 'Calendar', 'Compare', 'Distribution',
           'MAE / MFE', 'Tag score', 'Rating', 'Radar', 'Trades', 'Script')
METRICS = ('Net P&L', 'Gross P&L', 'R', 'Tag score', 'Rating')
GROUPS = ('Market', 'Exact contract', 'Direction', 'Entry hour', 'Weekday', 'Tags', 'Playbooks')


def identifier():
    return uuid.uuid4().hex


def label(value, limit=80):
    value = str(value or '').strip()
    if not value or len(value) > limit:
        raise ValueError(f'Use a name of 1–{limit} characters.')
    return value


def read(db):
    row = db.execute("SELECT value FROM journal_meta WHERE key='workspace'").fetchone()
    return {**{'version': 1, 'dashboards': {}, 'filters': {}, 'ratings': {}, 'checklists': {},
               'runs': {}, 'checkins': {}, 'drafts': {}, 'videos': {}, 'preferences': {}},
            **(json.loads(row[0]) if row else {})}


def write(db, state):
    db.execute("INSERT OR REPLACE INTO journal_meta VALUES('workspace',?)",
               (json.dumps(state, sort_keys=True, allow_nan=False),))


def validate_filter(value, library):
    """Explicit allowlist. Never evaluate a user's filter or script as code."""
    result = {}
    for field in ('accounts', 'symbols', 'markets', 'directions', 'status', 'tags_all', 'tags_any', 'tags_none'):
        values = value.get(field, [])
        if not isinstance(values, list) or len(values) > 500 or any(not isinstance(v, str) for v in values):
            raise ValueError('Filter choices must be a list of text values.')
        if field.startswith('tags_'):
            resolved = tagging.ids(library, values)
            if len(resolved) != len(set(values)):
                raise ValueError('A filter references an unknown tag.')
            values = sorted(resolved)
        result[field] = sorted(set(values))
    for field in ('start', 'end'):
        if value.get(field):
            result[field] = dt.date.fromisoformat(value[field]).isoformat()
    if result.get('start') and result.get('end') and result['start'] > result['end']:
        raise ValueError('Start must not follow end.')
    numeric = ('score', 'net', 'r', 'mae', 'mfe', 'quantity', 'duration')
    for field in (name + suffix for name in numeric for suffix in ('_min', '_max')):
        if value.get(field) is not None:
            result[field] = finite(value[field], field)
    for name in numeric:
        if result.get(name + '_min') is not None and result.get(name + '_max') is not None and result[name + '_min'] > result[name + '_max']:
            raise ValueError('A minimum must not exceed its maximum.')
    return result


def filter_sample(rows, rule):
    def matches(row):
        tags = set(row.get('tag_ids', []))
        # Rows from the app carry their entry day in the display time zone; others fall back to UTC.
        day = row.get('entry_day') or instant(row['opened']).date().isoformat()
        numbers = {'score': row.get('tag_score', 0), 'net': row.get('net'), 'r': row.get('r'),
                   'mae': row.get('mae'), 'mfe': row.get('mfe'), 'quantity': row.get('max_qty'),
                   'duration': (instant(row['closed']) - instant(row['opened'])).total_seconds() / 60 if row.get('closed') else None}
        numeric_match = all((rule.get(name + '_min') is None or val is not None and val >= rule[name + '_min'])
                            and (rule.get(name + '_max') is None or val is not None and val <= rule[name + '_max'])
                            for name, val in numbers.items())
        return (all(not rule.get(k) or row.get(f) in rule[k] for k, f in
                    [('accounts', 'account'), ('symbols', 'symbol'), ('markets', 'market'),
                     ('directions', 'direction'), ('status', 'status')])
                and set(rule.get('tags_all', [])) <= tags
                and (not rule.get('tags_any') or bool(set(rule['tags_any']) & tags))
                and not bool(set(rule.get('tags_none', [])) & tags)
                and (not rule.get('start') or day >= rule['start'])
                and (not rule.get('end') or day <= rule['end'])
                and numeric_match)
    return [r for r in rows if matches(r)]


def tag_score(library, chosen, included=None):
    chosen = set(chosen)
    if included is not None:
        chosen &= set(included)
    return sum(library['tags'][id].get('score', 0) for id in chosen if id in library['tags'])


def rating(library, chosen, definition):
    """Weighted satisfaction (0–100), not a P&L score or prediction."""
    eligible = [r for r in definition.get('rules', []) if not r.get('when') or
                tagging.matches(library, chosen, {'rules': r['when']})]
    total = sum(r['weight'] for r in eligible)
    return (100 * sum(r['weight'] for r in eligible if tagging.matches(library, chosen, r)) / total
            if total else None)


def validate_graph(graph, library):
    graph = copy.deepcopy(graph)
    nodes = graph.get('nodes', [])
    if not isinstance(nodes, list) or not 1 <= len(nodes) <= 200:
        raise ValueError('A checklist needs 1–200 nodes.')
    ids = {n.get('id') for n in nodes}
    if len(ids) != len(nodes) or any(not isinstance(id, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,80}', id) for id in ids):
        raise ValueError('Checklist node IDs must be unique simple identifiers.')
    if graph.get('start') not in ids:
        raise ValueError('Choose an existing starting node.')
    for node in nodes:
        node['text'] = label(node.get('text'), 400)
        if node.get('kind') not in ('question', 'answer', 'pass', 'counter'):
            raise ValueError('Unknown checklist node type.')
        links = node.get('next', [])
        if not isinstance(links, list) or len(set(links)) != len(links) or not set(links) <= ids:
            raise ValueError('Checklist links must point to existing nodes.')
        if node['kind'] != 'question' and len(links) > 1:
            raise ValueError('Only questions may branch to multiple answers.')
        if node['kind'] == 'question' and any(next(n for n in nodes if n['id'] == id)['kind'] != 'answer' for id in links):
            raise ValueError('Connect questions to answer nodes, then answers to the next question.')
        original_tags = node.get('tag_ids', [])
        node['tag_ids'] = sorted(tagging.ids(library, original_tags))
        if len(node['tag_ids']) != len(set(original_tags)):
            raise ValueError('Checklist references an unknown tag.')
        for axis in ('x', 'y'):
            node[axis] = max(0, min(10000, finite(node.get(axis, 0), 'Node position')))
        color = node.get('color', '#E2CF32')
        if not re.fullmatch(r'#[0-9a-fA-F]{6}', color):
            raise ValueError('Use a six-digit node color.')
        node['color'] = color
    # Automatic traversal must terminate or reach a manual question. Manual loops are allowed.
    index = {n['id']: n for n in nodes}
    for start in ids:
        seen, current = set(), start
        while current and index[current]['kind'] != 'question':
            if current in seen:
                raise ValueError('An automatic checklist loop never reaches a question. Break that loop.')
            seen.add(current)
            current = next(iter(index[current].get('next', [])), None)
    return {'start': graph['start'], 'nodes': nodes}


def replay_run(graph, answers):
    nodes = {n['id']: n for n in graph['nodes']}
    current, trail, counts, chosen = graph['start'], [], {}, set()
    def walk():
        nonlocal current
        for _ in range(201):
            if current is None or nodes[current]['kind'] == 'question':
                return
            node = nodes[current]
            trail.append(node['id'])
            chosen.update(node.get('tag_ids', []))
            if node['kind'] == 'counter':
                counts[node['id']] = counts.get(node['id'], 0) + 1
            current = next(iter(node.get('next', [])), None)
        raise ValueError('Checklist traversal exceeded its safety limit.')
    walk()
    for answer in answers:
        if current is None or answer not in nodes[current]['next']:
            raise ValueError('That answer is not connected to the current question.')
        trail.append(current)
        chosen.update(nodes[current].get('tag_ids', []))
        current = answer
        walk()
    return {'current': current, 'trail': trail, 'counters': counts, 'tag_ids': sorted(chosen), 'complete': current is None}


class Workspace:
    """Mixin keeping Journal's execution/price collector independent from review workflows."""
    def workspace(self):
        with self.connect() as db:
            return read(db)

    def _workspace_edit(self, operation):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            state = read(db)
            result = operation(state, tagging.library(db))
            write(db, state)
            return result

    def save_dashboard(self, name, widgets, *, id=None, filters=None, columns=2):
        name = label(name)
        if columns not in (1, 2, 3) or not isinstance(widgets, list) or not 1 <= len(widgets) <= 24:
            raise ValueError('Use 1–24 widgets and 1–3 columns.')
        def save(state, library):
            key = id or identifier()
            if id and id not in state['dashboards']:
                raise ValueError('Unknown dashboard.')
            result = []
            for widget in widgets:
                kind = widget.get('kind')
                if kind not in WIDGETS or widget.get('metric', 'Net P&L') not in METRICS:
                    raise ValueError('Unknown widget or metric.')
                group = widget.get('group', 'Market')
                if group not in GROUPS:
                    raise ValueError('Unknown widget comparison.')
                if not isinstance(widget.get('code', ''), str) or len(widget.get('code', '')) > 30000:
                    raise ValueError('Widget scripts must be text under 30,000 characters.')
                result.append({**widget, 'id': widget.get('id') or identifier(), 'kind': kind,
                               'title': label(widget.get('title') or kind), 'group': group,
                               'metric': widget.get('metric', 'Net P&L'),
                               'filters': validate_filter(widget.get('filters', {}), library)})
            if len({w['id'] for w in result}) != len(result):
                raise ValueError('Widget IDs must be unique.')
            state['dashboards'][key] = {'id': key, 'name': name, 'widgets': result, 'columns': columns,
                                        'filters': validate_filter(filters or {}, library),
                                        'order': state['dashboards'].get(key, {}).get('order', len(state['dashboards']))}
            return key
        return self._workspace_edit(save)

    def save_filter(self, name, filters):
        name = label(name)
        return self._workspace_edit(lambda s, lib: s['filters'].update({name: validate_filter(filters, lib)}))

    def save_rating(self, name, rules, *, id=None):
        name = label(name)
        if not rules or len(rules) > 100:
            raise ValueError('A rating needs 1–100 weighted criteria.')
        def save(state, library):
            normalized = []
            for rule in rules:
                def validate(combination):
                    valid = {}
                    for group, tags in combination.items():
                        choices = tagging.ids(library, tags)
                        if group not in library['groups'] or not choices or len(choices) != len(set(tags)) or any(
                                library['tags'][t]['group'] != group for t in choices):
                            raise ValueError('Rating rules need known tags in their groups.')
                        valid[group] = sorted(choices)
                    return valid
                combination = validate(rule.get('rules', {}))
                if not combination:
                    raise ValueError('Each rating criterion needs a tag rule.')
                normalized.append({'name': label(rule.get('name') or 'Criterion'),
                                   'weight': finite(rule.get('weight', 1), 'Weight', True),
                                   'rules': combination, 'when': validate(rule.get('when', {}))})
            key = id or identifier()
            if id and id not in state['ratings']:
                raise ValueError('Unknown rating definition.')
            state['ratings'][key] = {'id': key, 'name': name, 'rules': normalized}
            return key
        return self._workspace_edit(save)

    def save_checklist(self, name, graph, *, id=None, hidden=False):
        def save(state, library):
            key = id or identifier()
            if id and id not in state['checklists']:
                raise ValueError('Unknown checklist.')
            previous = state['checklists'].get(key, {})
            # Retain previous definitions as local backups, rather than deleting history.
            backups = previous.get('backups', [])
            if previous.get('graph') and previous['graph'] != graph:
                backups = [*backups, {'at': dt.datetime.now(dt.timezone.utc).isoformat(), 'graph': previous['graph']}]
            state['checklists'][key] = {'id': key, 'name': label(name), 'hidden': bool(hidden),
                                       'graph': validate_graph(graph, library), 'backups': backups,
                                       'order': previous.get('order', len(state['checklists']))}
            return key
        return self._workspace_edit(save)

    def start_checklist(self, id, trade_id=None):
        if trade_id and trade_id not in {t.id for t in self.trades()}:
            raise ValueError('Choose a trade from this journal.')
        def start(state, library):
            if id not in state['checklists']:
                raise ValueError('Unknown checklist.')
            graph = copy.deepcopy(state['checklists'][id]['graph'])
            key = identifier()
            state['runs'][key] = {'id': key, 'checklist_id': id, 'name': state['checklists'][id]['name'],
                                  'trade_id': trade_id, 'started': dt.datetime.now(dt.timezone.utc).isoformat(),
                                  'graph': graph, 'answers': [], 'answer_events': [], **replay_run(graph, [])}
            return key
        return self._workspace_edit(start)

    def answer_checklist(self, id, answer=None, *, undo=False):
        def respond(state, library):
            if id not in state['runs']:
                raise ValueError('Unknown checklist run.')
            run = state['runs'][id]
            if len(run['answers']) >= 1000 and not undo:
                raise ValueError('Start a new run after 1000 answers.')
            answers = run['answers'][:-1] if undo else [*run['answers'], answer]
            run.update(answers=answers, **replay_run(run['graph'], answers))
            run.setdefault('answer_events', []).append({'action': 'undo' if undo else 'answer', 'answer': answer,
                                                         'at': dt.datetime.now(dt.timezone.utc).isoformat()})
            return copy.deepcopy(run)
        return self._workspace_edit(respond)

    def record_checkin(self, group, choices, *, trade_id=None, at=None):
        if trade_id and trade_id not in {t.id for t in self.trades()}:
            raise ValueError('Choose a trade from this journal.')
        def save(state, library):
            ids = tagging.ids(library, choices)
            if not ids or len(ids) != len(set(choices)) or any(library['tags'][id]['group'] != group for id in ids):
                raise ValueError('Choose tags in this check-in group.')
            if library['groups'][group]['exclusive'] and len(ids) > 1:
                raise ValueError('Choose one tag in this exclusive group.')
            key = identifier()
            state['checkins'][key] = {'id': key, 'group': group, 'tag_ids': sorted(ids), 'trade_id': trade_id,
                                      'at': timestamp(at or dt.datetime.now(dt.timezone.utc).isoformat())}
            return key
        return self._workspace_edit(save)

    def save_preferences(self, **changes):
        allowed = {'timer_minutes', 'timer_groups', 'display_timezone', 'hide_weekends'}
        if not set(changes) <= allowed:
            raise ValueError('Unknown workspace preference.')
        if 'timer_minutes' in changes and not 1 <= finite(changes['timer_minutes'], 'Timer minutes') <= 120:
            raise ValueError('Timer must be 1–120 minutes.')
        if 'display_timezone' in changes:
            from zoneinfo import ZoneInfo
            try:
                ZoneInfo(changes['display_timezone'])
            except (ValueError, KeyError, TypeError) as exc:
                raise ValueError('Choose a valid display timezone.') from exc
        def save(state, library):
            if 'timer_groups' in changes and not set(changes['timer_groups']) <= set(library['groups']):
                raise ValueError('Choose known timer groups.')
            state['preferences'].update(changes)
        return self._workspace_edit(save)

    def save_draft(self, name, *, provider, account, symbol, start, end, tags=(), notes='', risk=None):
        start, end = timestamp(start), timestamp(end)
        if instant(end) <= instant(start) or (instant(end) - instant(start)).total_seconds() > 86400:
            raise ValueError('A draft window must be positive and at most 24 hours, on the platform clock.')
        existing = [t.id for t in self.trades()]
        def save(state, library):
            choices = tagging.ids(library, tags)
            if len(choices) != len(set(tags)) or any(library['tags'][id]['retired'] or 'trade' not in library['tags'][id]['levels'] for id in choices):
                raise ValueError('Choose active trade-level tags.')
            for group, settings in library['groups'].items():
                if settings['exclusive'] and sum(library['tags'][id]['group'] == group for id in choices) > 1:
                    raise ValueError('A draft has conflicting exclusive tags.')
            key = identifier()
            state['drafts'][key] = {'id': key, 'name': label(name), 'provider': label(provider), 'account': label(account),
                                    'symbol': label(symbol).upper(), 'start': start, 'end': end, 'tag_ids': sorted(choices),
                                    'notes': str(notes)[:20000], 'risk': finite(risk, 'Initial risk', True) if risk else None,
                                    'state': 'armed', 'trade_id': None, 'existing_trades': existing}
            return key
        return self._workspace_edit(save)

    def reconcile_drafts(self):
        """Only explicit account/provider/exact-contract/time windows; ambiguity attaches nothing.

        A consumed draft stays consumed after restart. No placeholder fill, P&L or price is invented.
        Called after bridge import, not by a read-only render. Replay uses the user's market clock.
        """
        if not any(d['state'] in ('armed', 'ambiguous') for d in self.workspace()['drafts'].values()):
            return
        trades = self.trades()
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            state, library = read(db), tagging.library(db)
            armed = [d for d in state['drafts'].values() if d['state'] in ('armed', 'ambiguous')]
            candidates = {d['id']: [t for t in trades if (t.provider, t.account, t.symbol) ==
                                   (d['provider'], d['account'], d['symbol']) and d['start'] <= t.opened < d['end']
                                   and t.id not in d.get('existing_trades', [])]
                          for d in armed}
            for draft in armed:
                possible = candidates[draft['id']]
                overlap = any(t.id in {p.id for p in candidates[other['id']]} for t in possible
                              for other in armed if other['id'] != draft['id'])
                if len(possible) != 1 or overlap:
                    draft['state'] = 'ambiguous' if possible else 'armed'
                    continue
                trade = possible[0]
                previous = db.execute('SELECT payload FROM annotations WHERE trade_id=?', (trade.id,)).fetchone()
                review = tagging.project(library, json.loads(previous[0]) if previous else {})
                session = db.execute('SELECT payload FROM sessions WHERE key=?', (tagging.session_key(library, trade.opened),)).fetchone()
                session = json.loads(session[0]) if session else {}
                # Existing manually chosen groups take precedence over a pre-trade draft.
                existing = tagging.selected(library, review)
                if any(id not in library['tags'] or library['tags'][id]['retired'] or 'trade' not in library['tags'][id]['levels']
                       for id in draft['tag_ids']):
                    draft['state'] = 'needs review'
                    continue
                if any(settings['exclusive'] and sum(library['tags'][id]['group'] == group for id in draft['tag_ids']) > 1
                       for group, settings in library['groups'].items()):
                    draft['state'] = 'needs review'
                    continue
                for group in library['groups']:
                    choice = [id for id in draft['tag_ids'] if library['tags'][id]['group'] == group]
                    if choice and not any(library['tags'][id]['group'] == group for id in existing):
                        review = tagging.set_group(library, review, group, choice, session)
                if not review.get('notes') and draft['notes']:
                    review['notes'] = draft['notes']
                if not review.get('initial_risk') and draft['risk']:
                    review['initial_risk'] = draft['risk']
                review['draft_id'] = draft['id']
                db.execute('INSERT OR REPLACE INTO annotations VALUES(?,?)', (trade.id, json.dumps(review, allow_nan=False)))
                draft.update(state='attached', trade_id=trade.id)
            write(db, state)

    def cancel_draft(self, id):
        def cancel(state, library):
            if id not in state['drafts'] or state['drafts'][id]['state'] == 'attached':
                raise ValueError('Choose an unconsumed draft. Attached history is preserved.')
            state['drafts'][id]['state'] = 'cancelled'
        return self._workspace_edit(cancel)
