"""Setup: is the study recording, the two settings, and the three pages of real setup.

The first page reads top to bottom: one status line, green and alone when the study is recording; the one
step that is missing when something is (install the study, add it to a chart, update an older study, or a
study that has gone quiet); Time Zone and MAE / MFE Units, with hide weekends under a small Advanced
heading; a tile each for Tags, Playbooks & Checklists, Rating Systems and Data, with a link to each
of their tabs; and one About line. Bridge paths and raw errors are on Data → Connection. The other pages are
drawn by the parent through ``tool``, under a button back to this one.

Everything that was a Setup page before 0.4.5 is still at most two clicks from the sidebar: Setup, then a
tile or one of its links. The Daily Report Card, the session tools and running a checklist moved to Trades;
dashboards to Stats; the saved filter to the Filters menus (common.navigation knows every old name).
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from typing import Any

import streamlit as st

import common
import theme
from mwjournal import Journal, paths
from views import connection

PageRenderer = Callable[[str], None]
DisplayRenderer = Callable[[Any, list[dict]], None]

HOME = 'Setup'
PAGES = (HOME, 'Tags', 'Ratings', 'Data')
LABELS = {HOME: 'Setup', 'Tags': 'Tags, Playbooks & Checklists', 'Ratings': 'Rating Systems', 'Data': 'Data'}
PAGE_HELP = {
    'Tags': 'Tag categories, tags, playbooks and day tags, and the branching checklists you run on a trade.',
    'Ratings': 'Weighted process ratings built from your tags, for dashboard widgets.',
    'Data': 'Import trades, export and back up, named journals and account names, and the connection to MotiveWave.',
}
# The tabs of each page, linked one by one from its tile so every one is two clicks from the sidebar.
TABS = {'Tags': ('Tags', 'Categories', 'Playbooks', 'Checklists'),
        'Data': ('Import Trades', 'Export & Backup', 'Journals & Accounts', 'Connection')}
BLURB = {'Ratings': 'Score how well a trade followed your rules, from its tags.'}

# One line each: what the state means and what to do about it. Never a path or a raw error. A state with a
# step (below) says what to do in the step; the others say it under the status.
STATUS_COPY = {
    'Connected': 'MotiveWave is sending data. Trade as usual: each trade appears in Trades by itself.',
    'Waiting For Study': 'Waiting for the MotiveWave Journal study. Do the step below; this turns green by itself.',
    'Stale': 'The study has gone quiet. Check that MotiveWave is open and the study is still on your chart.',
    'No Heartbeat': 'Prices arrive but the study is not reporting in. Switch on Capture Fills under the study\'s Advanced tab.',
    'Paused': 'Reading is off. Switch Read Trades From MotiveWave back on under Data → Connection → Advanced.',
    'Needs Attention': 'Capture needs attention. Open Data → Connection to see what the study reported.',
}
FLAT_TO_FLAT = ('One trade runs flat to flat for one provider, account and exact contract. The first fill that moves '
                'the position away from zero opens it. Scale-ins and partial exits stay inside that same trade. The '
                'fill that returns the position to zero closes it. A reversal closes the old trade at the crossing '
                'fill and opens a new one; other accounts and contracts stay separate. Replay works the same way and '
                'is kept in the separate Replay journal. Once a replay trade is flat, the study loads the real ticks '
                'of its holding time from MotiveWave\'s history, never past the replay\'s current time, so its MAE / '
                'MFE comes from real ticks, where that history has them, even when the replay itself plays from bars.')
RECORDING = 'Recording: the contracts the study wrote prices or fills for in the last hour.'
RECORDING_REPLAY = ('Recording: the contracts of the replay run the study wrote last, within an hour of replay time '
                    'of its newest price or fill.')
INSTALL = 'Install MotiveWave Study'
# The one step a setup that is not recording needs: (title, the line saying what to do, the ? beside it).
STEPS = {
    'install': ('Install the Study',
                'Click Install MotiveWave Study, restart MotiveWave if it is open, then add the study to your chart.',
                'Copies the study into your MotiveWave Extensions folder and keeps the previous copy. By hand '
                'instead: in MotiveWave choose Study → All Studies → Import and pick motivewave-journal.jar.'),
    'chart': ('Add It to Your Chart',
              'Add **Journal → MotiveWave Journal** to the chart you trade. There is nothing to set.',
              'A contract chart (ESZ6) or a continuous one (ES) both work: prices go with trades of the same '
              'product, whatever the contract month. Live and paper trades go to the '
              'Live journal, replay trades to the Replay journal. Restart MotiveWave first if the study was just '
              'installed. To install it again, use Data → Connection.'),
    'update': ('Update the Study', '',
               'Studies before 0.3.3 never received MotiveWave\'s live or replay prints, so MAE and MFE had only '
               'the trade tick backfill. Studies before 0.3.4 labelled replay prints simulated, which hides '
               'replay MAE and MFE. Studies before 0.3.5 never linked a prop account\'s stop orders to its trades, '
               'so those trades had no R. The previous copy is kept beside the new one.'),
    'quiet': ('The Study Has Gone Quiet',
              'Check that MotiveWave is open and the study is still on your chart.',
              'Nothing has been heard from the study for over a minute. It reports every ten seconds while it is '
              'on a chart, also when the market is closed. Data → Connection shows when it last reported.'),
}
DRAWN = 'setup-step-drawn'          # the step the page was drawn with, per environment: see status_fragment
INSTALLED_HERE = 'setup-study-installed'   # this visit installed the study: the step then says to restart


def update_line(link) -> str | None:
    """One line for a study older than the one this app bundles, else None. What each older version misses is in
    the step's help (STEPS['update'])."""
    if not common.study_outdated(link):
        return None
    how = ('click Install MotiveWave Study' if connection.JAR.is_file()
           else 'import the new motivewave-journal.jar under Study → All Studies → Import')
    return f'Study {link.version} is out of date; this app comes with {paths.STUDY_VERSION}: {how}, then restart MotiveWave.'


def needed_step(link, installed: bool) -> str | None:
    """The one step to show, or None when nothing is missing (or the status line already says what to do).

    `installed` is whether the study's jar is in MotiveWave's Extensions folder: before the study has ever
    reported in, that tells installing it apart from adding it to a chart."""
    if link is None:
        return None
    heard = link.state == 'Connected' or getattr(link, 'heard', None) is not None
    if heard and common.study_outdated(link):
        return 'update'
    if link.state == 'Stale':
        return 'quiet'
    if link.state == 'Waiting For Study':
        return 'chart' if installed else 'install'
    return None


def study_installed() -> bool:
    """Whether the study is in MotiveWave: its jar is in the Extensions folder, or it has reported in from a chart
    in either environment (Replay waits for a replay chart although Live has heard the study for weeks)."""
    if connection.study_installed():
        return True
    for dataset in paths.DATASETS:
        try:
            if common.study_link(Journal(dataset=dataset))[3].heard is not None:
                return True
        except (ValueError, OSError, sqlite3.Error):
            pass
    return False


def page_for(view: str | None) -> str:
    """The Setup page that shows a view, by any name it has had; anything else opens the first page."""
    page = common.SETUP_ROUTES.get(view, (view, None))[0] if view else HOME
    return page if page in PAGES else HOME


def open_page(page: str, tab: str | None = None):
    """An on_click handler that shows one Setup page, at one of its tabs."""
    return lambda: st.session_state.update({common.TOOL_KEY: page, common.TAB_KEY: tab})


def render(journal, rows: list[dict], *, tool: PageRenderer | None = None,
           display: DisplayRenderer | None = None) -> None:
    """The first page, or another page under a button back to it. ``tool(name)`` draws the others."""
    page = page_for(st.session_state.get(common.TOOL_KEY))
    st.session_state[common.TOOL_KEY] = page
    if page != HOME and tool is not None:
        st.button('← Setup', key='setup-back', type='tertiary', on_click=open_page(HOME),
                  help='Back to the study status, the settings and the other Setup pages.')
        tool(page)
        return
    home(journal, rows, display=display, links=tool is not None)


def home(journal, rows: list[dict], *, display: DisplayRenderer | None = None, links: bool = True) -> None:
    capture = getattr(journal, 'capture_journal', journal)
    link = live_status(capture)
    step(link, getattr(capture, 'dataset', ''))
    theme.section('Settings')
    if display is None:
        from views import preferences
        display = preferences.render
    display(journal, rows)
    if links:
        pages()
    about(link)


def live_status(journal):
    """The status line, redrawn every few seconds so starting the study turns it green without a click."""
    dataset = getattr(journal, 'dataset', None)
    if dataset in paths.DATASETS:
        link = common.study_link(journal)[3]
        st.session_state[f'{DRAWN}-{dataset}'] = needed_step(link, study_installed())
        status_fragment(dataset)
        return link
    return render_status(journal)


@st.fragment(run_every='5s')
def status_fragment(dataset: str) -> None:
    journal = common.open_journal(dataset)
    if journal is None:
        return
    link = render_status(journal)
    # The step under the line is drawn with the whole page. When the study's state calls for another step
    # (it turns green, goes quiet, or is installed), the page is drawn again so the step follows.
    if needed_step(link, study_installed()) != st.session_state.get(f'{DRAWN}-{dataset}'):
        st.rerun()


def headline(link, contracts=()) -> str:
    """'Connected · recording NQZ6, ESZ6 · study 0.3.4' while the study records; else the state's own label."""
    if link.state != 'Connected':
        return link.label
    shown = list(contracts[:4]) + ([f'+{len(contracts) - 4} more'] if len(contracts) > 4 else [])
    return ' · '.join(['Connected', *(['recording ' + ', '.join(shown)] if shown else []),
                       *([f'study {link.version}'] if link.version else [])])


def render_status(journal):
    """The state in one line, and under it what to do when no step says so. No bridge path or raw error."""
    name, _, _, link = common.study_link(journal)
    try:
        contracts = journal.recent_contracts() if link.state == 'Connected' else []
    except (AttributeError, TypeError, ValueError, OSError):
        contracts = []
    notes = []
    if link.state not in ('Connected', 'Waiting For Study', 'Stale'):      # those have a step, or need nothing
        notes.append((STATUS_COPY.get(link.state, 'Waiting for the MotiveWave study to report its capture status.'),
                      link.tone == 'critical'))
    try:
        others = common.other_problems(journal, name)
    except (AttributeError, TypeError, KeyError):
        others = 0
    if others:
        notes.append((f'{others} other watched file{"s" if others > 1 else ""} could not be read. '
                      'See Data → Connection.', True))
    recording = (RECORDING_REPLAY if getattr(journal, 'dataset', '') == 'replay' else RECORDING) if contracts else ''
    # Always one element: theme.status explains why a redrawn fragment must not change its element count.
    theme.status(headline(link, contracts), link.tone, link.state, big=True, notes=notes,
                 help=' '.join(part for part in (STATUS_COPY.get(link.state, ''), recording, FLAT_TO_FLAT) if part))
    return link


def step(link, dataset: str = '') -> None:
    """The one step that is missing, with one line saying what to do; nothing when the study is recording."""
    which = needed_step(link, study_installed())
    if which is None:
        return
    title, line, explained = STEPS[which]
    installed_here = st.session_state.get(INSTALLED_HERE)      # by the button below, earlier in this visit
    if which == 'update':
        line = 'The new study is installed. Restart MotiveWave to use it.' if installed_here else update_line(link)
    elif which == 'chart' and dataset == 'replay':
        line = 'Add **Journal → MotiveWave Journal** to the chart you replay. There is nothing to set.'
    elif which == 'install' and not connection.JAR.is_file():
        line = ('In MotiveWave choose **Study → All Studies → Import** and pick motivewave-journal.jar, then add '
                'the study to your chart.')
    if which == 'chart' and installed_here:
        line = 'Restart MotiveWave, then a' + line[1:]
    with st.container(border=True, key='setup-step-' + which):
        st.markdown(f'**{title}**  \n{line}', help=explained)
        if which in ('install', 'update') and connection.JAR.is_file() and not installed_here:
            if st.button(INSTALL, type='primary', key='simple-study-install'):
                try:
                    connection.install_study()
                except (OSError, ValueError) as exc:
                    st.error('The study could not be installed: ' + str(exc))
                else:
                    # Drawn again at once, so the step's own line says to restart MotiveWave and the button goes:
                    # never 'click Install' beside a note that it is installed.
                    st.session_state[INSTALLED_HERE] = True
                    st.rerun()


def pages() -> None:
    """Tags, Playbooks & Checklists; Rating Systems; Data: a tile each, with a link to each of its tabs."""
    theme.section('More Setup')
    for column, page in zip(st.columns(len(PAGES) - 1), PAGES[1:]):
        with column.container(border=True, height='stretch', key='setup-page-' + page.lower()):
            st.button(LABELS[page] + ' ›', key='setup-open-' + page, type='tertiary', on_click=open_page(page),
                      help=PAGE_HELP[page])
            if page in TABS:
                with st.container(horizontal=True, gap='small'):
                    for tab in TABS[page]:
                        st.button(tab, key=f'setup-tab-{page}-{tab}', type='tertiary', on_click=open_page(page, tab))
            else:
                st.caption(BLURB[page])


def about(link=None) -> None:
    """One line to quote in a bug report, then the chart library's credit and the independence notice."""
    version = getattr(link, 'version', '') or paths.STUDY_VERSION
    st.caption(f'{paths.APP_NAME} {paths.VERSION} · study {version} · data folder {paths.data_home()}',
               help='Quote this line in a bug report. The study version is the one MotiveWave last reported, or the '
                    'one this app comes with before it has. Trades, reviews and settings stay in the data folder on '
                    'this computer; Data → Export & Backup makes a copy.')
    with st.container(key='setup-fine-print', gap=None):
        st.caption(common.CREDIT)
        st.caption('MotiveWave Journal is an independent open-source project, not affiliated with or endorsed by '
                   'MotiveWave Software.')
