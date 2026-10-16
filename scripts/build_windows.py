"""Build the portable Windows MotiveWave Journal with PyInstaller + pywebview.

Run this script on Windows. It emits both an unpacked onedir application and a
ZIP suitable for sending to testers. No existing artifact is overwritten.
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile


ROOT = Path(__file__).resolve().parent.parent
STUDY_JAR = ROOT / "study" / "build" / "libs" / "motivewave-journal.jar"
APP_NAME = "MotiveWave Journal"
ZIP_NAME = "MotiveWave-Journal-Windows-x64"
SOURCE_DIRECTORIES = ("app", "mwjournal", ".streamlit", "docs")
SOURCE_FILES = ("README.md", "LICENSE", "THIRD_PARTY_NOTICES.md")


def require_study_jar(jar: Path = STUDY_JAR) -> Path:
    if not jar.is_file():
        raise FileNotFoundError(
            "The prebuilt MotiveWave study JAR is missing: "
            f"{jar}. Build the study first; Windows packages must never ship without it."
        )
    return jar


def copy_payload(root: Path, destination: Path, *, jar: Path | None = None) -> Path:
    """Create the journal data tree consumed by the frozen launcher."""
    jar = require_study_jar(jar or root / "study" / "build" / "libs" / "motivewave-journal.jar")
    project = destination / "journal"
    project.mkdir(parents=True)
    ignored = shutil.ignore_patterns("__pycache__", "*.pyc", ".DS_Store")
    for name in SOURCE_DIRECTORIES:
        source = root / name
        if not source.is_dir():
            raise FileNotFoundError(f"Required journal source directory is missing: {source}")
        shutil.copytree(source, project / name, ignore=ignored)
    for name in SOURCE_FILES:
        source = root / name
        if not source.is_file():
            raise FileNotFoundError(f"Required journal distribution file is missing: {source}")
        shutil.copy2(source, project / name)
    packaged_jar = project / "study" / "build" / "libs" / "motivewave-journal.jar"
    packaged_jar.parent.mkdir(parents=True)
    shutil.copy2(jar, packaged_jar)
    return project


def pyinstaller_command(python: Path, payload: Path, dist: Path, work: Path, spec: Path) -> list[str]:
    """Return an inspectable, shell-free PyInstaller invocation."""
    separator = os.pathsep
    return [
        str(python),
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        "--windowed",
        "--onedir",
        "--name",
        APP_NAME,
        "--distpath",
        str(dist),
        "--workpath",
        str(work),
        "--specpath",
        str(spec),
        "--add-data",
        f"{payload}{separator}journal",
        "--collect-all",
        "streamlit",
        "--collect-all",
        "webview",
        "--collect-all",
        "plotly",
        "--hidden-import",
        "pandas",
        str(ROOT / "desktop" / "windows" / "launcher.py"),
    ]


def verify_build_environment(python: Path) -> None:
    check = (
        "import struct, sys; "
        "assert sys.version_info >= (3, 11), 'Python 3.11 or newer is required'; "
        "assert struct.calcsize('P') * 8 == 64, 'A 64-bit Python build is required'; "
        "import PyInstaller, webview, streamlit, pandas, plotly; "
        "print('Windows packaging dependencies are available')"
    )
    try:
        subprocess.run([str(python), "-c", check], check=True)
    except (OSError, subprocess.CalledProcessError) as exc:
        raise RuntimeError(
            "The Windows builder needs 64-bit Python 3.11+ and its packaging dependencies. "
            "Install desktop\\windows\\requirements-build.txt with this Python first."
        ) from exc


def build(python: Path, output: Path) -> tuple[Path, Path]:
    jar = require_study_jar()
    if sys.platform != "win32":
        raise RuntimeError("The Windows distribution must be built on Windows; PyInstaller does not cross-compile.")

    python = python.absolute()
    output = output.absolute()
    app = output / APP_NAME
    archive = output / f"{ZIP_NAME}.zip"
    if app.exists() or archive.exists():
        raise FileExistsError(
            f"{app if app.exists() else archive} already exists. Move it aside; the builder will not overwrite releases."
        )
    verify_build_environment(python)
    output.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="mwj-windows-build-", dir=output) as temporary:
        temporary_path = Path(temporary)
        payload_root = temporary_path / "payload"
        payload = copy_payload(ROOT, payload_root, jar=jar)
        dist = temporary_path / "dist"
        work = temporary_path / "work"
        spec = temporary_path / "spec"
        work.mkdir()
        spec.mkdir()
        command = pyinstaller_command(
            python,
            payload,
            dist,
            work,
            spec,
        )
        subprocess.run(command, check=True, cwd=ROOT)
        built_app = dist / APP_NAME
        executable = built_app / f"{APP_NAME}.exe"
        bundled_jar = built_app / "_internal" / "journal" / "study" / "build" / "libs" / jar.name
        if not executable.is_file():
            raise RuntimeError(f"PyInstaller completed but the launcher is missing: {executable}")
        if not bundled_jar.is_file():
            raise RuntimeError(f"PyInstaller completed but the study JAR is missing: {bundled_jar}")
        os.rename(built_app, app)

    created = Path(shutil.make_archive(str(output / ZIP_NAME), "zip", root_dir=output, base_dir=APP_NAME))
    return app, created


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--python", type=Path, default=Path(sys.executable))
    parser.add_argument("--output", type=Path, default=ROOT / "build" / "windows")
    args = parser.parse_args(argv)
    app, archive = build(args.python, args.output)
    print(f"Portable app: {app}")
    print(f"Tester ZIP:   {archive}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
