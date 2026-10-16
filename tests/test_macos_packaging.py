"""Pure release-builder tests; these do not sign, notarize, or create a DMG."""
from argparse import Namespace
import pytest

from scripts import build_macos_release as release


def arguments(**overrides):
    values = dict(
        notary_keychain_profile=None,
        apple_id=None,
        team_id=None,
        notary_password=None,
    )
    values.update(overrides)
    return Namespace(**values)


def test_release_launcher_uses_relative_executable_contract():
    assert release.launcher_configuration() == {
        "executable": "runtime/MotiveWaveJournalBackend",
        "project": "journal",
        "port": 8600,
    }
    assert "python" not in release.launcher_configuration()
    swift = (release.ROOT / "desktop" / "macos" / "Main.swift").read_text(encoding="utf-8")
    assert "let executable: String?" in swift
    assert "resources.appendingPathComponent(configured)" in swift


def test_backend_entry_calls_desktop_main_directly():
    source = release.backend_entry_source()
    assert "from mwjournal.desktop import main" in source
    assert "raise SystemExit(main())" in source
    assert "streamlit.web.cli" not in source


def test_swift_architectures_match_pyinstaller_target():
    assert release.swift_architectures(None) == ()
    assert release.swift_architectures("arm64") == ("arm64",)
    assert release.swift_architectures("x86_64") == ("x86_64",)
    assert release.swift_architectures("universal2") == ("arm64", "x86_64")
    with pytest.raises(release.ReleaseError, match="Unsupported"):
        release.swift_architectures("sparc")


@pytest.mark.parametrize("value", ["1", "TRUE", " yes ", "On"])
def test_true_environment_flags(value):
    assert release.environment_flag(value)


@pytest.mark.parametrize("value", [None, "", "0", "FALSE", " no ", "off"])
def test_false_environment_flags(value):
    assert not release.environment_flag(value)


def test_invalid_environment_flag_fails_closed():
    with pytest.raises(release.ReleaseError, match="Invalid boolean"):
        release.environment_flag("probably")


def test_notary_keychain_profile_is_preferred_explicitly():
    credentials = release.notary_credentials(
        arguments(notary_keychain_profile="journal-release"), {}
    )
    assert credentials.arguments() == ["--keychain-profile", "journal-release"]


def test_notary_credentials_can_come_from_environment():
    credentials = release.notary_credentials(arguments(), {
        "MWJ_NOTARY_APPLE_ID": "builder@example.com",
        "MWJ_NOTARY_TEAM_ID": "TEAM123",
        "MWJ_NOTARY_PASSWORD": "secret",
    })
    assert credentials.arguments() == [
        "--apple-id", "builder@example.com",
        "--team-id", "TEAM123",
        "--password", "secret",
    ]


def test_partial_or_mixed_notary_credentials_fail_closed():
    partial = release.NotaryCredentials(apple_id="builder@example.com")
    with pytest.raises(release.ReleaseError, match="requires apple ID, team ID"):
        partial.arguments()
    mixed = release.NotaryCredentials(
        keychain_profile="journal-release",
        apple_id="builder@example.com",
        team_id="TEAM123",
        password="secret",
    )
    with pytest.raises(release.ReleaseError, match="either a notary keychain profile"):
        mixed.arguments()


def test_output_validation_never_overwrites_implicitly(tmp_path):
    output = tmp_path / "journal.dmg"
    output.write_bytes(b"previous release")
    with pytest.raises(release.ReleaseError, match="nothing was overwritten"):
        release.validate_output(output, force=False)
    assert release.validate_output(output, force=True) == output
    assert output.read_bytes() == b"previous release"


def test_output_must_be_a_dmg(tmp_path):
    with pytest.raises(release.ReleaseError, match="must end in .dmg"):
        release.validate_output(tmp_path / "journal.zip", force=False)


def test_version_rejects_path_like_value(tmp_path):
    plist = tmp_path / "Info.plist"
    import plistlib
    with plist.open("wb") as target:
        plistlib.dump({"CFBundleShortVersionString": "../../unsafe"}, target)
    with pytest.raises(release.ReleaseError, match="unsafe version"):
        release.version_from_plist(plist)


def test_required_jar_fails_clearly(tmp_path):
    with pytest.raises(release.ReleaseError, match="Required MotiveWave study JAR is missing"):
        release.require_file(tmp_path / "missing.jar", "MotiveWave study JAR")


def test_private_runtime_path_is_bundle_relative():
    path = release.launcher_configuration()["executable"]
    assert path == release.BACKEND_RESOURCE_PATH
    assert not str(path).startswith("/")
    assert ".." not in str(path).split("/")


def test_release_validation_rejects_python_or_absolute_launcher(tmp_path):
    app = tmp_path / release.APP_NAME
    resources = app / "Contents" / "Resources"
    (app / "Contents" / "MacOS").mkdir(parents=True)
    (app / "Contents" / "MacOS" / "MotiveWaveJournal").touch()
    (resources / "journal" / "app").mkdir(parents=True)
    (resources / "journal" / "app" / "app.py").touch()
    (app / "Contents" / "Info.plist").touch()
    launcher = resources / "launcher.json"
    launcher.write_text('{"python":"/developer/venv/bin/python","project":"journal","port":8600}')
    with pytest.raises(release.ReleaseError, match="reviewed bundle-relative contract"):
        release.validate_release_app(app, "motivewave-journal.jar")

    launcher.write_text('{"executable":"/tmp/backend","project":"journal","port":8600}')
    with pytest.raises(release.ReleaseError, match="reviewed bundle-relative contract"):
        release.validate_release_app(app, "motivewave-journal.jar")


@pytest.mark.parametrize("magic", sorted(release.MACHO_MAGICS))
def test_macho_magic_detection_is_pure_and_handles_fat_binaries(tmp_path, magic):
    binary = tmp_path / "binary"
    binary.write_bytes(magic + b"synthetic")
    assert release.is_macho(binary)
    text = tmp_path / "text"
    text.write_text("not executable code")
    assert not release.is_macho(text)
