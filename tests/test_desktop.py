"""Desktop startup/lifecycle tests: synthetic services only, no account or screen capture."""
from pathlib import Path
import subprocess
from unittest.mock import Mock
import urllib.error

import pytest

from mwjournal import desktop


def test_reuses_only_identified_journal_and_never_stops_existing_server(monkeypatch, tmp_path):
    monkeypatch.setattr(desktop, 'http_bytes', lambda url: b'ok' if url.endswith('/health') else desktop.APP_MARKER)
    popen = Mock(side_effect=AssertionError('Must not start another backend'))
    monkeypatch.setattr(desktop.subprocess, 'Popen', popen)
    backend = desktop.Backend(tmp_path, Path('/missing-python'))
    assert backend.start() == 'http://127.0.0.1:8600/'
    assert not backend.owned
    backend.stop()
    popen.assert_not_called()


@pytest.mark.parametrize('health,component', [(b'ok', b'another Streamlit component'),
                                              (b'not ok', desktop.APP_MARKER)])
def test_unrelated_service_is_not_accepted_as_journal(monkeypatch, health, component):
    monkeypatch.setattr(desktop, 'http_bytes', lambda url: health if url.endswith('/health') else component)
    assert not desktop.is_journal('http://127.0.0.1:8600/')


def test_closed_port_is_not_treated_as_connected(monkeypatch):
    def unavailable(_url):
        raise urllib.error.URLError('synthetic service unavailable')
    monkeypatch.setattr(desktop, 'http_bytes', unavailable)
    assert not desktop.is_journal('http://127.0.0.1:8600/')


def test_new_backend_is_local_headless_preserves_runtime_and_only_stops_owned_child(monkeypatch, tmp_path):
    project = tmp_path / 'bundled journal'
    (project / 'app').mkdir(parents=True)
    (project / 'app' / 'app.py').touch()
    python = tmp_path / 'virtual env' / 'bin' / 'python'
    python.parent.mkdir(parents=True)
    python.touch()
    python.chmod(0o700)
    process = Mock()
    process.poll.return_value = None
    popen = Mock(return_value=process)
    monkeypatch.setattr(desktop, 'is_journal', lambda _: False)
    monkeypatch.setattr(desktop, 'free_port', lambda _: 9123)
    monkeypatch.setattr(desktop, 'http_bytes', lambda _: b'ok')
    monkeypatch.setattr(desktop.subprocess, 'Popen', popen)
    backend = desktop.Backend(project, python, log=tmp_path / 'desktop' / 'backend.log')
    assert backend.start() == 'http://127.0.0.1:9123/'
    assert backend.owned
    arguments, kwargs = popen.call_args
    command = arguments[0]
    assert command[0] == str(python)
    assert '--server.address=127.0.0.1' in command and '--server.headless=true' in command
    assert '--browser.gatherUsageStats=false' in command
    assert '--global.developmentMode=false' in command
    assert kwargs['cwd'] == project
    assert kwargs['env']['PYTHONDONTWRITEBYTECODE'] == '1'
    assert '--server.fileWatcherType=none' in command
    assert '--server.maxUploadSize=2000' in command        # a backup to restore can be larger than 200 MB
    backend.stop()
    process.terminate.assert_called_once()
    process.kill.assert_not_called()
    assert not backend.owned


def test_frozen_backend_relaunches_itself_in_server_mode(monkeypatch, tmp_path):
    project = tmp_path / 'journal'
    (project / 'app').mkdir(parents=True)
    (project / 'app' / 'app.py').touch()
    executable = tmp_path / 'mwjournal-backend'
    executable.touch()
    executable.chmod(0o700)
    process = Mock()
    process.poll.return_value = None
    monkeypatch.setattr(desktop, 'is_journal', lambda _: False)
    monkeypatch.setattr(desktop, 'free_port', lambda _: 9123)
    monkeypatch.setattr(desktop, 'http_bytes', lambda _: b'ok')
    popen = Mock(return_value=process)
    monkeypatch.setattr(desktop.subprocess, 'Popen', popen)

    backend = desktop.Backend(project, executable, frozen_server=executable)
    backend.start()

    command = popen.call_args.args[0]
    assert command == [str(executable), '--serve', '--project', str(project.resolve()), '--port', '9123']
    backend.stop()


def test_failed_start_cleans_up_only_its_owned_backend(monkeypatch, tmp_path):
    (tmp_path / 'app').mkdir()
    (tmp_path / 'app' / 'app.py').touch()
    python = tmp_path / 'python'
    python.touch()
    python.chmod(0o700)
    process = Mock()
    process.poll.return_value = None
    process.wait.side_effect = [subprocess.TimeoutExpired('synthetic-child', 8), None]
    monkeypatch.setattr(desktop, 'is_journal', lambda _: False)
    monkeypatch.setattr(desktop, 'free_port', lambda _: 9123)
    monkeypatch.setattr(desktop.subprocess, 'Popen', lambda *args, **kwargs: process)
    backend = desktop.Backend(tmp_path, python)
    with pytest.raises(RuntimeError, match='did not start'):
        backend.start(timeout=0)
    process.terminate.assert_called_once()
    process.kill.assert_called_once()
    assert not backend.owned


def test_http_health_requests_never_use_environment_proxy(monkeypatch):
    response = Mock()
    response.read.return_value = b'ok'
    manager = Mock()
    manager.__enter__ = Mock(return_value=response)
    manager.__exit__ = Mock(return_value=False)
    opener = Mock()
    opener.open.return_value = manager
    build = Mock(return_value=opener)
    monkeypatch.setattr(desktop.urllib.request, 'build_opener', build)
    assert desktop.http_bytes('http://127.0.0.1:8600/_stcore/health') == b'ok'
    assert build.call_args.args[0].proxies == {}
    response.read.assert_called_once_with(512 * 1024)
