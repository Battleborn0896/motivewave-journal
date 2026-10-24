"""Connection, on the Data page: the study writes a file on this computer and the app reads it. Nothing has to be
typed.

The app starts reading the study's bridge file by itself (runtime.start), and always reads it. This tab says what it
hears from the study (its state, when it was last heard from, the fills in this journal) and installs the study
again. What helps when something is wrong waits under a closed Troubleshooting: the file being read, the data folder,
the journal's database, the study's own messages and the ticks it reported dropped. The first page of Settings shows
the one step that is missing before the study records (views/setup.py).
"""

from __future__ import annotations

import datetime as dt
import os
from pathlib import Path
import shutil

import pandas as pd
import streamlit as st

import common
import theme
from mwjournal import paths

JAR = Path(__file__).resolve().parents[2] / 'study' / 'build' / 'libs' / 'motivewave-journal.jar'


def study_extension_folder() -> Path:
    configured = os.environ.get('MOTIVEWAVE_EXTENSIONS', '').strip()
    return Path(configured).expanduser() if configured else Path.home() / 'MotiveWave Extensions'


def study_installed() -> bool:
    """Whether MotiveWave's Extensions folder holds the study. Only looked at, never changed."""
    try:
        return (study_extension_folder() / JAR.name).is_file()
    except OSError:
        return False


def bundled_study_installed() -> bool:
    """Whether the Extensions folder holds the very study this app comes with, not an older copy. Only looked at."""
    try:
        return JAR.is_file() and (study_extension_folder() / JAR.name).read_bytes() == JAR.read_bytes()
    except OSError:
        return False


def install_study(jar: Path = JAR, destination: Path | None = None) -> tuple[Path, Path | None]:
    """Atomically install only this app's JAR; preserve the prior copy for easy recovery."""
    jar = Path(jar)
    if not jar.is_file() or jar.name != 'motivewave-journal.jar':
        raise ValueError('The packaged MotiveWave Journal study is missing.')
    folder = Path(destination) if destination is not None else study_extension_folder()
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / jar.name
    temporary = folder / ('.' + jar.name + '.new')
    previous = folder / (jar.name + '.previous')
    backup = None
    try:
        if target.is_file():
            shutil.copy2(target, previous)
            backup = previous
        shutil.copy2(jar, temporary)
        os.replace(temporary, target)
        (folder / '.last_updated').touch()
    except OSError:
        temporary.unlink(missing_ok=True)
        raise
    return target, backup


def render(journal, rows) -> None:
    theme.section('Connection')
    status_card(journal.dataset)
    study_section()
    with st.expander('Troubleshooting'):
        troubleshooting(journal)


def study_section() -> None:
    """Installing the study again, whatever its state: Settings offers the install only while the study is
    missing or older than the one this app bundles, so this is where any other install lives."""
    theme.section('Study')
    if not JAR.is_file():
        st.caption('In MotiveWave choose Study → All Studies → Import and pick motivewave-journal.jar.')
        return
    if st.button('Install MotiveWave Study', key='connection-study-install',
                 help='Copies the study that came with this app into your MotiveWave Extensions folder, keeping the '
                      'copy already there as a backup.'):
        try:
            _, backup = install_study()
            st.success('Installed. If Settings still names an older study in a few seconds, quit MotiveWave completely '
                       'and open it again.'
                       + (' The previous copy was kept as a backup.' if backup else ''))
        except (OSError, ValueError) as exc:
            st.error('The study could not be installed: ' + str(exc))


@st.fragment(run_every='5s')
def status_card(dataset: str) -> None:
    """Redrawn every few seconds, so starting the study turns this to Recording without a click."""
    journal = common.open_journal(dataset)
    if journal is None:
        return
    link = common.study_link(journal)[3]
    # One element, whatever there is to say: see theme.status for why a fragment must not vary its count.
    theme.status(link.label, link.tone, link.state, big=True, notes=[(link.detail, False)])
    now = dt.datetime.now(dt.timezone.utc)
    c1, c2 = st.columns(2)
    c1.metric('Last Heard From Study', common.ago((now - link.heard).total_seconds()) if link.heard else '—')
    c2.metric('Fills in This Journal', f'{journal.execution_count():,}')


def troubleshooting(journal) -> None:
    """The file being read, the data folder and the journal's database, what the study said, and the ticks it
    reported dropped."""
    _, profile, status, _ = common.study_link(journal)
    st.caption('File being read')
    st.code(str((profile or {}).get('path', '')) or 'no file', language=None)
    st.caption(f'Data folder, with your trades, reviews and settings: {paths.data_home()}')
    st.caption(f'Journal database: {journal.path}')
    theme.notes(study_messages(status))
    gaps = journal.capture_gaps()
    if gaps:
        st.warning('The study reported dropped ticks: the trades they fall in show no MAE / MFE, and say why.')
        st.dataframe(gap_table(gaps[-20:], common.display_zone(journal)), hide_index=True, width='stretch')


def study_messages(status: dict) -> list[tuple[str, bool]]:
    """What the study said in its last report, and the lines of its file that were skipped, as (text, is_warning)."""
    heartbeat = status.get('heartbeat') or {}
    said = ' · '.join(str(v) for v in (heartbeat.get('detail'), heartbeat.get('workspace')
                                       and f"workspace {heartbeat['workspace']}") if v)
    damaged, unknown, warned = status.get('damaged_lines'), status.get('unknown_events'), status.get('warnings')
    return ([('The study says: ' + said, False)] * bool(said and not status.get('error'))
            # What the study skipped or could not link while it records: fills it could not read, or a working
            # stop no position takes (MotiveWave's log says why, one line per stop). Not a warning: on a prop
            # account 'fills with an assumed currency' is said at every heartbeat, and nothing is wrong.
            + [(f'The study also says: {warned}.', False)] * bool(warned and not status.get('error'))
            + [(f"{damaged} damaged lines in the study's file were skipped. MotiveWave closing in the middle of a "
                'write leaves one; the events around it are unaffected.', True)] * bool(damaged)
            + [(f'Events of a kind this version does not read, skipped: {unknown}. A newer study wrote them; fills, '
                'stops and prices are unaffected.', False)] * bool(unknown)
            or [('No other messages from the study.', False)])


def gap_table(gaps: list[dict], zone: str) -> pd.DataFrame:
    """The study's dropped-tick reports as a person reads them: when, which contract, how many."""
    def when(value):
        try:
            return common.moment(value, zone) if value else '—'
        except (ValueError, TypeError):
            return str(value)
    return pd.DataFrame([{'When': when(g.get('ts')), 'Contract': g.get('symbol') or 'All',
                          'Ticks Dropped': g.get('dropped', '—'), 'Source': g.get('provider', '')} for g in gaps])
