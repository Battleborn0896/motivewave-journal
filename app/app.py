"""MotiveWave Journal, the app. Start it from the repository root:  streamlit run app/app.py

Only the frame lives here: page setup, and a sidebar that holds nothing but the logo, the Live /
Replay switch, the three pages and the study's connection badge (the Journal menu of views or a currency picker
appears only when there is a choice to make). Trades holds the session tools and the Daily Report Card,
Stats the dashboards, and both their Filters menus the saved filter; Settings holds the settings, tags and
data pages. What views share is in common.py; each view is one module in views/.
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
from views import data, edge, preferences, setup, tag_settings, trades
from mwjournal import journals, merging
from mwjournal.workspace import filter_sample

SETUP_PAGES = {'Tags': tag_settings, 'Data': data}
ENVIRONMENTS = {'Live': 'live', 'Replay': 'replay'}
SHOWN_KEY, SEEN_KEY = 'journal-view-shown', 'journal-seen'

st.set_page_config(page_title=paths.APP_NAME, page_icon=theme.ICON, layout='wide',
                   initial_sidebar_state='expanded')
theme.apply()


@st.fragment(run_every='5s')
def badge(dataset: str) -> None:
    """The study's state in a word or two, kept current, and a full rerun when new trades have arrived. The
    button laid over it (outside this fragment, so a click reruns the whole page) opens Settings."""
    journal = common.open_journal(dataset)
    if journal is None:
        return
    _, _, _, link = common.study_link(journal)
    seen = common.stamp(journal)
    # The state word only: 'Recording · study 0.3.10' is wider than the sidebar. Settings shows the version.
    word, tone = link.state, link.tone
    if dataset == 'replay' and link.state != common.ATTENTION and seen and not seen[0]:
        word, tone = common.NO_REPLAY_TRADES, 'neutral'     # no fills yet: nothing to say until a replay trade
    st.markdown(theme.chip(word, tone, state=word) + "<span class='mwj-go'>Settings ›</span>",
                unsafe_allow_html=True)
    if seen != st.session_state.get(common.key(journal, SEEN_KEY)):
        st.rerun()                         # recorded trades appear without a manual refresh


def open_settings() -> None:
    st.session_state[common.VIEW_KEY] = 'Settings'
    st.session_state[common.TOOL_KEY] = setup.HOME


def keep_view() -> None:
    """Clicking the selected view again clears the control; the journal still shows that view. Choosing
    Settings in the sidebar, or choosing it again, always shows its first page; choosing Trades shows the
    selected trade rather than a Daily Report Card."""
    if st.session_state.get(common.VIEW_KEY) not in common.PRIMARY_VIEWS:
        previous = st.session_state.get(SHOWN_KEY, 'Trades')
        st.session_state[common.VIEW_KEY] = previous if previous in common.PRIMARY_VIEWS else 'Trades'
    if st.session_state[common.VIEW_KEY] == 'Settings':
        st.session_state[common.TOOL_KEY] = setup.HOME
    elif st.session_state[common.VIEW_KEY] == 'Trades':
        for name in [k for k in st.session_state if str(k).startswith(trades.REPORT + '-')]:
            st.session_state[name] = False


with st.sidebar:
    theme.brand(paths.APP_NAME, 'Trades. Review. Improve.')
    environment = st.radio('Trading environment', list(ENVIRONMENTS), horizontal=True, key='journal-environment',
                           help='Live holds live and paper trades, Replay your replay trades, each with its own '
                                'reviews and stats; both share your tags, filters and Time Zone.')
dataset = ENVIRONMENTS[environment]
journal = common.open_journal(dataset)
rows, currency = [], 'USD'
if journal is not None:
    views = journals.registry(journal)['views']
    view_key, pending_key = common.key(journal, 'journal-choice'), common.key(journal, 'journal-choice-next')
    if pending_key in st.session_state:    # a view just made on Settings → Data → Journals & Accounts
        st.session_state[view_key] = st.session_state.pop(pending_key)
    if st.session_state.get(view_key) not in [None, *views]:
        st.session_state[view_key] = None
    if views:                              # only someone who made a view has one to choose
        with st.sidebar:
            st.selectbox('Journal', [None, *views], key=view_key,
                         format_func=lambda id: views[id]['name'] if id else journals.ALL,
                         help='A view shows only its accounts, with the same reviews, tags and settings as All '
                              'accounts.')
    chosen = views.get(st.session_state.get(view_key))
    journal = merging.ReviewJournal(journal)
    for name in paths.DATASETS:            # both are read all the time: replay trades are recorded while Live is on screen
        try:
            runtime.start(name)
        except (ValueError, OSError, sqlite3.Error):
            pass                           # the other journal cannot be opened; switching to it says why
    st.session_state[common.key(journal, SEEN_KEY)] = common.stamp(journal)
    rows = common.rows(journal)
    if chosen:                             # before the saved filter, which then filters the view's trades
        accounts = {tuple(account) for account in chosen['accounts']}
        rows = [r for r in rows if (r['provider'], r['account']) in accounts]
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
    if journal is not None:
        with st.container(key='connection-badge'):
            badge(dataset)
            st.button('Open Settings', key='connection-badge-open', on_click=open_settings,
                      help='Whether the study is recording, the one step to take if it is not, and every setting.')
if journal is None:
    st.stop()

for pending, target in [('dashboard-next', 'dashboard-page'), ('checklist-next', 'checklist-editor-choice')]:
    if common.key(journal, pending) in st.session_state:
        st.session_state[common.key(journal, target)] = st.session_state.pop(common.key(journal, pending))
saved_filters = journal.workspace()['filters']
# Chosen in Filters on Trades or Stats, whose button counts it among the filters on.
global_filter = preferences.chosen_filter(journal, saved_filters)
every_row = rows                           # the session tools on Trades and the settings on Settings: every trade of the view
if global_filter:
    rows = filter_sample(rows, saved_filters[global_filter])

if view == 'Settings':
    setup.render(journal, rows, tool=lambda page: SETUP_PAGES[page].render(journal, rows),
                 display=lambda target, _: preferences.render(target, every_row))
elif view == 'Analytics':
    edge.render(journal, rows)
else:
    trades.render(journal, rows, every=every_row)
