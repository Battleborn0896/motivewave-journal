"""Windows packaging tests that run without Windows, PyInstaller or pywebview."""
from __future__ import annotations

import importlib.util
import os
import sys
import tomllib
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock

import pytest


ROOT = Path(__file__).resolve().parents[1]


def load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


builder = load("mwj_windows_builder", ROOT / "scripts" / "build_windows.py")
launcher = load("mwj_windows_launcher", ROOT / "desktop" / "windows" / "launcher.py")


def fake_source(root: Path, *, with_jar: bool = True) -> Path:
    for folder in builder.SOURCE_DIRECTORIES:
        target = root / folder
        target.mkdir(parents=True)
        (target / "kept.txt").write_text(folder)
        (target / "ignored.pyc").write_bytes(b"cache")
    for filename in builder.SOURCE_FILES:
        (root / filename).write_text(filename)
    for name in builder.REQUIRED_FILES:              # the app, its settings and the bundled fonts
        (root / name).parent.mkdir(parents=True, exist_ok=True)
        (root / name).write_bytes(b"required")
    jar = root / "study" / "build" / "libs" / "motivewave-journal.jar"
    if with_jar:
        jar.parent.mkdir(parents=True)
        jar.write_bytes(b"prebuilt study")
    return jar


def test_payload_contains_source_and_prebuilt_study_jar(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    jar = fake_source(source)
    project = builder.copy_payload(source, tmp_path / "payload", jar=jar)

    assert (project / "app" / "kept.txt").read_text() == "app"
    assert not (project / "app" / "ignored.pyc").exists()
    assert (project / "study" / "build" / "libs" / jar.name).read_bytes() == b"prebuilt study"
    assert (project / "LICENSE").is_file()
    assert (project / "app" / "static" / "fonts" / "IBMPlexSans-Regular.woff2").read_bytes() == b"required"
    assert (project / ".streamlit" / "config.toml").is_file()


def test_payload_without_the_bundled_fonts_fails(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    jar = fake_source(source)
    (source / "app" / "static" / "fonts" / "IBMPlexSans-Italic.woff2").unlink()
    with pytest.raises(FileNotFoundError, match="IBMPlexSans-Italic.woff2"):
        builder.copy_payload(source, tmp_path / "payload", jar=jar)


def test_missing_study_jar_fails_with_actionable_message(tmp_path):
    missing = tmp_path / "study" / "build" / "libs" / "motivewave-journal.jar"
    with pytest.raises(FileNotFoundError, match="prebuilt MotiveWave study JAR is missing"):
        builder.require_study_jar(missing)


def test_pyinstaller_build_is_windowed_onedir_and_bundles_payload(tmp_path):
    payload = tmp_path / "payload" / "journal"
    command = builder.pyinstaller_command(
        Path("python.exe"), payload, tmp_path / "dist", tmp_path / "work", tmp_path / "spec"
    )

    assert command[:3] == ["python.exe", "-m", "PyInstaller"]
    assert "--windowed" in command and "--onedir" in command
    assert "--onefile" not in command
    add_data = command[command.index("--add-data") + 1]
    assert add_data == f"{payload}{os.pathsep}journal"
    assert command[-1].replace("\\", "/").endswith("desktop/windows/launcher.py")


def test_backend_arguments_are_loopback_headless_and_do_not_arm_recording(tmp_path):
    arguments = launcher.streamlit_arguments(tmp_path, 9123)
    joined = " ".join(arguments).lower()
    assert "--server.address=127.0.0.1" in arguments
    assert "--server.port=9123" in arguments
    assert "--server.headless=true" in arguments
    assert "--global.developmentMode=false" in arguments
    assert "--server.enableStaticServing=true" in arguments      # serves the bundled fonts in app/static
    assert "--server.maxUploadSize=2000" in arguments            # a backup to restore can be larger than 200 MB
    assert "arm" not in joined and "record" not in joined and "media" not in joined


def test_the_upload_limit_is_the_same_with_or_without_the_settings_file(tmp_path):
    limit = tomllib.loads((ROOT / ".streamlit" / "config.toml").read_text(encoding="utf-8"))["server"]["maxUploadSize"]
    assert f"--server.maxUploadSize={limit}" in launcher.streamlit_arguments(tmp_path, 9123)


def test_native_window_allows_downloads(monkeypatch):
    """pywebview cancels every download unless ALLOW_DOWNLOADS is on: Back Up Journal and the CSV are downloads."""
    seen = {}
    webview = SimpleNamespace(settings={"ALLOW_DOWNLOADS": False}, create_window=Mock(return_value=MagicMock()),
                              start=lambda **_: seen.update(webview.settings))
    monkeypatch.setitem(sys.modules, "webview", webview)
    launcher.open_native_window("http://127.0.0.1:9123/", Mock())
    assert seen["ALLOW_DOWNLOADS"] is True


def test_frozen_bundle_uses_pyinstaller_data_root(monkeypatch, tmp_path):
    monkeypatch.setattr(launcher.sys, "_MEIPASS", str(tmp_path), raising=False)
    assert launcher.bundle_root() == tmp_path
    assert launcher.bundled_project() == tmp_path / "journal"


def test_backend_owns_local_child_and_stops_it(monkeypatch, tmp_path):
    project = tmp_path / "journal"
    (project / "app").mkdir(parents=True)
    (project / "app" / "app.py").touch()
    process = Mock()
    process.poll.return_value = None
    popen = Mock(return_value=process)
    monkeypatch.setattr(launcher, "free_loopback_port", lambda _port: 9123)
    monkeypatch.setattr(launcher, "health_bytes", lambda _url: b"ok")
    monkeypatch.setattr(launcher.subprocess, "Popen", popen)

    backend = launcher.Backend(project, log=tmp_path / "desktop.log")
    assert backend.start() == "http://127.0.0.1:9123/"
    command = popen.call_args.args[0]
    assert command[0] == launcher.sys.executable
    assert command[1:3] == ["--backend", "--project"]
    assert popen.call_args.kwargs["cwd"] == project.resolve()

    backend.stop()
    process.terminate.assert_called_once()
    process.kill.assert_not_called()
    assert backend.process is None
