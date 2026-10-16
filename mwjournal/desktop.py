"""Local backend lifecycle for the macOS desktop window; never opens a browser."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request

APP_MARKER = b'The chart of MotiveWave Journal'


def http_bytes(url, *, timeout=1):
    # Local connections must not travel through a configured HTTP proxy.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(url, timeout=timeout) as response:
        return response.read(512 * 1024)


def is_journal(url):
    """Do not attach to an unrelated service just because its port answers."""
    try:
        return (http_bytes(url + '_stcore/health').strip() == b'ok'
                and APP_MARKER in http_bytes(url + 'component/tvchart.tvchart/index.html'))
    except (OSError, urllib.error.URLError, ValueError):
        return False


def free_port(preferred):
    with socket.socket() as sock:
        try:
            sock.bind(('127.0.0.1', preferred))
        except OSError:
            sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


def streamlit_arguments(port: int) -> list[str]:
    return ['run', 'app/app.py', '--server.address=127.0.0.1', f'--server.port={port}',
            '--server.headless=true', '--browser.gatherUsageStats=false', '--server.fileWatcherType=none',
            '--global.developmentMode=false']


class Backend:
    def __init__(self, project: Path, python: Path, *, port=8600, log: Path | None = None,
                 frozen_server: Path | None = None):
        self.project = project.resolve()
        self.python = python
        self.port = port
        self.log = log
        self.frozen_server = frozen_server
        self.process = None
        self.log_file = None
        self.url = None

    @property
    def owned(self):
        return self.process is not None

    def start(self, *, timeout=45):
        existing = f'http://127.0.0.1:{self.port}/'
        if is_journal(existing):
            self.url = existing
            return existing
        if not (self.project / 'app' / 'app.py').is_file():
            raise RuntimeError('The bundled journal source is missing.')
        if not self.python.is_file() or not os.access(self.python, os.X_OK):
            raise RuntimeError('The configured Python environment is missing; rebuild the desktop app with --python.')
        self.port = free_port(self.port)
        self.url = f'http://127.0.0.1:{self.port}/'
        output = subprocess.DEVNULL
        if self.log:
            self.log.parent.mkdir(parents=True, exist_ok=True)
            descriptor = os.open(self.log, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
            self.log_file = os.fdopen(descriptor, 'ab')
            output = self.log_file
        environment = {**os.environ, 'PYTHONDONTWRITEBYTECODE': '1'}
        command = ([str(self.frozen_server), '--serve', '--project', str(self.project), '--port', str(self.port)]
                   if self.frozen_server else
                   [str(self.python), '-m', 'streamlit', *streamlit_arguments(self.port)])
        try:
            self.process = subprocess.Popen(command, cwd=self.project, env=environment,
                                            stdin=subprocess.DEVNULL, stdout=output, stderr=output)
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                if self.process.poll() is not None:
                    raise RuntimeError(f'The journal backend exited. See {self.log or "the Python environment"}.')
                # The component registers only after the first UI session; health is sufficient
                # for a child we launched ourselves, unlike an existing unidentified service.
                try:
                    if http_bytes(self.url + '_stcore/health').strip() == b'ok':
                        return self.url
                except (OSError, urllib.error.URLError):
                    pass
                time.sleep(.2)
            raise RuntimeError(f'The journal did not start within {timeout:g} seconds. See {self.log}.')
        except BaseException:
            self.stop()
            raise

    def stop(self):
        # Only ever stop the child THIS controller started, never a reused server or MotiveWave.
        if self.process is not None:
            if self.process.poll() is None:
                self.process.terminate()
                try:
                    self.process.wait(timeout=8)
                except subprocess.TimeoutExpired:
                    self.process.kill()
                    self.process.wait(timeout=3)
            self.process = None
        if self.log_file:
            self.log_file.close()
            self.log_file = None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project', type=Path, required=True)
    parser.add_argument('--port', type=int, default=8600)
    parser.add_argument('--log', type=Path)
    parser.add_argument('--serve', action='store_true', help=argparse.SUPPRESS)
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error('port must be between 1 and 65535')
    if args.serve:
        os.chdir(args.project)
        if str(args.project) not in sys.path:
            sys.path.insert(0, str(args.project))
        sys.argv = ['streamlit', *streamlit_arguments(args.port)]
        from streamlit.web.cli import main as streamlit_main
        return streamlit_main()
    executable = Path(sys.executable)
    backend = Backend(args.project, executable, port=args.port, log=args.log,
                      frozen_server=executable if getattr(sys, 'frozen', False) else None)

    def interrupted(_signal, _frame):
        raise SystemExit(0)

    signal.signal(signal.SIGTERM, interrupted)
    signal.signal(signal.SIGINT, interrupted)
    try:
        url = backend.start()
        print(json.dumps({'url': url, 'owned': backend.owned}), flush=True)
        if backend.owned:
            while backend.process.poll() is None:
                time.sleep(.5)
    except Exception as exc:
        print(json.dumps({'error': str(exc)}), flush=True)
        return 1
    finally:
        backend.stop()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
