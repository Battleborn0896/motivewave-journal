"""Shared by every test module."""
import pytest


@pytest.fixture(autouse=True)
def extensions_folder(tmp_path, monkeypatch):
    """A MotiveWave Extensions folder of each test's own: no test sees or changes the real one, and Settings'
    'is the study installed' answer is the same on every machine (no study until a test puts one there)."""
    folder = tmp_path / 'MotiveWave Extensions'
    monkeypatch.setenv('MOTIVEWAVE_EXTENSIONS', str(folder))
    return folder
