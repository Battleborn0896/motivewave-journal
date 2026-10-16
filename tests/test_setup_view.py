"""The simple Setup view explains automatic capture without leaking configuration paths."""

from contextlib import contextmanager
from types import SimpleNamespace

from views import setup


class FakeStreamlit:
    def __init__(self):
        self.markdowns = []
        self.infos = []
        self.captions = []
        self.expanders = []
        self.buttons = []

    def markdown(self, value):
        self.markdowns.append(value)

    def info(self, value):
        self.infos.append(value)

    def caption(self, value):
        self.captions.append(value)

    def button(self, label, **kwargs):
        self.buttons.append((label, kwargs))
        return False

    @contextmanager
    def expander(self, label, *, expanded=False):
        self.expanders.append((label, expanded))
        yield


def link(state='Connected', tone='good', version='0.3.1'):
    label = state + (f' · study {version}' if state == 'Connected' and version else '')
    return SimpleNamespace(state=state, tone=tone, label=label)


def test_default_view_is_path_free_and_explains_the_complete_capture_loop(monkeypatch):
    fake = FakeStreamlit()
    statuses = []
    monkeypatch.setattr(setup, 'st', fake)
    monkeypatch.setattr(setup.theme, 'section', lambda *args: None)
    monkeypatch.setattr(setup.theme, 'status', lambda *args, **kwargs: statuses.append((args, kwargs)))
    monkeypatch.setattr(setup.common, 'study_link', lambda journal: ('MotiveWave', {}, {}, link()))

    journal = SimpleNamespace(path='/private/secret/bridge/motivewave.ndjson')
    setup.render(journal, [])

    visible = ' '.join(fake.markdowns + fake.infos + fake.captions + [str(statuses)])
    assert 'Connected' in visible and 'MotiveWave is sending data' in visible
    assert 'no export or import step' not in visible  # section copy is owned by the shared theme helper
    assert 'Fills and observed ticks are sent to this journal automatically' in visible
    assert 'flat-to-flat for one provider, account, and exact contract' in visible
    assert 'Scale-ins and partial exits stay inside that same trade' in visible
    assert 'reversal closes the old trade' in visible
    assert 'separate Replay journal' in visible and 'real tick data' in visible
    assert '/private/secret' not in visible and 'motivewave.ndjson' not in visible
    assert fake.expanders == [('Advanced setup', False)]


def test_waiting_status_uses_controlled_copy_instead_of_raw_error_or_path(monkeypatch):
    shown = []
    raw = '/Users/someone/private/motivewave.ndjson could not be opened'
    monkeypatch.setattr(
        setup.common,
        'study_link',
        lambda journal: ('MotiveWave', {'path': raw}, {'error': raw}, link('Waiting For Study', 'warning', '')),
    )
    monkeypatch.setattr(setup.theme, 'status', lambda *args, **kwargs: shown.append((args, kwargs)))

    setup.render_status(object())

    rendered = str(shown)
    assert 'Waiting For Study' in rendered
    assert 'Waiting for the MotiveWave Journal study to start sending data' in rendered
    assert raw not in rendered


def test_advanced_renderer_is_a_parent_wiring_point(monkeypatch):
    fake = FakeStreamlit()
    calls = []
    monkeypatch.setattr(setup, 'st', fake)
    monkeypatch.setattr(setup.theme, 'section', lambda *args: None)
    monkeypatch.setattr(setup, 'render_status', lambda journal: None)
    journal, rows = object(), [{'id': 'trade-1'}]

    setup.render(journal, rows, advanced=lambda got_journal, got_rows: calls.append((got_journal, got_rows)))

    assert calls == [(journal, rows)]
    assert fake.expanders == [('Advanced setup', False)]
    assert not any('parent app' in caption for caption in fake.captions)
