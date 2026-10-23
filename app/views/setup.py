"""Settings: is the study recording, the two settings, and the two pages of real setup.

The first page reads top to bottom: once after an upgrade, one quiet line saying what it did, with OK; one status
line, Recording, Not recording or Needs attention, green and alone
when the study records; under it, one line saying the one thing to do when there is something (install the study,
add it to a chart, open MotiveWave, or update an older study); Time Zone and MAE / MFE Units, with hide weekends
under a small Advanced heading; a tile each for Tags, Playbooks & Checklists and Data, with a link to each of
their tabs; and one About line. Bridge paths and raw errors are on Data → Connection. The other pages
are drawn by the parent through ``tool``, under a button back to this one.

Everything that was its own page before 0.4.5 is still at most two clicks from the sidebar: Settings, then a
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

HOME = 'Settings'
PAGES = (HOME, 'Tags', 'Data')
LABELS = {HOME: 'Settings', 'Tags': 'Tags, Playbooks & Checklists', 'Data': 'Data'}
PAGE_HELP = {
    'Tags': 'Tag categories, tags, playbooks and day tags, and the branching checklists you run on a trade.',
    'Data': 'Import trades, export and back up, named journals and account names, and the connection to MotiveWave.',
}
# The tabs of each page, linked one by one from its tile so every one is two clicks from the sidebar.
TABS = {'Tags': ('Tags', 'Categories', 'Playbooks', 'Checklists'),
        'Data': ('Import Trades', 'Export & Backup', 'Journals & Accounts', 'Connection')}

# One line per state: what it means, first in the status line's ?. Needs attention also says it under the status;
# Not recording has the one step to take there instead (STEPS). Never a path or a raw error.
STATUS_COPY = {
    common.RECORDING: 'MotiveWave is sending data. Trade as usual: each trade appears in Trades by itself.',
    common.NOT_RECORDING: 'The study is not sending data. Do the step under this line; it turns green by itself.',
    common.ATTENTION: 'Something stops the recording. Open Data → Connection to see what.',
}
FLAT_TO_FLAT = ('One trade runs flat to flat for one provider, account and exact contract. The first fill that moves '
                'the position away from zero opens it. Scale-ins and partial exits stay inside that same trade. The '
                'fill that returns the position to zero closes it. A reversal closes the old trade at the crossing '
                'fill and opens a new one; other accounts and contracts stay separate. Replay works the same way and '
                'is kept in the separate Replay journal. Once a replay trade is flat, the study loads the real ticks '
                'of its holding time from MotiveWave\'s history, never past the replay\'s current time, so its MAE / '
                'MFE comes from real ticks, where that history has them, even when the replay itself plays from bars.')
CONTRACTS = 'The contracts named are those the study wrote prices or fills for in the last hour.'
CONTRACTS_REPLAY = ('The contracts named are those of the replay run the study wrote last, within an hour of replay '
                    'time of its newest price or fill.')
INSTALL = 'Install MotiveWave Study'
# The one thing to do when the study is not recording or is older than this app's: (the line, the ? beside it).
STEPS = {
    'install': ('Click Install MotiveWave Study, restart MotiveWave if it is open, then add the study to your chart.',
                'Copies the study into your MotiveWave Extensions folder and keeps the previous copy; by hand, use '
                'Study → All Studies → Import in MotiveWave.'),
    'chart': ('Add **Journal → MotiveWave Journal** to the chart you trade. There is nothing to set. Not in '
              'MotiveWave\'s study list yet? Quit MotiveWave and open it again.',
              'A contract chart (ESZ6) or a continuous one (ES) both work; live and paper trades go to Live, replay '
              'trades to Replay.'),
    'open': ('Open MotiveWave with the study on your chart.',
             'The study reports every ten seconds while it is on a chart; Data → Connection shows when it last did.'),
    'update': ('',
               'Older studies miss prints, stops or replay prices; from 0.3.6 on, a new study takes over from the old '
               'one without a restart.'),
}
DRAWN = 'setup-step-drawn'          # the step the page was drawn with, per environment: see status_fragment
HANDS_OVER = (0, 3, 6)              # a running study from this version on hands over to a new one by itself


def update_line(link) -> str | None:
    """One line for a study older than the one this app bundles, else None. Once the new study is in the
    Extensions folder, the line says what is left: MotiveWave runs the old one until it starts the new (from
    0.3.6 on, by itself in a few seconds)."""
    if not common.study_outdated(link):
        return None
    if connection.bundled_study_installed():
        if not common.recent(link.heard):                # MotiveWave is closed, or the study is off its charts
            return f'Study {paths.STUDY_VERSION} is installed: open MotiveWave and it starts the new one.'
        if (common.version_parts(link.version) or ()) >= HANDS_OVER:
            return f'Study {paths.STUDY_VERSION} is installed: MotiveWave switches to it in a few seconds.'
        return (f'Study {paths.STUDY_VERSION} is installed and MotiveWave is still running {link.version}. '
                'Quit MotiveWave completely and open it again.')
    how = ('click Install MotiveWave Study' if connection.JAR.is_file()
           else 'import the new motivewave-journal.jar under Study → All Studies → Import')
    return f'Study {link.version} is out of date; this app comes with {paths.STUDY_VERSION}: {how}.'


def needed_step(link, installed: bool) -> str | None:
    """The one thing to do, or None, from what is known of the study rather than from its state's name.

    An older study is updated first. A study heard from within the last minute needs nothing else. Otherwise: not
    installed (its jar is not in MotiveWave's Extensions folder and it has never reported in), install it; never
    heard from, add it to a chart; heard from before, open MotiveWave with it on the chart."""
    if link is None:
        return None
    if common.study_outdated(link):
        return 'update'
    if common.recent(link.heard):
        return None
    if not installed:
        return 'install'
    return 'chart' if link.heard is None else 'open'


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
    """The Settings page that shows a view, by any name it has had; anything else opens the first page."""
    page = common.SETTINGS_ROUTES.get(view, (view, None))[0] if view else HOME
    return page if page in PAGES else HOME


def open_page(page: str, tab: str | None = None):
    """An on_click handler that shows one Settings page, at one of its tabs."""
    return lambda: st.session_state.update({common.TOOL_KEY: page, common.TAB_KEY: tab})


def render(journal, rows: list[dict], *, tool: PageRenderer | None = None,
           display: DisplayRenderer | None = None) -> None:
    """The first page, or another page under a button back to it. ``tool(name)`` draws the others."""
    page = page_for(st.session_state.get(common.TOOL_KEY))
    st.session_state[common.TOOL_KEY] = page
    if page != HOME and tool is not None:
        st.button('← Settings', key='setup-back', type='tertiary', on_click=open_page(HOME),
                  help='Back to the study status, Time Zone, MAE / MFE Units and the other pages.')
        tool(page)
        return
    home(journal, rows, display=display, links=tool is not None)


def home(journal, rows: list[dict], *, display: DisplayRenderer | None = None, links: bool = True) -> None:
    capture = getattr(journal, 'capture_journal', journal)
    upgrade_notes([journal] + ([capture] if capture is not journal else []))
    link = live_status(capture)
    step(link)
    if display is None:
        from views import preferences
        display = preferences.render
    display(journal, rows)
    if links:
        pages()
    about(link)


def upgrade_notes(journals) -> None:
    """What opening an older journal did once (Journal.upgrade_note), said once: one quiet line of the sentences not
    seen yet, and OK, which marks them seen. Nothing when there is nothing new to say."""
    unseen = [(journal, journal.unseen_notes()) for journal in journals]
    said = [note for _, notes in unseen for note in notes.values()]
    if not said:
        return
    with st.container(horizontal=True, vertical_alignment='center', gap='small', key='setup-upgrade-notes'):
        st.caption(' '.join(said))
        if st.button('OK', type='tertiary', key='setup-upgrade-notes-ok'):
            for journal, notes in unseen:
                journal.notes_seen(notes)
            st.rerun()


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
    # The step under the line is drawn with the whole page. When the study calls for another step (it starts
    # recording, goes quiet, or is installed), the page is drawn again so the step follows.
    if needed_step(link, study_installed()) != st.session_state.get(f'{DRAWN}-{dataset}'):
        st.rerun()


def headline(link, contracts=()) -> str:
    """'Recording · NQZ6, ESZ6 · study 0.3.10' while the study records; else the state's own word."""
    if link.state != common.RECORDING:
        return link.label
    shown = list(contracts[:4]) + ([f'+{len(contracts) - 4} more'] if len(contracts) > 4 else [])
    return ' · '.join([link.state, *([', '.join(shown)] if shown else []),
                       *([f'study {link.version}'] if link.version else [])])


def render_status(journal):
    """The state in one line, and under it what to do when it needs attention. No bridge path or raw error."""
    link = common.study_link(journal)[3]
    try:
        contracts = journal.recent_contracts() if link.state == common.RECORDING else []
    except (AttributeError, TypeError, ValueError, OSError):
        contracts = []
    notes = [(STATUS_COPY[common.ATTENTION], True)] if link.state == common.ATTENTION else []
    named = (CONTRACTS_REPLAY if getattr(journal, 'dataset', '') == 'replay' else CONTRACTS) if contracts else ''
    # Always one element: theme.status explains why a redrawn fragment must not change its element count.
    theme.status(headline(link, contracts), link.tone, link.state, big=True, notes=notes,
                 help=' '.join(part for part in (STATUS_COPY.get(link.state, ''), named, FLAT_TO_FLAT) if part))
    return link


def step(link) -> None:
    """The one thing to do, in one line under the status; nothing when the study is recording."""
    which = needed_step(link, study_installed())
    if which is None:
        return
    line, explained = STEPS[which]
    if which == 'update':
        line = update_line(link)
    elif which == 'install' and not connection.JAR.is_file():
        line = ('In MotiveWave choose **Study → All Studies → Import** and pick motivewave-journal.jar, then add '
                'the study to your chart.')
    with st.container(key='setup-step-' + which):
        st.markdown(line, help=explained)
        if which in ('install', 'update') and connection.JAR.is_file() and not connection.bundled_study_installed():
            if st.button(INSTALL, type='primary', key='simple-study-install'):
                try:
                    connection.install_study()
                except (OSError, ValueError) as exc:
                    st.error('The study could not be installed: ' + str(exc))
                else:
                    # Drawn again at once, so the line says what is left and the button goes: never 'click
                    # Install' beside a study that is installed.
                    st.rerun()


def pages() -> None:
    """Tags, Playbooks & Checklists; Data: a tile each, with a link to each of its tabs."""
    theme.section('More Settings')
    for column, page in zip(st.columns(len(PAGES) - 1), PAGES[1:]):
        with column.container(border=True, height='stretch', key='setup-page-' + page.lower()):
            st.button(LABELS[page] + ' ›', key='setup-open-' + page, type='tertiary', on_click=open_page(page),
                      help=PAGE_HELP[page])
            with st.container(horizontal=True, gap='small'):
                for tab in TABS[page]:
                    st.button(tab, key=f'setup-tab-{page}-{tab}', type='tertiary', on_click=open_page(page, tab))


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
