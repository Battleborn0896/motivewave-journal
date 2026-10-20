"""Setup: one status line, the one step that is missing, Time Zone and MAE / MFE Units with a small Advanced
heading, three pages of real setup, one About line, and a sidebar with nothing to unfold. Everything that
was a Setup page before 0.4.5 stays two clicks from the sidebar. Disposable journals only, in a temporary
data home."""

import datetime as dt
from types import SimpleNamespace

import pytest

import common
import runtime
from mwjournal import Execution, Journal, PriceSample, paths
from test_app import (CREDIT, heartbeat, helped, open_app, press, said, show, states, status_of, study_writes,
                      wait_until, widget)
from test_chart import seed_preview
from views import connection, setup


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    monkeypatch.setenv('MWJ_HOME', str(tmp_path))
    yield tmp_path
    runtime.stop_all()


def link(state='Connected', tone='good', version='0.3.1', *, heard=None, connector=paths.STUDY_CONNECTOR):
    label = state + (f' · study {version}' if state == 'Connected' and version else '')
    return SimpleNamespace(state=state, tone=tone, label=label, version=version, heard=heard, connector=connector)


def order(app, *texts):
    """Where each text first appears among the page's markdown, top to bottom."""
    shown = [m.value for m in app.main.markdown]
    return [next(i for i, value in enumerate(shown) if text in value) for text in texts]


def steps_shown(app):
    """The titles of the setup steps on the page."""
    return [title for title, _, _ in setup.STEPS.values() if said(app.main.markdown, f'**{title}**')]


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

def test_the_first_page_reads_status_then_the_missing_step_then_two_settings_then_advanced_then_the_pages():
    app = open_app('Setup')
    assert states(app.main) == ['Waiting For Study'] and steps_shown(app) == ['Install the Study']
    status, step, settings, advanced, pages = order(
        app, "data-state='Waiting For Study'", '**Install the Study**', '>Settings<', '>Advanced<', '>More Setup<')
    assert status < step < settings < advanced < pages
    # Time Zone and MAE / MFE Units, then accuracy and hide weekends under Advanced: nothing else to set here.
    assert [s.label for s in app.main.selectbox] == ['Time Zone', 'MAE / MFE Units', 'MAE / MFE Accuracy']
    assert [c.label for c in app.main.checkbox] == ['Hide Weekends in Calendar']
    assert "<div class='mwj-subsection'>Advanced</div>" in [m.value for m in app.main.markdown][advanced]
    assert not app.main.expander and not app.main.get('popover')         # a plain heading, nothing that folds
    assert not any('filter' in s.label.lower() for s in app.main.selectbox)  # the saved filter lives in Filters
    assert [b.label for b in app.main.button if b.key and b.key.startswith('setup-open-')] == [
        'Tags, Playbooks & Checklists ›', 'Rating Systems ›', 'Data ›']


def test_the_first_page_has_no_paths_raw_errors_or_paragraphs_of_jargon():
    app = open_app('Setup')
    visible = ' '.join(e.value for kind in ('markdown', 'info', 'code') for e in getattr(app.main, kind))
    assert str(paths.bridge_file('live')) not in visible and 'motivewave.ndjson' not in visible
    assert 'Scale-ins and partial exits' not in visible                    # how trades are made is in the ?
    assert helped(app.main.markdown, 'flat to flat') and helped(app.main.markdown, 'Scale-ins and partial exits')
    assert helped(app.main.markdown, "real ticks of its holding time from MotiveWave's history")


def test_when_the_study_records_setup_is_one_green_line_naming_the_contracts_and_nothing_else():
    now = dt.datetime.now(dt.timezone.utc).replace(microsecond=0)
    at = lambda minutes: (now - dt.timedelta(minutes=minutes)).isoformat()
    study_writes([heartbeat(), live_bar('ESZ6', 5000, at(2)), live_bar('NQZ6', 20000, at(2)),
                  live_fill('n1', 'NQZ6', 'buy', 20000, at(6)), live_fill('n2', 'NQZ6', 'sell', 20004, at(4))])
    app = open_app('Setup')
    wait_until(lambda: status_of().get('heartbeat') and Journal().trades())
    app.run()
    line = status_line(app)
    assert states(app.main) == ['Connected']
    assert f'>Connected · recording NQZ6, ESZ6 · study {paths.STUDY_VERSION}<' in line     # traded first
    assert "data-state='Connected'" in line and f'color:{setup.theme.UP}' in line             # green
    assert 'mwj-status-note' not in line and steps_shown(app) == []                          # one line, no step
    assert not any(b.label == setup.INSTALL for b in app.button)
    assert helped(app.main.markdown, 'prices or fills for in the last hour')


def test_a_study_that_has_reported_no_prices_yet_leaves_the_contracts_out():
    study_writes([heartbeat()])
    app = open_app('Setup')
    wait_until(lambda: status_of().get('heartbeat'))
    app.run()
    assert f'>Connected · study {paths.STUDY_VERSION}<' in status_line(app) and steps_shown(app) == []


def test_before_the_study_reports_in_setup_asks_to_install_it_then_to_add_it_to_a_chart(extensions_folder):
    app = open_app('Setup')
    assert steps_shown(app) == ['Install the Study'] and said(app.main.markdown, 'then add the study to your chart')
    assert 'mwj-status-note' not in status_line(app)                  # the step says what to do, once
    if connection.JAR.is_file():
        press(app, setup.INSTALL)
        assert (extensions_folder / 'motivewave-journal.jar').read_bytes() == connection.JAR.read_bytes()
        # The click itself redraws the page: the next step already says to restart, and the button is gone.
        assert steps_shown(app) == ['Add It to Your Chart'] and not app.success
        assert said(app.main.markdown, 'Restart MotiveWave, then add **Journal → MotiveWave Journal**')
        assert not any(b.label == setup.INSTALL for b in app.button)
    else:
        install_the_study(extensions_folder)
    app.run()
    assert steps_shown(app) == ['Add It to Your Chart'] and states(app.main) == ['Waiting For Study']
    step = next(m for m in app.main.markdown if '**Add It to Your Chart**' in m.value)
    assert ('add **Journal → MotiveWave Journal** to the chart of each contract you trade' in step.value
            or 'Add **Journal → MotiveWave Journal** to the chart of each contract you trade' in step.value)
    assert step.value.count('Restart MotiveWave, then add') == int(connection.JAR.is_file())   # installed just now
    assert 'There is nothing to set.' in step.value and 'File' not in step.value
    assert 'To install it again, use Data → Connection.' in step.proto.help
    assert not any(b.label == setup.INSTALL for b in app.button)


def test_replay_waiting_for_a_replay_chart_asks_for_the_chart_once_live_has_heard_the_study():
    study_writes([heartbeat()])
    app = open_app('Setup')
    wait_until(lambda: status_of().get('heartbeat'))
    app.radio(key='journal-environment').set_value('Replay').run()
    assert not app.exception and states(app.main) == ['Waiting For Study']
    assert steps_shown(app) == ['Add It to Your Chart']           # installed: Live hears it, whatever the folder
    assert said(app.main.markdown, 'to the replay chart of each contract you replay')
    assert not any(b.label == setup.INSTALL for b in app.button)


def test_a_study_that_has_gone_quiet_gets_only_the_quiet_step():
    study_writes([heartbeat(age=600)])
    app = open_app('Setup')
    wait_until(lambda: status_of().get('heartbeat'))
    app.run()
    assert states(app.main) == ['Stale'] and steps_shown(app) == ['The Study Has Gone Quiet']
    assert said(app.main.markdown, 'Check that MotiveWave is open and the study is still on your chart.')
    assert not any(b.label == setup.INSTALL for b in app.button)


@pytest.mark.parametrize('version', ['0.3.2', '0.1.0'])
def test_a_study_older_than_the_bundled_one_gets_only_the_update_step(version):
    study_writes([heartbeat(version=version)])
    app = open_app('Setup')
    wait_until(lambda: status_of().get('heartbeat'))
    app.run()
    assert states(app.main) == ['Connected'] and steps_shown(app) == ['Update the Study']
    line = f'Study {version} records no live or replay ticks'
    assert said(app.main.markdown, line) and said(app.main.markdown, 'then restart MotiveWave')
    assert 'mwj-status-note' not in status_line(app)
    assert not said(app.sidebar.markdown, line)
    if connection.JAR.is_file():
        assert said(app.main.markdown, 'click Install MotiveWave Study')
        press(app, setup.INSTALL)                  # MotiveWave reports the old study until it restarts
        # Straight after the click, without another run: the line says to restart, never 'click Install'.
        assert steps_shown(app) == ['Update the Study'] and not said(app.main.markdown, 'click Install')
        assert said(app.main.markdown, 'The new study is installed. Restart MotiveWave to use it.')
        assert not any(b.label == setup.INSTALL for b in app.main.button)
        app.run()
        assert steps_shown(app) == ['Update the Study']
        assert said(app.main.markdown, 'The new study is installed. Restart MotiveWave to use it.')
        assert not any(b.label == setup.INSTALL for b in app.main.button)


def test_the_bundled_study_or_another_writer_gets_no_update_step():
    study_writes([heartbeat()])
    app = open_app('Setup')
    wait_until(lambda: status_of().get('heartbeat'))
    app.run()
    assert states(app.main) == ['Connected'] and steps_shown(app) == []
    on = lambda **fields: SimpleNamespace(**{'connector': paths.STUDY_CONNECTOR, 'version': '', **fields})
    assert common.study_outdated(on(version='0.3.2')) and common.study_outdated(on(version='0.2.10'))
    assert not common.study_outdated(on(version=paths.STUDY_VERSION)) and not common.study_outdated(on(version='0.3.10'))
    assert not common.study_outdated(on(version='0.4.0')) and not common.study_outdated(on())
    assert not common.study_outdated(on(version='0.3.2-beta'))                 # not a version this app wrote
    assert not common.study_outdated(on(version='0.1.0', connector='another-writer'))
    assert not common.study_outdated(SimpleNamespace(state='Connected'))
    assert setup.update_line(on(version='0.3.2')).startswith('Study 0.3.2 records no live or replay ticks: ')
    assert setup.update_line(on(version=paths.STUDY_VERSION)) is None


def test_exactly_one_step_is_needed_at_a_time_and_none_while_recording():
    heard = dt.datetime.now(dt.timezone.utc)
    current = paths.STUDY_VERSION
    assert setup.needed_step(link(version=current), installed=True) is None
    assert setup.needed_step(link(version=current), installed=False) is None     # it reports in: it is installed
    assert setup.needed_step(link(version='0.3.2'), installed=True) == 'update'
    assert setup.needed_step(link('Stale', 'warning', '0.3.2', heard=heard), installed=True) == 'update'
    assert setup.needed_step(link('Stale', 'warning', current, heard=heard), installed=True) == 'quiet'
    assert setup.needed_step(link('Waiting For Study', 'warning', ''), installed=False) == 'install'
    assert setup.needed_step(link('Waiting For Study', 'warning', ''), installed=True) == 'chart'
    for state in ('No Heartbeat', 'Paused', 'Needs Attention'):                  # the status line says what to do
        assert setup.needed_step(link(state, 'warning', ''), installed=True) is None
        assert state in setup.STATUS_COPY
    assert setup.needed_step(None, installed=True) is None


def test_the_status_uses_one_controlled_line_instead_of_a_raw_error_or_path(monkeypatch):
    shown = []
    raw = '/Users/someone/private/motivewave.ndjson could not be opened'
    for state in ('Waiting For Study', 'Needs Attention'):
        monkeypatch.setattr(setup.common, 'study_link',
                            lambda journal, state=state: ('MotiveWave', {'path': raw}, {'error': raw}, link(state, 'warning', '')))
        monkeypatch.setattr(setup.common, 'other_problems', lambda journal, name: 0)
        monkeypatch.setattr(setup.theme, 'status', lambda *args, **kwargs: shown.append((args, kwargs)))
        setup.render_status(SimpleNamespace(dataset='live'))
    rendered = str(shown)
    assert 'Waiting For Study' in rendered and 'Open Data → Connection' in rendered and raw not in rendered
    assert all(len(copy.split('. ')) <= 2 for copy in setup.STATUS_COPY.values())      # one line each


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
def test_each_setup_page_is_one_click_from_the_first_and_one_click_back(page):
    app = open_app('Setup')
    button = app.button(key='setup-open-' + page)
    assert button.label == setup.LABELS[page] + ' ›' and button.help == setup.PAGE_HELP[page]
    button.click().run()
    assert not app.exception and app.session_state[common.TOOL_KEY] == page
    assert any(b.key == 'setup-back' for b in app.button)
    app.button(key='setup-back').click().run()
    assert not app.exception and app.session_state[common.TOOL_KEY] == setup.HOME
    assert said(app.main.markdown, '>Settings<')


@pytest.mark.parametrize('page,tab', [(page, tab) for page, tabs in setup.TABS.items() for tab in tabs])
def test_every_tab_of_a_setup_page_has_its_own_link_on_the_first_page(page, tab):
    app = open_app('Setup')
    link_button = app.button(key=f'setup-tab-{page}-{tab}')
    assert link_button.label == tab
    link_button.click().run()
    assert not app.exception and app.session_state[common.TOOL_KEY] == page
    assert [t.label for t in app.main.tabs][:len(setup.TABS[page])] == list(setup.TABS[page])
    assert app.session_state[{'Tags': 'tag-settings-tabs-live', 'Data': 'data-tabs-live'}[page]] == tab


LANDMARKS = {
    'Tags': lambda app: any(t.label == 'New tag name' for t in app.text_input),
    'Ratings': lambda app: any(s.label == 'Rating system' for s in app.selectbox),
    'Checklists': lambda app: any(s.label == 'Checklist' for s in app.selectbox),
    'Dashboard': lambda app: any(s.label == 'Dashboard page' for s in app.selectbox),
    'Daily review': lambda app: any(b.label == 'Save Daily Review' for b in app.button),
    'Live session': lambda app: (any(b.key == 'session-timer-live-play' for b in app.button)
                                 and [p.proto.popover.label for p in app.main.get('popover')][:3]
                                 == ['Check-Ins', 'Drafts', 'Checklist']),
    'Connection': lambda app: any(t.label == connection.READ_TRADES for t in app.toggle),
    'Journals': lambda app: any(t.label == 'New journal name' for t in app.text_input),
    'Import': lambda app: any(s.label == 'Import format' for s in app.selectbox),
    'Export': lambda app: [b.label for b in app.download_button] == ['Download Journal JSON', 'Download Trades CSV',
                                                                     'Download Full Backup'],
    'General': lambda app: any(s.label == 'Time Zone' for s in app.selectbox),
}


@pytest.mark.parametrize('view', LANDMARKS)
def test_every_page_that_was_on_setup_is_at_most_two_clicks_from_the_sidebar(home, view):
    """show() clicks the sidebar, then at most one link, tab or day header: what a person would click."""
    seed_preview(home)
    app = open_app('Edge')                         # somewhere else first, so the sidebar click is a real move
    show(app, view)
    assert not app.exception and LANDMARKS[view](app)


def test_every_name_the_app_routes_by_has_a_place():
    for view in (*common.TOOLS, 'Setup', 'Dashboard', 'Daily review', 'Live session', 'Edge', 'Trades'):
        route = common.navigation(view)
        assert route[common.VIEW_KEY] in common.PRIMARY_VIEWS
        if route[common.VIEW_KEY] == 'Settings':
            assert route[common.TOOL_KEY] in setup.PAGES and setup.page_for(view) == route[common.TOOL_KEY]
            assert route[common.TAB_KEY] is None or route[common.TAB_KEY] in setup.TABS[route[common.TOOL_KEY]]
    assert setup.page_for('General') == setup.page_for(None) == setup.page_for('Unknown') == setup.HOME
    assert set(setup.LABELS) == set(setup.PAGES) == {setup.HOME, *setup.PAGE_HELP}
    assert common.navigation('Dashboard') == {common.VIEW_KEY: 'Analytics', common.TAB_KEY: 'Dashboards'}
    assert common.navigation('Daily review') == {common.VIEW_KEY: 'Trades', common.REPORT_KEY: True}


def test_choosing_setup_in_the_sidebar_always_opens_its_first_page():
    app = open_app('Tags')
    show(app, 'Trades')
    app.segmented_control(key=common.VIEW_KEY).set_value('Settings').run()
    assert not app.exception and app.session_state[common.TOOL_KEY] == setup.HOME
    # Clicking Setup again while one of its pages is open clears the control in a browser, which AppTest
    # cannot do; that path was checked by hand in the browser.


def test_the_connection_badge_opens_setup_from_any_page(home):
    seed_preview(home)
    app = open_app('Edge')
    assert states(app.sidebar) == ['Waiting For Study'] and said(app.sidebar.markdown, 'Setup ›')
    app.button(key='connection-badge-open').click().run()
    assert not app.exception and app.segmented_control(key=common.VIEW_KEY).value == 'Settings'
    assert app.session_state[common.TOOL_KEY] == setup.HOME and states(app.main) == ['Waiting For Study']


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
    assert drawn == ['Tags'] and fake.buttons == ['← Setup']
    fake.session_state[common.TOOL_KEY] = 'Import'                         # an old page name: its new page
    setup.render(object(), [], tool=drawn.append)
    assert drawn == ['Tags', 'Data'] and fake.session_state[common.TOOL_KEY] == 'Data'
    fake.session_state[common.TOOL_KEY] = 'General'                        # display settings: the first page
    setup.render(object(), [], tool=drawn.append)
    assert drawn == ['Tags', 'Data', 'home'] and fake.session_state[common.TOOL_KEY] == setup.HOME


# ---------------------------------------------------------------- the two settings, and the saved filter

def test_units_and_the_time_zone_are_set_on_setup_and_saved_for_the_journal(home):
    journal = seed_preview(home)
    journal.set_excursion_policy('best-available')        # the fixture's prices are candles
    app = open_app('Setup')
    widget(app.selectbox, 'MAE / MFE Units').set_value('points').run()
    widget(app.selectbox, 'Time Zone').set_value('Europe/London').run()
    assert not app.exception
    assert journal.display_settings()['excursion_unit'] == 'points'
    assert common.display_zone(Journal()) == common.display_zone(Journal(dataset='replay')) == 'Europe/London'
    show(app, 'Trades')
    assert widget(app.metric, 'MFE').value.endswith('pts')


def test_hide_weekends_is_under_advanced_and_saved_for_the_calendar(home):
    journal = seed_preview(home)
    app = open_app('Setup')
    widget(app.checkbox, 'Hide Weekends in Calendar').check().run()
    assert not app.exception and journal.workspace()['preferences']['hide_weekends'] is True
    show(app, 'Dashboard')                          # the default page's calendar leaves the weekend out
    days = [c.value for c in app.main.caption]
    assert 'Fri' in days and 'Sat' not in days and 'Sun' not in days


@pytest.mark.parametrize('page', ['Trades', 'Edge'])
def test_the_saved_filter_is_chosen_in_the_filters_menu_and_applies_to_every_page(home, page):
    journal = seed_preview(home)
    journal.save_filter('Shorts only', {'directions': ['short']})
    app = open_app(page)
    choice = widget(app.selectbox, 'Saved Filter on Every Page')
    assert choice.options == ['All trades', 'Shorts only']
    choice.set_value('Shorts only').run()
    assert not app.exception and said(app.main.caption, 'Saved filter: Shorts only')
    for other in ('Trades', 'Edge', 'Setup'):          # the selectbox is drawn on two pages; the choice holds on all
        show(app, other)
        assert said(app.main.caption, 'Saved filter: Shorts only')
    show(app, 'Edge')
    assert said(app.info, 'Statistics need') and widget(app.selectbox, 'Saved Filter on Every Page').value == 'Shorts only'
    show(app, 'Trades')
    assert said(app.info, 'No trades match these filters')         # the menu stays, so the filter can be undone
    widget(app.selectbox, 'Saved Filter on Every Page').set_value('').run()
    assert not said(app.main.caption, 'Saved filter') and not said(app.info, 'No trades match')
    show(app, 'Edge')
    assert any(m.label == 'Net P&L' for m in app.metric)


def test_saving_a_new_filter_offers_every_contract_even_while_a_saved_filter_is_on():
    journal = Journal()
    for symbol, hour in (('ESZ6', 14), ('NQZ6', 15)):
        journal.ingest([(Execution('motivewave', 'paper', symbol, 'buy', 1, 100, f'2026-10-07T{hour}:00:00Z',
                                   execution_id=symbol + 'a'), {}),
                        (Execution('motivewave', 'paper', symbol, 'sell', 1, 101, f'2026-10-07T{hour}:05:00Z',
                                   execution_id=symbol + 'b'), {})])
    journal.save_filter('ES only', {'symbols': ['ESZ6']})
    app = open_app()
    widget(app.selectbox, 'Saved Filter on Every Page').set_value('ES only').run()
    assert not app.exception and said(app.main.caption, 'Saved filter: ES only')
    assert any(e.label == 'Save a Filter' for e in app.main.expander)
    assert app.multiselect(key='saved-global-symbols-live').options == ['ESZ6', 'NQZ6']
    widget(app.text_input, 'Filter name').set_value('NQ only')
    app.multiselect(key='saved-global-symbols-live').set_value(['NQZ6'])
    press(app, 'Save Filter')
    assert journal.workspace()['filters']['NQ only']['symbols'] == ['NQZ6']
    assert widget(app.selectbox, 'Saved Filter on Every Page').options == ['All trades', 'ES only', 'NQ only']


SMALL = {'a', 'an', 'and', 'as', 'at', 'by', 'for', 'from', 'in', 'into', 'of', 'on', 'or', 'the', 'to', 'with'}


def title_case(label):
    words = [w for w in label.replace('·', ' ').split() if w[:1].isalpha()]
    return all(w[0].isupper() or w in SMALL for w in words)


@pytest.mark.parametrize('view', ['Setup', 'Tags', 'Checklists', 'Ratings', 'Import', 'Export', 'Journals', 'Connection'])
def test_buttons_menus_tabs_and_settings_on_every_setup_page_are_title_case(home, view):
    seed_preview(home)
    app = open_app(view)
    labels = [b.label for b in app.main.button if not b.label[:1].isdigit()]
    labels += [p.proto.popover.label for p in app.main.get('popover')]
    labels += [e.label for e in app.main.expander]
    labels += [t.label for t in app.main.tabs]
    if view == 'Setup':
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


def test_reading_trades_from_motivewave_is_an_advanced_switch_on_the_connection_tab_and_on_by_default():
    app = open_app('Connection')
    toggle = widget(app.toggle, connection.READ_TRADES)
    assert toggle.value is True and 'the study keeps writing' in toggle.help
    assert not any(t.label == 'Capture enabled' for t in app.toggle)
    multiplier, advanced = order(app, '>Instrument Multiplier<', "<div class='mwj-subsection'>Advanced</div>")
    assert multiplier < advanced                                   # last on the tab, under its own heading
    assert setup.STATUS_COPY['Paused'].endswith('under Data → Connection → Advanced.')


def test_the_study_can_be_installed_again_from_connection_after_it_has_reported_in(home, monkeypatch):
    extensions = home / 'extensions'
    monkeypatch.setenv('MOTIVEWAVE_EXTENSIONS', str(extensions))
    study_writes([heartbeat()])
    app = open_app('Setup')                                   # the app starts the reader that hears the study
    wait_until(lambda: status_of().get('heartbeat'))
    app.run()
    assert steps_shown(app) == [] and not any(b.label == setup.INSTALL for b in app.button)
    show(app, 'Connection')
    if not connection.JAR.is_file():
        pytest.skip('No built study jar in this checkout.')
    install = app.button(key='connection-study-install')
    assert install.label == setup.INSTALL and 'kept beside it as a backup' in install.help
    install.click().run()
    assert not app.exception and said(app.success, 'Installed. Restart MotiveWave')
    assert (extensions / 'motivewave-journal.jar').read_bytes() == connection.JAR.read_bytes()
    app.button(key='connection-study-install').click().run()                     # again: the copy there is kept
    assert said(app.success, 'The previous copy was kept as a backup.')
    assert (extensions / 'motivewave-journal.jar.previous').is_file()


def test_about_is_one_line_with_both_versions_and_the_data_folder_then_the_fine_print():
    app = open_app('Setup')
    captions = [c.value for c in app.main.caption]
    assert captions[-3] == (f'{paths.APP_NAME} {paths.VERSION} · study {paths.STUDY_VERSION} · data folder '
                            f'{paths.data_home()}')
    assert CREDIT in captions[-2] and 'not affiliated with or endorsed by MotiveWave' in captions[-1]
    assert not said(app.main.markdown, '>About<')
