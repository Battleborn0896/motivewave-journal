"""MotiveWave Journal, the app. Start it from the repository root:  streamlit run app/app.py

Only the frame lives here: page setup, the Live / Replay switch, the study-connection badge, the
view switcher and the hand-off to one module per view (views/). What views share is in common.py.
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
for folder in (HERE, HERE.parent):         # app/ holds theme, common and views; the repository root holds mwjournal
    if str(folder) not in sys.path:
        sys.path.insert(0, str(folder))

import streamlit as st

import common
import runtime
import theme
from mwjournal import paths
from views import connection, daily, edge, export, importing, trades, tag_settings, dashboard, checklists, session, ratings, preferences, journal_settings, setup
from mwjournal import journals, merging

VIEWS = dict(zip(common.VIEWS, (trades, edge, importing, connection, daily, export)))
VIEWS.update(Tags=tag_settings, Ratings=ratings, Journals=journal_settings, General=preferences, Dashboard=dashboard, Checklists=checklists,
             **{'Live session': session})
ENVIRONMENTS = {'Live': 'live', 'Replay': 'replay'}
SHOWN_KEY, SEEN_KEY = 'journal-view-shown', 'journal-seen'

st.set_page_config(page_title=paths.APP_NAME, page_icon=':material/candlestick_chart:', layout='wide',
                   initial_sidebar_state='expanded')
theme.apply()


@st.fragment(run_every='5s')
def badge(dataset: str, selected_journal=None) -> None:
    """The study's state in one word, kept current, and a full rerun when new trades have arrived."""
    journal = common.open_journal(dataset)
    if journal is None:
        return
    name, _, _, link = common.study_link(journal)
    others = common.other_problems(journal, name)
    theme.status(link.label, link.tone, link.state)
    with st.popover('Connection details'):
        st.markdown(link.detail)
        if others:
            st.warning(f'{others} other watched file{"s" if others > 1 else ""} could not be read · see Connection')
    seen_key = common.key(journal, SEEN_KEY) if not selected_journal else f'{SEEN_KEY}-{dataset}-{selected_journal}'
    if common.stamp(journal) != st.session_state.get(seen_key):
        st.rerun()                         # captured trades appear without a manual refresh


def keep_view() -> None:
    """Clicking the selected view again clears the control; the journal still shows that view."""
    if st.session_state.get(common.VIEW_KEY) not in common.PRIMARY_VIEWS:
        previous = st.session_state.get(SHOWN_KEY, 'Trades')
        st.session_state[common.VIEW_KEY] = previous if previous in common.PRIMARY_VIEWS else 'Trades'


with st.sidebar:
    theme.brand(paths.APP_NAME, 'Trades. Review. Improve.')
    environment = st.radio('Trading environment', list(ENVIRONMENTS), horizontal=True, key='journal-environment',
                           help='Live holds live and paper trades. Replay holds trades taken in replay mode: '
                                'a separate journal with its own reviews and statistics.')
    current = st.session_state.get(common.VIEW_KEY, 'Trades')
    if current in common.VIEWS:
        st.session_state.update(common.navigation(current))
    st.session_state.setdefault(common.VIEW_KEY, 'Trades')
    view = st.segmented_control('Journal', common.PRIMARY_VIEWS, key=common.VIEW_KEY, format_func=lambda v: common.NAV_LABELS.get(v, v),
                                label_visibility='collapsed', on_change=keep_view)
    if view not in common.PRIMARY_VIEWS:
        view = st.session_state.get(SHOWN_KEY, 'Trades')
    st.session_state[SHOWN_KEY] = view
dataset = ENVIRONMENTS[environment]
journal = common.open_journal(dataset)
if journal is None:
    st.stop()
capture = journal
state = journals.registry(capture)
active_journals = {id: d for id, d in state['journals'].items() if not d['archived']}
with st.sidebar:
    named_key = common.key(capture, 'named-journal')
    pending_key = common.key(capture, 'named-journal-next')
    if pending_key in st.session_state:
        st.session_state[named_key] = st.session_state.pop(pending_key)
    if st.session_state.get(named_key) not in [None, *active_journals]:
        st.session_state[named_key] = None
    selected_journal = None
    if active_journals:
        with st.expander('Advanced journal'):
            selected_journal = st.selectbox('Local journal', [None, *active_journals], key=named_key,
                                            format_func=lambda id: active_journals[id]['name'] if id else 'All captures · original')
if selected_journal:
    try:
        journal = journals.open_named(capture, selected_journal)
    except (ValueError, OSError, sqlite3.Error) as exc:
        st.error('The named journal could not be opened: ' + str(exc))
        st.stop()
journal = merging.ReviewJournal(journal)
for name in paths.DATASETS:                # both are read all the time: replay trades are captured while Live is on screen
    try:
        runtime.start(name)
    except (ValueError, OSError, sqlite3.Error):
        pass                               # the other journal cannot be opened; switching to it says why

st.session_state[common.key(journal, SEEN_KEY)] = common.stamp(capture)    # authoritative source counts trigger a mirror refresh
rows = common.rows_for(journal)
for warning in journal.merge_warnings:
    st.warning(warning)
currencies = sorted({r['currency'] for r in rows})
with st.sidebar:
    badge(dataset, selected_journal)
    if len(currencies) > 1:                # one journal can hold accounts in several currencies; they are never summed
        currency = st.selectbox('Journal currency', currencies, key=common.key(journal, 'currency'))
    else:
        currency = currencies[0] if currencies else 'USD'
rows = [r for r in rows if r['currency'] == currency]
for pending, target in [('dashboard-next', 'dashboard-page'), ('checklist-next', 'checklist-editor-choice')]:
    if common.key(journal, pending) in st.session_state:
        st.session_state[common.key(journal, target)] = st.session_state.pop(common.key(journal, pending))
saved_filters = journal.workspace()['filters']
with st.sidebar, st.expander('Display & advanced'):
    common.unit_selector(journal)
    choice_key = common.key(journal, 'global-saved-filter')
    st.session_state.setdefault(choice_key, None)
    if st.session_state[choice_key] not in [None, *saved_filters]:
        st.session_state[choice_key] = None
    global_filter = st.selectbox('Global filter', [None, *saved_filters], format_func=lambda name: name or 'All trades', key=choice_key)
    with st.popover('Save / edit filters'):
        with st.form(common.key(journal, 'global-filter-form')):
            name = st.text_input('Filter name', max_chars=80)
            rule = dashboard.filter_editor(journal, rows, 'saved-global', saved_filters.get(global_filter, {}))
            st.caption('Saved by stable tag identity. Combines with page / widget filters. It never alters your executions.')
            if st.form_submit_button('Save reusable filter'):
                try:
                    journal.save_filter(name, rule)
                    st.rerun()
                except ValueError as exc:
                    st.error(str(exc))
    st.caption(f'Data folder: {paths.data_home()}')
    st.caption(common.CREDIT)
    st.caption('An independent open-source project, not affiliated with or endorsed by MotiveWave Software.')
if global_filter:
    from mwjournal.workspace import filter_sample
    rows = filter_sample(rows, saved_filters[global_filter])
    st.caption('Global filter: ' + global_filter)

if dataset == 'replay':
    st.info('Replay journal · its own database, reviews and statistics. Replay prices generated from bars are '
            'labelled simulated and are never presented as tick-accurate history.')
if view == 'Settings':
    requested_tool = st.session_state.get(common.TOOL_KEY)
    def advanced_setup(_journal, _rows):
        labels = {'Connection': 'Study diagnostics', 'Tags': 'Tags & playbooks',
                  'Ratings': 'Rating systems', 'Journals': 'Journals & accounts', 'General': 'Display preferences',
                  'Import': 'Manual import', 'Export': 'Export & backup', 'Dashboard': 'Custom dashboards',
                  'Daily review': 'Daily review', 'Live session': 'Session tools', 'Checklists': 'Checklists'}
        tool = st.selectbox('Advanced tool', common.TOOLS, key=common.TOOL_KEY, format_func=labels.get)
        target = capture if tool == 'Connection' and selected_journal else journal
        target_rows = common.rows_for(capture) if target is capture else rows
        VIEWS[tool].render(target, target_rows)
    setup.render(journal, rows, advanced=advanced_setup, show_status=requested_tool != 'Connection')
elif view == 'Analytics':
    VIEWS['Edge'].render(journal, rows)
else:
    VIEWS[view].render(journal, rows)
