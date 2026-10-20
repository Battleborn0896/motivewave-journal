"""MotiveWave Journal, the app. Start it from the repository root:  streamlit run app/app.py

Only the frame lives here: page setup, and a sidebar that holds nothing but the logo, the Live /
Replay switch, the three pages and the study's connection badge (a journal picker or a currency picker
appears only when there is a choice to make). Every setting lives on Setup. What views share is in
common.py; each view is one module in views/.
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
from mwjournal.workspace import filter_sample

VIEWS = dict(zip(common.VIEWS, (trades, edge, importing, connection, daily, export)))
VIEWS.update(Tags=tag_settings, Ratings=ratings, Journals=journal_settings, General=preferences, Dashboard=dashboard, Checklists=checklists,
             **{'Live session': session})
ENVIRONMENTS = {'Live': 'live', 'Replay': 'replay'}
SHOWN_KEY, SEEN_KEY = 'journal-view-shown', 'journal-seen'

st.set_page_config(page_title=paths.APP_NAME, page_icon=theme.ICON, layout='wide',
                   initial_sidebar_state='expanded')
theme.apply()


@st.fragment(run_every='5s')
def badge(dataset: str, selected_journal=None) -> None:
    """The study's state in one word, kept current, and a full rerun when new trades have arrived. The
    button laid over it (outside this fragment, so a click reruns the whole page) opens Setup."""
    journal = common.open_journal(dataset)
    if journal is None:
        return
    _, _, _, link = common.study_link(journal)
    # The state word only: 'Connected · study 0.3.3' is wider than the sidebar. Setup shows the version.
    st.markdown(theme.chip(link.state, link.tone, state=link.state) + "<span class='mwj-go'>Setup ›</span>",
                unsafe_allow_html=True)
    seen_key = common.key(journal, SEEN_KEY) if not selected_journal else f'{SEEN_KEY}-{dataset}-{selected_journal}'
    if common.stamp(journal) != st.session_state.get(seen_key):
        st.rerun()                         # captured trades appear without a manual refresh


def open_setup() -> None:
    st.session_state[common.VIEW_KEY] = 'Settings'
    st.session_state[common.TOOL_KEY] = setup.HOME


def keep_view() -> None:
    """Clicking the selected view again clears the control; the journal still shows that view. Choosing
    Setup in the sidebar, or choosing it again, always shows its first page."""
    if st.session_state.get(common.VIEW_KEY) not in common.PRIMARY_VIEWS:
        previous = st.session_state.get(SHOWN_KEY, 'Trades')
        st.session_state[common.VIEW_KEY] = previous if previous in common.PRIMARY_VIEWS else 'Trades'
    if st.session_state[common.VIEW_KEY] == 'Settings':
        st.session_state[common.TOOL_KEY] = setup.HOME


with st.sidebar:
    theme.brand(paths.APP_NAME, 'Trades. Review. Improve.')
    environment = st.radio('Trading environment', list(ENVIRONMENTS), horizontal=True, key='journal-environment',
                           help='Live holds live and paper trades. Replay holds trades taken in replay mode: '
                                'a separate journal with its own reviews and statistics.')
dataset = ENVIRONMENTS[environment]
capture = common.open_journal(dataset)
journal, selected_journal, rows, currency = None, None, [], 'USD'
if capture is not None:
    state = journals.registry(capture)
    active_journals = {id: d for id, d in state['journals'].items() if not d['archived']}
    named_key = common.key(capture, 'named-journal')
    pending_key = common.key(capture, 'named-journal-next')
    if pending_key in st.session_state:
        st.session_state[named_key] = st.session_state.pop(pending_key)
    if st.session_state.get(named_key) not in [None, *active_journals]:
        st.session_state[named_key] = None
    if active_journals:                    # only someone who made a named journal has a journal to choose
        with st.sidebar:
            selected_journal = st.selectbox('Local journal', [None, *active_journals], key=named_key,
                                            format_func=lambda id: active_journals[id]['name'] if id else 'All captures',
                                            help='A named journal from Setup → Journals & Accounts, with its own '
                                                 'reviews and stats. All captures is everything the study recorded.')
    journal = capture
    if selected_journal:
        try:
            journal = journals.open_named(capture, selected_journal)
        except (ValueError, OSError, sqlite3.Error) as exc:
            st.error('The named journal could not be opened: ' + str(exc))
            journal = None
if journal is not None:
    journal = merging.ReviewJournal(journal)
    for name in paths.DATASETS:            # both are read all the time: replay trades are captured while Live is on screen
        try:
            runtime.start(name)
        except (ValueError, OSError, sqlite3.Error):
            pass                           # the other journal cannot be opened; switching to it says why
    st.session_state[common.key(journal, SEEN_KEY)] = common.stamp(capture)    # authoritative source counts trigger a mirror refresh
    rows = common.rows_for(journal)
    for warning in journal.merge_warnings:
        st.warning(warning)
    currencies = sorted({r['currency'] for r in rows})
    if len(currencies) > 1:                # one journal can hold accounts in several currencies; they are never summed
        with st.sidebar:
            currency = st.selectbox('Journal currency', currencies, key=common.key(journal, 'currency'),
                                    help='Accounts in different currencies are never added together.')
    elif currencies:
        currency = currencies[0]
    rows = [r for r in rows if r['currency'] == currency]

with st.sidebar:
    current = st.session_state.get(common.VIEW_KEY, 'Trades')
    if current in common.VIEWS:
        st.session_state.update(common.navigation(current))
    st.session_state.setdefault(common.VIEW_KEY, 'Trades')
    view = st.segmented_control('Journal', common.PRIMARY_VIEWS, key=common.VIEW_KEY, format_func=lambda v: common.NAV_LABELS.get(v, v),
                                label_visibility='collapsed', on_change=keep_view)
    if view not in common.PRIMARY_VIEWS:
        view = st.session_state.get(SHOWN_KEY, 'Trades')
    st.session_state[SHOWN_KEY] = view
    if capture is not None:
        with st.container(key='connection-badge'):
            badge(dataset, selected_journal)
            st.button('Open Setup', key='connection-badge-open', on_click=open_setup,
                      help='The study connection, the three setup steps and every setting.')
if journal is None:
    st.stop()

for pending, target in [('dashboard-next', 'dashboard-page'), ('checklist-next', 'checklist-editor-choice')]:
    if common.key(journal, pending) in st.session_state:
        st.session_state[common.key(journal, target)] = st.session_state.pop(common.key(journal, pending))
saved_filters = journal.workspace()['filters']
global_filter = preferences.chosen_filter(journal, saved_filters)      # chosen under Setup → Display
every_row = rows                           # the filter editor on Setup offers every account, contract and tag
if global_filter:
    rows = filter_sample(rows, saved_filters[global_filter])
    st.caption(common.md(f'Saved filter: {global_filter}'),
               help='Only trades this saved filter keeps are shown on every page. Choose All trades under '
                    'Setup → Display to see everything.')

if dataset == 'replay':
    st.info('Replay journal · its own trades, reviews and stats. The study adds real ticks to each replay trade '
            'once it is flat; prices built from bars are marked simulated.')
if view == 'Settings':
    def tool(page):
        target = capture if page == 'Connection' and selected_journal else journal
        target_rows = common.rows_for(capture) if target is capture else rows
        VIEWS[page].render(target, target_rows)
    setup.render(journal, rows, tool=tool, display=lambda target, _: preferences.render(target, every_row))
elif view == 'Analytics':
    VIEWS['Edge'].render(journal, rows)
else:
    VIEWS[view].render(journal, rows)
