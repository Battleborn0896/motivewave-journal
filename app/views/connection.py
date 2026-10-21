"""Connection, on the Data page: the study writes a file on this computer and the app reads it. Nothing has to be
typed.

The app starts watching the study's default bridge file by itself (runtime.start). This tab says what it
currently hears from the study, in detail, and holds the less common settings in titled sections: installing
the study again, another bridge file, extra watched CSV files, the instrument multiplier, and under Advanced
whether the app reads trades from MotiveWave at all. Setup's first page shows the one step that is missing
before the study records (views/setup.py).
"""

from __future__ import annotations

import datetime as dt
import html
import os
from pathlib import Path
import shutil

import pandas as pd
import streamlit as st

import common
import theme
from mwjournal import paths
from mwjournal.model import instant
from mwjournal.sync import DEFAULT_CONNECTION

JAR = Path(__file__).resolve().parents[2] / 'study' / 'build' / 'libs' / 'motivewave-journal.jar'
READ_TRADES = 'Read Trades From MotiveWave'
FORMATS = {'executions': 'Execution CSV', 'prices': 'Prices CSV', 'bridge': 'Another bridge file · NDJSON'}
RESERVED = (DEFAULT_CONNECTION, 'worker')      # 'worker' is the status row of the background reader itself


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


LIMITS = ('The study is read-only: it never places, changes or cancels an order. Fills are read from MotiveWave '
          'internals that are not part of its supported SDK, so a MotiveWave update can stop fill capture; the '
          'status above then shows the study\'s own message, and prices keep recording either way. A stop order '
          'counts when it is on your position\'s account and contract and on its closing side (a sell stop for a '
          'long, a buy stop for a short); take-profit orders are not read. Fees can be the platform\'s estimates; such trades are flagged. Prices are recorded per chart: put '
          'the study on the chart you trade, contract or continuous; prices go with trades of the same product, '
          'whatever the contract month, and one study reads the account for all of them. Replay prices are '
          'recorded as they play, and rewinding starts a new run. They count as real ticks unless the study\'s '
          'Real Tick Data box is switched off, for a replay generated from bars. Once a replay trade is flat, the study '
          'also loads the real ticks of its holding time from MotiveWave\'s history, up to the replay\'s current '
          'time and no further; those count as real ticks of that run either way. Studies before 0.3.3 recorded no '
          'live or replay ticks, before 0.3.4 labelled replay prints simulated, and before 0.3.6 recorded no stop '
          'order at all: install the study again from this page. From 0.3.6 on, a new study takes over from the old '
          'one in a few seconds; an older one stops recording prices until MotiveWave is quit and opened again.')


def render(journal, rows) -> None:
    theme.section('Connection')
    status_card(journal.dataset)
    name, profile, _, _ = common.study_link(journal)
    st.caption('What the study can and cannot see', help=LIMITS)
    gaps = journal.capture_gaps()
    if gaps:
        st.warning('The study reported dropped ticks. Excursions for the affected windows carry a warning.')
        st.dataframe(gap_table(gaps[-20:], common.display_zone(journal)), hide_index=True, width='stretch')
    study_section()
    theme.section('Bridge File')
    bridge_file_form(journal)
    watched_files(journal, name)
    theme.section('Instrument Multiplier')
    common.multiplier_form(journal, 'multiplier')
    st.caption(f'Journal database: {journal.path}')
    if profile is not None:
        theme.subsection('Advanced')
        reading(journal, name, profile)


def reading(journal, name: str, profile: dict) -> None:
    """Whether the app reads the study's bridge file. On unless someone switched it off; the study writes
    either way."""
    enabled = st.toggle(READ_TRADES, value=bool(profile.get('enabled')), key=common.key(journal, 'capture-enabled'),
                        help='Off stops this journal reading the file the study writes, so no new trades or prices '
                             'appear. Nothing is lost: the study keeps writing, and reading carries on from the same '
                             'place when this is on again. Live and Replay each have their own switch.')
    if enabled != bool(profile.get('enabled')):
        journal.put('profiles', name, {**profile, 'enabled': enabled})
        st.rerun()


def study_section() -> None:
    """Installing the study again, whatever its state: Setup offers the install only while the study is
    missing or older than the one this app bundles, so this is where any other install lives."""
    theme.section('Study')
    if not JAR.is_file():
        st.caption('In MotiveWave choose Study → All Studies → Import and pick motivewave-journal.jar.')
        return
    if st.button('Install MotiveWave Study', key='connection-study-install',
                 help='Copies the study that came with this app into your MotiveWave Extensions folder. The copy '
                      'already there is kept beside it as a backup.'):
        try:
            _, backup = install_study()
            st.success('Installed. If Setup still names an older study in a few seconds, quit MotiveWave completely '
                       'and open it again.'
                       + (' The previous copy was kept as a backup.' if backup else ''))
        except (OSError, ValueError) as exc:
            st.error('The study could not be installed: ' + str(exc))


@st.fragment(run_every='5s')
def status_card(dataset: str) -> None:
    """Redrawn every few seconds, so starting the study turns this to Connected without a click."""
    journal = common.open_journal(dataset)
    if journal is None:
        return
    name, profile, status, link = common.study_link(journal)
    heartbeat = status.get('heartbeat') or {}
    said = ' · '.join(str(v) for v in (heartbeat.get('detail'), heartbeat.get('workspace')
                                       and f"workspace {heartbeat['workspace']}") if v)
    damaged, others = status.get('damaged_lines'), common.other_problems(journal, name)
    unknown, warned = status.get('unknown_events'), status.get('warnings')
    # One element, whatever there is to say: see theme.status for why a fragment must not vary its count.
    theme.status(link.label, link.tone, link.state, big=True, notes=(
        [(link.detail, False)]
        + [('The study says: ' + said, False)] * bool(said and not status.get('error'))
        # What the study skipped or could not link while capture works: fills it could not read, or a working
        # stop no position takes (MotiveWave's log says why, one line per stop). Not drawn as a warning: on a prop
        # account 'fills with an assumed currency' is said at every heartbeat, and nothing is wrong.
        + [(f'The study also says: {warned}.', False)] * bool(warned and not status.get('error'))
        + [(f'{damaged} damaged lines in the bridge file were skipped. MotiveWave closing in the middle of a '
            'write leaves one; the events around it are unaffected.', True)] * bool(damaged)
        + [(f'Events of a kind this version does not read, skipped: {unknown}. A newer study wrote them; fills, '
            'stops and prices are unaffected.', False)] * bool(unknown)
        + [(f'{others} other watched file{"s" if others > 1 else ""} could not be read. See Other Watched Files below.', True)]
        * bool(others)))
    now = dt.datetime.now(dt.timezone.utc)
    c1, c2, c3 = st.columns(3)
    c1.metric('Last Heard From Study', common.ago((now - link.heard).total_seconds()) if link.heard else '—')
    c2.metric('Last New Events', read_at(status, now))
    c3.metric('Fills in This Journal', f'{journal.execution_count():,}')
    st.caption('Watching this file')
    st.code(str((profile or {}).get('path', '')) or 'no file', language=None)


def gap_table(gaps: list[dict], zone: str) -> pd.DataFrame:
    """The study's dropped-tick reports as a person reads them: when, which contract, how many."""
    def when(value):
        try:
            return common.moment(value, zone) if value else '—'
        except (ValueError, TypeError):
            return str(value)
    return pd.DataFrame([{'When': when(g.get('ts')), 'Contract': g.get('symbol') or 'All',
                          'Ticks Dropped': g.get('dropped', '—'), 'Source': g.get('provider', '')} for g in gaps])


def read_at(status: dict, now: dt.datetime) -> str:
    try:
        return common.ago((now - instant(status['checked'])).total_seconds()) if status.get('status') == 'synced' else '—'
    except (KeyError, ValueError, TypeError, AttributeError):
        return '—'


# ---------------------------------------------------------------- advanced

def live_bridge() -> Path:
    """The live file the study connection reads. The study is given this one path; it derives the replay file."""
    journal = common.open_journal('live')
    _, profile = common.study_connection(journal) if journal is not None else (None, None)
    return Path(profile['path']) if profile and profile.get('path') else paths.bridge_file('live')


def watch(live_file: Path) -> str | None:
    """Point both environments at this live bridge file and the replay file beside it. An error, or None."""
    if not live_file.is_absolute():
        return 'Give the full path of the file, starting from the drive or the root folder.'
    if live_file.is_dir():
        return 'That is a folder. Name the file inside it, for example motivewave.ndjson.'
    if live_file.name.endswith('.replay.ndjson'):
        return 'That is a replay file. Enter the live file; the replay file is found beside it.'
    targets = []
    for dataset, file in (('live', live_file), ('replay', paths.replay_bridge(live_file))):
        journal = common.open_journal(dataset)
        if journal is None:
            return f'The {dataset} journal could not be opened. Neither connection was changed.'
        targets.append((journal, file))
    for journal, file in targets:
        name, profile = common.study_connection(journal)
        if profile is not None and os.path.normcase(str(profile.get('path'))) == os.path.normcase(str(file)):
            continue
        journal.put('profiles', name or DEFAULT_CONNECTION,
                    {**(profile or {'enabled': True}), 'path': str(file), 'mode': 'bridge'})
        journal.clear_status(name or DEFAULT_CONNECTION)       # what the other file said no longer applies
    return None


def bridge_file_form(journal) -> None:
    current = live_bridge()
    if paths.chosen_home() is not None:
        st.warning('This app was started with MWJ_HOME, so it does not use the standard folder. MotiveWave sees '
                   'MWJ_HOME only when it is started from the same environment. Otherwise copy this path into the '
                   'study\'s File setting:')
        st.code(str(paths.bridge_file('live')), language=None)
    with st.form(common.key(journal, 'bridge-file')):
        typed = st.text_input('Live bridge file · the full path from the study\'s File setting', str(current),
                              help='Only needed when the study\'s File setting was changed: both must name the same '
                                   f'file. The replay file is found beside it: {paths.replay_bridge(current).name}')
        c1, c2, _ = st.columns([1, 1, 2])
        chosen = c1.form_submit_button('Watch This File', type='primary')
        default = c2.form_submit_button('Use the Default File')
    if not (chosen or default):
        return
    if chosen and not typed.strip():
        st.error('Enter the full path of the bridge file.')
        return
    error = watch(paths.bridge_file('live') if default else Path(typed.strip()).expanduser())
    if error:
        st.error(error)
    else:
        st.rerun()


def watched_files(journal, study: str | None) -> None:
    theme.section('Other Watched Files')
    profiles, statuses = journal.get('profiles'), journal.get('sync_status')
    for name, profile in sorted(profiles.items()):
        if name == study:
            continue
        mode = profile.get('mode')
        c1, c2, c3 = st.columns([6, 2, 1])
        c1.markdown(f"**{html.escape(name)}** · {FORMATS.get(mode, html.escape(str(mode)))}", unsafe_allow_html=True)
        c1.caption(str(profile.get('path', '')))
        enabled = c2.toggle('Enabled', value=bool(profile.get('enabled')), key=common.key(journal, 'enabled-' + name))
        if enabled != bool(profile.get('enabled')):
            journal.put('profiles', name, {**profile, 'enabled': enabled})
            st.rerun()
        if c3.button('Remove', key=common.key(journal, 'remove-' + name)):
            journal.remove_profile(name)
            st.rerun()
        status = statuses.get(name, {})
        if status.get('error') and enabled:
            st.warning(status['error'])
        elif status.get('status') == 'synced':
            st.caption(f"Read {common.moment(status['checked'], common.display_zone(journal)) if status.get('checked') else 'just now'} · "
                       f"{status.get('executions', 0)} new executions, {status.get('prices', 0)} price updates")
    with st.expander('Watch Another File', expanded=False), st.form(common.key(journal, 'new-watched-file')):
        name = st.text_input('Name', placeholder='My broker export',
                             help='Optional: a CSV or bridge file another program keeps rewriting. The '
                                  'journal reads new lines from it the same way it reads the study.')
        path = st.text_input('File to watch · full path', placeholder='/Users/me/exports/fills.csv')
        mode = st.selectbox('Format', list(FORMATS), format_func=FORMATS.get)
        c1, c2 = st.columns(2)
        timezone = c1.text_input('Time zone of dates without an offset', 'America/New_York')
        provider = c2.text_input('Source identity', 'csv', help='Kept with every fill or price read from this file.')
        c1, c2, c3 = st.columns(3)
        symbol = c1.text_input('Exact contract · prices CSV only', 'ESZ6')
        seconds = c2.selectbox('Bar duration · prices CSV only', [60, 1, 0, 300], format_func=lambda n: 'Ticks' if n == 0 else f'{n} s')
        proxy = c3.checkbox('Prices are a proxy / continuous contract')
        remembered = st.session_state.get(common.MAPPING_KEY)
        mapping = st.checkbox('Execution CSV: use the column mapping from my last preview in Import', value=False)
        if not st.form_submit_button('Add This File'):
            return
        file = Path(path.strip()).expanduser()
        name = name.strip()
        if not name or name in profiles or name in RESERVED:
            st.error('Give the file a name that is not in use.')
        elif not path.strip() or not file.is_absolute() or (mode != 'bridge' and not file.is_file()):
            st.error('Give the full path of an existing file.')
        elif mapping and not remembered:
            st.error('Preview the CSV in Import first; its column mapping is remembered from there.')
        else:
            journal.put('profiles', name, {'path': str(file), 'mode': mode, 'timezone': timezone, 'provider': provider,
                                          'mapping': remembered if mapping else None, 'symbol': symbol.strip().upper(),
                                          'resolution_seconds': seconds, 'proxy': proxy, 'enabled': True})
            st.rerun()
