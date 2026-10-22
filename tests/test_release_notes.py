"""The text each tester release shows on GitHub (scripts/release_notes.py): what is new in that version, then
how to update from an older one and how to install."""
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'scripts'))

import release_notes  # noqa: E402
from mwjournal import paths  # noqa: E402


def test_a_release_shows_what_is_new_in_its_version_then_how_to_update_and_install():
    text = release_notes.notes(paths.VERSION)
    assert text.startswith(f"## What's new in {paths.VERSION}")
    assert "## What's new in" not in text[3:]                      # that version's section alone
    update, install = text.index('## Updating from an older version'), text.index('## First install')
    assert update < install
    assert 'no need to install the ones in between' in text and 'Install MotiveWave Study' in text
    assert 'quit MotiveWave completely and open it again, once' in text   # an older study, once


def test_every_version_in_the_readme_has_its_notes_and_the_workflow_uses_them():
    readme = (ROOT / 'README.md').read_text(encoding='utf-8')
    versions = re.findall(r"^## What's new in ([0-9.]+)", readme, re.M)
    assert paths.VERSION in versions and len(versions) > 3
    for version in versions:
        assert release_notes.notes(version).startswith(f"## What's new in {version}")
    assert release_notes.notes('9.9.9').startswith('## Updating from an older version')   # none: the guide alone
    run = subprocess.run([sys.executable, str(ROOT / 'scripts' / 'release_notes.py'), f'v{paths.VERSION}-alpha.3'],
                         capture_output=True, text=True, check=True)
    assert run.stdout.startswith(f"## What's new in {paths.VERSION}")                   # from the tag's name
    workflow = (ROOT / '.github' / 'workflows' / 'tester-builds.yml').read_text(encoding='utf-8')
    assert 'scripts/release_notes.py "${GITHUB_REF_NAME}" > release-notes.md' in workflow
    assert 'body_path: release-notes.md' in workflow
