"""Where MotiveWave Journal keeps a person's files. The capture study follows the same rule, so
the study and the app meet at one bridge file without either being configured.

    macOS    ~/Library/Application Support/MotiveWave Journal
    Windows  %LOCALAPPDATA%\\MotiveWave Journal
    other    ~/.local/share/motivewave-journal

MWJ_HOME overrides the folder. A desktop MotiveWave does not inherit a terminal's environment,
so set it for both programs or for neither.

    <home>/trades.sqlite3                   live journal; its screenshots sit beside it
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
VERSION = '0.4.15'
# The study this app bundles (JournalPaths.CONNECTOR_VERSION; tests/test_version.py checks). Setup asks a
# user whose study reports an older version to install this one: studies before 0.3.3 receive no live or
# replay ticks, before 0.3.4 a replay's prints are labelled simulated unless Real Tick Data is ticked, and
# before 0.3.6 no stop order is ever recorded (each order's instrument was read as the wrong type), so trades
# have no R unless the stop is typed, and before 0.3.8 a chart records every print all day by default (about
# 540,000 an NQ day); 0.3.8 records each trade's and loads the minutes after its exit from MotiveWave's history.
# From 0.3.6 on a new study takes over from the old one without a restart.
STUDY_VERSION = '0.3.9'
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


def database(dataset: str = 'live') -> Path:
    home = data_home()
    return (home / 'replay' if _checked(dataset) == 'replay' else home) / 'trades.sqlite3'
