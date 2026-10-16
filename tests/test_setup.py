"""Zero setup: one data home shared with the study, a default connection, and the headless entry point."""
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from mwjournal import Journal, paths, sync
from mwjournal.sync import DEFAULT_CONNECTION, ensure_default_connection
from test_capture import fill, lines, tick


def at_home(monkeypatch, folder):
    monkeypatch.setattr(Path, 'home', classmethod(lambda cls: folder))


def test_data_home_layout_is_the_one_the_study_uses(tmp_path, monkeypatch):
    monkeypatch.setenv('MWJ_HOME', str(tmp_path))
    assert paths.data_home() == tmp_path
    assert paths.database() == tmp_path / 'trades.sqlite3'
    assert paths.database('replay') == tmp_path / 'replay' / 'trades.sqlite3'
    assert paths.bridge_file() == tmp_path / 'bridge' / 'motivewave.ndjson'
    assert paths.bridge_file('replay') == tmp_path / 'bridge' / 'motivewave.replay.ndjson'
    # The study's replay naming rule also applies to a bridge file the user placed elsewhere.
    assert paths.replay_bridge('/x/custom.ndjson') == Path('/x/custom.replay.ndjson')
    assert paths.replay_bridge('/x/custom.log') == Path('/x/custom.log.replay.ndjson')
    for call in (paths.database, paths.bridge_file):
        with pytest.raises(ValueError):
            call('paper')


def test_default_data_home_per_platform_and_blank_override_is_ignored(tmp_path, monkeypatch):
    monkeypatch.setenv('MWJ_HOME', '   ')
    at_home(monkeypatch, tmp_path)
    monkeypatch.setattr(paths, 'sys', SimpleNamespace(platform='darwin'))
    assert paths.data_home() == tmp_path / 'Library' / 'Application Support' / 'MotiveWave Journal'
    monkeypatch.setattr(paths, 'sys', SimpleNamespace(platform='linux'))
    assert paths.data_home() == tmp_path / '.local' / 'share' / 'motivewave-journal'
    monkeypatch.setattr(paths, 'sys', SimpleNamespace(platform='win32'))
    monkeypatch.setenv('LOCALAPPDATA', str(tmp_path / 'Local'))
    assert paths.data_home() == tmp_path / 'Local' / 'MotiveWave Journal'
    monkeypatch.delenv('LOCALAPPDATA')
    assert paths.data_home() == tmp_path / 'AppData' / 'Local' / 'MotiveWave Journal'
    monkeypatch.delenv('MWJ_HOME')
    assert paths.chosen_home() is None


def test_environment_path_whitespace_matches_the_java_study(tmp_path, monkeypatch):
    monkeypatch.setenv('MWJ_HOME', '  ' + str(tmp_path) + '  ')
    assert paths.chosen_home() == tmp_path and paths.data_home() == tmp_path
    monkeypatch.setenv('MWJ_HOME', '  ')
    monkeypatch.setattr(paths, 'sys', SimpleNamespace(platform='win32'))
    monkeypatch.setenv('LOCALAPPDATA', '  ' + str(tmp_path / 'Local') + '  ')
    assert paths.data_home() == tmp_path / 'Local' / 'MotiveWave Journal'


@pytest.mark.skipif(os.name == 'nt', reason='POSIX permission bits')
def test_journal_files_are_private_but_a_chosen_folder_keeps_its_permissions(tmp_path, monkeypatch):
    def mode(path):
        return path.stat().st_mode & 0o777
    chosen = tmp_path / 'chosen'
    chosen.mkdir()
    chosen.chmod(0o755)
    monkeypatch.setenv('MWJ_HOME', str(chosen))
    live, replay = Journal(), Journal(dataset='replay')
    assert live.path == paths.database() and replay.path == paths.database('replay')
    assert mode(live.path) == 0o600 and mode(replay.path.parent) == 0o700 and mode(chosen) == 0o755
    # The study can run first and make the default folder with ordinary permissions.
    monkeypatch.delenv('MWJ_HOME')
    at_home(monkeypatch, tmp_path)
    paths.bridge_file().parent.mkdir(parents=True)
    paths.data_home().chmod(0o755)
    Journal()
    assert mode(paths.data_home()) == 0o700


def test_default_connection_is_created_once_per_environment(tmp_path, monkeypatch):
    monkeypatch.setenv('MWJ_HOME', str(tmp_path))
    for dataset in ('live', 'replay'):
        journal = Journal(dataset=dataset)
        assert ensure_default_connection(journal) is True
        assert journal.get('profiles') == {'MotiveWave': {
            'path': str(paths.bridge_file(dataset)), 'mode': 'bridge', 'enabled': True}}
        assert ensure_default_connection(journal) is False
        assert list(journal.get('profiles')) == [DEFAULT_CONNECTION]


def test_default_connection_never_undoes_what_the_user_set(tmp_path, monkeypatch):
    monkeypatch.setenv('MWJ_HOME', str(tmp_path))
    journal = Journal()
    ensure_default_connection(journal)
    disabled = {**journal.get('profiles')['MotiveWave'], 'enabled': False}
    journal.put('profiles', 'MotiveWave', disabled)
    assert not ensure_default_connection(journal) and journal.get('profiles') == {'MotiveWave': disabled}
    # The same file under the user's own name and another spelling of the path: no second connection.
    journal.remove_profile('MotiveWave')
    spellings = [str(tmp_path / 'bridge' / '..' / 'bridge' / 'motivewave.ndjson')]
    if os.name != 'nt':
        (tmp_path / 'link').symlink_to(tmp_path, target_is_directory=True)
        spellings.append(str(tmp_path / 'link' / 'bridge' / 'motivewave.ndjson'))
    for spelling in spellings:
        own = {'path': spelling, 'mode': 'bridge', 'enabled': False}
        journal.put('profiles', 'my capture', own)
        assert not ensure_default_connection(journal) and journal.get('profiles') == {'my capture': own}
    # A 'MotiveWave' connection the user pointed at another file is theirs, and is not overwritten.
    journal.remove_profile('my capture')
    moved = {'path': str(tmp_path / 'elsewhere.ndjson'), 'mode': 'bridge', 'enabled': True}
    journal.put('profiles', 'MotiveWave', moved)
    assert not ensure_default_connection(journal) and journal.get('profiles') == {'MotiveWave': moved}


def test_default_connection_recognises_the_bridge_once_the_file_exists(tmp_path, monkeypatch):
    monkeypatch.setenv('MWJ_HOME', str(tmp_path))
    journal = Journal()
    bridge = paths.bridge_file()
    bridge.parent.mkdir()
    bridge.write_text('')
    other = tmp_path / 'other.ndjson'
    os.link(bridge, other)                       # a second name for the very same file
    journal.put('profiles', 'by another name', {'path': str(other), 'mode': 'bridge', 'enabled': True})
    assert not ensure_default_connection(journal) and list(journal.get('profiles')) == ['by another name']


def test_headless_entry_point_connects_and_captures_with_no_setup(tmp_path, monkeypatch):
    monkeypatch.setenv('MWJ_HOME', str(tmp_path))
    bridge = paths.bridge_file('replay')
    bridge.parent.mkdir(parents=True)
    bridge.write_text(lines([fill('a'), tick(5001), fill('b', 'sell', 5002, ts='2022-01-03T14:32:00Z')]))
    sync.main(['--once', '--dataset', 'replay'])
    replay = Journal(dataset='replay')
    assert replay.get('profiles')['MotiveWave']['enabled'] and len(replay.trades()) == 1
    assert replay.best_metric(replay.trades()[0])['precision'] == 'observed-ticks'
    assert replay.get('sync_status')['MotiveWave']['status'] == 'synced'
    # A second run finds the connection already there and nothing new in the file.
    sync.main(['--once', '--dataset', 'replay'])
    assert list(replay.get('profiles')) == ['MotiveWave'] and len(replay.executions()) == 2
    assert not Journal().executions()
