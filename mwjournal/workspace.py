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
from zoneinfo import ZoneInfo

from . import humanize, tagging
from .model import finite, instant, product, timestamp
from .risk import stop_for_risk

WIDGETS = ('Summary', 'Equity', 'Drawdown', 'Calendar', 'Compare', 'Distribution',
           'MAE / MFE', 'Tag score', 'Rating', 'Radar', 'Trades', 'Script')
METRICS = ('Net P&L', 'Gross P&L', 'R', 'Tag score', 'Rating')
# The one list trades are compared by: Stats' Compare by, the Compare widget and the After Exit tab.
GROUPS = ('Tags', 'Playbooks', 'Market', 'Exact contract', 'Direction', 'Entry hour', 'Weekday')
WEEKDAYS = ('Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday')


def group_labels(row, group, zone) -> list[str]:
    """What a trade row counts under when trades are compared by `group` (GROUPS): the name of each of its tags or
    playbooks (none: an empty list), or one name: its market, exact contract or direction, or the hour ('09:00') or
    weekday ('Monday') it was entered in `zone`."""
    if group in ('Tags', 'Playbooks'):
        return list(row[group.lower()])
    if group in ('Entry hour', 'Weekday'):
        opened = instant(row['opened']).astimezone(ZoneInfo(zone))
        return [f'{opened.hour:02d}:00' if group == 'Entry hour' else WEEKDAYS[opened.weekday()]]
    return [product(row['symbol']) if group == 'Market' else row['symbol'] if group == 'Exact contract'
            else row['direction']]


def identifier():
    return uuid.uuid4().hex


def label(value, limit=80):
    value = str(value or '').strip()
    if not value or len(value) > limit:
        raise ValueError(f'Use a name of 1–{limit} characters.')
    return value


def read(db):
    row = db.execute("SELECT value FROM journal_meta WHERE key='workspace'").fetchone()
    return {**{'version': 1, 'dashboards': {}, 'filters': {}, 'checklists': {},
               'runs': {}, 'checkins': {}, 'drafts': {}, 'videos': {}, 'preferences': {}},
            **(json.loads(row[0]) if row else {})}


def write(db, state):
    db.execute("INSERT OR REPLACE INTO journal_meta VALUES('workspace',?)",
               (json.dumps(state, sort_keys=True, allow_nan=False),))


def time_zone(db) -> str:
    """The one time zone of this journal, read on `db` (inside a caller's transaction, its own view): every time is
    shown in it and every trading day runs from midnight to midnight there. The Time Zone saved on Settings; a
    journal saves this computer's when it is made or upgraded to schema 4, so its days never move with a computer
    taken elsewhere. This computer's zone only when none valid is saved."""
    chosen = read(db)['preferences'].get('display_timezone')
    return chosen if humanize.valid_zone(chosen) else humanize.local_zone()


# Saved-filter fields a trade passes by having any one of the values listed: its account, exact contract,
# market, direction, status (closed, open or incomplete) and Rating (1 to 5 stars).
CHOICES = ('accounts', 'symbols', 'markets', 'directions', 'status', 'ratings')
# Number limits, each as name_min and name_max: signed tag score, net P&L, R, MAE and MFE in the chosen MAE / MFE
# Units (a stored limit is read in the unit chosen now), peak contracts and minutes held.
NUMBERS = ('score', 'net', 'r', 'mae', 'mfe', 'quantity', 'duration')


def validate_filter(value, library):
    """Explicit allowlist. Never evaluate a user's filter or script as code. Every field is optional, so a
    filter saved before a field existed stays valid."""
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
    ratings = value.get('ratings', [])
    if not isinstance(ratings, list) or any(type(v) is not int or not 1 <= v <= 5 for v in ratings):
        raise ValueError('Ratings must be whole numbers from 1 to 5.')
    result['ratings'] = sorted(set(ratings))
    if value.get('playbook') is not None:
        if not isinstance(value['playbook'], str) or value['playbook'] not in library['playbooks']:
            raise ValueError('A filter references an unknown playbook.')
        result['playbook'] = value['playbook']
    for field in ('start', 'end'):
        if value.get(field):
            result[field] = dt.date.fromisoformat(value[field]).isoformat()
    if result.get('start') and result.get('end') and result['start'] > result['end']:
        raise ValueError('Start must not follow end.')
    for field in (name + suffix for name in NUMBERS for suffix in ('_min', '_max')):
        if value.get(field) is not None:
            result[field] = finite(value[field], field)
    for name in NUMBERS:
        if result.get(name + '_min') is not None and result.get(name + '_max') is not None and result[name + '_min'] > result[name + '_max']:
            raise ValueError('A minimum must not exceed its maximum.')
    return result


def filter_sample(rows, rule):
    def matches(row):
        tags = set(row.get('tag_ids', []))
        # Rows from the app carry their entry day in the display time zone; others fall back to UTC.
        day = row.get('entry_day') or instant(row['opened']).date().isoformat()
        # MAE and MFE as the app shows them, in the chosen MAE / MFE Units (common.rows_for).
        numbers = {'score': row.get('tag_score', 0), 'net': row.get('net'), 'r': row.get('r'),
                   'mae': row.get('display_mae'), 'mfe': row.get('display_mfe'), 'quantity': row.get('max_qty'),
                   'duration': (instant(row['closed']) - instant(row['opened'])).total_seconds() / 60 if row.get('closed') else None}
        numeric_match = all((rule.get(name + '_min') is None or val is not None and val >= rule[name + '_min'])
                            and (rule.get(name + '_max') is None or val is not None and val <= rule[name + '_max'])
                            for name, val in numbers.items())
        return (all(not rule.get(k) or row.get(f) in rule[k] for k, f in
                    [('accounts', 'account'), ('symbols', 'symbol'), ('markets', 'market'),
                     ('directions', 'direction'), ('status', 'status'), ('ratings', 'rating')])
                and set(rule.get('tags_all', [])) <= tags
                and (not rule.get('tags_any') or bool(set(rule['tags_any']) & tags))
                and not bool(set(rule.get('tags_none', [])) & tags)
                # Rows from the app list the playbooks their tags complete; a row without the list completes none.
                and (not rule.get('playbook') or rule['playbook'] in row.get('playbook_ids', []))
                and (not rule.get('start') or day >= rule['start'])
                and (not rule.get('end') or day <= rule['end'])
                and numeric_match)
    return [r for r in rows if matches(r)]


def combine_filters(first, second):
    """One rule that keeps exactly the trades both rules keep. Choices intersect, required and excluded tags
    add up, dates take the later start and the earlier end, number limits the tighter bound. Raises
    ValueError, in one line, where no single rule says exactly that: two different 'any of these tags'
    choices (each of two or more tags, neither met by a required tag), two different playbooks, or limits no
    trade can meet together (an empty choice means any)."""
    nothing = ValueError('No trade can pass all of these filters, so there is nothing to save.')
    result = {}
    for field in CHOICES:
        a, b = set(first.get(field) or ()), set(second.get(field) or ())
        if a and b and not a & b:
            raise nothing                      # an empty choice would mean any value, not none
        result[field] = sorted(a & b if a and b else a | b)
    required = {tag for rule in (first, second) for tag in rule.get('tags_all') or ()}
    choices = [set(rule.get('tags_any') or ()) for rule in (first, second)]
    # Any of one tag is that tag required, and a choice a required tag already meets asks nothing more.
    required |= {tag for choice in choices if len(choice) == 1 for tag in choice}
    choices = [choice for choice in choices if choice and not choice & required]
    if len(choices) == 2 and not (choices[0] <= choices[1] or choices[1] <= choices[0]):
        raise ValueError("Two different 'any of these tags' choices cannot be saved as one filter.")
    result['tags_any'] = sorted(min(choices, key=len)) if choices else []     # the smaller choice implies the larger
    result['tags_all'] = sorted(required)
    result['tags_none'] = sorted({tag for rule in (first, second) for tag in rule.get('tags_none') or ()})
    books = {rule['playbook'] for rule in (first, second) if rule.get('playbook')}
    if len(books) > 1:
        raise ValueError('Two different playbooks cannot be saved as one filter.')
    if books:
        result['playbook'] = books.pop()
    for low, high in [('start', 'end'), *[(name + '_min', name + '_max') for name in NUMBERS]]:
        lows = [rule[low] for rule in (first, second) if rule.get(low) is not None]
        highs = [rule[high] for rule in (first, second) if rule.get(high) is not None]
        if lows:
            result[low] = max(lows)
        if highs:
            result[high] = min(highs)
        if lows and highs and result[low] > result[high]:
            raise nothing
    return result


def tag_score(library, chosen, included=None):
    chosen = set(chosen)
    if included is not None:
        chosen &= set(included)
    return sum(library['tags'][id].get('score', 0) for id in chosen if id in library['tags'])


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


# Where a checklist runs: on every trade's page, or on the Daily Report Card, once a day.
RUNS_ON = {'trade': 'Each trade', 'session': 'Before the session'}
# What an answer does unless a branch says otherwise: go on to the next question, collect no tag, count nothing.
BRANCH = {'go': 'next', 'tag_ids': [], 'count': False}
ANSWERS = ('yes', 'no')
MAX_QUESTIONS = 40                     # five nodes each at most: validate_graph's 200


def question(text, yes=None, no=None) -> dict:
    """One question of a checklist's list: its text and what its Yes and its No do, each a BRANCH. A branch's 'go' is
    'next' (the next question; after the last one, Finish), 'finish', or the number of a question (1 is the first);
    'tag_ids' are the tags the answer collects; 'count' is Count this answer."""
    return {'text': text, 'yes': {**BRANCH, **(yes or {})}, 'no': {**BRANCH, **(no or {})}}


def compile_checklist(questions) -> dict:
    """The graph a list of yes/no questions (question) is stored and run as; checklist_questions is its inverse.
    Question n is node 'qn', linked to its answer nodes 'qn-yes' and 'qn-no', which carry the tags they collect and link to
    where they go (Finish: no link). Count this answer puts counter node 'qn-yes-count' between the answer and where
    it goes. Raises ValueError, in plain words, on an empty question or a branch to no question."""
    if not isinstance(questions, list) or not 1 <= len(questions) <= MAX_QUESTIONS:
        raise ValueError(f'A checklist has 1 to {MAX_QUESTIONS} questions.')
    nodes, last = [], len(questions)
    for number, asked in enumerate(questions, 1):
        text = str(asked.get('text') or '').strip()
        if not text or len(text) > 400:
            raise ValueError(f'Type question {number}, in at most 400 characters.')
        id = f'q{number}'
        nodes.append({'id': id, 'kind': 'question', 'text': text, 'next': [f'{id}-{answer}' for answer in ANSWERS]})
        for answer in ANSWERS:
            branch = {**BRANCH, **(asked.get(answer) or {})}
            go = branch['go']
            if go not in ('next', 'finish') and not (type(go) is int and 1 <= go <= last):
                raise ValueError(f'Question {number} branches to a question this checklist does not have.')
            if not isinstance(branch['tag_ids'], list) or not isinstance(branch['count'], bool):
                raise ValueError('A branch collects a list of tags and counts or not.')
            target = (None if go == 'finish' or go == 'next' and number == last
                      else f'q{number + 1}' if go == 'next' else f'q{go}')
            links = [target] if target else []
            node = {'id': f'{id}-{answer}', 'kind': 'answer', 'text': answer.title(), 'next': links,
                    'tag_ids': list(branch['tag_ids'])}
            nodes.append(node)
            if branch['count']:
                counter = f'{id}-{answer}-count'
                node['next'] = [counter]
                nodes.append({'id': counter, 'kind': 'counter', 'text': f'{text[:390]} · {answer.title()}',
                              'next': links})
    return {'start': 'q1', 'nodes': nodes}


def follow(nodes, id):
    """What the chain of nodes from node `id` up to the next question does (`nodes` by id): the tags it collects
    (sorted), whether it counts, and the question it reaches, None when it finishes."""
    tags, count, seen = set(), False, set()
    while id is not None and nodes[id]['kind'] != 'question' and id not in seen:
        seen.add(id)
        tags.update(nodes[id].get('tag_ids', []))
        count = count or nodes[id]['kind'] == 'counter'
        id = next(iter(nodes[id].get('next', [])), None)
    return sorted(tags), count, id if id is not None and nodes[id]['kind'] == 'question' else None


def checklist_questions(graph) -> list[dict]:
    """The list of yes/no questions (question) a stored checklist graph is: compile_checklist's inverse. The questions
    come in the graph's order, its start question first. A question's first answer is its Yes and its second its No;
    a missing one goes on to the next question. The nodes after an answer up to the next question (pass and counter
    nodes in a graph saved by 0.4.17 or earlier) fold into it: their tags join its tags, a counter is Count this
    answer, and the question they lead to is where it goes."""
    nodes = {node['id']: node for node in graph['nodes']}
    start = follow(nodes, graph['start'])[2]
    order = [id for id in [start, *nodes] if id is not None and nodes[id]['kind'] == 'question']
    order = list(dict.fromkeys(order))
    result = []
    for place, id in enumerate(order):
        following = order[place + 1] if place + 1 < len(order) else None
        branches = {}
        for answer, linked in zip(ANSWERS, [*nodes[id].get('next', [])[:2], None, None]):
            if linked is None:
                branches[answer] = dict(BRANCH)
                continue
            tags, count, reached = follow(nodes, linked)
            go = 'next' if reached == following else 'finish' if reached is None else order.index(reached) + 1
            branches[answer] = {'go': go, 'tag_ids': tags, 'count': count}
        result.append(question(nodes[id]['text'], **branches))
    return result


def store_checklist(state, library, key, name, questions, runs_on) -> None:
    """Saves checklist `key` in the workspace `state` as the graph of `questions` (compile_checklist), inside the
    caller's transaction. A changed graph keeps the one it replaces in the checklist's backups; runs keep their own."""
    if runs_on not in RUNS_ON:
        raise ValueError('A checklist runs on each trade or before the session.')
    graph = validate_graph(compile_checklist(questions), library)
    previous = state['checklists'].get(key, {})
    backups = previous.get('backups', [])
    if previous.get('graph') and previous['graph'] != graph:
        backups = [*backups, {'at': dt.datetime.now(dt.timezone.utc).isoformat(), 'graph': previous['graph']}]
    state['checklists'][key] = {'id': key, 'name': label(name), 'runs_on': runs_on, 'graph': graph,
                               'backups': backups, 'order': previous.get('order', len(state['checklists']))}


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

    def time_zone(self) -> str:
        """The journal's Time Zone: what every time is shown in and where every trading day starts (time_zone)."""
        with self.connect() as db:
            return time_zone(db)

    def _workspace_edit(self, operation):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            state = read(db)
            result = operation(state, tagging.library(db))
            write(db, state)
            return result

    def save_dashboard(self, name, widgets, *, id=None, filters=None, columns=2):
        name = label(name)
        if columns not in (1, 2, 3) or not isinstance(widgets, list) or len(widgets) > 24:
            raise ValueError('Use up to 24 widgets and 1–3 columns.')
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
        """Saves the rule under `name`, replacing a filter of that name, and returns the name as saved."""
        name = label(name)
        self._workspace_edit(lambda s, lib: s['filters'].update({name: validate_filter(filters, lib)}))
        return name

    def delete_filter(self, name):
        """Removes the saved filter `name`."""
        name = label(name)
        def delete(state, library):
            if name not in state['filters']:
                raise ValueError(f'There is no saved filter named {name}.')
            del state['filters'][name]
        self._workspace_edit(delete)

    def rename_filter(self, old, new):
        """Gives the saved filter `old` the name `new`, a name no other saved filter has, and returns it as saved."""
        old, new = label(old), label(new)
        def rename(state, library):
            if old not in state['filters']:
                raise ValueError(f'There is no saved filter named {old}.')
            if new != old and new in state['filters']:
                raise ValueError(f'A saved filter is already named {new}.')
            state['filters'][new] = state['filters'].pop(old)
        self._workspace_edit(rename)
        return new

    def save_checklist(self, name, questions, *, runs_on='trade', id=None):
        """Saves a checklist: its name, its list of yes/no questions (question) and where it runs (RUNS_ON), as a new
        one or over checklist `id`, and returns its id."""
        def save(state, library):
            key = id or identifier()
            if id and id not in state['checklists']:
                raise ValueError('Unknown checklist.')
            store_checklist(state, library, key, name, questions, runs_on)
            return key
        return self._workspace_edit(save)

    def delete_checklist(self, id):
        """Removes checklist `id`. Its runs stay stored, each with its own copy of the checklist it answered."""
        def delete(state, library):
            if id not in state['checklists']:
                raise ValueError('Unknown checklist.')
            del state['checklists'][id]
        self._workspace_edit(delete)

    def start_checklist(self, id, trade_id=None, *, day=None):
        """Starts a run of checklist `id` on one trade, or on one day ('YYYY-MM-DD', a date in the Time Zone), and
        returns its id. The run keeps its own copy of the checklist."""
        if bool(trade_id) == (day is not None):
            raise ValueError('Run a checklist on one trade or on one day.')
        if trade_id and trade_id not in {t.id for t in self.trades()}:
            raise ValueError('Choose a trade from this journal.')
        target = {'trade_id': trade_id} if trade_id else {'day': dt.date.fromisoformat(str(day)).isoformat()}
        def start(state, library):
            if id not in state['checklists']:
                raise ValueError('Unknown checklist.')
            graph = copy.deepcopy(state['checklists'][id]['graph'])
            key = identifier()
            state['runs'][key] = {'id': key, 'checklist_id': id, 'name': state['checklists'][id]['name'], **target,
                                  'started': dt.datetime.now(dt.timezone.utc).isoformat(),
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
        allowed = {'timer_minutes', 'timer_groups', 'display_timezone'}
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

    def save_draft(self, *, provider, account, symbol, tags=(), notes='', stop=None):
        """Tag My Next Trade: tags, a note and the stop price R will use (risk.py) for the next trade on one account
        and exact contract, whenever it comes (reconcile_drafts). It waits for a trade the journal does not hold yet,
        and replaces one still waiting on that account and contract. Drafts saved before 0.4.7 carry a dollar 'risk'
        instead of a stop; drafts saved by 0.4.17 and earlier also keep a name and a time window, no longer read."""
        existing = [t.id for t in self.trades()]
        def save(state, library):
            choices = tagging.ids(library, tags)
            if len(choices) != len(set(tags)) or any(library['tags'][id]['retired'] or 'trade' not in library['tags'][id]['levels'] for id in choices):
                raise ValueError('Choose active trade-level tags.')
            for group, settings in library['groups'].items():
                if settings['exclusive'] and sum(library['tags'][id]['group'] == group for id in choices) > 1:
                    raise ValueError(f'Choose one tag in {group}.')
            if not choices and not str(notes).strip() and stop is None:
                raise ValueError('Choose a tag, or type a Stop or a note.')
            scope = (label(provider), label(account), label(symbol).upper())
            for draft in state['drafts'].values():
                if draft['state'] == 'armed' and (draft['provider'], draft['account'], draft['symbol']) == scope:
                    draft['state'] = 'cancelled'
            key = identifier()
            state['drafts'][key] = {'id': key, 'provider': scope[0], 'account': scope[1], 'symbol': scope[2],
                                    'tag_ids': sorted(choices), 'notes': str(notes)[:20000],
                                    'stop': None if stop is None else finite(stop, 'Stop'), 'state': 'armed',
                                    'trade_id': None, 'existing_trades': existing,
                                    'at': timestamp(dt.datetime.now(dt.timezone.utc).isoformat())}
            return key
        return self._workspace_edit(save)

    def reconcile_drafts(self, trades=None):
        """Each waiting draft goes on the next trade on its account and exact contract: of the trades there that were
        not in the journal when it was armed (existing_trades), the one opened first. Its tags fill the categories
        nothing is chosen in yet, and its note and stop go on only where the trade has none. A tag retired since, or
        two tags now in a one-choice category, attach nothing: the draft says Needs review and names that trade.

        A used draft stays used after a restart. No placeholder fill, P&L or price is invented. Called after the
        watcher's poll, not by a read-only render.
        """
        if not any(d['state'] == 'armed' for d in self.workspace()['drafts'].values()):
            return
        trades = self.trades() if trades is None else trades
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            state, library, zone = read(db), tagging.library(db), time_zone(db)
            changed = False
            for draft in (d for d in state['drafts'].values() if d['state'] == 'armed'):
                scope, existing = (draft['provider'], draft['account'], draft['symbol']), set(draft['existing_trades'])
                new = [t for t in trades if (t.provider, t.account, t.symbol) == scope and t.id not in existing]
                if not new:
                    continue
                trade = min(new, key=lambda t: instant(t.opened))
                changed = True
                draft['trade_id'] = trade.id
                tags = [library['tags'].get(id) for id in draft['tag_ids']]
                retired = any(tag is None or tag['retired'] or 'trade' not in tag['levels'] for tag in tags)
                conflicting = any(settings['exclusive'] and sum(tag['group'] == group for tag in tags if tag) > 1
                                  for group, settings in library['groups'].items())
                if retired or conflicting:
                    draft['state'] = 'needs review'
                    continue
                previous = db.execute('SELECT payload FROM annotations WHERE trade_id=?', (trade.id,)).fetchone()
                review = tagging.project(library, json.loads(previous[0]) if previous else {})
                # The trade's day in the Time Zone: in a category the draft chooses, that day's tags give way on it.
                session = db.execute('SELECT payload FROM sessions WHERE key=?',
                                     (tagging.day_key(trade.opened, zone),)).fetchone()
                session = json.loads(session[0]) if session else {}
                # A category already chosen on the trade keeps its choice.
                chosen = tagging.selected(library, review)
                for group in library['groups']:
                    choice = [id for id in draft['tag_ids'] if library['tags'][id]['group'] == group]
                    if choice and not any(library['tags'][id]['group'] == group for id in chosen):
                        review = tagging.set_group(library, review, group, choice, session)
                if not review.get('notes') and draft['notes']:
                    review['notes'] = draft['notes']
                if review.get('planned_stop') is None:
                    # A draft saved before 0.4.7 holds a dollar risk instead: it becomes the stop giving that risk.
                    stop = draft['stop'] if draft.get('stop') is not None else stop_for_risk(trade, draft.get('risk'))
                    if stop is not None:
                        review['planned_stop'] = stop
                review['draft_id'] = draft['id']
                db.execute('INSERT OR REPLACE INTO annotations VALUES(?,?)', (trade.id, json.dumps(review, allow_nan=False)))
                draft['state'] = 'attached'
            # The watcher asks every second, and a draft can wait for hours: write only when one found its trade.
            if changed:
                write(db, state)

    def cancel_draft(self, id):
        def cancel(state, library):
            if id not in state['drafts'] or state['drafts'][id]['state'] not in ('armed', 'needs review'):
                raise ValueError('Choose tags that are waiting or need review. Used tags stay on their trade.')
            state['drafts'][id]['state'] = 'cancelled'
        return self._workspace_edit(cancel)
