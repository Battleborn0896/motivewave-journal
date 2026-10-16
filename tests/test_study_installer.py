"""The user-triggered study installer only replaces our named JAR and keeps a recovery copy."""
from pathlib import Path

import pytest

from views.connection import install_study


def test_installs_atomically_and_preserves_previous_copy(tmp_path):
    source = tmp_path / 'source' / 'motivewave-journal.jar'
    source.parent.mkdir()
    source.write_bytes(b'new study')
    extensions = tmp_path / 'MotiveWave Extensions'
    extensions.mkdir()
    target = extensions / source.name
    target.write_bytes(b'old study')

    installed, backup = install_study(source, extensions)

    assert installed == target and installed.read_bytes() == b'new study'
    assert backup == extensions / 'motivewave-journal.jar.previous'
    assert backup.read_bytes() == b'old study'
    assert (extensions / '.last_updated').is_file()
    assert not (extensions / '.motivewave-journal.jar.new').exists()


def test_refuses_missing_or_differently_named_files(tmp_path):
    with pytest.raises(ValueError, match='missing'):
        install_study(tmp_path / 'motivewave-journal.jar', tmp_path / 'extensions')
    wrong = tmp_path / 'something-else.jar'
    wrong.write_bytes(b'not ours')
    with pytest.raises(ValueError, match='missing'):
        install_study(wrong, tmp_path / 'extensions')
