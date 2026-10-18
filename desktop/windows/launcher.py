"""Windows native launcher for the self-contained MotiveWave Journal bundle.

The UI is served only on loopback and shown in pywebview. This launcher starts
the journal's study-file reader through the normal app. Screen/video recording
is not part of the desktop journal.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request


APP_NAME = "MotiveWave Journal"
DEFAULT_PORT = 8600
# The journal's one typeface, from the bundled journal/app/static/fonts. The same settings as
# .streamlit/config.toml and mwjournal/fonts.py (tests keep the three identical), passed as flags so the
# backend serves and uses the fonts even if it does not read that file. Kept here rather than imported
# so this launcher stays self-contained.
FONT_FAMILY = "IBM Plex Sans"
FONT_FACES = (
    ("IBMPlexSans-Regular.woff2", 400, "normal"),
    ("IBMPlexSans-Italic.woff2", 400, "italic"),
    ("IBMPlexSans-Medium.woff2", 500, "normal"),
    ("IBMPlexSans-SemiBold.woff2", 600, "normal"),
    ("IBMPlexSans-Bold.woff2", 700, "normal"),
)


def bundle_root() -> Path:
    """Return PyInstaller's data root, or the repository root when run in source."""
    frozen_root = getattr(sys, "_MEIPASS", None)
    if frozen_root:
        return Path(frozen_root)
    return Path(__file__).resolve().parents[2]


def bundled_project() -> Path:
    return bundle_root() / "journal"


def log_path() -> Path:
    local = os.environ.get("LOCALAPPDATA", "").strip()
    home = Path(local) if local else Path.home() / "AppData" / "Local"
    return home / APP_NAME / "logs" / "desktop.log"


def free_loopback_port(preferred: int = DEFAULT_PORT) -> int:
    """Reserve-test a loopback port; the backend is the only network listener."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        try:
            sock.bind(("127.0.0.1", preferred))
        except OSError:
            sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def health_bytes(url: str, *, timeout: float = 1.0) -> bytes:
    """Read local health without allowing an environment proxy to intercept it."""
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(url, timeout=timeout) as response:
        return response.read(64)


def font_arguments() -> list[str]:
    """Static serving on and every Streamlit font set to the bundled IBM Plex Sans."""
    faces = [{"family": FONT_FAMILY, "url": f"app/static/fonts/{file}", "weight": weight, "style": style}
             for file, weight, style in FONT_FACES]
    return [
        "--server.enableStaticServing=true",
        f"--theme.font={FONT_FAMILY}",
        f"--theme.headingFont={FONT_FAMILY}",
        f"--theme.codeFont={FONT_FAMILY}",
        "--theme.fontFaces=" + json.dumps(faces, separators=(",", ":")),
    ]


def streamlit_arguments(project: Path, port: int) -> list[str]:
    """Arguments used by the private backend process."""
    return [
        "streamlit",
        "run",
        str(project / "app" / "app.py"),
        "--server.address=127.0.0.1",
        f"--server.port={port}",
        "--server.headless=true",
        "--server.fileWatcherType=none",
        "--browser.gatherUsageStats=false",
        "--server.enableXsrfProtection=true",
        "--global.developmentMode=false",
        "--server.enableCORS=true",
        *font_arguments(),
    ]


def run_backend(project: Path, port: int) -> int:
    """Run Streamlit inside the frozen child process."""
    entrypoint = project / "app" / "app.py"
    if not entrypoint.is_file():
        raise RuntimeError(f"The bundled journal source is missing: {entrypoint}")
    os.chdir(project)
    sys.path[:0] = [str(project), str(project / "app")]
    os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
    sys.argv = streamlit_arguments(project, port)
    from streamlit.web import cli as streamlit_cli

    return int(streamlit_cli.main() or 0)


class Backend:
    """Own exactly one localhost Streamlit child and no external application."""

    def __init__(self, project: Path, *, port: int = DEFAULT_PORT, log: Path | None = None):
        self.project = project.resolve()
        self.port = port
        self.log = log or log_path()
        self.process: subprocess.Popen[bytes] | None = None
        self._log_file = None

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}/"

    def command(self) -> list[str]:
        return [sys.executable, "--backend", "--project", str(self.project), "--port", str(self.port)]

    def start(self, *, timeout: float = 45.0) -> str:
        if not (self.project / "app" / "app.py").is_file():
            raise RuntimeError("The bundled journal source is missing. Reinstall the Windows distribution.")
        self.port = free_loopback_port(self.port)
        self.log.parent.mkdir(parents=True, exist_ok=True)
        self._log_file = self.log.open("ab")
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        self.process = subprocess.Popen(
            self.command(),
            cwd=self.project,
            stdin=subprocess.DEVNULL,
            stdout=self._log_file,
            stderr=self._log_file,
            creationflags=creationflags,
            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        )
        deadline = time.monotonic() + timeout
        try:
            while time.monotonic() < deadline:
                if self.process.poll() is not None:
                    raise RuntimeError(f"The journal backend exited. See {self.log}.")
                try:
                    if health_bytes(self.url + "_stcore/health").strip() == b"ok":
                        return self.url
                except (OSError, urllib.error.URLError):
                    pass
                time.sleep(0.2)
            raise RuntimeError(f"The journal did not start within {timeout:g} seconds. See {self.log}.")
        except BaseException:
            self.stop()
            raise

    def stop(self) -> None:
        process, self.process = self.process, None
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=8)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=3)
        if self._log_file is not None:
            self._log_file.close()
            self._log_file = None


def show_error(message: str) -> None:
    """Give windowed builds a visible failure instead of silently exiting."""
    try:
        import ctypes

        ctypes.windll.user32.MessageBoxW(None, message, APP_NAME, 0x10)
    except Exception:
        print(message, file=sys.stderr)


def open_native_window(url: str, backend: Backend) -> None:
    import webview

    window = webview.create_window(
        APP_NAME,
        url,
        width=1440,
        height=920,
        min_size=(1050, 700),
        text_select=True,
    )
    window.events.closed += backend.stop
    webview.start(private_mode=False)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backend", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--project", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help=argparse.SUPPRESS)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if not 1 <= args.port <= 65535:
        raise SystemExit("port must be between 1 and 65535")
    if args.backend:
        if args.project is None:
            raise SystemExit("--project is required for the private backend")
        return run_backend(args.project, args.port)

    backend = Backend(bundled_project(), port=args.port)
    try:
        url = backend.start()
        open_native_window(url, backend)
    except Exception as exc:
        show_error(str(exc))
        return 1
    finally:
        backend.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
