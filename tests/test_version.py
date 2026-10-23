"""One app version everywhere it is written down, and the study keeps its own."""
import plistlib
import re
from pathlib import Path

from mwjournal import paths

ROOT = Path(__file__).resolve().parent.parent


def test_the_app_version_is_the_same_everywhere_it_is_declared():
    pyproject = (ROOT / 'pyproject.toml').read_text(encoding='utf-8')
    assert re.search(r'^version = "([^"]+)"$', pyproject, re.M).group(1) == paths.VERSION
    with open(ROOT / 'desktop' / 'macos' / 'Info.plist', 'rb') as source:
        plist = plistlib.load(source)
    assert plist['CFBundleShortVersionString'] == paths.VERSION
    assert plist['CFBundleVersion'] == paths.VERSION.split('.', 1)[1]          # 0.4.5 -> 4.5
    swift = (ROOT / 'desktop' / 'macos' / 'Main.swift').read_text(encoding='utf-8')
    assert f'.applicationVersion: "{paths.VERSION}"' in swift
    template = (ROOT / '.github' / 'ISSUE_TEMPLATE' / 'bug_report.yml').read_text(encoding='utf-8')
    assert f'Journal {paths.VERSION};' in template


def test_the_study_keeps_its_own_version():
    java = (ROOT / 'study' / 'src' / 'main' / 'java' / 'org' / 'mwjournal' / 'study' / 'JournalPaths.java')
    source = java.read_text(encoding='utf-8')
    assert 'CONNECTOR_VERSION = "0.3.10"' in source
    assert paths.VERSION == '0.4.16'
    # The 'update the study' line on Settings compares against the study this app bundles.
    assert f'CONNECTOR_VERSION = "{paths.STUDY_VERSION}"' in source
    assert f'CONNECTOR = "{paths.STUDY_CONNECTOR}"' in source
    template = (ROOT / '.github' / 'ISSUE_TEMPLATE' / 'bug_report.yml').read_text(encoding='utf-8')
    assert f'Journal {paths.VERSION}; study {paths.STUDY_VERSION};' in template
