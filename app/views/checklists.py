"""Checklist definitions, offline node canvas and snapshot-based runs on selected trades."""
import json
import streamlit as st

import common
import theme
import checklist_editor
from mwjournal import tagging


def starter():
    return {'start': 'plan', 'nodes': [
        {'id': 'plan', 'kind': 'question', 'text': 'Is the setup valid and risk defined?', 'next': ['yes', 'no'], 'x': 220, 'y': 25},
        {'id': 'yes', 'kind': 'answer', 'text': 'Yes — continue my plan', 'next': [], 'x': 30, 'y': 180},
        {'id': 'no', 'kind': 'answer', 'text': 'No — stand aside', 'next': [], 'x': 420, 'y': 180}]}


def render(journal, rows):
    theme.section('Checklists', 'branching questions · tagged answers · local history')
    state = journal.workspace()
    definitions = state['checklists']
    manage, runs = st.tabs(['Build & manage', 'Run history'])
    with manage:
        selected = st.selectbox('Checklist', [None, *sorted(definitions, key=lambda id: definitions[id]['order'])],
                                 format_func=lambda id: definitions[id]['name'] if id else '＋ New checklist',
                                 key=common.key(journal, 'checklist-editor-choice'))
        old = definitions.get(selected, {})
        with st.form(common.key(journal, 'checklist-name-' + str(selected))):
            name = st.text_input('Checklist name', old.get('name', 'Pre-trade plan'), max_chars=80)
            hidden = st.checkbox('Hide from trade review · preserve history', value=old.get('hidden', False))
            if st.form_submit_button('Save checklist name', type='primary'):
                id = journal.save_checklist(name, old.get('graph', starter()), id=selected, hidden=hidden)
                st.session_state[common.key(journal, 'checklist-next')] = id
                st.rerun()
        if selected:
            result = checklist_editor.show(old['graph'], journal.tag_library()['tags'], common.key(journal, 'node-canvas-' + selected))
            saved_key = common.key(journal, 'node-canvas-saved-' + selected)
            if result and result != st.session_state.get(saved_key):
                try:
                    journal.save_checklist(old['name'], result['graph'], id=selected, hidden=old['hidden'])
                    st.session_state[saved_key] = result
                    st.rerun()
                except (ValueError, TypeError, KeyError) as exc:
                    st.error('Graph not saved: ' + str(exc))
            st.caption('Questions link to answers. Answers can link to another question, pass or counter. '
                       'Question loops require a click; non-terminating automatic loops are rejected. Save graph explicitly.')
            with st.expander('Import, export & local versions'):
                st.download_button('Export checklist JSON', json.dumps({'name': old['name'], 'graph': old['graph']}, indent=2),
                                     file_name='journal-checklist.json', mime='application/json')
                uploaded = st.file_uploader('Import our checklist JSON format', type=['json'], key=common.key(journal, 'graph-import'))
                if uploaded and st.button('Import as a new checklist', key=common.key(journal, 'graph-import-apply')):
                    try:
                        if uploaded.size > 1_000_000:
                            raise ValueError('Checklist files must be under 1 MB.')
                        data = json.loads(uploaded.getvalue())
                        id = journal.save_checklist(data['name'], data['graph'])
                        st.session_state[common.key(journal, 'checklist-next')] = id
                        st.rerun()
                    except (ValueError, KeyError, TypeError) as exc:
                        st.error('Import not applied: ' + str(exc))
                versions = old.get('backups', [])
                if versions:
                    version = st.selectbox('Local version', range(len(versions)), format_func=lambda i: versions[i]['at'])
                    if st.button('Restore as a new checklist', key=common.key(journal, 'restore-checklist')):
                        id = journal.save_checklist(old['name'] + ' restored', versions[version]['graph'])
                        st.session_state[common.key(journal, 'checklist-next')] = id
                        st.rerun()
    with runs:
        history = sorted(state['runs'].values(), key=lambda r: r['started'], reverse=True)
        if not history:
            st.info('Run a checklist in Daily trades → Checklists, or in Live session. No checklist can place or block orders.')
        else:
            st.dataframe([{'Checklist': r['name'], 'Started (UTC)': r['started'], 'Trade': r['trade_id'],
                           'Answers': len(r['answers']), 'Complete': r['complete']} for r in history], hide_index=True)


def run_panel(journal, trade=None, *, prefix='trade'):
    state = journal.workspace()
    defs = {id: d for id, d in state['checklists'].items() if not d['hidden']}
    if not defs:
        st.info('Create a checklist once; run it here with click-to-answer branches.')
        st.button('Build a checklist', on_click=common.go('Checklists'), key=common.key(journal, prefix + '-build-checklist'))
        return
    key = lambda name: common.key(journal, prefix + '-checklist-' + name)
    id = st.selectbox('Run checklist', list(defs), format_func=lambda id: defs[id]['name'], key=key('definition'))
    run_id_key = key('current-' + id + '-' + (trade.id if trade else 'session'))
    relevant = [r for r in state['runs'].values() if r['checklist_id'] == id and r['trade_id'] == (trade.id if trade else None)]
    if run_id_key not in st.session_state and relevant:
        st.session_state[run_id_key] = max(relevant, key=lambda r: r['started'])['id']
    if st.button('Start new run', key=key('start-' + id)):
        st.session_state[run_id_key] = journal.start_checklist(id, trade.id if trade else None)
        st.rerun()
    run = state['runs'].get(st.session_state.get(run_id_key))
    if not run:
        return
    nodes = {n['id']: n for n in run['graph']['nodes']}
    if run['complete']:
        st.success('Checklist complete · answers saved locally')
    else:
        node = nodes[run['current']]
        st.markdown('### ' + node['text'])
        if not node['next']:
            st.warning('This question has no answers. Add answer links in the editor.')
        for answer in node['next']:
            if st.button(nodes[answer]['text'], key=key('answer-' + answer), width='stretch'):
                journal.answer_checklist(run['id'], answer)
                st.rerun()
    cols = st.columns(2)
    if cols[0].button('Undo last answer', disabled=not run['answers'], key=key('undo')):
        journal.answer_checklist(run['id'], undo=True)
        st.rerun()
    if trade and cols[1].button('Apply collected tags', disabled=not run['tag_ids'], key=key('apply')):
        try:
            journal.update_annotation(trade.id, {}, add_tags=tagging.names(journal.tag_library(), run['tag_ids']))
            st.rerun()
        except ValueError as exc:
            st.error(str(exc))
    st.caption('Collected tags: ' + (', '.join(tagging.names(journal.tag_library(), run['tag_ids'])) or 'None'))
    if run['counters']:
        st.caption('Counters: ' + ' · '.join(f'{nodes[id]["text"]}: {count}' for id, count in run['counters'].items()))
    with st.expander('Answer path'):
        for id in run['trail']:
            st.write(nodes[id]['kind'].title() + ': ' + nodes[id]['text'])
        if run.get('answer_events'):
            st.dataframe(run['answer_events'], hide_index=True, height=180)
