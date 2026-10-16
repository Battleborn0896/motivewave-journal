"""Offline, independently implemented node canvas. Backend validates every saved graph."""
from pathlib import Path
import streamlit.components.v1 as components

_editor = components.declare_component('journal_checklist_editor', path=str(Path(__file__).parent / 'components' / 'checklist'))


def show(graph, tags, key):
    return _editor(graph=graph, tags=[{'id': id, 'name': t['name']} for id, t in tags.items()], key=key, default=None)
