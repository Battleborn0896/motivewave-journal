"""Build a self-contained, distributable macOS DMG.

The existing ``build_desktop.py`` remains the source of the app's native shell.
This release builder selects the launcher's relative ``executable`` mode and
puts a PyInstaller backend inside the app bundle.

No dependencies are installed by this script.  The selected build Python must
already contain the project dependencies and PyInstaller.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
import importlib.util
import json
import os
from pathlib import Path
import platform
import plistlib
import re
import shutil
import subprocess
import sys
import tempfile


ROOT = Path(__file__).resolve().parent.parent
APP_NAME = "MotiveWave Journal.app"
BACKEND_NAME = "MotiveWaveJournalBackend"
DEFAULT_JAR = ROOT / "study" / "build" / "libs" / "motivewave-journal.jar"
BACKEND_RESOURCE_PATH = f"runtime/{BACKEND_NAME}"
MACOS_DEPLOYMENT_TARGET = "13.0"
MACHO_MAGICS = {
    b"\xfe\xed\xfa\xce", b"\xfe\xed\xfa\xcf", b"\xce\xfa\xed\xfe", b"\xcf\xfa\xed\xfe",
    b"\xca\xfe\xba\xbe", b"\xbe\xba\xfe\xca", b"\xca\xfe\xba\xbf", b"\xbf\xba\xfe\xca",
}


class ReleaseError(RuntimeError):
    """A release preflight or packaging operation failed safely."""


@dataclass(frozen=True)
class NotaryCredentials:
    keychain_profile: str | None = None
    apple_id: str | None = None
    team_id: str | None = None
    password: str | None = None

    def arguments(self) -> list[str]:
        if self.keychain_profile:
            if self.apple_id or self.team_id or self.password:
                raise ReleaseError(
                    "Use either a notary keychain profile or Apple ID credentials, not both."
                )
            return ["--keychain-profile", self.keychain_profile]
        supplied = [self.apple_id, self.team_id, self.password]
        if any(supplied) and not all(supplied):
            raise ReleaseError(
                "Apple ID notarization requires apple ID, team ID, and app-specific password."
            )
        if all(supplied):
            return [
                "--apple-id", self.apple_id or "",
                "--team-id", self.team_id or "",
                "--password", self.password or "",
            ]
        raise ReleaseError(
            "Notarization needs --notary-keychain-profile (recommended) or complete "
            "Apple ID credentials."
        )


def environment_flag(value: str | None) -> bool:
    if value is None or not value.strip():
        return False
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ReleaseError(f"Invalid boolean environment value: {value!r}")


def version_from_plist(path: Path) -> str:
    try:
        with path.open("rb") as source:
            version = plistlib.load(source)["CFBundleShortVersionString"]
    except (OSError, KeyError, plistlib.InvalidFileException) as exc:
        raise ReleaseError(f"Could not read the app version from {path}: {exc}") from exc
    if not isinstance(version, str) or not re.fullmatch(r"[0-9A-Za-z][0-9A-Za-z._-]*", version):
        raise ReleaseError(f"The app plist has an unsafe version value: {version!r}")
    return version


def default_output(version: str) -> Path:
    return ROOT / "build" / "release" / f"MotiveWave-Journal-{version}.dmg"


def validate_output(path: Path, *, force: bool) -> Path:
    path = path.expanduser().absolute()
    if path.suffix.lower() != ".dmg":
        raise ReleaseError("Release output must end in .dmg.")
    if path.exists() and not force:
        raise ReleaseError(
            f"{path} already exists. Move it aside or explicitly pass --force; nothing was overwritten."
        )
    if path.exists() and not path.is_file():
        raise ReleaseError(f"Release output is not a regular file: {path}")
    return path


def launcher_configuration() -> dict[str, object]:
    """Configuration understood by the current, unmodified native launcher."""
    return {"executable": BACKEND_RESOURCE_PATH, "project": "journal", "port": 8600}


def swift_architectures(target_arch: str | None) -> tuple[str, ...]:
    if target_arch is None:
        return ()
    if target_arch == "universal2":
        return ("arm64", "x86_64")
    if target_arch in {"arm64", "x86_64"}:
        return (target_arch,)
    raise ReleaseError(f"Unsupported macOS target architecture: {target_arch}")


def notary_credentials(args: argparse.Namespace, environment: dict[str, str]) -> NotaryCredentials:
    return NotaryCredentials(
        keychain_profile=args.notary_keychain_profile or environment.get("MWJ_NOTARY_KEYCHAIN_PROFILE"),
        apple_id=args.apple_id or environment.get("MWJ_NOTARY_APPLE_ID"),
        team_id=args.team_id or environment.get("MWJ_NOTARY_TEAM_ID"),
        password=args.notary_password or environment.get("MWJ_NOTARY_PASSWORD"),
    )


def require_file(path: Path, label: str) -> Path:
    path = path.expanduser().absolute()
    if not path.is_file():
        raise ReleaseError(f"Required {label} is missing: {path}")
    return path


def require_tool(name: str) -> str:
    found = shutil.which(name)
    if not found:
        raise ReleaseError(f"Required macOS build tool is unavailable: {name}")
    return found


def run(command: list[str], *, label: str, cwd: Path | None = None,
        capture: bool = False,
        environment: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            command,
            cwd=cwd,
            env=environment,
            check=True,
            text=True,
            stdout=subprocess.PIPE if capture else None,
            stderr=subprocess.PIPE if capture else None,
        )
    except FileNotFoundError as exc:
        raise ReleaseError(f"{label} could not start: {command[0]} was not found.") from exc
    except subprocess.CalledProcessError as exc:
        # Do not echo the command: notarization commands can contain an app-specific password.
        detail = (exc.stderr or exc.stdout or "").strip()
        suffix = f"\n{detail}" if detail else ""
        raise ReleaseError(f"{label} failed with exit code {exc.returncode}.{suffix}") from exc


def check_python(python: Path) -> Path:
    python = python.expanduser().absolute()
    require_file(python, "build Python")
    if not os.access(python, os.X_OK):
        raise ReleaseError(f"Build Python is not executable: {python}")
    probe = (
        "import PyInstaller, streamlit, pandas, plotly; "
        "print(PyInstaller.__version__)"
    )
    run([str(python), "-c", probe], label="Build Python dependency check", capture=True)
    return python


def load_desktop_builder():
    path = ROOT / "scripts" / "build_desktop.py"
    spec = importlib.util.spec_from_file_location("mwj_build_desktop", path)
    if spec is None or spec.loader is None:
        raise ReleaseError(f"Could not load the existing desktop builder: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def validate_app(app: Path) -> None:
    required = [
        app / "Contents" / "Info.plist",
        app / "Contents" / "MacOS" / "MotiveWaveJournal",
        app / "Contents" / "Resources" / "launcher.json",
        app / "Contents" / "Resources" / "journal" / "app" / "app.py",
    ]
    missing = [str(path) for path in required if not path.is_file()]
    if missing:
        raise ReleaseError("The base app is incomplete; missing: " + ", ".join(missing))


def prepare_base_app(python: Path, destination: Path, source_app: Path | None) -> Path:
    if source_app is not None:
        source_app = source_app.expanduser().absolute()
        validate_app(source_app)
        shutil.copytree(source_app, destination, symlinks=True)
    else:
        builder = load_desktop_builder()
        builder.build(python, destination)
    validate_app(destination)
    return destination


def backend_entry_source() -> str:
    return """# Generated only inside the release build workspace.
from mwjournal.desktop import main

if __name__ == \"__main__\":
    raise SystemExit(main())
"""


def build_backend(python: Path, workspace: Path, app: Path, target_arch: str | None) -> Path:
    entry = workspace / "backend_entry.py"
    entry.write_text(backend_entry_source(), encoding="utf-8")
    dist = workspace / "pyinstaller-dist"
    command = [
        str(python), "-m", "PyInstaller",
        "--noconfirm", "--clean", "--onedir", "--console",
        "--name", BACKEND_NAME,
        "--distpath", str(dist),
        "--workpath", str(workspace / "pyinstaller-work"),
        "--specpath", str(workspace / "pyinstaller-spec"),
        "--collect-all", "streamlit",
        "--collect-all", "plotly",
        "--hidden-import", "pandas",
        "--collect-submodules", "mwjournal",
    ]
    if target_arch:
        command.extend(["--target-arch", target_arch])
    command.append(str(entry))
    environment = dict(os.environ)
    # A release build must not depend on a writable user Library or reuse stale global cache entries.
    environment["PYINSTALLER_CONFIG_DIR"] = str(workspace / "pyinstaller-config")
    run(command, label="PyInstaller backend build", cwd=ROOT, environment=environment)

    built = dist / BACKEND_NAME
    executable = built / BACKEND_NAME
    require_file(executable, "frozen backend executable")
    runtime = app / "Contents" / "Resources" / "runtime"
    if runtime.exists():
        shutil.rmtree(runtime)
    shutil.copytree(built, runtime, symlinks=True)
    packaged = runtime / BACKEND_NAME
    if not os.access(packaged, os.X_OK):
        raise ReleaseError(f"Frozen backend is not executable after packaging: {packaged}")
    return packaged


def rebuild_release_launcher(app: Path, workspace: Path, target_arch: str | None) -> None:
    """Compile the current native launcher unchanged and select its frozen-backend mode."""
    source_path = ROOT / "desktop" / "macos" / "Main.swift"
    binary = app / "Contents" / "MacOS" / "MotiveWaveJournal"
    base_command = [
        "xcrun", "swiftc", "-O", "-swift-version", "5",
        "-module-cache-path", str(workspace / "swift-module-cache"),
        "-framework", "AppKit", "-framework", "WebKit", "-framework", "ImageIO",
    ]
    architectures = swift_architectures(target_arch)
    if len(architectures) < 2:
        command = list(base_command)
        if architectures:
            command.extend([
                "-target", f"{architectures[0]}-apple-macos{MACOS_DEPLOYMENT_TARGET}",
            ])
        command.extend([str(source_path), "-o", str(binary)])
        run(command, label="Release launcher compilation")
    else:
        slices = []
        for architecture in architectures:
            target = workspace / f"MotiveWaveJournal-{architecture}"
            command = [
                *base_command,
                "-target", f"{architecture}-apple-macos{MACOS_DEPLOYMENT_TARGET}",
                str(source_path), "-o", str(target),
            ]
            run(command, label=f"{architecture} release launcher compilation")
            slices.append(str(target))
        run(["xcrun", "lipo", "-create", *slices, "-output", str(binary)],
            label="Universal release launcher creation")
    launcher = app / "Contents" / "Resources" / "launcher.json"
    launcher.write_text(json.dumps(launcher_configuration()), encoding="utf-8")


def install_jar(app: Path, jar: Path) -> None:
    embedded = app / "Contents" / "Resources" / "journal" / "study" / "build" / "libs" / jar.name
    embedded.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(jar, embedded)
    if embedded.stat().st_size != jar.stat().st_size:
        raise ReleaseError("The embedded MotiveWave study JAR did not copy completely.")


def validate_release_app(app: Path, jar_name: str) -> None:
    """Prove that the staged app uses only its bundle-relative backend contract."""
    validate_app(app)
    resources = app / "Contents" / "Resources"
    launcher = resources / "launcher.json"
    try:
        configuration = json.loads(launcher.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ReleaseError(f"The release launcher configuration is unreadable: {exc}") from exc
    if configuration != launcher_configuration():
        raise ReleaseError(
            "The release launcher configuration is not the reviewed bundle-relative contract."
        )
    relative = Path(str(configuration["executable"]))
    if relative.is_absolute() or ".." in relative.parts:
        raise ReleaseError("The release backend path must stay inside Contents/Resources.")
    executable = resources / relative
    require_file(executable, "bundle-relative frozen backend")
    if not os.access(executable, os.X_OK):
        raise ReleaseError(f"Bundled backend is not executable: {executable}")
    require_file(
        resources / "journal" / "study" / "build" / "libs" / jar_name,
        "embedded MotiveWave study JAR",
    )


def is_macho(path: Path) -> bool:
    if not path.is_file() or path.is_symlink():
        return False
    try:
        with path.open("rb") as source:
            return source.read(4) in MACHO_MAGICS
    except OSError as exc:
        raise ReleaseError(f"Could not inspect packaged code {path}: {exc}") from exc


def sign_app(app: Path, identity: str | None) -> None:
    identity = identity or "-"
    nested = sorted(
        (path for path in app.rglob("*") if is_macho(path)),
        key=lambda path: len(path.parts),
        reverse=True,
    )
    for path in nested:
        command = ["codesign", "--force", "--sign", identity]
        if identity != "-":
            command.extend(["--options", "runtime", "--timestamp"])
        command.append(str(path))
        run(command, label=f"Code signing {path.name}", capture=True)
    containers = sorted(
        (path for path in app.rglob("*")
         if path.is_dir() and not path.is_symlink()
         and path.suffix.lower() in {".framework", ".bundle", ".xpc", ".appex", ".app"}),
        key=lambda path: len(path.parts),
        reverse=True,
    )
    for path in containers:
        command = ["codesign", "--force", "--sign", identity]
        if identity != "-":
            command.extend(["--options", "runtime", "--timestamp"])
        command.append(str(path))
        run(command, label=f"Code signing {path.name}", capture=True)
    command = ["codesign", "--force", "--sign", identity]
    if identity != "-":
        command.extend(["--options", "runtime", "--timestamp"])
    command.append(str(app))
    run(command, label="App bundle code signing", capture=True)
    run(["codesign", "--verify", "--deep", "--strict", "--verbose=2", str(app)],
        label="App bundle signature verification", capture=True)


def create_dmg(app: Path, jar: Path, output: Path, workspace: Path, volume_name: str) -> Path:
    contents = workspace / "dmg-contents"
    contents.mkdir()
    shutil.copytree(app, contents / APP_NAME, symlinks=True)
    shutil.copy2(jar, contents / jar.name)
    (contents / "Applications").symlink_to("/Applications")
    candidate = workspace / output.name
    run([
        "hdiutil", "create", "-fs", "HFS+", "-format", "UDZO",
        "-volname", volume_name, "-srcfolder", str(contents), str(candidate),
    ], label="DMG creation")
    return require_file(candidate, "release DMG")


def sign_dmg(dmg: Path, identity: str) -> None:
    run([
        "codesign", "--force", "--sign", identity, "--timestamp", str(dmg)
    ], label="DMG code signing")
    run(["codesign", "--verify", "--verbose=2", str(dmg)],
        label="DMG signature verification", capture=True)


def notarize(dmg: Path, credentials: NotaryCredentials) -> None:
    command = [
        "xcrun", "notarytool", "submit", str(dmg), "--wait", "--output-format", "json",
        *credentials.arguments(),
    ]
    result = run(command, label="Apple notarization", capture=True)
    try:
        response = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise ReleaseError("Apple notarization returned unreadable output.") from exc
    if str(response.get("status", "")).lower() != "accepted":
        submission = response.get("id", "unknown")
        status = response.get("status", "unknown")
        raise ReleaseError(f"Apple notarization was not accepted (status {status}, id {submission}).")
    run(["xcrun", "stapler", "staple", str(dmg)], label="Notarization ticket stapling")
    run(["xcrun", "stapler", "validate", str(dmg)], label="Notarization ticket validation")


def preflight(*, python: Path, jar: Path, source_app: Path | None, notarizing: bool,
              identity: str | None, credentials: NotaryCredentials | None) -> tuple[Path, Path]:
    if sys.platform != "darwin":
        raise ReleaseError("This release builder must run on macOS.")
    for tool in ("xcrun", "codesign", "hdiutil"):
        require_tool(tool)
    run(["xcrun", "--find", "swiftc"], label="Swift compiler check", capture=True)
    if notarizing:
        run(["xcrun", "--find", "notarytool"], label="Notary tool check", capture=True)
        if not identity:
            raise ReleaseError("--notarize requires a Developer ID Application signing identity.")
        if credentials is None:
            raise ReleaseError("Notarization credentials were not configured.")
        credentials.arguments()
    jar = require_file(jar, "MotiveWave study JAR")
    python = check_python(python)
    if source_app is not None:
        validate_app(source_app.expanduser().absolute())
    return python, jar


def build_release(*, python: Path, jar: Path, output: Path, source_app: Path | None = None,
                  force: bool = False, sign_identity: str | None = None,
                  notarizing: bool = False, credentials: NotaryCredentials | None = None,
                  target_arch: str | None = None) -> Path:
    output = validate_output(output, force=force)
    python, jar = preflight(
        python=python, jar=jar, source_app=source_app, notarizing=notarizing,
        identity=sign_identity, credentials=credentials,
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".mwj-release-", dir=output.parent) as temporary:
        workspace = Path(temporary)
        app = prepare_base_app(python, workspace / APP_NAME, source_app)
        build_backend(python, workspace, app, target_arch)
        rebuild_release_launcher(app, workspace, target_arch)
        install_jar(app, jar)
        validate_release_app(app, jar.name)
        sign_app(app, sign_identity)

        version = version_from_plist(app / "Contents" / "Info.plist")
        dmg = create_dmg(app, jar, output, workspace, f"MotiveWave Journal {version}")
        if sign_identity:
            sign_dmg(dmg, sign_identity)
        if notarizing:
            if credentials is None:
                raise ReleaseError("Notarization credentials were not configured.")
            notarize(dmg, credentials)

        # The previous release remains untouched until every requested validation succeeds.
        os.replace(dmg, output)
    return output


def parser() -> argparse.ArgumentParser:
    plist = ROOT / "desktop" / "macos" / "Info.plist"
    version = version_from_plist(plist)
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--python", type=Path, default=Path(sys.executable),
                        help="Python environment containing PyInstaller and project dependencies")
    result.add_argument("--jar", type=Path, default=DEFAULT_JAR,
                        help="required compiled MotiveWave study JAR")
    result.add_argument("--base-app", type=Path,
                        help="wrap an existing app instead of running scripts/build_desktop.py")
    result.add_argument("--output", type=Path, default=default_output(version))
    result.add_argument("--force", action="store_true",
                        help="atomically replace an existing DMG only after a successful build")
    result.add_argument("--target-arch", choices=("arm64", "x86_64", "universal2"),
                        default=None,
                        help=f"PyInstaller target architecture (default: build Python, currently {platform.machine()})")
    result.add_argument("--sign-identity", default=os.environ.get("MWJ_SIGN_IDENTITY"),
                        help="Developer ID Application identity (or MWJ_SIGN_IDENTITY)")
    result.add_argument("--notarize", action="store_true",
                        default=environment_flag(os.environ.get("MWJ_NOTARIZE")),
                        help="submit signed DMG to Apple (or set MWJ_NOTARIZE=1)")
    result.add_argument("--notary-keychain-profile",
                        help="notarytool keychain profile (or MWJ_NOTARY_KEYCHAIN_PROFILE)")
    result.add_argument("--apple-id", help="notary Apple ID (or MWJ_NOTARY_APPLE_ID)")
    result.add_argument("--team-id", help="Apple developer team ID (or MWJ_NOTARY_TEAM_ID)")
    result.add_argument("--notary-password",
                        help="app-specific password (or MWJ_NOTARY_PASSWORD; keychain profile preferred)")
    return result


def main() -> int:
    try:
        args = parser().parse_args()
        credentials = notary_credentials(args, os.environ) if args.notarize else None
        result = build_release(
            python=args.python,
            jar=args.jar,
            output=args.output,
            source_app=args.base_app,
            force=args.force,
            sign_identity=args.sign_identity,
            notarizing=args.notarize,
            credentials=credentials,
            target_arch=args.target_arch,
        )
    except ReleaseError as exc:
        print(f"release build failed: {exc}", file=sys.stderr)
        return 2
    except (OSError, subprocess.SubprocessError) as exc:
        print(f"release build failed safely: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2
    print(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
