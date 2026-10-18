# Windows distribution

Build this distribution on a 64-bit Windows 10 or Windows 11 machine with
Python 3.11 or newer. PyInstaller cannot produce the Windows executable from
macOS.

```powershell
py -3.11 -m venv .venv-windows-build
.\.venv-windows-build\Scripts\python -m pip install -U pip
.\.venv-windows-build\Scripts\python -m pip install -r desktop\windows\requirements-build.txt
.\.venv-windows-build\Scripts\python scripts\build_windows.py
```

The prebuilt study must exist at
`study\build\libs\motivewave-journal.jar` before the build starts. The builder
stops with a clear error if it is missing, and also if the bundled IBM Plex Sans
fonts (`app\static\fonts`) or `.streamlit\config.toml` are missing.

Artifacts are written to `build\windows`:

- `MotiveWave Journal\MotiveWave Journal.exe` is the unpacked portable app.
- `MotiveWave-Journal-Windows-x64.zip` is the file to send to testers.

The tester extracts the entire ZIP and runs `MotiveWave Journal.exe`. The app
opens a native pywebview window and serves its bundled journal only on
`127.0.0.1`. It contains no screen/video recorder. The bundled study JAR
is inside the app at `journal\study\build\libs\motivewave-journal.jar` and can
be installed from the app's setup flow.

Windows may require the Microsoft Edge WebView2 Runtime. It is included with
current Windows 10 and Windows 11 installations.
