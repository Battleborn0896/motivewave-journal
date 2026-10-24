"""Where MotiveWave Journal keeps a person's files. The capture study follows the same rule, so
the study and the app meet at one bridge file without either being configured.

    macOS    ~/Library/Application Support/MotiveWave Journal
    Windows  %LOCALAPPDATA%\\MotiveWave Journal
    other    ~/.local/share/motivewave-journal

MWJ_HOME overrides the folder. A desktop MotiveWave does not inherit a terminal's environment,
so set it for both programs or for neither.

    <home>/trades.sqlite3                   live journal, which also keeps the tags and settings Replay shares;
                                            its screenshots sit beside it
    <home>/replay/trades.sqlite3            replay journal
    <home>/bridge/motivewave.ndjson         live events written by the study
    <home>/bridge/motivewave.replay.ndjson  replay events
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

APP_NAME = 'MotiveWave Journal'
# The app's version. pyproject.toml, desktop/macos/Info.plist and desktop/macos/Main.swift say the same;
# tests/test_version.py checks. The study has its own version (study/.../JournalPaths.java).
VERSION = '0.4.17'
# The study this app bundles; Settings asks an older one to update, as older studies miss ticks, stops or runners.
STUDY_VERSION = '0.3.10'
STUDY_CONNECTOR = 'motivewave-journal-study'
ENV = 'MWJ_HOME'
DATASETS = ('live', 'replay')


def chosen_home() -> Path | None:
    """The folder named by MWJ_HOME, or None when the per-user default applies."""
    value = os.environ.get(ENV, '')
    return Path(value.strip()) if value.strip() else None


def data_home() -> Path:
    chosen = chosen_home()
    if chosen is not None:
        return chosen
    if sys.platform == 'darwin':
        return Path.home() / 'Library' / 'Application Support' / APP_NAME
    if sys.platform == 'win32':
        local = os.environ.get('LOCALAPPDATA', '')
        return (Path(local.strip()) if local.strip() else Path.home() / 'AppData' / 'Local') / APP_NAME
    return Path.home() / '.local' / 'share' / 'motivewave-journal'


def _checked(dataset: str) -> str:
    if dataset not in DATASETS:
        raise ValueError('Journal dataset must be live or replay.')
    return dataset


def replay_bridge(live_file) -> Path:
    """The study's naming rule for the replay file that sits beside any live bridge file."""
    live_file = Path(live_file)
    name = live_file.name
    return live_file.with_name((name[:-len('.ndjson')] if name.endswith('.ndjson') else name) + '.replay.ndjson')


def bridge_file(dataset: str = 'live') -> Path:
    live = data_home() / 'bridge' / 'motivewave.ndjson'
    return replay_bridge(live) if _checked(dataset) == 'replay' else live


def replay_beside(live) -> Path:
    """The Replay journal that shares the tags and settings of the Live journal at `live`: trades.sqlite3 in a
    replay folder beside it."""
    return Path(live).parent / 'replay' / 'trades.sqlite3'


def database(dataset: str = 'live') -> Path:
    live = data_home() / 'trades.sqlite3'
    return replay_beside(live) if _checked(dataset) == 'replay' else live
