"""Setup: one state at the top, the three steps, the display settings, every other page one click away,
and a sidebar with nothing to unfold. Disposable journals only, in a temporary data home."""

from types import SimpleNamespace

import pytest

import common
import runtime
from mwjournal import Journal, paths
from test_app import heartbeat, helped, open_app, said, show, states, status_of, study_writes, wait_until, widget
from test_chart import seed_preview
from views import setup


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    monkeypatch.setenv('MWJ_HOME', str(tmp_path))
    yield tmp_path
    runtime.stop_all()


def link(state='Connected', tone='good', version='0.3.1'):
    label = state + (f' · study {version}' if state == 'Connected' and version else '')
    return SimpleNamespace(state=state, tone=tone, label=label)


def order(app, *texts):
    """Where each text first appears among the page's markdown, top to bottom."""
    shown = [m.value for m in app.main.markdown]
    return [next(i for i, value in enumerate(shown) if text in value) for text in texts]


# ---------------------------------------------------------------- the first page

def test_the_first_page_reads_state_then_steps_then_display_then_everything_else():
    app = open_app('Setup')
    assert states(app.main) == ['Waiting For Study']
    status, started, step, display, more, about = order(
        app, "data-state='Waiting For Study'", '>Get Started<', '1 · Install the Study', '>Display<',
        '>More Settings<', '>About<')
    assert status < started < step < display < more < about
    assert said(app.main.markdown, 'Follow the three steps below')          # one line saying what to do
    assert [s.label for s in app.main.selectbox][:3] == ['Display time zone', 'MAE / MFE units', 'MAE / MFE accuracy']
    assert any(c.label == 'Hide weekends in calendar' for c in app.checkbox)
    # Nothing to unfold to find a setting; the one expander is inside the Save a Filter form.
    assert [e.label for e in app.main.expander] == ['Dates and Number Ranges']


def test_the_first_page_has_no_paths_raw_errors_or_paragraphs_of_jargon():
    app = open_app('Setup')
    visible = ' '.join(e.value for kind in ('markdown', 'caption', 'info', 'code') for e in getattr(app.main, kind))
    assert str(paths.bridge_file('live')) not in visible and 'motivewave.ndjson' not in visible
    assert 'Scale-ins and partial exits' not in visible                    # flat to flat is in the ? of step 3
    assert helped(app.main.markdown, 'flat to flat') and helped(app.main.markdown, 'Scale-ins and partial exits')
    assert helped(app.main.markdown, "real ticks of its holding time from MotiveWave's history")


def test_a_study_that_has_gone_quiet_still_counts_as_installed_and_on_the_chart():
    study_writes([heartbeat(age=600)])
    app = open_app('Setup')
    wait_until(lambda: status_of().get('heartbeat'))
    app.run()
    assert states(app.main) == ['Stale'] and said(app.main.markdown, 'study is still on your chart')
    assert [m.value for m in app.main.markdown].count(':green[✓ Done]') == 2
    assert not any(b.label == 'Install MotiveWave Study' for b in app.button)


def test_step_two_says_only_to_add_the_study_to_each_chart_with_nothing_to_set():
    app = open_app('Setup')
    step = next(m for m in app.main.markdown if '2 · Add It to Your Chart' in m.value)
    assert 'Add **Journal → MotiveWave Journal** to the chart of each contract you trade' in step.value
    assert 'nothing to set' in step.value
    assert 'File' not in step.value and 'File' not in (step.proto.help or '') and 'setting' not in (step.proto.help or '')


@pytest.mark.parametrize('version', ['0.3.2', '0.1.0'])
def test_a_study_older_than_the_bundled_one_is_asked_to_update_because_it_records_no_live_or_replay_ticks(version):
    study_writes([heartbeat(version=version)])
    app = open_app('Setup')
    wait_until(lambda: status_of().get('heartbeat'))
    app.run()
    assert states(app.main) == ['Connected']
    line = f'Study {version} records no live or replay ticks'
    assert said(app.main.markdown, line) and said(app.main.markdown, 'then restart MotiveWave')
    status = next(m.value for m in app.main.markdown if line in m.value)
    assert status.count('mwj-status-note') == 2                    # still one element: the state, then one line
    if setup.connection.JAR.is_file():
        assert said(app.main.markdown, 'click Install MotiveWave Study below')
        assert any(b.label == 'Install MotiveWave Study' for b in app.main.button)      # step 1 is open again
        assert [m.value for m in app.main.markdown].count(':green[✓ Done]') == 1         # step 2 stays done
    assert not said(app.sidebar.markdown, line)


def test_the_bundled_study_or_another_writer_gets_no_update_line():
    study_writes([heartbeat()])
    app = open_app('Setup')
    wait_until(lambda: status_of().get('heartbeat'))
    app.run()
    assert states(app.main) == ['Connected'] and not said(app.main.markdown, 'records no live or replay ticks')
    assert not any(b.label == 'Install MotiveWave Study' for b in app.main.button)
    on = lambda **fields: SimpleNamespace(**{'connector': paths.STUDY_CONNECTOR, 'version': '', **fields})
    assert common.study_outdated(on(version='0.3.2')) and common.study_outdated(on(version='0.2.10'))
    assert not common.study_outdated(on(version=paths.STUDY_VERSION)) and not common.study_outdated(on(version='0.3.10'))
    assert not common.study_outdated(on(version='0.4.0')) and not common.study_outdated(on())
    assert not common.study_outdated(on(version='0.3.2-beta'))                 # not a version this app wrote
    assert not common.study_outdated(on(version='0.1.0', connector='another-writer'))
    assert not common.study_outdated(SimpleNamespace(state='Connected'))
    assert setup.update_line(on(version='0.3.2')).startswith('Study 0.3.2 records no live or replay ticks: ')
    assert setup.update_line(on(version=paths.STUDY_VERSION)) is None


def test_the_waiting_status_uses_one_controlled_line_instead_of_a_raw_error_or_path(monkeypatch):
    shown = []
    raw = '/Users/someone/private/motivewave.ndjson could not be opened'
    monkeypatch.setattr(setup.common, 'study_link',
                        lambda journal: ('MotiveWave', {'path': raw}, {'error': raw}, link('Waiting For Study', 'warning', '')))
    monkeypatch.setattr(setup.common, 'other_problems', lambda journal, name: 0)
    monkeypatch.setattr(setup.theme, 'status', lambda *args, **kwargs: shown.append((args, kwargs)))
    setup.render_status(object())
    rendered = str(shown)
    assert 'Waiting For Study' in rendered and 'Follow the three steps below' in rendered
    assert raw not in rendered
    assert all(len(copy.split('. ')) <= 2 for copy in setup.STATUS_COPY.values())      # one line each


@pytest.mark.parametrize('page', [p for p in setup.PAGES if p != setup.HOME])
def test_every_other_setup_page_is_one_click_from_the_first_and_one_click_back(page):
    app = open_app('Setup')
    button = app.button(key='setup-open-' + page)
    assert button.label == setup.LABELS[page] and button.help == setup.PAGE_HELP[page]
    button.click().run()
    assert not app.exception and app.session_state[common.TOOL_KEY] == page
    assert any(b.key == 'setup-back' for b in app.button)
    app.button(key='setup-back').click().run()
    assert not app.exception and app.session_state[common.TOOL_KEY] == setup.HOME
    assert said(app.main.markdown, '1 · Install the Study')


def test_every_tool_the_app_routes_to_has_a_setup_page():
    assert {setup.page_for(tool) for tool in common.TOOLS} <= set(setup.PAGES)
    assert setup.page_for('General') == setup.page_for(None) == setup.page_for('Unknown') == setup.HOME
    assert set(setup.LABELS) == set(setup.PAGES) == {setup.HOME, *setup.PAGE_HELP}


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
    fake.session_state[common.TOOL_KEY] = 'General'                        # display settings: the first page
    setup.render(object(), [], tool=drawn.append)
    assert drawn == ['Tags', 'home'] and fake.session_state[common.TOOL_KEY] == setup.HOME


# ---------------------------------------------------------------- settings that moved off the sidebar

def test_the_saved_filter_chosen_on_setup_applies_to_every_page_and_survives_leaving_setup(home):
    journal = seed_preview(home)
    journal.save_filter('Shorts only', {'directions': ['short']})
    app = open_app('Setup')
    widget(app.selectbox, 'Saved filter on every page').set_value('Shorts only').run()
    assert not app.exception
    show(app, 'Edge')                                  # the selectbox is not drawn here, the choice still holds
    assert said(app.main.caption, 'Saved filter: Shorts only') and said(app.info, 'Statistics need')
    show(app, 'Setup')
    assert widget(app.selectbox, 'Saved filter on every page').value == 'Shorts only'
    assert widget(app.selectbox, 'Saved filter on every page').options == ['All trades', 'Shorts only']
    widget(app.selectbox, 'Saved filter on every page').set_value('').run()
    show(app, 'Edge')
    assert not said(app.main.caption, 'Saved filter') and any(m.label == 'Net P&L' for m in app.metric)


def test_units_and_the_display_time_zone_are_set_on_setup_and_saved_for_the_journal(home):
    journal = seed_preview(home)
    journal.set_excursion_policy('best-available')        # the fixture's prices are candles
    app = open_app('Setup')
    widget(app.selectbox, 'MAE / MFE units').set_value('points').run()
    widget(app.selectbox, 'Display time zone').set_value('Europe/London').run()
    assert not app.exception
    assert journal.display_settings()['excursion_unit'] == 'points'
    assert common.display_zone(Journal()) == common.display_zone(Journal(dataset='replay')) == 'Europe/London'
    show(app, 'Trades')
    assert widget(app.metric, 'MFE').value.endswith('pts')


SMALL = {'a', 'an', 'and', 'as', 'at', 'by', 'for', 'from', 'in', 'into', 'of', 'on', 'or', 'the', 'to', 'with'}


def title_case(label):
    words = [w for w in label.replace('·', ' ').split() if w[:1].isalpha()]
    return all(w[0].isupper() or w in SMALL for w in words)


@pytest.mark.parametrize('page', setup.PAGES)
def test_buttons_menus_and_sections_on_every_setup_page_are_title_case(home, page):
    seed_preview(home)
    app = open_app(page if page != setup.HOME else 'Setup')
    labels = [b.label for b in app.main.button if not b.label[:1].isdigit()]
    labels += [p.proto.popover.label for p in app.main.get('popover')]
    labels += [e.label for e in app.main.expander]
    labels += [t.label for t in app.main.tabs]
    assert labels and [label for label in labels if not title_case(label)] == []


def test_dropped_tick_reports_read_in_display_time_on_the_connection_page():
    journal = Journal()
    journal.save_preferences(display_timezone='America/New_York')
    journal.ingest_gaps([{'type': 'gap', 'provider': 'motivewave-ticks', 'ts': '2026-10-07T14:00:00Z',
                          'dropped': 3, 'symbol': 'ESZ6'}])
    app = open_app('Connection')
    table = next(d.value for d in app.dataframe if 'Ticks Dropped' in d.value.columns)
    when = common.moment('2026-10-07T14:00:00Z', 'America/New_York')          # 'Oct 7, 10:00:00 AM' this year
    assert table.iloc[0].to_dict() == {'When': when, 'Contract': 'ESZ6', 'Ticks Dropped': 3,
                                       'Source': 'motivewave-ticks'}


def test_the_study_can_be_installed_again_from_connection_after_it_has_reported_in(home, monkeypatch):
    from views import connection
    extensions = home / 'extensions'
    monkeypatch.setenv('MOTIVEWAVE_EXTENSIONS', str(extensions))
    study_writes([heartbeat()])
    app = open_app('Setup')                                   # the app starts the reader that hears the study
    wait_until(lambda: status_of().get('heartbeat'))
    app.run()
    assert [m.value for m in app.main.markdown].count(':green[✓ Done]') == 2      # no install button on step 1
    assert helped(app.main.markdown, 'To install it again after an update, use Data → Connection')
    show(app, 'Connection')
    if not connection.JAR.is_file():
        pytest.skip('No built study jar in this checkout.')
    install = app.button(key='connection-study-install')
    assert install.label == 'Install MotiveWave Study' and 'kept beside it as a backup' in install.help
    install.click().run()
    assert not app.exception and said(app.success, 'Installed. Restart MotiveWave')
    assert (extensions / 'motivewave-journal.jar').read_bytes() == connection.JAR.read_bytes()
    app.button(key='connection-study-install').click().run()                     # again: the copy there is kept
    assert said(app.success, 'The previous copy was kept as a backup.')
    assert (extensions / 'motivewave-journal.jar.previous').is_file()


def test_about_names_the_app_version_for_bug_reports():
    app = open_app('Setup')
    assert said(app.main.caption, f'{paths.APP_NAME} {paths.VERSION}')


def test_saving_a_new_filter_offers_every_contract_even_while_a_saved_filter_is_on():
    from mwjournal import Execution
    journal = Journal()
    for symbol, hour in (('ESZ6', 14), ('NQZ6', 15)):
        journal.ingest([(Execution('motivewave', 'paper', symbol, 'buy', 1, 100, f'2026-10-07T{hour}:00:00Z',
                                   execution_id=symbol + 'a'), {}),
                        (Execution('motivewave', 'paper', symbol, 'sell', 1, 101, f'2026-10-07T{hour}:05:00Z',
                                   execution_id=symbol + 'b'), {})])
    journal.save_filter('ES only', {'symbols': ['ESZ6']})
    app = open_app('Setup')
    widget(app.selectbox, 'Saved filter on every page').set_value('ES only').run()
    assert not app.exception and said(app.main.caption, 'Saved filter: ES only')
    assert widget(app.multiselect, 'Exact contracts').options == ['ESZ6', 'NQZ6']
