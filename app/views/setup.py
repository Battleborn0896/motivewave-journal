"""Setup: the study's state, three steps to start capturing, the display settings, and every other
setting or tool one click away.

The first page is what a first-time user needs, top to bottom: one line that says whether the study is
connected and what to do, the three steps, how times and excursions are shown, then buttons for every
other page in three titled groups. It holds no bridge paths or raw errors; those are on the Connection
page. The other pages are drawn by the parent through ``tool``, under a button back to this one.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import streamlit as st

import common
import theme
from mwjournal import paths
from views import connection

PageRenderer = Callable[[str], None]
DisplayRenderer = Callable[[Any, list[dict]], None]

HOME = 'Setup'
LABELS = {HOME: 'Setup', 'Connection': 'Connection', 'Tags': 'Tags & Playbooks', 'Ratings': 'Rating Systems',
          'Checklists': 'Checklists', 'Dashboard': 'Dashboards', 'Daily review': 'Daily Report Card',
          'Live session': 'Session Tools', 'Journals': 'Journals & Accounts', 'Import': 'Import Trades',
          'Export': 'Export & Backup'}
# The other pages, in three titled groups on the first page. Each button explains its page on hover.
GROUPS = (('Reviewing', ('Tags', 'Ratings', 'Checklists')),
          ('Reports', ('Dashboard', 'Daily review', 'Live session')),
          ('Data', ('Connection', 'Journals', 'Import', 'Export')))
PAGE_HELP = {
    'Tags': 'Rename, recolour, retire or delete tags and categories; build playbooks; day tags.',
    'Ratings': 'Weighted process ratings built from your tags, for dashboard widgets.',
    'Checklists': 'Branching checklists to run before or during a trade, and their history.',
    'Dashboard': 'Your own pages of charts, tables, calendars and summaries.',
    'Daily review': 'Grade one trading day and write down what to change next session.',
    'Live session': 'A check-in timer and pre-trade drafts that attach to the next trade.',
    'Connection': 'What the study last reported, capture on or off, another bridge file, multipliers.',
    'Journals': 'Named journals for chosen accounts, and display names for accounts.',
    'Import': 'Trades the study did not capture: a fills CSV, a bridge file, your own prices or one fill.',
    'Export': 'Download the whole journal as JSON, a trades CSV or a full backup.',
}
PAGES = (HOME, *(page for _, pages in GROUPS for page in pages))
FOLDED = {'General': HOME}        # display preferences are part of the first page now

# One line each: what the state means and what to do about it. Never a path or a raw error.
STATUS_COPY = {
    'Connected': 'MotiveWave is sending data. Trade as usual: each trade appears in Trades by itself.',
    'Waiting For Study': 'Waiting for the MotiveWave Journal study. Follow the three steps below; this turns green by itself.',
    'Stale': 'The study has gone quiet. Check that MotiveWave is open and the study is still on your chart.',
    'No Heartbeat': 'Prices arrive but the study is not reporting in. Switch on Capture Fills in the study settings.',
    'Paused': 'Capture is switched off. Switch it back on under Data → Connection.',
    'Needs Attention': 'Capture needs attention. Open Data → Connection to see what the study reported.',
}
FLAT_TO_FLAT = ('One trade runs flat to flat for one provider, account and exact contract. The first fill that moves '
                'the position away from zero opens it. Scale-ins and partial exits stay inside that same trade. The '
                'fill that returns the position to zero closes it. A reversal closes the old trade at the crossing '
                'fill and opens a new one; other accounts and contracts stay separate. Replay works the same way and '
                'is kept in the separate Replay journal. Its MAE / MFE is tick-accurate only when the replay itself '
                'runs from real tick data.')


def page_for(view: str | None) -> str:
    """The Setup page that shows a view: a page that was folded into the first one opens that one."""
    return FOLDED.get(view, view if view in PAGES else HOME)


def open_page(page: str):
    """An on_click handler that shows one Setup page."""
    return lambda: st.session_state.update({common.TOOL_KEY: page})


def render(journal, rows: list[dict], *, tool: PageRenderer | None = None,
           display: DisplayRenderer | None = None) -> None:
    """The first page, or another page under a button back to it. ``tool(name)`` draws the others."""
    page = page_for(st.session_state.get(common.TOOL_KEY))
    st.session_state[common.TOOL_KEY] = page
    if page != HOME and tool is not None:
        st.button('← Setup', key='setup-back', type='tertiary', on_click=open_page(HOME),
                  help='Back to the study status, the three steps and the display settings.')
        tool(page)
        return
    home(journal, rows, display=display, links=tool is not None)


def home(journal, rows: list[dict], *, display: DisplayRenderer | None = None, links: bool = True) -> None:
    capture = getattr(journal, 'capture_journal', journal)
    link = live_status(capture)
    steps(link)
    theme.section('Display')
    if display is None:
        from views import preferences
        display = preferences.render
    display(journal, rows)
    if links:
        more()
    about()


def live_status(journal):
    """The status line, redrawn every few seconds so starting the study turns it green without a click."""
    dataset = getattr(journal, 'dataset', None)
    if dataset in paths.DATASETS:
        status_fragment(dataset)
        return common.study_link(journal)[3]
    return render_status(journal)


@st.fragment(run_every='5s')
def status_fragment(dataset: str) -> None:
    journal = common.open_journal(dataset)
    if journal is not None:
        render_status(journal)


def render_status(journal):
    """The state in one word and one line saying what to do, without a bridge path or a raw error."""
    name, _, _, link = common.study_link(journal)
    note = STATUS_COPY.get(link.state, 'Waiting for the MotiveWave study to report its capture status.')
    notes = [(note, link.tone == 'critical')]
    try:
        others = common.other_problems(journal, name)
    except (AttributeError, TypeError, KeyError):
        others = 0
    # Always one element: theme.status explains why a redrawn fragment must not change its element count.
    theme.status(link.label, link.tone, link.state, big=True, notes=notes + [
        (f'{others} other watched file{"s" if others > 1 else ""} could not be read. See Data → Connection.', True)]
        * bool(others))
    return link


def steps(link) -> None:
    # Steps 1 and 2 are done once the study has ever reported in, even if it has gone quiet since.
    connected = link is not None and (link.state == 'Connected' or getattr(link, 'heard', None) is not None)
    theme.section('Get Started')
    one, two, three = st.columns(3)
    with one.container(border=True, height='stretch'):
        st.markdown('**1 · Install the Study**  \nAdds MotiveWave Journal to MotiveWave.',
                    help='Copies the study into your MotiveWave Extensions folder and keeps the previous copy. '
                         'By hand instead: in MotiveWave choose Study → All Studies → Import and pick '
                         'motivewave-journal.jar. To install it again after an update, use Data → Connection.')
        if connected:
            st.markdown(':green[✓ Done]')
        elif connection.JAR.is_file():
            if st.button('Install MotiveWave Study', type='primary', key='simple-study-install'):
                try:
                    connection.install_study()
                    st.success('Installed. Restart MotiveWave, then do step 2.')
                except (OSError, ValueError) as exc:
                    st.error('The study could not be installed: ' + str(exc))
        else:
            st.markdown('In MotiveWave choose **Study → All Studies → Import**.')
    with two.container(border=True, height='stretch'):
        st.markdown('**2 · Add It to Your Chart**  \nRestart MotiveWave, then add **Journal → MotiveWave Journal** '
                    'to the chart of each contract you trade.',
                    help='Use the exact contract, such as ESZ6, not a continuous contract. Leave the study\'s File '
                         'setting as it is: the journal already watches that file. Live and paper trades go to the '
                         'Live journal, replay trades to the Replay journal.')
        if connected:
            st.markdown(':green[✓ Done]')
    with three.container(border=True, height='stretch'):
        st.markdown('**3 · Trade as Usual**  \nEach trade appears in **Trades** by itself once your position is flat.',
                    help=FLAT_TO_FLAT)


def more() -> None:
    """Every other page, one button each, in three titled groups."""
    theme.section('More Settings')
    for column, (title, pages) in zip(st.columns(len(GROUPS)), GROUPS):
        with column.container(border=True, height='stretch', key='setup-group-' + title.lower()):
            st.markdown(f'**{title}**')
            for page in pages:
                st.button(LABELS[page], key='setup-open-' + page, on_click=open_page(page), width='stretch',
                          help=PAGE_HELP[page])


def about() -> None:
    theme.section('About')
    st.caption(f'{paths.APP_NAME} {paths.VERSION}',
               help='The version to quote in a bug report. The study\'s own version is beside Connected at the '
                    'top of this page.')
    st.caption(f'Your data folder: {paths.data_home()}',
               help='Trades, reviews and settings stay in this folder on this computer. Export & Backup makes a copy.')
    st.caption(common.CREDIT)
    st.caption('MotiveWave Journal is an independent open-source project, not affiliated with or endorsed by '
               'MotiveWave Software.')
