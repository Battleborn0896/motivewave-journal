"""Settings: one status line in one of three words, the one step that is missing in one line, Time Zone and
MAE / MFE Units and nothing else to set, three pages of real setup, one About line, and a sidebar with
nothing to unfold. Everything that was its own page before 0.4.5 stays two clicks from the sidebar. Disposable
journals only, in a temporary data home."""

import datetime as dt
from types import SimpleNamespace

import pytest

import common
import runtime
from mwjournal import Execution, Journal, PriceSample, paths, tagging
from test_app import (CREDIT, backup_controls, heartbeat, helped, listed, menu_label, open_app, press, said, show,
                      states, status_of, study_writes, wait_until, widget)
from test_capture import fill, lines
from test_chart import seed_preview, seed_ticks
from views import connection, setup


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    monkeypatch.setenv('MWJ_HOME', str(tmp_path))
    yield tmp_path
    runtime.stop_all()


def link(state='Recording', tone='good', version='0.3.1', *, heard=None, connector=paths.STUDY_CONNECTOR):
    label = state + (f' · study {version}' if state == 'Recording' and version else '')
    return SimpleNamespace(state=state, tone=tone, label=label, version=version, heard=heard, connector=connector)


def order(app, *texts):
    """Where each text first appears among the page's markdown, top to bottom."""
    shown = [m.value for m in app.main.markdown]
    return [next(i for i, value in enumerate(shown) if text in value) for text in texts]


def steps_shown(app):
    """The steps on the page, known by the ? beside their line: never more than one."""
    return [name for name, (_, explained) in setup.STEPS.items() if helped(app.main.markdown, explained)]


def status_line(app):
    return next(m.value for m in app.main.markdown if 'data-state=' in m.value)


def install_the_study(folder):
    """What installing the study leaves in MotiveWave's Extensions folder."""
    folder.mkdir(parents=True, exist_ok=True)
    (folder / 'motivewave-journal.jar').write_bytes(b'study')


def live_fill(id, symbol, side, price, ts):
    return {'version': 1, 'type': 'execution', 'mode': 'live', 'provider': 'motivewave-native:workspace:live',
            'account': 'SIM', 'symbol': symbol, 'execution_id': id, 'side': side, 'qty': 1, 'price': price, 'ts': ts,
            'point_value': 20, 'commission': 1, 'currency': 'USD', 'native_position_id': 'p-' + symbol}


def live_bar(symbol, price, ts):
    return {'version': 1, 'type': 'bar', 'mode': 'live', 'provider': 'motivewave-bars:1m', 'symbol': symbol, 'ts': ts,
            'resolution_seconds': 60, 'open': price, 'high': price + 1, 'low': price - 1, 'close': price, 'volume': 3}


# ---------------------------------------------------------------- the first page

def test_the_first_page_reads_status_then_the_missing_step_then_two_settings_then_the_pages():
    app = open_app('Settings')
    assert states(app.main) == ['Not recording'] and steps_shown(app) == ['install']
    status, step, pages = order(app, "data-state='Not recording'", 'then add the study to your chart', '>More Settings<')
    assert status < step < pages
    # No heading repeats the page's name, and the step is one plain line: no box, no title.
    assert not said(app.main.markdown, "<div class='mwj-section'>Settings<") and not said(app.main.markdown, '**Install')
    # Time Zone and MAE / MFE Units: nothing else to set here, and no Advanced heading. There is no MAE / MFE
    # Accuracy, as only recorded ticks measure a trade, and no weekends to hide: the calendar shows them when traded.
    assert [s.label for s in app.main.selectbox] == ['Time Zone', 'MAE / MFE Units']
    assert not app.main.checkbox and not said(app.main.markdown, '>Advanced<')
    assert not app.main.expander and not app.main.get('popover')         # nothing that folds
    assert not any('filter' in s.label.lower() for s in app.main.selectbox)  # the saved filter lives in Filters
    assert [b.label for b in app.main.button if b.key and b.key.startswith('setup-open-')] == [
        'Tags, Playbooks & Checklists ›', 'Data ›']
    assert common.NAV_LABELS['Settings'] == 'Settings' and setup.LABELS[setup.HOME] == 'Settings'


def test_the_first_page_has_no_paths_raw_errors_or_paragraphs_of_jargon():
    app = open_app('Settings')
    visible = ' '.join(e.value for kind in ('markdown', 'info', 'code') for e in getattr(app.main, kind))
    assert str(paths.bridge_file('live')) not in visible and 'motivewave.ndjson' not in visible
    assert 'Scale-ins and partial exits' not in visible                    # how trades are made is in the ?
    assert helped(app.main.markdown, 'flat to flat') and helped(app.main.markdown, 'Scale-ins and partial exits')
    assert helped(app.main.markdown, "real ticks of its holding time from MotiveWave's history")


def test_when_the_study_records_settings_is_one_green_line_naming_the_contracts_and_nothing_else():
    now = dt.datetime.now(dt.timezone.utc).replace(microsecond=0)
    at = lambda minutes: (now - dt.timedelta(minutes=minutes)).isoformat()
    study_writes([heartbeat(), live_bar('ESZ6', 5000, at(2)), live_bar('NQZ6', 20000, at(2)),
                  live_fill('n1', 'NQZ6', 'buy', 20000, at(6)), live_fill('n2', 'NQZ6', 'sell', 20004, at(4))])
    app = open_app('Settings')
    wait_until(lambda: status_of().get('heartbeat') and Journal().trades())
    app.run()
    line = status_line(app)
    assert states(app.main) == ['Recording'] and states(app.sidebar) == ['Recording']
    assert f'>Recording · NQZ6, ESZ6 · study {paths.STUDY_VERSION}<' in line              # traded first
    assert "data-state='Recording'" in line and f'color:{setup.theme.UP}' in line            # green
    assert 'mwj-status-note' not in line and steps_shown(app) == []                          # one line, no step
    assert not any(b.label == setup.INSTALL for b in app.button)
    assert helped(app.main.markdown, 'prices or fills for in the last hour')


def test_a_study_that_has_reported_no_prices_yet_leaves_the_contracts_out():
    study_writes([heartbeat()])
    app = open_app('Settings')
    wait_until(lambda: status_of().get('heartbeat'))
    app.run()
    assert f'>Recording · study {paths.STUDY_VERSION}<' in status_line(app) and steps_shown(app) == []


def test_before_the_study_reports_in_settings_asks_to_install_it_then_to_add_it_to_a_chart(extensions_folder):
    app = open_app('Settings')
    assert steps_shown(app) == ['install'] and said(app.main.markdown, 'then add the study to your chart')
    assert 'mwj-status-note' not in status_line(app)                  # the step says what to do, once
    assert f'color:{setup.theme.MUTED}' in status_line(app)           # grey: nothing is wrong, nothing records yet
    if connection.JAR.is_file():
        press(app, setup.INSTALL)
        assert (extensions_folder / 'motivewave-journal.jar').read_bytes() == connection.JAR.read_bytes()
        # The click itself redraws the page: the next step is already shown, and the button is gone.
        assert steps_shown(app) == ['chart'] and not app.success
        assert not any(b.label == setup.INSTALL for b in app.button)
    else:
        install_the_study(extensions_folder)
    app.run()
    assert steps_shown(app) == ['chart'] and states(app.main) == ['Not recording']
    step = next(m for m in app.main.markdown if 'Add **Journal → MotiveWave Journal**' in m.value)
    assert step.value.startswith('Add **Journal → MotiveWave Journal** to the chart you trade.')
    assert 'contract chart (ESZ6) or a continuous one (ES) both work' in step.proto.help
    # Shown only until the study reports in, so it never lingers once the study is on a chart.
    assert "Not in MotiveWave's study list yet? Quit MotiveWave and open it again." in step.value
    assert 'There is nothing to set.' in step.value and 'File' not in step.value
    assert not any(b.label == setup.INSTALL for b in app.button)


def test_an_empty_replay_journal_says_no_replay_trades_yet_until_its_first_trade():
    study_writes([heartbeat()])
    app = open_app()
    wait_until(lambda: status_of().get('heartbeat'))
    app.radio(key='journal-environment').set_value('Replay').run()
    assert not app.exception and states(app.sidebar) == ['No replay trades yet']
    chip = next(m.value for m in app.sidebar.markdown if 'data-state=' in m.value)
    assert f'color:{setup.theme.MUTED}' in chip and 'Settings ›' in chip                       # grey
    assert said(app.info, 'No replay trades yet. Start a replay in MotiveWave with the study on its chart.')
    assert not said(app.info, 'Replay journal')                     # no banner on every page
    # Settings has the same three words and steps as Live: no replay variant of the step.
    show(app, 'Settings')
    assert states(app.main) == ['Not recording'] and steps_shown(app) == ['chart']   # installed: Live hears it
    assert said(app.main.markdown, 'to the chart you trade') and not said(app.main.markdown, 'chart you replay')
    assert not any(b.label == setup.INSTALL for b in app.button)
    # The first replay trade: the chip says the study's state again.
    study_writes([heartbeat(mode='replay'), fill('r1')], 'replay')
    wait_until(lambda: Journal(dataset='replay').executions() and status_of('replay').get('heartbeat'))
    app.run()
    assert not app.exception and states(app.sidebar) == ['Recording']


def test_a_study_that_has_gone_quiet_is_asked_to_open_motivewave():
    study_writes([heartbeat(age=600)])
    app = open_app('Settings')
    wait_until(lambda: status_of().get('heartbeat'))
    app.run()
    assert states(app.main) == ['Not recording'] and steps_shown(app) == ['open']
    assert said(app.main.markdown, 'Open MotiveWave with the study on your chart.')
    assert f'color:{setup.theme.MUTED}' in status_line(app)
    assert not any(b.label == setup.INSTALL for b in app.button)


def test_a_bridge_file_the_app_cannot_read_needs_attention_and_never_asks_to_install_the_study_again():
    """A failed read keeps the study's last report, so Settings says what is wrong and does not send a study that is
    on its chart back to Install or to adding it to a chart."""
    study_writes([heartbeat()])
    app = open_app('Settings')
    wait_until(lambda: status_of().get('heartbeat'))
    app.run()
    assert states(app.main) == ['Recording'] and steps_shown(app) == []
    study_writes([{'version': 1, 'type': 'tick', 'mode': 'live'}])          # a line the reader cannot take
    wait_until(lambda: status_of().get('error'))
    assert status_of().get('heartbeat')                                     # what the study said last is kept
    app.run()
    assert states(app.main) == ['Needs attention'] and said(app.main.markdown, 'Open Data → Connection')
    assert steps_shown(app) == [] and not any(b.label == setup.INSTALL for b in app.button)


@pytest.mark.parametrize('version', ['0.3.6', '0.3.5', '0.3.2', '0.1.0'])
def test_a_study_older_than_the_bundled_one_gets_only_the_update_step(version):
    study_writes([heartbeat(version=version)])
    app = open_app('Settings')
    wait_until(lambda: status_of().get('heartbeat'))
    app.run()
    assert states(app.main) == ['Recording'] and steps_shown(app) == ['update']
    line = f'Study {version} is out of date; this app comes with {paths.STUDY_VERSION}'
    assert said(app.main.markdown, line)
    assert 'mwj-status-note' not in status_line(app)
    assert not said(app.sidebar.markdown, line)
    if connection.JAR.is_file():
        assert said(app.main.markdown, 'click Install MotiveWave Study')
        press(app, setup.INSTALL)                  # MotiveWave reports the old study until it starts the new one
        # Straight after the click, without another run: the line says what is left, never 'click Install'.
        installed = (f'Study {paths.STUDY_VERSION} is installed: MotiveWave switches to it in a few seconds.'
                     if version == '0.3.6' else       # a study from 0.3.6 on hands over to the new one by itself
                     f'Study {paths.STUDY_VERSION} is installed and MotiveWave is still running {version}. '
                     'Quit MotiveWave completely and open it again.')
        assert steps_shown(app) == ['update'] and not said(app.main.markdown, 'click Install')
        assert said(app.main.markdown, installed)
        assert not any(b.label == setup.INSTALL for b in app.main.button)
        app.run()
        assert steps_shown(app) == ['update'] and said(app.main.markdown, installed)
        # Another window, or the app opened again: the study in the Extensions folder says the same.
        again = open_app('Settings')
        assert steps_shown(again) == ['update'] and said(again.main.markdown, installed)
        assert not any(b.label == setup.INSTALL for b in again.main.button)


def test_the_installed_studys_line_says_what_motivewave_does_next(extensions_folder):
    """Installed and waiting: a running study from 0.3.6 on hands over by itself, an older one needs MotiveWave
    quit and opened again, and with MotiveWave closed the new one starts when it opens."""
    if not connection.JAR.is_file():
        pytest.skip('No built study jar in this checkout.')
    extensions_folder.mkdir(parents=True, exist_ok=True)
    (extensions_folder / connection.JAR.name).write_bytes(connection.JAR.read_bytes())
    now = dt.datetime.now(dt.timezone.utc)
    on = lambda heard, version: SimpleNamespace(connector=paths.STUDY_CONNECTOR, version=version, heard=heard)
    assert setup.update_line(on(now, '0.3.6')).endswith('MotiveWave switches to it in a few seconds.')
    assert setup.update_line(on(now, '0.3.5')).endswith(
        'still running 0.3.5. Quit MotiveWave completely and open it again.')
    assert setup.update_line(on(now - dt.timedelta(minutes=10), '0.3.6')).endswith(
        'open MotiveWave and it starts the new one.')


def test_the_bundled_study_or_another_writer_gets_no_update_step():
    study_writes([heartbeat()])
    app = open_app('Settings')
    wait_until(lambda: status_of().get('heartbeat'))
    app.run()
    assert states(app.main) == ['Recording'] and steps_shown(app) == []
    on = lambda **fields: SimpleNamespace(**{'connector': paths.STUDY_CONNECTOR, 'version': '', **fields})
    assert common.study_outdated(on(version='0.3.2')) and common.study_outdated(on(version='0.2.10'))
    assert not common.study_outdated(on(version=paths.STUDY_VERSION)) and not common.study_outdated(on(version='0.3.10'))
    assert not common.study_outdated(on(version='0.4.0')) and not common.study_outdated(on())
    assert not common.study_outdated(on(version='0.3.2-beta'))                 # not a version this app wrote
    assert not common.study_outdated(on(version='0.1.0', connector='another-writer'))
    assert not common.study_outdated(SimpleNamespace(state='Recording'))
    assert setup.update_line(on(version='0.3.2')).startswith(f'Study 0.3.2 is out of date; this app comes with {paths.STUDY_VERSION}: ')
    assert setup.update_line(on(version=paths.STUDY_VERSION)) is None


def test_the_step_follows_what_is_known_of_the_study_never_the_name_of_its_state():
    now = dt.datetime.now(dt.timezone.utc)
    quiet = now - dt.timedelta(seconds=common.STALE_AFTER + 5)
    current = paths.STUDY_VERSION
    # Recording: nothing to do, whatever the Extensions folder holds (a study that reports in is installed).
    assert setup.needed_step(link(version=current, heard=now), installed=True) is None
    assert setup.needed_step(link(version=current, heard=now), installed=False) is None
    # Not installed, then installed but never heard from, then heard from before but gone quiet.
    assert setup.needed_step(link('Not recording', 'neutral', ''), installed=False) == 'install'
    assert setup.needed_step(link('Not recording', 'neutral', ''), installed=True) == 'chart'
    assert setup.needed_step(link('Not recording', 'neutral', current, heard=quiet), installed=True) == 'open'
    # An older study: the update comes first, recording or not.
    assert setup.needed_step(link(version='0.3.2', heard=now), installed=True) == 'update'
    assert setup.needed_step(link('Not recording', 'neutral', '0.3.2', heard=quiet), installed=True) == 'update'
    # The same facts under any word give the same step.
    for state in (common.RECORDING, common.NOT_RECORDING, common.ATTENTION, 'Anything'):
        assert setup.needed_step(link(state, version=current, heard=quiet), installed=True) == 'open'
        assert setup.needed_step(link(state, version=current, heard=now), installed=True) is None
    assert setup.needed_step(None, installed=True) is None
    assert set(setup.STEPS) == {'install', 'chart', 'open', 'update'}
    assert setup.STEPS['open'][0] == 'Open MotiveWave with the study on your chart.'
    assert setup.STEPS['chart'][0].endswith("Not in MotiveWave's study list yet? Quit MotiveWave and open it again.")


def test_the_status_uses_one_controlled_line_instead_of_a_raw_error_or_path(monkeypatch):
    shown = []
    raw = '/Users/someone/private/motivewave.ndjson could not be opened'
    for state in (common.NOT_RECORDING, common.ATTENTION):
        monkeypatch.setattr(setup.common, 'study_link',
                            lambda journal, state=state: ('MotiveWave', {'path': raw}, {'error': raw}, link(state, 'critical', '')))
        monkeypatch.setattr(setup.theme, 'status', lambda *args, **kwargs: shown.append((args, kwargs)))
        setup.render_status(SimpleNamespace(dataset='live'))
    rendered = str(shown)
    assert 'Not recording' in rendered and 'Open Data → Connection' in rendered and raw not in rendered
    assert shown[0][1]['notes'] == [] and shown[1][1]['notes'] == [(setup.STATUS_COPY[common.ATTENTION], True)]
    assert set(setup.STATUS_COPY) == {common.RECORDING, common.NOT_RECORDING, common.ATTENTION}
    assert all(len(copy.split('. ')) <= 2 for copy in setup.STATUS_COPY.values())      # one line each
    # No line asks the trader to change a setting of the study.
    for text in [*setup.STATUS_COPY.values(), *(part for step in setup.STEPS.values() for part in step)]:
        assert not any(word in text for word in ('Capture Fills', 'Real Tick Data', 'Advanced tab', "study's Advanced"))


def test_the_status_is_one_element_in_every_state_so_its_five_second_redraw_leaves_nothing_behind():
    """status_fragment redraws the status every five seconds. A redraw with fewer elements than the one before can
    leave the old ones on screen (theme.status), so each state draws exactly one element at the same place, its
    lines inside it; only the step under it, drawn with the whole page, comes and goes."""
    app = open_app('Settings')

    def status():
        shown = [m.value for m in app.main.markdown]
        found = [i for i, value in enumerate(shown) if 'data-state=' in value]
        assert not app.exception and len(found) == 1
        return found[0], shown[found[0]]

    where, line = status()
    assert "data-state='Not recording'" in line and 'mwj-status-note' not in line
    study_writes([heartbeat()])
    wait_until(lambda: status_of().get('heartbeat'))
    app.run()
    assert status()[0] == where and "data-state='Recording'" in status()[1]
    study_writes([heartbeat('error', detail='Unsupported MotiveWave version')])
    wait_until(lambda: status_of().get('error'))
    app.run()
    assert status()[0] == where and "data-state='Needs attention'" in status()[1]
    assert 'Open Data → Connection' in status()[1] and 'mwj-status-note warn' in status()[1]
    assert not said(app.main.markdown, 'Unsupported MotiveWave version')       # the raw error is on Connection


def test_recording_names_the_contracts_the_study_wrote_recently_traded_ones_first(tmp_path):
    live = Journal(tmp_path / 'live.sqlite3')
    now = dt.datetime(2026, 10, 7, 15, 0, tzinfo=dt.timezone.utc)
    ago = lambda minutes: (now - dt.timedelta(minutes=minutes)).isoformat()
    live.ingest_prices([PriceSample('ESZ6', ago(2), 'motivewave-ticks', 5000, 5000),
                        PriceSample('NQZ6', ago(30), 'motivewave-bars:1m', 20001, 19999, 60),
                        PriceSample('CLZ6', ago(300), 'motivewave-ticks', 60, 60),               # hours ago
                        PriceSample('GCZ6', ago(1), 'my-data', 2000, 2000),                      # imported
                        PriceSample('YMZ6', ago(1), 'motivewave-ticks:history', 40000, 40000)])  # a backfill
    assert live.recent_contracts(now=now) == ['ESZ6', 'NQZ6']
    live.ingest([(Execution('motivewave-native:ws:live', 'SIM', 'RTYZ6', 'buy', 1, 2000, ago(20),
                            execution_id='r1'), {}),
                 (Execution('csv', 'SIM', 'MESZ6', 'buy', 1, 5000, ago(1), execution_id='m1'), {})])
    assert live.recent_contracts(now=now) == ['RTYZ6', 'ESZ6', 'NQZ6']
    assert live.recent_contracts(now=now + dt.timedelta(hours=3)) == []
    assert Journal(tmp_path / 'empty.sqlite3').recent_contracts(now=now) == []
    # Replay times are the replay's own clock: the run written last counts, whatever its date.
    replay = Journal(tmp_path / 'replay.sqlite3', dataset='replay')
    replay.ingest_prices([PriceSample('ESZ6', '2024-03-01T15:00:00Z', 'motivewave-replay:old:ticks', 1, 1)])
    replay.ingest_prices([PriceSample('NQZ6', '2022-01-03T14:30:00Z', 'motivewave-replay:new:ticks', 1, 1),
                          PriceSample('MNQZ6', '2022-01-03T14:00:00Z', 'motivewave-replay:new:simulated', 1, 1),
                          PriceSample('M2KZ6', '2022-01-03T12:00:00Z', 'motivewave-replay:new:ticks', 1, 1)])
    assert replay.recent_contracts() == ['MNQZ6', 'NQZ6']
    replay.ingest([(Execution('motivewave-native:ws:replay:new', 'REPLAY', 'NQZ6', 'buy', 1, 1,
                              '2022-01-03T14:20:00Z', execution_id='x'), {})])
    assert replay.recent_contracts() == ['NQZ6', 'MNQZ6']


# ---------------------------------------------------------------- the pages, two clicks from the sidebar

@pytest.mark.parametrize('page', [p for p in setup.PAGES if p != setup.HOME])
def test_each_settings_page_is_one_click_from_the_first_and_one_click_back(page):
    app = open_app('Settings')
    button = app.button(key='setup-open-' + page)
    assert button.label == setup.LABELS[page] + ' ›' and button.help == setup.PAGE_HELP[page]
    button.click().run()
    assert not app.exception and app.session_state[common.TOOL_KEY] == page
    assert app.button(key='setup-back').label == '← Settings'
    app.button(key='setup-back').click().run()
    assert not app.exception and app.session_state[common.TOOL_KEY] == setup.HOME
    assert said(app.main.markdown, '>More Settings<')


@pytest.mark.parametrize('page,tab', [(page, tab) for page, tabs in setup.TABS.items() for tab in tabs])
def test_every_tab_of_a_settings_page_has_its_own_link_on_the_first_page(page, tab):
    app = open_app('Settings')
    link_button = app.button(key=f'setup-tab-{page}-{tab}')
    assert link_button.label == tab
    link_button.click().run()
    assert not app.exception and app.session_state[common.TOOL_KEY] == page
    assert [t.label for t in app.main.tabs][:len(setup.TABS[page])] == list(setup.TABS[page])
    assert app.session_state[{'Tags': 'tag-settings-tabs-live', 'Data': 'data-tabs-live'}[page]] == tab


LANDMARKS = {
    'Tags': lambda app: any(t.label == 'New tag name' for t in app.text_input),
    'Checklists': lambda app: any(s.label == 'Checklist' for s in app.selectbox),
    'Dashboard': lambda app: any(s.label == 'Dashboard page' for s in app.selectbox),
    'Daily review': lambda app: any(t.label.startswith('What I learned') for t in app.text_area),
    'Live session': lambda app: (any(b.key == 'session-timer-live-play' for b in app.button)
                                 and [p.proto.popover.label for p in app.main.get('popover')][:2]
                                 == ['Check-Ins', 'Drafts']),
    'Connection': lambda app: any(e.label == 'Troubleshooting' for e in app.expander),
    'Journals': lambda app: any(t.label == 'New journal name' for t in app.text_input),
    'Import': lambda app: any(u.label == 'Fills CSV or Bridge File' for u in app.file_uploader),
    'Export': lambda app: backup_controls(app),
    'Settings': lambda app: any(s.label == 'Time Zone' for s in app.selectbox),
}


@pytest.mark.parametrize('view', LANDMARKS)
def test_every_page_that_was_on_settings_is_at_most_two_clicks_from_the_sidebar(home, view):
    """show() clicks the sidebar, then at most one link, tab or day header: what a person would click."""
    seed_preview(home)
    app = open_app('Edge')                         # somewhere else first, so the sidebar click is a real move
    show(app, view)
    assert not app.exception and LANDMARKS[view](app)


def test_every_name_the_app_routes_by_has_a_place():
    for view in (*common.TOOLS, 'Settings', 'Dashboard', 'Daily review', 'Live session', 'Edge', 'Trades'):
        route = common.navigation(view)
        assert route[common.VIEW_KEY] in common.PRIMARY_VIEWS
        if route[common.VIEW_KEY] == 'Settings':
            assert route[common.TOOL_KEY] in setup.PAGES and setup.page_for(view) == route[common.TOOL_KEY]
            assert route[common.TAB_KEY] is None or route[common.TAB_KEY] in setup.TABS[route[common.TOOL_KEY]]
    # One name for the first page. A page kept under a name it no longer has (Setup, General), or a page that is gone
    # (Ratings, the Rating Systems of 0.4.16), opens the first.
    assert [view for view, (page, _) in common.SETTINGS_ROUTES.items() if page == setup.HOME] == ['Settings']
    assert setup.page_for('Setup') == setup.page_for('General') == setup.page_for('Ratings') == setup.page_for(None) == setup.HOME
    assert setup.page_for('Unknown') == setup.HOME
    assert set(setup.LABELS) == set(setup.PAGES) == {setup.HOME, *setup.PAGE_HELP}
    assert common.navigation('Dashboard') == {common.VIEW_KEY: 'Analytics', common.TAB_KEY: 'Dashboards'}
    assert common.navigation('Daily review') == {common.VIEW_KEY: 'Trades', common.REPORT_KEY: True}


def test_choosing_settings_in_the_sidebar_always_opens_its_first_page():
    app = open_app('Tags')
    show(app, 'Trades')
    app.segmented_control(key=common.VIEW_KEY).set_value('Settings').run()
    assert not app.exception and app.session_state[common.TOOL_KEY] == setup.HOME
    # Clicking Settings again while one of its pages is open clears the control in a browser, which AppTest
    # cannot do; that path was checked by hand in the browser.


def test_the_connection_badge_opens_settings_from_any_page(home):
    seed_preview(home)
    app = open_app('Edge')
    assert states(app.sidebar) == ['Not recording'] and said(app.sidebar.markdown, 'Settings ›')
    assert app.button(key='connection-badge-open').label == 'Open Settings'
    app.button(key='connection-badge-open').click().run()
    assert not app.exception and app.segmented_control(key=common.VIEW_KEY).value == 'Settings'
    assert app.session_state[common.TOOL_KEY] == setup.HOME and states(app.main) == ['Not recording']


def test_the_parent_draws_every_page_but_the_first(monkeypatch):
    class Fake:
        session_state = {common.TOOL_KEY: 'Tags'}
        buttons = []

        def button(self, label, **kwargs):
            self.buttons.append(label)
            return False

    fake, drawn = Fake(), []
    monkeypatch.setattr(setup, 'st', fake)
    monkeypatch.setattr(setup, 'home', lambda *args, **kwargs: drawn.append('home'))
    setup.render(object(), [], tool=drawn.append)
    assert drawn == ['Tags'] and fake.buttons == ['← Settings']
    fake.session_state[common.TOOL_KEY] = 'Import'                         # an old page name: its new page
    setup.render(object(), [], tool=drawn.append)
    assert drawn == ['Tags', 'Data'] and fake.session_state[common.TOOL_KEY] == 'Data'
    fake.session_state[common.TOOL_KEY] = 'Setup'                          # the first page's old name: the first page
    setup.render(object(), [], tool=drawn.append)
    assert drawn == ['Tags', 'Data', 'home'] and fake.session_state[common.TOOL_KEY] == setup.HOME


# ---------------------------------------------------------------- the two settings, and the saved filter

def test_units_and_the_time_zone_are_set_on_settings_and_saved_for_the_journal(home):
    journal = seed_ticks(seed_preview(home))              # recorded ticks: what MAE / MFE are measured from
    app = open_app('Settings')
    widget(app.selectbox, 'MAE / MFE Units').set_value('points').run()
    widget(app.selectbox, 'Time Zone').set_value('Europe/London').run()
    assert not app.exception
    assert journal.display_settings()['excursion_unit'] == 'points'
    assert common.display_zone(Journal()) == common.display_zone(Journal(dataset='replay')) == 'Europe/London'
    show(app, 'Trades')
    assert widget(app.metric, 'MFE').value.endswith('pts')


@pytest.mark.parametrize('page', ['Trades', 'Edge'])
def test_the_saved_filter_is_chosen_in_the_filters_menu_and_applies_to_every_page(home, page):
    journal = seed_preview(home)
    journal.save_filter('Shorts only', {'directions': ['short']})
    app = open_app(page)
    choice = widget(app.selectbox, 'Saved Filter on Every Page')
    assert choice.options == ['All trades', 'Shorts only']
    choice.set_value('Shorts only').run()
    assert not app.exception and menu_label(app) == 'Filters · 1'          # the saved filter in use is one filter on
    assert not said(app.main.caption, 'Saved filter')                     # said by the button, not by a line
    for other in ('Settings', 'Edge', 'Trades'):       # the selectbox is drawn on two pages; the choice holds on all
        show(app, other)
    show(app, 'Edge')
    assert said(app.info, 'Statistics need') and widget(app.selectbox, 'Saved Filter on Every Page').value == 'Shorts only'
    assert menu_label(app) == 'Filters · 1'
    show(app, 'Trades')
    assert said(app.info, 'No trades match these filters')         # the menu stays, so the filter can be undone
    widget(app.selectbox, 'Saved Filter on Every Page').set_value('').run()
    assert menu_label(app) == 'Filters' and not said(app.info, 'No trades match')
    show(app, 'Edge')
    assert any(m.label == 'Net P&L' for m in app.metric)


def round_trips(journal, *trades):
    """A closed trade for each (account, contract, day, hour): in on the hour, out five minutes later."""
    for account, symbol, day, hour in trades:
        id = f'{account}-{symbol}-{day}-{hour}'
        journal.ingest([(Execution('motivewave', account, symbol, 'buy', 1, 100, f'{day}T{hour}:00:00Z',
                                   execution_id=id + '-in'), {}),
                        (Execution('motivewave', account, symbol, 'sell', 1, 101, f'{day}T{hour}:05:00Z',
                                   execution_id=id + '-out'), {})])
    return journal


def save(app, name):
    """Saving a filter, once the Filters menu is open: type a name, click Save Filter."""
    widget(app.text_input, 'Filter Name').set_value(name)
    return press(app, 'Save Filter')


def test_saving_a_filter_is_a_name_and_one_click_and_shows_the_same_trades():
    journal = round_trips(Journal(), ('paper', 'ESZ6', '2026-10-07', 14), ('paper', 'NQZ6', '2026-10-07', 15))
    app = open_app(review=False)
    widget(app.multiselect, 'Markets').set_value(['NQ']).run()
    shown = listed(app)
    assert len(shown) == 1
    save(app, 'NQ only')
    assert journal.workspace()['filters']['NQ only']['markets'] == ['NQ']
    # The new filter is the one in use and the menu's own filters are empty again: the same trade, listed alone.
    assert widget(app.selectbox, 'Saved Filter on Every Page').value == 'NQ only' and menu_label(app) == 'Filters · 1'
    assert widget(app.multiselect, 'Markets').value == [] and widget(app.text_input, 'Filter Name').value == ''
    # New widgets, not the old ones emptied: a browser goes on showing a widget's choices under the same key.
    assert widget(app.multiselect, 'Markets').key == 'filter-markets-1-live'
    assert widget(app.text_input, 'Filter Name').key == 'save-filter-name-1-live'
    assert listed(app) == shown
    widget(app.selectbox, 'Saved Filter on Every Page').set_value('').run()
    assert len(listed(app)) == 2


def test_saving_on_stats_is_the_same_two_steps_and_keeps_the_same_numbers():
    round_trips(Journal(), ('paper', 'ESZ6', '2026-10-07', 14), ('paper', 'NQZ6', '2026-10-07', 15),
                ('paper', 'NQZ6', '2026-10-08', 15))
    app = open_app('Edge')
    assert not any(e.label == 'Save a Filter' for e in app.expander)
    widget(app.multiselect, 'Markets').set_value(['NQ']).run()
    numbers = [(m.label, m.value) for m in app.metric]
    assert widget(app.metric, 'Net P&L').value == '+$40.00'
    save(app, 'NQ only')
    assert menu_label(app) == 'Filters · 1' and widget(app.multiselect, 'Markets').value == []
    assert [(m.label, m.value) for m in app.metric] == numbers


def test_saving_while_a_saved_filter_is_on_saves_the_two_together_as_one_filter():
    journal = round_trips(Journal(), ('paper', 'ESZ6', '2026-10-07', 14), ('paper', 'NQZ6', '2026-10-07', 15),
                          ('second', 'NQZ6', '2026-10-07', 16))
    journal.save_filter('Paper', {'accounts': ['paper']})
    app = open_app(review=False)
    widget(app.selectbox, 'Saved Filter on Every Page').set_value('Paper').run()
    widget(app.multiselect, 'Markets').set_value(['NQ']).run()
    shown = listed(app)
    assert len(shown) == 1
    save(app, 'Paper NQ')
    saved = journal.workspace()['filters']
    assert (saved['Paper NQ']['accounts'], saved['Paper NQ']['markets']) == (['paper'], ['NQ'])
    assert saved['Paper']['markets'] == []                        # the filter that was in use is left as it was
    assert widget(app.selectbox, 'Saved Filter on Every Page').value == 'Paper NQ' and listed(app) == shown
    widget(app.multiselect, 'Directions').set_value(['long']).run()
    save(app, 'Paper NQ')                                         # the same name replaces it
    assert journal.workspace()['filters']['Paper NQ']['directions'] == ['long'] and listed(app) == shown


def test_a_filter_no_single_rule_can_say_is_refused_in_one_line_and_nothing_changes(home):
    journal = seed_preview(home)
    trade = journal.trades()[0]
    journal.set_trade_tag_group(trade, 'Setup', ['Breakout'])
    journal.set_trade_tag_group(trade, 'Mindset', ['Patient'])
    journal.save_filter('Breakout or pullback', {'tags_any': ['Breakout', 'Pullback']})
    app = open_app(review=False)
    widget(app.selectbox, 'Saved Filter on Every Page').set_value('Breakout or pullback').run()
    widget(app.multiselect, 'Tags').set_value(['Patient', 'PAPER FIXTURE']).run()
    widget(app.radio, 'Tag Match').set_value('Any selected').run()
    shown = listed(app)
    assert len(shown) == 1
    save(app, 'Mixed')
    assert [e.value for e in app.error] == ["Two different 'any of these tags' choices cannot be saved as one filter."]
    assert 'Mixed' not in journal.workspace()['filters'] and widget(app.text_input, 'Filter Name').value == 'Mixed'
    assert widget(app.multiselect, 'Tags').value == ['Patient', 'PAPER FIXTURE'] and listed(app) == shown
    # Any of one tag is that tag required: one rule says that exactly, so it saves.
    widget(app.multiselect, 'Tags').set_value(['Patient']).run()
    save(app, 'Mixed')
    rule = journal.workspace()['filters']['Mixed']
    assert not app.error and len(rule['tags_all']) == 1 and len(rule['tags_any']) == 2 and listed(app) == shown


def test_only_a_narrowed_side_of_the_entry_dates_is_saved_so_a_new_days_trades_still_show():
    journal = round_trips(Journal(), *[('paper', 'ESZ6', f'2026-10-0{day}', 14) for day in (6, 7, 8)])
    app = open_app(review=False)
    assert widget(app.date_input, 'Entry Dates').value == (dt.date(2026, 10, 6), dt.date(2026, 10, 8))
    save(app, 'Everything')
    assert not {'start', 'end'} & set(journal.workspace()['filters']['Everything'])      # the full span: no limit
    widget(app.date_input, 'Entry Dates').set_value((dt.date(2026, 10, 7), dt.date(2026, 10, 8))).run()
    assert len(listed(app)) == 2
    save(app, 'From Oct 7')
    rule = journal.workspace()['filters']['From Oct 7']
    assert rule['start'] == '2026-10-07' and 'end' not in rule
    round_trips(journal, ('paper', 'ESZ6', '2026-10-09', 14))
    app.run()
    assert len(listed(app)) == 3 and widget(app.selectbox, 'Saved Filter on Every Page').value == 'From Oct 7'
    assert widget(app.date_input, 'Entry Dates').value == (dt.date(2026, 10, 7), dt.date(2026, 10, 9))


# What the one filter editor asks, wherever it is drawn: the Filters menu, a dashboard page and a dashboard widget.
EDITOR = {'Markets', 'Entry Dates', 'Accounts', 'Exact Contracts', 'Tags', 'Tag Match', 'Exclude Tags', 'Playbook',
          'Trade Status', 'Directions', 'Rating', 'Number Ranges'}


def editor_labels(app, prefix):
    """The labels of the widgets whose keys start with `prefix`."""
    kinds = ('multiselect', 'selectbox', 'date_input', 'radio', 'number_input', 'text_input')
    return {w.label for kind in kinds for w in getattr(app, kind) if w.key and w.key.startswith(prefix)}


def test_the_filters_menu_is_one_editor_with_short_title_case_labels(home):
    seed_preview(home)
    app = open_app(review=False)
    assert widget(app.multiselect, 'Number Ranges').options == ['Net P&L', 'R', 'MAE', 'MFE', 'Contracts',
                                                                'Minutes held']
    widget(app.multiselect, 'Number Ranges').set_value(['net']).run()
    assert len(listed(app)) == 1                                  # an empty box is no limit
    widget(app.number_input, 'Max Net P&L').set_value(0.0).run()
    assert not app.exception and not listed(app)                 # the fixture's trade made money
    assert not any(e.label == 'Save a Filter' for e in app.expander)
    labels = editor_labels(app, ('filter-', 'save-filter', 'global-saved-filter'))
    assert labels == {'Saved Filter on Every Page', *EDITOR, 'Min Net P&L', 'Max Net P&L', 'Filter Name'}
    assert all(title_case(label) for label in labels) and any(b.label == 'Save Filter' for b in app.button)


def test_page_filters_and_widget_filters_are_the_filters_menu_with_the_same_words(home):
    seed_preview(home)
    app = show(open_app(review=False), 'Dashboard')
    assert editor_labels(app, 'filter-') == editor_labels(app, 'page-filter-default-') == EDITOR
    assert editor_labels(app, 'widget-default-new-') == EDITOR
    for prefix in ('page-filter-default-', 'widget-default-new-'):
        assert app.multiselect(key=prefix + 'numbers-live').options == widget(app.multiselect, 'Number Ranges').options


def test_mae_and_mfe_limits_are_in_the_chosen_units(home):
    journal = seed_ticks(seed_preview(home))              # MAE 1 point, $100
    journal.set_excursion_unit('points')
    app = open_app(review=False)
    widget(app.multiselect, 'Number Ranges').set_value(['mae']).run()
    assert said(app.caption, 'MAE and MFE are in Points')
    assert [n.label for n in app.number_input if n.key.startswith('filter-')] == ['Min MAE', 'Max MAE']
    widget(app.number_input, 'Max MAE').set_value(2.0).run()
    assert len(listed(app)) == 1                                  # 1 point, not $100
    widget(app.number_input, 'Max MAE').set_value(0.5).run()
    assert not listed(app)


def test_a_stored_page_rule_opens_by_its_tags_names_now_and_keeps_what_the_old_editor_saved(home):
    journal = seed_preview(home)
    library = journal.tag_library()
    breakout, patient, fomo = (next(iter(tagging.ids(library, [name]))) for name in ('Breakout', 'Patient', 'FOMO'))
    # Only the old dashboard editor could require every tag and any tag at once.
    page = journal.save_dashboard('Mine', [{'kind': 'Summary', 'filters': {'directions': ['short']}}],
                                  filters={'tags_all': [breakout], 'tags_any': [patient, fomo], 'net_min': 5})
    journal.edit_tag(breakout, name='Clean breakout')
    app = show(open_app(review=False), 'Dashboard')
    widget(app.selectbox, 'Dashboard page').set_value(page).run()
    assert app.multiselect(key=f'page-filter-{page}-tags-live').value == ['Clean breakout']
    assert app.radio(key=f'page-filter-{page}-tag-match-live').value == 'All selected'
    assert app.number_input(key=f'page-filter-{page}-net_min-live').value == 5
    assert said(app.caption, 'Also only trades with any of ') and said(app.caption, '; Any selected replaces these.')
    assert menu_label(app, 'Page Filters') == 'Page Filters · 3'
    app.multiselect(key=f'page-filter-{page}-directions-live').set_value(['long'])
    press(app, 'Save Page Filters')
    rule = journal.workspace()['dashboards'][page]['filters']
    assert (rule['tags_all'], rule['tags_any']) == ([breakout], sorted([patient, fomo]))
    assert (rule['net_min'], rule['directions']) == (5, ['long'])
    assert menu_label(app, 'Page Filters') == 'Page Filters · 4'
    # A widget's form opens on the widget's own rule, and saving it keeps that rule.
    widget(app.selectbox, 'Edit widget').set_value(0).run()
    first = journal.workspace()['dashboards'][page]['widgets'][0]['id']
    assert app.multiselect(key=f'widget-{page}-{first}-directions-live').value == ['short']
    press(app, 'Save Widget')
    assert journal.workspace()['dashboards'][page]['widgets'][0]['filters']['directions'] == ['short']


def test_a_widget_moved_down_opens_on_its_own_filters(home):
    journal = seed_preview(home)
    page = journal.save_dashboard('Mine', [{'kind': 'Summary', 'title': 'Shorts', 'filters': {'directions': ['short']}},
                                           {'kind': 'Summary', 'title': 'Longs', 'filters': {'directions': ['long']}}])
    app = show(open_app(review=False), 'Dashboard')
    widget(app.selectbox, 'Dashboard page').set_value(page).run()
    widget(app.selectbox, 'Edit widget').set_value(0).run()
    press(app, '↓ Later')
    longs = journal.workspace()['dashboards'][page]['widgets'][0]
    assert longs['title'] == 'Longs' and widget(app.text_input, 'Widget title').value == 'Longs'
    assert app.multiselect(key=f"widget-{page}-{longs['id']}-directions-live").value == ['long']
    press(app, 'Save Widget')
    widgets = journal.workspace()['dashboards'][page]['widgets']
    assert [w['filters']['directions'] for w in widgets] == [['long'], ['short']]


def test_the_filters_button_counts_the_filters_on_and_clear_filters_empties_them():
    journal = round_trips(Journal(), ('paper', 'ESZ6', '2026-10-06', 14), ('paper', 'NQZ6', '2026-10-07', 15),
                          ('second', 'NQZ6', '2026-10-08', 15))
    journal.save_filter('Paper', {'accounts': ['paper']})
    app = open_app(review=False)
    assert menu_label(app) == 'Filters' and len(listed(app)) == 3
    widget(app.selectbox, 'Saved Filter on Every Page').set_value('Paper').run()
    assert menu_label(app) == 'Filters · 1' and len(listed(app)) == 2           # the saved filter in use counts 1
    widget(app.multiselect, 'Markets').set_value(['NQ']).run()
    assert menu_label(app) == 'Filters · 2'
    widget(app.multiselect, 'Directions').set_value(['long']).run()
    assert menu_label(app) == 'Filters · 3'
    assert widget(app.date_input, 'Entry Dates').value == (dt.date(2026, 10, 6), dt.date(2026, 10, 7))
    widget(app.date_input, 'Entry Dates').set_value((dt.date(2026, 10, 7), dt.date(2026, 10, 7))).run()
    assert menu_label(app) == 'Filters · 4'                     # one narrowed side: the full span is no filter
    widget(app.multiselect, 'Number Ranges').set_value(['net']).run()
    assert menu_label(app) == 'Filters · 4'                     # empty boxes limit nothing
    widget(app.number_input, 'Min Net P&L').set_value(0.0).run()
    assert menu_label(app) == 'Filters · 5' and len(listed(app)) == 1
    press(app, 'Clear Filters')
    assert menu_label(app) == 'Filters' and len(listed(app)) == 3
    assert widget(app.selectbox, 'Saved Filter on Every Page').value == ''          # All trades
    assert widget(app.multiselect, 'Markets').value == [] and widget(app.multiselect, 'Directions').value == []
    # New widgets, as after Save Filter: a browser goes on showing a widget's choices under the same key.
    assert widget(app.multiselect, 'Markets').key == 'filter-markets-1-live'
    assert set(journal.workspace()['filters']) == {'Paper'}                        # nothing saved is deleted


def test_a_filter_left_on_stats_shows_on_the_trades_button_and_clears_there():
    round_trips(Journal(), ('paper', 'ESZ6', '2026-10-07', 14), ('paper', 'NQZ6', '2026-10-07', 15))
    app = open_app('Edge')
    widget(app.multiselect, 'Markets').set_value(['NQ']).run()
    assert menu_label(app) == 'Filters · 1'
    show(app, 'Trades')
    assert menu_label(app) == 'Filters · 1' and len(listed(app)) == 1
    press(app, 'Clear Filters')
    assert menu_label(app) == 'Filters' and len(listed(app)) == 2


def test_a_saved_filter_is_renamed_and_deleted_beside_where_it_is_chosen():
    journal = round_trips(Journal(), ('paper', 'ESZ6', '2026-10-07', 14), ('second', 'NQZ6', '2026-10-07', 15))
    journal.save_filter('Paper', {'accounts': ['paper']})
    journal.save_filter('Second', {'accounts': ['second']})
    app = open_app(review=False)
    assert not any(b.label in ('Rename', 'Delete') for b in app.button)          # only beside a chosen filter
    widget(app.selectbox, 'Saved Filter on Every Page').set_value('Paper').run()
    shown = listed(app)
    assert len(shown) == 1
    widget(app.text_input, 'New Name').set_value('Second')
    press(app, 'Rename')
    assert said(app.error, 'already named Second') and set(journal.workspace()['filters']) == {'Paper', 'Second'}
    widget(app.text_input, 'New Name').set_value('Paper account')
    press(app, 'Rename')
    assert set(journal.workspace()['filters']) == {'Paper account', 'Second'}
    # Renamed, it stays the filter in use, and the name box is empty again.
    assert widget(app.selectbox, 'Saved Filter on Every Page').value == 'Paper account'
    assert menu_label(app) == 'Filters · 1' and listed(app) == shown and widget(app.text_input, 'New Name').value == ''
    press(app, 'Delete')
    assert set(journal.workspace()['filters']) == {'Second'}
    assert widget(app.selectbox, 'Saved Filter on Every Page').value == '' and menu_label(app) == 'Filters'
    assert len(listed(app)) == 2                                   # back to All trades


def test_dashboard_page_filters_still_save_on_their_own(home):
    journal = seed_preview(home)
    app = show(open_app(review=False), 'Dashboard')
    assert menu_label(app, 'Page Filters') == 'Page Filters'
    app.multiselect(key='page-filter-default-directions-live').set_value(['short'])
    press(app, 'Save Page Filters')
    page = next(iter(journal.workspace()['dashboards'].values()))
    assert page['filters']['directions'] == ['short'] and said(app.caption, '0 closed trades on this page')
    assert menu_label(app, 'Page Filters') == 'Page Filters · 1'
    assert not journal.workspace()['filters']                    # a dashboard's filters are not a saved filter


SMALL = {'a', 'an', 'and', 'as', 'at', 'by', 'for', 'from', 'in', 'into', 'of', 'on', 'or', 'the', 'to', 'with'}


def title_case(label):
    words = [w for w in label.replace('·', ' ').split() if w[:1].isalpha()]
    return all(w[0].isupper() or w in SMALL for w in words)


@pytest.mark.parametrize('view', ['Settings', 'Tags', 'Checklists', 'Import', 'Export', 'Journals', 'Connection'])
def test_buttons_menus_tabs_and_settings_on_every_setup_page_are_title_case(home, view):
    seed_preview(home)
    app = open_app(view)
    labels = [b.label for b in app.main.button if not b.label[:1].isdigit()]
    labels += [p.proto.popover.label for p in app.main.get('popover')]
    labels += [e.label for e in app.main.expander]
    labels += [t.label for t in app.main.tabs]
    if view == 'Settings':
        labels += [w.label for w in (*app.main.selectbox, *app.main.checkbox)]
    if view == 'Connection':
        labels += [t.label for t in app.main.toggle]
    assert labels and [label for label in labels if not title_case(label)] == []


# ---------------------------------------------------------------- Data → Connection, and About

def test_dropped_tick_reports_read_in_display_time_on_the_connection_tab():
    journal = Journal()
    journal.save_preferences(display_timezone='America/New_York')
    journal.ingest_gaps([{'type': 'gap', 'provider': 'motivewave-ticks', 'ts': '2026-10-07T14:00:00Z',
                          'dropped': 3, 'symbol': 'ESZ6'}])
    app = open_app('Connection')
    table = next(d.value for d in app.dataframe if 'Ticks Dropped' in d.value.columns)
    when = common.moment('2026-10-07T14:00:00Z', 'America/New_York')          # 'Oct 7, 10:00:00 AM' this year
    assert table.iloc[0].to_dict() == {'When': when, 'Contract': 'ESZ6', 'Ticks Dropped': 3,
                                       'Source': 'motivewave-ticks'}


def test_the_connection_tab_has_no_switch_to_stop_reading_trades_and_no_study_setting_to_change():
    app = open_app('Connection')
    assert not app.main.toggle and not said(app.main.markdown, "<div class='mwj-subsection'>Advanced</div>")
    assert not hasattr(connection, 'READ_TRADES') and not hasattr(connection, 'reading')
    assert states(app.main) == ['Not recording'] and said(app.main.markdown, 'Not recording')
    texts = ' '.join([*(e.value for kind in ('markdown', 'caption', 'warning') for e in getattr(app.main, kind)),
                      *(t.help or '' for t in app.main.text_input)])
    assert 'File setting' not in texts and 'Real Tick Data' not in texts and 'Capture Fills' not in texts


def test_the_study_can_be_installed_again_from_connection_after_it_has_reported_in(home, monkeypatch):
    extensions = home / 'extensions'
    monkeypatch.setenv('MOTIVEWAVE_EXTENSIONS', str(extensions))
    study_writes([heartbeat()])
    app = open_app('Settings')                                # the app starts the reader that hears the study
    wait_until(lambda: status_of().get('heartbeat'))
    app.run()
    assert steps_shown(app) == [] and not any(b.label == setup.INSTALL for b in app.button)
    show(app, 'Connection')
    if not connection.JAR.is_file():
        pytest.skip('No built study jar in this checkout.')
    install = app.button(key='connection-study-install')
    assert install.label == setup.INSTALL and 'kept beside it as a backup' in install.help
    install.click().run()
    assert not app.exception and said(app.success, 'Installed. If Settings still names an older study')
    assert (extensions / 'motivewave-journal.jar').read_bytes() == connection.JAR.read_bytes()
    app.button(key='connection-study-install').click().run()                     # again: the copy there is kept
    assert said(app.success, 'The previous copy was kept as a backup.')
    assert (extensions / 'motivewave-journal.jar.previous').is_file()


def test_what_an_upgrade_did_is_said_once_at_the_top_of_settings_until_ok():
    journal = Journal()
    journal._note('packed', {'prints': 3, 'note': 'Packed the recorded ticks, and every tick is kept.'})
    journal.put('profiles', 'Broker export', {'path': '/nowhere/fills.csv', 'mode': 'executions', 'enabled': True})
    with journal.connect() as db:                         # as 0.4.16 left it: one more step to take at the next open
        db.execute("DELETE FROM journal_meta WHERE key='one_connection'")
    app = open_app('Settings')
    said_once = ('Packed the recorded ticks, and every tick is kept. The journal now reads only the study\'s own file; '
                 '1 other watched file (Broker export) was removed, and the trades read from it stay.')
    assert app.main.caption[0].value == said_once                               # one quiet line, at the top
    assert not app.main.info and not app.main.get('dialog')
    app.button(key='setup-upgrade-notes-ok').click().run()
    assert not app.exception and not said(app.main.caption, 'Packed the recorded ticks')
    assert Journal().unseen_notes() == {} and set(Journal().upgrade_note()) >= {'packed', 'connections'}
    assert not said(open_app('Settings').main.caption, 'Packed the recorded ticks')          # seen: never again
    # A journal with nothing to say shows nothing.
    app.radio(key='journal-environment').set_value('Replay').run()
    assert not app.exception and not any(b.key == 'setup-upgrade-notes-ok' for b in app.button)


def test_a_step_a_named_journal_and_its_captures_both_took_is_said_once():
    from mwjournal import journals
    capture = Journal()
    named = journals.open_named(capture, journals.create(capture, 'Practice', [('motivewave', 'paper')]))
    days = 'Days now follow your Time Zone (America/New_York); day reviews moved to their dates.'
    for journal in (capture, named):
        journal._note('days', {'note': days})
    app = open_app('Settings')
    widget(app.sidebar.selectbox, 'Local journal').set_value(named.path.parent.name).run()
    assert not app.exception and app.main.caption[0].value == days
    app.button(key='setup-upgrade-notes-ok').click().run()
    assert not app.exception and capture.unseen_notes() == named.unseen_notes() == {}


def test_about_is_one_line_with_both_versions_and_the_data_folder_then_the_fine_print():
    app = open_app('Settings')
    captions = [c.value for c in app.main.caption]
    assert captions[-3] == (f'{paths.APP_NAME} {paths.VERSION} · study {paths.STUDY_VERSION} · data folder '
                            f'{paths.data_home()}')
    assert CREDIT in captions[-2] and 'not affiliated with or endorsed by MotiveWave' in captions[-1]
    assert not said(app.main.markdown, '>About<')
