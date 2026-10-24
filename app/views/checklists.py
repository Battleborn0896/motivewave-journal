"""Checklists: numbered yes/no questions, built under Settings → Tags, Playbooks & Checklists → Checklists, and
answered where they run. One that runs on each trade is the Checklist section of every trade's page, drawn once such
a checklist exists; one that runs before the session is on the Daily Report Card, once a day. A run keeps its own
copy of the checklist, so editing or deleting one never changes answers already given."""
import streamlit as st

import common
import theme
from mwjournal import tagging
from mwjournal.workspace import (ANSWERS, RUNS_ON, checklist_questions, compile_checklist, identifier, question,
                                 replay_run)

STARTER = 'Is the setup valid and my risk defined?'
WHERE = {'trade': "On every trade's page.", 'session': 'On the Daily Report Card, once a day.'}


def starter():
    """The questions a new checklist starts with."""
    return [question(STARTER)]


# ---------------------------------------------------------------- the editor, on Settings

def render(journal, rows):
    theme.section('Checklists', help='Yes/no questions you answer on each trade or on the Daily Report Card; no '
                                     'checklist can place or block an order.')
    definitions = journal.workspace()['checklists']
    ordered = sorted(definitions, key=lambda id: definitions[id]['order'])
    choice = common.key(journal, 'checklist-editor-choice')
    if st.session_state.get(choice) not in (None, *ordered):
        st.session_state.pop(choice)                       # a checklist deleted a moment ago
    # Opens on the first checklist; a new one is the last choice.
    selected = st.selectbox('Checklist', [*ordered, None], key=choice,
                            format_func=lambda id: definitions[id]['name'] if id else '+ New checklist')
    editor(journal, selected, definitions.get(selected))


def draft(definition) -> dict:
    """What the editor holds while a checklist is edited: its name, where it runs and its questions. Each question has
    an id of its own, and a branch names the question it goes to by that id, so it follows that question when it
    moves."""
    asked = checklist_questions(definition['graph']) if definition else starter()
    ids = [identifier()[:12] for _ in asked]
    return {'name': definition['name'] if definition else 'Pre-trade plan',
            'runs_on': definition['runs_on'] if definition else 'trade',
            'questions': [{'uid': uid, 'text': q['text'],
                           **{a: {**q[a], 'go': ids[q[a]['go'] - 1] if type(q[a]['go']) is int else q[a]['go']}
                              for a in ANSWERS}} for uid, q in zip(ids, asked)]}


def saved_as(held) -> list[dict]:
    """The draft's questions as a checklist is saved (workspace.question): a branch to a question by its number."""
    place = {q['uid']: n for n, q in enumerate(held['questions'], 1)}
    return [question(q['text'], **{a: {**q[a], 'go': place.get(q[a]['go'], q[a]['go'])} for a in ANSWERS})
            for q in held['questions']]


def unsaved(held, definition) -> bool:
    """Whether the draft says something the saved checklist does not."""
    try:
        now = checklist_questions(compile_checklist(saved_as(held)))
    except ValueError:
        return True
    return ((held['name'].strip(), held['runs_on'], now)
            != (definition['name'], definition['runs_on'], checklist_questions(definition['graph'])))


def editor(journal, selected, definition):
    """The checklist as a list: its name, where it runs, its numbered questions, and one Save Checklist. Each
    question's branches wait behind a closed Add a Branch, and a line under it says what they do. Every box writes
    into the draft as it changes, so nothing typed is lost by opening another page before saving."""
    held_key = common.key(journal, 'checklist-draft-' + (selected or 'new'))
    if held_key not in st.session_state:
        st.session_state[held_key] = draft(definition)
    held = st.session_state[held_key]
    library = journal.tag_library()
    uids = [q['uid'] for q in held['questions']]

    def asked(uid):
        return next(q for q in st.session_state[held_key]['questions'] if q['uid'] == uid)

    def keep(widget, field, uid=None, answer=None):
        target = st.session_state[held_key] if uid is None else asked(uid)
        (target[answer] if answer else target)[field] = st.session_state.get(widget)

    def box(name, value, *path):
        """The key and on_change of a box showing `value` from the draft, which it writes back to `path`."""
        widget = f'{held_key}-{name}'
        st.session_state[widget] = value
        return {'key': widget, 'on_change': keep, 'args': (widget, *path)}

    def add():
        st.session_state[held_key]['questions'].append({'uid': identifier()[:12], **question('')})

    def move(place, offset):
        listed = st.session_state[held_key]['questions']
        listed[place], listed[place + offset] = listed[place + offset], listed[place]

    def remove(place):
        listed = st.session_state[held_key]['questions']
        gone = listed.pop(place)['uid']
        for other in listed:
            for a in ANSWERS:
                if other[a]['go'] == gone:
                    other[a]['go'] = 'next'

    st.text_input('Checklist name', max_chars=80, **box('name', held['name'], 'name'))
    st.radio('Runs on', list(RUNS_ON), format_func=RUNS_ON.get, horizontal=True,
             **box('runs-on', held['runs_on'], 'runs_on'))
    st.caption(WHERE[held['runs_on']])
    level = 'trade' if held['runs_on'] == 'trade' else 'day'
    for place, item in enumerate(held['questions']):
        uid, last = item['uid'], place == len(uids) - 1
        for answer in ANSWERS:
            branch = item[answer]
            if branch['go'] not in ('next', 'finish', *uids) or last and branch['go'] == 'finish':
                branch['go'] = 'next'              # its question was removed, or Finish is now the next step
            branch['tag_ids'] = [id for id in branch['tag_ids'] if id in library['tags']]
        with st.container(border=True):
            with st.container(horizontal=True, vertical_alignment='bottom', gap='small'):
                st.text_input(f'Question {place + 1}', max_chars=400, placeholder='Did I wait for my setup?',
                              **box(f'{uid}-text', item['text'], 'text', uid))
                st.button('Move Up', key=f'{held_key}-{uid}-up', disabled=place == 0, on_click=move, args=(place, -1))
                st.button('Move Down', key=f'{held_key}-{uid}-down', disabled=last, on_click=move, args=(place, 1))
                st.button('Remove', key=f'{held_key}-{uid}-remove', disabled=len(uids) == 1, on_click=remove,
                          args=(place,))
            if words := branches(item, uids, library):
                st.caption(words)
            with st.expander('Add a Branch'):
                for column, answer in zip(st.columns(2), ANSWERS):
                    branch, word = item[answer], answer.title()
                    options = ['next', *[other for n, other in enumerate(uids)
                                         if n not in (place, place + 1) or other == branch['go']],
                               *([] if last else ['finish'])]
                    with column:
                        st.selectbox(f'{word} goes to', options,
                                     format_func=lambda go, last=last: target(go, uids, last), **box(f'{uid}-{answer}-go', branch['go'], 'go', uid, answer))
                        st.multiselect(f'{word} collects tags', tag_choices(library, level, branch['tag_ids']),
                                       format_func=lambda id: library['tags'][id]['name'], placeholder='No tags',
                                       **box(f'{uid}-{answer}-tags', branch['tag_ids'], 'tag_ids', uid, answer))
                        st.checkbox('Count this answer', help='Shows under the checklist how many times this answer '
                                                              'was given in that run.',
                                    **box(f'{uid}-{answer}-count', branch['count'], 'count', uid, answer))
    st.button('Add Question', key=held_key + '-add', on_click=add)
    with st.container(horizontal=True, vertical_alignment='center', gap='small'):
        if st.button('Save Checklist', type='primary', key=held_key + '-save'):
            try:
                id = journal.save_checklist(held['name'], saved_as(held), runs_on=held['runs_on'], id=selected)
            except ValueError as exc:
                st.error(str(exc))
            else:
                st.session_state.pop(held_key)
                st.session_state[common.key(journal, 'checklist-next')] = id
                st.rerun()
        if selected:
            if st.button('Delete Checklist', key=held_key + '-delete',
                         help='Answers already given keep their own copy of this checklist.'):
                journal.delete_checklist(selected)
                st.session_state.pop(held_key)
                st.session_state[common.key(journal, 'checklist-next')] = None
                st.rerun()
            if unsaved(held, definition):
                st.caption('Not saved yet.')


def target(go, uids, last) -> str:
    """Where a branch goes, as the editor says it."""
    if go == 'next':
        return 'Finish' if last else 'Next question'
    return 'Finish' if go == 'finish' else f'Question {uids.index(go) + 1}'


def branches(item, uids, library) -> str:
    """One line saying what the question's answers do besides going on to the next question, or ''."""
    last = item['uid'] == uids[-1]
    words = []
    for answer in ANSWERS:
        branch, word = item[answer], answer.title()
        if branch['go'] in ('finish', *uids):
            words.append(f'{word} goes to {target(branch["go"], uids, last)}')
        if names := tagging.names(library, branch['tag_ids']):
            words.append(f'{word} collects {", ".join(names)}')
        if branch['count']:
            words.append(f'{word} is counted')
    return ' · '.join(words)


def tag_choices(library, level, chosen) -> list[str]:
    """The tags an answer can collect: those in use on trades (or on days, for a checklist that runs before the
    session), by category and order, and any it collects already."""
    groups = {group: settings['order'] for group, settings in library['groups'].items()}
    tags = sorted(library['tags'].values(), key=lambda t: (groups.get(t['group'], len(groups)), t['order']))
    return [t['id'] for t in tags if not t['retired'] and level in t['levels'] or t['id'] in chosen]


# ---------------------------------------------------------------- answering, on a trade or a day

def offered(journal, runs_on='trade') -> bool:
    """Whether some checklist runs on each trade ('trade') or before the session ('session'): only then does a trade's
    page, or the Daily Report Card, have a Checklist section."""
    return any(d['runs_on'] == runs_on for d in journal.workspace()['checklists'].values())


def run_panel(journal, *, trade=None, day=None):
    """Each checklist that runs on each trade, answered for `trade`, or before the session, answered for `day`
    ('YYYY-MM-DD'), in their order."""
    state = journal.workspace()
    runs_on = 'trade' if trade else 'session'
    for definition in sorted(state['checklists'].values(), key=lambda d: d['order']):
        if definition['runs_on'] == runs_on:
            answer_panel(journal, state, definition, trade, day)


def answer_panel(journal, state, definition, trade, day):
    """One checklist's questions in order, each with its answers as pills; only the question due takes a click, and a
    branch hides the questions it skipped. It shows the latest run on this trade or day; before its first answer,
    the checklist as it is now. Undo takes back the last answer, and the tags the answers collect are applied only
    by Apply Collected Tags."""
    mine = ((lambda r: r.get('trade_id') == trade.id) if trade
            else (lambda r: not r.get('trade_id') and r.get('day') == day))
    run = max((r for r in state['runs'].values() if r['checklist_id'] == definition['id'] and mine(r)),
              key=lambda r: r['started'], default=None)
    if run and not run['answers'] and run['graph'] != definition['graph']:
        run = None                                         # nothing answered yet: the checklist as it is now
    graph = run['graph'] if run else definition['graph']
    progress = run or replay_run(graph, [])
    nodes = {node['id']: node for node in graph['nodes']}
    questions = [node for node in graph['nodes'] if node['kind'] == 'question']
    trail, current = progress['trail'], progress['current']
    # In the trail each answered question is followed by its answer; a question asked again shows its last one.
    given = {asked: answer for asked, answer in zip(trail, trail[1:]) if nodes[asked]['kind'] == 'question'}
    due = next((n for n, asked in enumerate(questions) if asked['id'] == current), len(questions))
    prefix = common.key(journal, f"checklist-{trade.id if trade else day}-{definition['id']}")
    theme.subsection(definition['name'])
    if run and run['graph'] != definition['graph']:
        st.caption('Answered on an earlier version of this checklist; Undo every answer to answer it as it is now.')

    def answer(widget, run_id):
        choice = st.session_state.get(widget)
        if not choice:
            return
        try:
            if run_id is None:
                run_id = journal.start_checklist(definition['id'], trade.id if trade else None,
                                                 day=None if trade else day)
            journal.answer_checklist(run_id, choice)
        except ValueError:
            pass                                           # a click on a question no longer due, arriving again

    for place, asked in enumerate(questions):
        if asked['id'] not in given and asked['id'] != current and place < due:
            continue                                       # a branch skipped it
        if not asked.get('next'):
            st.caption(f"{place + 1} · {asked['text']} · this question has no answers")
            continue
        widget = f"{prefix}-{asked['id']}"
        st.session_state[widget] = None if asked['id'] == current else given.get(asked['id'])
        st.pills(f"{place + 1} · {asked['text']}", asked['next'], format_func=lambda id: nodes[id]['text'],
                 key=widget, disabled=asked['id'] != current, on_change=answer,
                 args=(widget, run['id'] if run else None))
    if progress['complete']:
        st.success('Checklist complete')
    library = journal.tag_library()
    with st.container(horizontal=True, vertical_alignment='center', gap='small'):
        if st.button('Undo Last Answer', key=prefix + '-undo', disabled=not (run and run['answers'])):
            journal.answer_checklist(run['id'], undo=True)
            st.rerun()
        if st.button('Apply Collected Tags', key=prefix + '-apply', disabled=not progress['tag_ids']):
            try:
                if trade:
                    journal.update_annotation(trade.id, {}, add_tags=tagging.names(library, progress['tag_ids']))
                else:
                    journal.add_day_tags(day, progress['tag_ids'])
                st.rerun()
            except ValueError as exc:
                st.error(str(exc))
    st.caption('Collected tags: ' + (', '.join(tagging.names(library, progress['tag_ids'])) or 'None'))
    if not trade:
        off = tagging.names(library, [id for id in progress['tag_ids']
                                      if id in library['tags'] and 'day' not in library['tags'][id]['levels']])
        if off:
            st.caption(f'{", ".join(off)} {"is" if len(off) == 1 else "are"} not available on days, so Apply leaves '
                       f'{"it" if len(off) == 1 else "them"} off the day.')
    if progress['counters']:
        st.caption('Counted: ' + ' · '.join(f'{nodes[id]["text"]}: {count}'
                                            for id, count in progress['counters'].items()))
