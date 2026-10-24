"""Shared by every test module."""
import pytest


@pytest.fixture(autouse=True)
def extensions_folder(tmp_path, monkeypatch):
    """A MotiveWave Extensions folder of each test's own: no test sees or changes the real one, and Settings'
    'is the study installed' answer is the same on every machine (no study until a test puts one there)."""
    folder = tmp_path / 'MotiveWave Extensions'
    monkeypatch.setenv('MOTIVEWAVE_EXTENSIONS', str(folder))
    return folder


@pytest.fixture(autouse=True)
def open_folds_stay_open(monkeypatch):
    """AppTest sends back every widget's state with each run but an open fold's (an expander that keeps its state,
    such as a trade's Details). A browser sends that too, under the id the fold was drawn with, so the fold stays
    open as long as that id does. Sending it here makes AppTest run the way a browser does."""
    from streamlit.testing.v1.element_tree import ElementTree

    sent = ElementTree.get_widget_states

    def get_widget_states(tree):
        states = sent(tree)
        for node in tree:
            if node.type == 'expander' and node.proto.id and node.proto.expanded:
                states.widgets.add(id=node.proto.id, bool_value=True)
        return states

    monkeypatch.setattr(ElementTree, 'get_widget_states', get_widget_states)
