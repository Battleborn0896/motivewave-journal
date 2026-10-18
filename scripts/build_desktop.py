"""Build a local macOS .app with native AppKit/WebKit chrome and an existing Python runtime.

This is a developer/local installation, not a self-contained notarized distribution.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parent.parent
SOURCE_DIRECTORIES = ('app', 'mwjournal', '.streamlit', 'docs')
SOURCE_FILES = ('README.md', 'LICENSE', 'THIRD_PARTY_NOTICES.md')
# The bundled typeface (IBM Plex Sans, app/static/fonts) and the settings that serve it. Without them the
# app would draw in fallback fonts, so no build ships without them.
REQUIRED_FILES = (
    'app/app.py',
    '.streamlit/config.toml',
    'app/static/fonts/IBM-Plex-Sans-LICENSE.txt',
    'app/static/fonts/IBMPlexSans-Regular.woff2',
    'app/static/fonts/IBMPlexSans-Italic.woff2',
    'app/static/fonts/IBMPlexSans-Medium.woff2',
    'app/static/fonts/IBMPlexSans-SemiBold.woff2',
    'app/static/fonts/IBMPlexSans-Bold.woff2',
)


def copy_source(destination: Path, root: Path = ROOT) -> Path:
    """The journal's source tree inside the app: the code, its settings, the fonts and the notices."""
    for name in SOURCE_DIRECTORIES:
        shutil.copytree(root / name, destination / name,
                        ignore=shutil.ignore_patterns('__pycache__', '*.pyc', '.DS_Store'))
    for name in SOURCE_FILES:
        shutil.copy2(root / name, destination / name)
    missing = [name for name in REQUIRED_FILES if not (destination / name).is_file()]
    if missing:
        raise FileNotFoundError('The journal source is incomplete; missing: ' + ', '.join(missing))
    return destination


def write_icns(iconset: Path, target: Path) -> None:
    """Write the modern PNG-backed ICNS container when iconutil rejects a valid generated set."""
    entries = (
        (b'icp4', 'icon_16x16.png'),
        (b'icp5', 'icon_32x32.png'),
        (b'icp6', 'icon_32x32@2x.png'),
        (b'ic07', 'icon_128x128.png'),
        (b'ic08', 'icon_256x256.png'),
        (b'ic09', 'icon_512x512.png'),
        (b'ic10', 'icon_512x512@2x.png'),
    )
    chunks = []
    for kind, name in entries:
        payload = (iconset / name).read_bytes()
        chunks.append(kind + struct.pack('>I', len(payload) + 8) + payload)
    body = b''.join(chunks)
    target.write_bytes(b'icns' + struct.pack('>I', len(body) + 8) + body)


def build(python: Path, output: Path):
    if sys.platform != 'darwin':
        raise RuntimeError('This builder currently supports macOS only.')
    # Keep the venv entry point: resolving its symlink would lose its installed dependencies.
    python = python.absolute()
    output = output.absolute()
    if output.exists():
        raise FileExistsError(f'{output} already exists; move it aside before building. No app was overwritten.')
    if output.suffix != '.app':
        raise ValueError('Output must be a new .app path.')
    subprocess.run([str(python), '-c', 'import streamlit, pandas, plotly'], check=True)
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.mwj-desktop-build-', dir=output.parent) as temporary:
        app = Path(temporary) / 'MotiveWave Journal.app'
        resources = app / 'Contents' / 'Resources'
        binary = app / 'Contents' / 'MacOS' / 'MotiveWaveJournal'
        source = resources / 'journal'
        binary.parent.mkdir(parents=True)
        source.mkdir(parents=True)
        copy_source(source)
        jar = ROOT / 'study' / 'build' / 'libs' / 'motivewave-journal.jar'
        if jar.is_file():
            packaged_jar = source / 'study' / 'build' / 'libs' / jar.name
            packaged_jar.parent.mkdir(parents=True)
            shutil.copy2(jar, packaged_jar)
        shutil.copy2(ROOT / 'desktop' / 'macos' / 'Info.plist', app / 'Contents' / 'Info.plist')
        (resources / 'launcher.json').write_text(json.dumps({'python': str(python), 'project': 'journal', 'port': 8600}))
        subprocess.run(['xcrun', 'swiftc', '-O', '-swift-version', '5', '-module-cache-path',
                        str(output.parent / '.swift-module-cache'), '-framework', 'AppKit', '-framework', 'WebKit',
                        '-framework', 'ImageIO',
                        str(ROOT / 'desktop' / 'macos' / 'Main.swift'), '-o', str(binary)], check=True)
        iconset = Path(temporary) / 'Journal.iconset'
        subprocess.run([str(binary), '--make-icon', str(iconset)], check=True)
        icon = resources / 'Journal.icns'
        converted = subprocess.run(['iconutil', '-c', 'icns', str(iconset), '-o', str(icon),],
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if converted.returncode:
            write_icns(iconset, icon)
        subprocess.run(['codesign', '--force', '--sign', '-', str(app)], check=True)
        subprocess.run(['codesign', '--verify', '--deep', '--strict', str(app)], check=True)
        os.rename(app, output)
    return output


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--python', type=Path, default=Path(sys.executable))
    parser.add_argument('--output', type=Path, default=ROOT / 'build' / 'desktop' / 'MotiveWave Journal.app')
    args = parser.parse_args()
    print(build(args.python, args.output))


if __name__ == '__main__':
    main()
