"""The app end to end through Streamlit's AppTest, on paper fixtures in a temporary data home.

AppTest runs the real script and the real background reader, but no browser: these tests see which
elements exist and what they say, not how the page looks.
"""
import dataclasses
import datetime as dt
import io
import json
import re
import sqlite3
import threading
import time
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

import common
import runtime
from views import setup
from mwjournal import Execution, Journal, PriceSample, backup, paths
from mwjournal.importers import apply_preview, preview_bridge
from mwjournal.metrics import measurements
from mwjournal.sync import DEFAULT_CONNECTION
from test_capture import fill, lines, tick
from test_chart import bar, round_trip, seed_preview, seed_ticks

APP = Path(__file__).resolve().parent.parent / 'app' / 'app.py'
VIEWS = ['Trades', 'Edge', 'Import', 'Connection', 'Daily review', 'Export']
CREDIT = 'TradingView Lightweight Charts™, Copyright (c) 2025 TradingView, Inc.'
FILLS_CSV = ('Account,Instrument,Side,Quantity,Avg Price,Date,Time,Trade ID,Commission\n'
             'paper,ESZ6,Buy,1,5000,2026-10-07,10:00:23,entry,2\n'
             'paper,ESZ6,Sell,1,5002,2026-10-07,10:03:00,exit,2\n')


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    """Every test gets its own data home, and the readers the app started are stopped afterwards."""
    monkeypatch.setenv('MWJ_HOME', str(tmp_path))
    yield tmp_path
    runtime.stop_all()


def open_app(view='Trades', environment='Live', *, review=True):
    app = AppTest.from_file(str(APP), default_timeout=30).run()
    assert not app.exception
    if environment != 'Live':
        app.radio(key='journal-environment').set_value(environment).run()
        assert not app.exception
    if view != 'Trades':
        return show(app, view)
    return open_review(app) if review else app


def open_review(app):
    if any(b.label == 'Open trade' for b in app.button):
        press(app, 'Open trade')
    return app


def show(app, view):
    """Open a view the way a person does: its page in the sidebar, then, for a page under Setup, its tile or
    the tile's link to one of its tabs on Setup's first page; for the dashboards, the Dashboards tab of Stats;
    for the Daily Report Card, a day's header in the trade list. Two clicks at most."""
    route = common.navigation(view)
    app.segmented_control(key=common.VIEW_KEY).set_value(route[common.VIEW_KEY]).run()
    assert not app.exception
    if route[common.VIEW_KEY] == 'Settings':
        if app.session_state[common.TOOL_KEY] != setup.HOME:
            app.button(key='setup-back').click().run()        # already on Setup, on another of its pages
        page, tab = route[common.TOOL_KEY], route[common.TAB_KEY]
        if page != setup.HOME:
            app.button(key=f'setup-tab-{page}-{tab}' if tab else 'setup-open-' + page).click().run()
        assert not app.exception and app.session_state[common.TOOL_KEY] == page
    elif view == 'Dashboard':
        open_tab(app, 'Dashboards')
    elif view == 'Daily review':
        open_report_card(app)
    elif view == 'Trades' and any(b.label == '← Back to Trades' for b in app.button):
        press(app, '← Back to Trades')                 # the sidebar was already on Trades, showing a day
    return open_review(app) if view == 'Trades' else app


def open_tab(app, label):
    """Click a tab. AppTest cannot click one, so this asks for it the way another page's link does."""
    app.session_state[common.TAB_KEY] = label
    app.run()
    assert not app.exception
    return app


def open_report_card(app):
    """Click the newest day's header in the trade list; with no trades, ask for the card like a calendar day does."""
    header = next((b for b in app.button if b.key and b.key.startswith('open-day-')), None)
    if header is not None:
        header.click().run()
    else:
        app.session_state[common.REPORT_KEY] = True
        app.run()
    assert not app.exception and report_open(app)
    return app


def report_open(app):
    """The Daily Report Card on screen: its note box."""
    return any(t.label.startswith('What I learned') for t in app.text_area)


def setup_page(app):
    """The Setup page on screen."""
    return app.session_state[common.TOOL_KEY]


def widget(widgets, label):
    return next(w for w in widgets if w.label.startswith(label))


def press(app, label):
    widget(app.button, label).click().run()
    assert not app.exception
    return app


def listed(app, scope='live'):
    """The trade ids in the trade list, top to bottom: each row is a button keyed by its trade."""
    return [b.key[len('pick-'):-len('-' + scope)] for b in app.button
            if b.key and b.key.startswith('pick-') and b.key.endswith('-' + scope)]


def pick(app, trade_id, scope='live'):
    """Click a trade's row in the list."""
    app.button(key=f'pick-{trade_id}-{scope}').click().run()
    assert not app.exception
    return app


def chosen(app, scope='live'):
    """The trade open on the right."""
    return app.session_state[f'selected-trade-{scope}']


def states(block):
    return re.findall(r"data-state='([^']+)'", ' '.join(m.value for m in block.markdown))


def said(elements, text):
    return any(text in e.value for e in elements)


def helped(elements, text):
    """The text is in the ? tooltip of one of the elements."""
    return any(text in (getattr(e.proto, 'help', '') or '') for e in elements)


def display_setting(app, label, value, back='Trades'):
    """Change one setting where it lives, on Setup's first page, then go back to the page that shows it."""
    show(app, 'General')
    widget(app.selectbox, label).set_value(value).run()
    assert not app.exception
    return show(app, back)


def heartbeat(status='connected', *, age=0, mode='live', detail='Read-only capture', version=paths.STUDY_VERSION):
    """A heartbeat of the study this app bundles, unless another version is named."""
    stamp = dt.datetime.now(dt.timezone.utc) - dt.timedelta(seconds=age)
    return {'version': 1, 'type': 'heartbeat', 'mode': mode, 'provider': 'motivewave-native', 'ts': stamp.isoformat(),
            'workspace': 'paper', 'status': status, 'detail': detail,
            'connector': 'motivewave-journal-study', 'connector_version': version}


def study_writes(events, dataset='live'):
    """What the study does: append complete lines to the bridge file nobody had to configure."""
    file = paths.bridge_file(dataset)
    file.parent.mkdir(parents=True, exist_ok=True)
    with file.open('a') as fh:
        fh.write(lines(events))


def wait_until(ready, seconds=15):
    """The app's own background reader does the import; the test only waits for it."""
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if ready():
            return
        time.sleep(.05)
    raise AssertionError('The background reader did not pick up the bridge file in time.')


def status_of(dataset='live'):
    return Journal(dataset=dataset).get('sync_status').get(DEFAULT_CONNECTION, {})


def landmarks(app, view, empty):
    """Something only this view draws, so 'no exception' cannot hide a view that drew nothing."""
    if view == 'Trades':
        return said(app.info, 'No trades yet') if empty else bool(listed(app))
    if view == 'Edge':
        return said(app.info, 'Statistics need') if empty else any(m.label == 'Win Rate' for m in app.metric)
    if view == 'Import':
        return any(s.label == 'Import format' for s in app.selectbox) and len(app.file_uploader) == 2
    if view == 'Connection':
        return (any(t.label == 'Read Trades From MotiveWave' for t in app.toggle) and len(states(app.main)) == 1
                and any(c.value == str(paths.bridge_file(Journal().dataset)) for c in app.main.code))
    if view == 'Daily review':
        return report_open(app)
    return backup_controls(app)


def backup_controls(app):
    """Export & Backup on screen: its two downloads and the backup file box."""
    return ([b.label for b in app.download_button] == ['Back Up Journal', 'Export Trades CSV']
            and [u.label for u in app.file_uploader] == ['Restore From Backup'])


# ---------------------------------------------------------------- every view

@pytest.mark.parametrize('view', VIEWS)
def test_every_view_renders_with_a_real_round_trip(home, view):
    seed_preview(home)
    app = open_app(view)
    assert landmarks(app, view, empty=False)
    assert app.segmented_control(key=common.VIEW_KEY).value == common.navigation(view)[common.VIEW_KEY]


@pytest.mark.parametrize('view', VIEWS)
def test_every_view_renders_with_an_empty_journal(view):
    app = open_app(view)
    assert landmarks(app, view, empty=True)
    assert not Journal().executions()


@pytest.mark.parametrize('view', VIEWS)
def test_the_sidebar_holds_only_the_switch_the_pages_and_the_badge_on_every_view(view):
    app = open_app(view)
    assert [r.label for r in app.sidebar.radio] == ['Trading environment']
    assert [s.key for s in app.sidebar.segmented_control] == [common.VIEW_KEY]
    assert not app.sidebar.selectbox and not app.sidebar.expander and not app.sidebar.caption
    assert not app.sidebar.get('popover')
    assert states(app.sidebar) and [b.key for b in app.sidebar.button] == ['connection-badge-open']


def test_chart_library_notice_data_folder_and_independence_are_on_setup():
    app = open_app('General')
    notice = [c.value for c in app.main.caption if CREDIT in c.value]
    assert len(notice) == 1 and '(https://www.tradingview.com/)' in notice[0]
    assert said(app.main.caption, 'not affiliated with or endorsed by MotiveWave')
    assert said(app.main.caption, str(paths.data_home()))


@pytest.mark.parametrize('view', VIEWS)
def test_replay_environment_views_render_separately(view):
    replay = Journal(dataset='replay')
    apply_preview(replay, preview_bridge(lines([fill('a'), tick(5001),
                           fill('b', 'sell', 5002, ts='2022-01-03T14:32:00Z')]), dataset='replay'))
    app = open_app(view, 'Replay')
    assert said(app.info, 'Replay journal')
    assert app.segmented_control(key=common.VIEW_KEY).value == common.navigation(view)[common.VIEW_KEY]
    if view == 'Trades':
        assert listed(app, 'replay') == [replay.trades()[0].id] and chosen(app, 'replay') == replay.trades()[0].id
    if view == 'Connection':
        assert any(c.value == str(paths.bridge_file('replay')) for c in app.main.code)
    assert not Journal().executions()
    app.radio(key='journal-environment').set_value('Live').run()
    assert not app.exception and not said(app.info, 'Replay journal')
    assert not listed(app, 'replay') and not listed(app, 'live')


def test_a_replay_trade_without_ticks_says_the_study_fills_them_in_after_it_closes():
    replay = Journal(dataset='replay')
    apply_preview(replay, preview_bridge(lines([fill('a'), fill('b', 'sell', 5002, ts='2022-01-03T14:32:00Z')]),
                                         dataset='replay'))
    app = open_app('Trades', 'Replay')
    assert chosen(app, 'replay') == replay.trades()[0].id
    assert [c.value for c in app.main.caption].count(common.REPLAY_TICKS_LATER) == 1
    assert not said(app.main.caption, 'MAE / MFE need recorded ticks')
    assert said(app.info, common.REPLAY_TICKS_LATER)                   # More → MAE / MFE says the same
    # Once the study has backfilled it, the line goes and the real ticks measure the trade.
    apply_preview(replay, preview_bridge(lines([tick(price, suffix='history', ts=f'2022-01-03T14:3{minute}:00Z')
                                                for minute, price in ((0, 5000.25), (1, 4998))]), dataset='replay'))
    from mwjournal.sync import SyncWorker
    worker = SyncWorker(replay)
    worker.rebuild = True
    worker.recalculate()
    app.run()
    assert not app.exception and not said(app.main.caption, common.REPLAY_TICKS_LATER)
    assert widget(app.metric, 'MAE').value == '$100.00'
    assert said(app.main.caption, 'Observed ticks · Backfilled from MotiveWave history')
    # A live trade never gets the line, ticks or not.
    live = Journal()
    apply_preview(live, preview_bridge(lines([fill('l1', mode='live', ts='2026-10-07T14:00:00Z'),
                                              fill('l2', 'sell', 5002, mode='live', ts='2026-10-07T14:02:00Z')])))
    app.radio(key='journal-environment').set_value('Live').run()
    assert not app.exception and not said(app.main.caption, common.REPLAY_TICKS_LATER)


def test_view_switcher_always_shows_a_view_and_empty_journal_shortcuts_open_theirs():
    app = open_app()
    assert not app.main.expander                    # nothing to unfold on an empty journal: two buttons
    press(app, 'Import Old Trades')
    assert app.segmented_control(key=common.VIEW_KEY).value == 'Settings'
    assert setup_page(app) == 'Data' and landmarks(app, 'Import', True)          # Data, on its Import Trades tab
    # Clicking the selected view again clears a segmented control. AppTest cannot click one empty, so
    # the cleared state is set directly: the journal keeps showing the view it was on.
    app.session_state[common.VIEW_KEY] = None
    app.run()
    assert not app.exception and landmarks(app, 'Import', True)
    show(app, 'Trades')
    press(app, 'Open Setup')                        # the one step that is missing, not the diagnostics
    assert app.segmented_control(key=common.VIEW_KEY).value == 'Settings'
    assert setup_page(app) == setup.HOME and said(app.markdown, '**Install the Study**')
    assert not said(app.markdown, 'Add It to Your Chart')


def test_a_journal_from_a_newer_version_is_refused_with_a_message_not_a_crash(home):
    db = sqlite3.connect(home / 'trades.sqlite3')
    db.execute('PRAGMA user_version = 99')
    db.commit()
    db.close()
    app = AppTest.from_file(str(APP), default_timeout=30).run()
    assert not app.exception and said(app.error, 'newer version of MotiveWave Journal')
    assert app.segmented_control(key=common.VIEW_KEY).options == [common.NAV_LABELS.get(v, v) for v in common.PRIMARY_VIEWS]
    app.radio(key='journal-environment').set_value('Replay').run()     # the other journal is still usable
    assert not app.exception and not app.error and landmarks(app, 'Trades', empty=True)


# ---------------------------------------------------------------- trades and reviews

def test_empty_journal_then_a_review_is_saved():
    app = open_app()
    assert said(app.info, 'No trades yet')
    journal = Journal()
    journal.ingest([(Execution('test', 'paper', 'ESZ6', 'buy', 1, 5000, '2026-10-07T14:00:00Z',
                              execution_id='a'), {})])
    app.run()
    open_review(app)
    widget(app.text_input, 'Setup / playbook').set_value('Failed breakout')
    widget(app.text_area, 'Review notes').set_value('Waited for the retest.').run()
    widget(app.text_input, 'Category name').set_value('Psychology')
    press(app, 'Create Category')
    widget(app.selectbox, 'Tag category').set_value('Psychology').run()
    widget(app.text_input, 'Tag name').set_value('Steady')
    press(app, 'Create and Apply Tag')
    widget(app.selectbox, 'Tag category').set_value('Psychology').run()
    widget(app.text_input, 'Tag name').set_value('A+')
    press(app, 'Create and Apply Tag')
    annotation = journal.annotations()[journal.trades()[0].id]
    assert annotation['setup'] == 'Failed breakout' and annotation['tags'] == ['A+', 'Steady']
    assert annotation['notes'] == 'Waited for the retest.' and annotation.get('initial_risk') is None
    assert widget(app.text_input, 'Setup / playbook').value == 'Failed breakout'


def test_full_recorded_path_and_runner_controls_render_without_claiming_missing_data(home):
    seed_ticks(seed_preview(home))
    app = open_app()
    widget(app.checkbox, 'Show/export full recorded trade path').set_value(True).run()
    assert not app.exception
    assert any(b.label == 'Download Full Path CSV' for b in app.download_button)
    widget(app.selectbox, 'Look after exit').set_value(120).run()
    assert not app.exception and said(app.caption, 'Partial or gapped horizon')
    # What it was measured from, in words: no program codes.
    assert said(app.caption, 'From ticks · price after exit not complete yet · 1 contract')
    assert said(app.caption, 'Stop 4,998 · Your saved stop · the one R uses · waiting for prices')
    assert not any(code in c.value for c in app.caption for code in ('estimated-bars', 'partial-window', 'awaiting-prices'))
    show(app, 'Edge')
    assert not any(s.label == 'Runner comparison horizon' for s in app.selectbox)    # drawn only when its tab is open
    open_tab(app, 'Runners')
    assert not app.exception and not said(app.info, 'No comparable completed windows')   # not run until asked
    # A form sends its choices with the click, as a browser does: a run between them drops the choice (Streamlit 1.66).
    widget(app.selectbox, 'Runner comparison horizon').set_value(120)
    press(app, 'Compare Runners')
    assert said(app.info, 'No comparable completed windows')
    assert widget(app.selectbox, 'Compare runners by').options == [
        'Individual tags', 'Tag categories', 'Setup / playbook']
    assert not any(b.label in ('Analyze recorded post-exit prices', 'Calculate post-exit tag analysis')
                   for b in app.checkbox)


def test_measured_trade_risk_renders_from_the_traded_price_alone():
    journal = Journal()
    journal.ingest([(Execution('test', 'paper', 'ESZ6', 'buy', 1, 5000, '2026-10-07T14:00:00Z', execution_id='a'), {}),
                    (Execution('test', 'paper', 'ESZ6', 'sell', 1, 5002, '2026-10-07T14:03:00Z', execution_id='b'), {})])
    trade = journal.trades()[0]
    samples = [PriceSample('ESZ6', '2026-10-07T14:00:30Z', 'test-prices', 4999, 4999, bid=4998.75, ask=4999.25)]
    journal.annotate(trade.id, {'setup': 'Failed breakout', 'planned_stop': 4999, 'adherence': 'Yes'})
    for metric in measurements(trade, samples, 'test-prices'):
        journal.record_metric(trade, metric)
    # One measurement, from the traded price: the bid of 4998.75 plays no part, and there is nothing to choose.
    assert [m['basis'] for m in journal.metric_options(trade)] == ['last']
    app = open_app()
    assert widget(app.metric, 'MAE / risk').value == '1.00R'
    assert not any(s.label == 'Measurement' for s in app.selectbox)
    open_tab(show(app, 'Edge'), 'Compare')
    widget(app.selectbox, 'Compare by').set_value('Followed my plan').run()
    assert not app.exception


def test_chart_renders_and_planned_protection_persists_with_tags(home):
    journal = seed_preview(home)
    app = open_app()
    assert any(x.label == 'Chart price source' for x in app.selectbox)
    assert widget(app.selectbox, 'Chart price source').value == 'motivewave-bars:1m'
    assert said(app.main.caption, CREDIT)           # under the chart too, for a closed sidebar
    assert not any(w.label.startswith(('Planned take-profit', 'Initial planned risk'))
                   for w in [*app.text_input, *app.number_input])
    widget(app.text_input, 'Stop').set_value('4997.75').run()                  # saves when the box is left
    annotation = journal.annotations()[journal.trades()[0].id]
    assert annotation['planned_stop'] == 4997.75 and annotation['planned_target'] == 5005   # kept, not shown
    assert annotation['tags'] == ['PAPER FIXTURE', 'breakout']
    widget(app.text_input, 'Stop').set_value('not-a-price').run()
    assert said(app.error, 'must be a number')
    assert journal.annotations()[journal.trades()[0].id]['planned_stop'] == 4997.75
    assert any(t.label == 'Executions' for t in app.tabs)      # the rest of the workspace is still drawn


def test_changing_trade_switches_chart_and_review_without_mixing_annotations(home):
    journal = seed_preview(home)
    journal.ingest([(Execution('motivewave', 'paper', 'ESZ6', 'sell', 1, 5002,
                              '2026-10-07T14:10:00Z', execution_id='new-entry'), {}),
                    (Execution('motivewave', 'paper', 'ESZ6', 'buy', 1, 5001,
                              '2026-10-07T14:12:00Z', execution_id='new-exit'), {})])
    trades = journal.trades()
    app = open_app()
    assert chosen(app) == trades[-1].id and listed(app) == [trades[-1].id, trades[0].id]   # newest first
    pick(app, trades[0].id)
    assert chosen(app) == trades[0].id
    assert widget(app.text_input, 'Setup / playbook').value == 'VISUAL TEST ONLY'
    pick(app, trades[-1].id)
    assert widget(app.text_input, 'Setup / playbook').value == ''


def test_filters_narrow_the_list_and_say_so_when_nothing_matches(home):
    journal = seed_preview(home)
    journal.ingest([(Execution('motivewave', 'second', 'NQZ6', 'buy', 1, 20000, '2026-10-08T14:10:00Z',
                              execution_id='n1'), {}),
                    (Execution('motivewave', 'second', 'NQZ6', 'sell', 1, 20010, '2026-10-08T14:12:00Z',
                              execution_id='n2'), {})])
    app = open_app(review=False)
    assert len(listed(app)) == 2
    widget(app.multiselect, 'Markets').set_value(['NQ']).run()
    assert not app.exception and len(listed(app)) == 1
    widget(app.multiselect, 'Tags').set_value(['breakout']).run()
    assert not app.exception and said(app.info, 'No trades match these filters')
    assert not said(app.info, 'No trades yet')


def test_rollover_filters_group_markets_but_keep_micro_contracts_separate(home):
    journal = Journal()
    for i, symbol in enumerate(('NQZ6', 'NQM7', 'MNQZ6', 'ESZ6', 'ESH7', 'MESZ6')):
        journal.ingest([(Execution('test', 'paper', symbol, 'buy', 1, 100,
                                  f'2026-10-07T14:{i:02d}:00Z', execution_id=f'{symbol}-in'), {}),
                        (Execution('test', 'paper', symbol, 'sell', 1, 101,
                                   f'2026-10-07T14:{i:02d}:30Z', execution_id=f'{symbol}-out'), {})])
    app = open_app(review=False)
    assert widget(app.multiselect, 'Markets').options == ['ES', 'MES', 'MNQ', 'NQ']
    widget(app.multiselect, 'Markets').set_value(['NQ']).run()
    assert not app.exception and len(listed(app)) == 2
    assert {r['symbol'] for r in common.rows_for(journal) if r['market'] == 'NQ'} == {'NQZ6', 'NQM7'}
    widget(app.multiselect, 'Exact Contracts').set_value(['NQM7']).run()
    assert len(listed(app)) == 1
    widget(app.multiselect, 'Exact Contracts').set_value([]).run()
    widget(app.multiselect, 'Markets').set_value(['MNQ']).run()
    assert len(listed(app)) == 1
    open_tab(show(app, 'Edge'), 'Compare')
    assert widget(app.multiselect, 'Markets').value == ['MNQ']
    assert widget(app.selectbox, 'Compare by').options[:2] == ['Market', 'Exact contract']


def test_trade_rows_are_built_again_only_when_something_they_show_changed(home, monkeypatch):
    journal = seed_ticks(seed_preview(home))
    built = []
    original = common.rows_for
    monkeypatch.setattr(common, 'rows_for', lambda journal: built.append(1) or original(journal))
    app = open_app()
    assert built and 'breakout' in widget(app.multiselect, 'Tags').options

    def clicks(*actions):
        """How many times the rows were built during these clicks."""
        before = len(built)
        for action in actions:
            action()
            assert not app.exception
        return len(built) - before

    trade = journal.trades()[0]
    # Clicks that change nothing reuse the rows: a rerun, the open trade's row, another tab, Stats and back.
    assert clicks(app.run, lambda: pick(app, trade.id), lambda: open_tab(app, 'Notes'),
                  lambda: show(app, 'Edge'), lambda: show(app, 'Trades')) == 0
    # A tag, a note, a fill and a measurement each build them once, with the change in them.
    assert clicks(lambda: widget(app.pills, 'Other tags').set_value(['PAPER FIXTURE']).run()) == 1
    assert 'breakout' not in widget(app.multiselect, 'Tags').options
    assert clicks(lambda: widget(app.text_area, 'Review notes').set_value('Waited for the retest.').run()) == 1
    assert clicks(app.run) == 0
    journal.ingest([(Execution('motivewave', 'paper', 'ESZ6', 'buy', 1, 5010, '2026-10-08T14:00:00Z',
                               execution_id='later'), {})])
    assert clicks(app.run) == 1 and len(listed(app)) == 2
    later = next(t for t in journal.trades() if t.id != trade.id)
    journal.record_metric(later, {'source': 'motivewave-bars:1m', 'precision': 'pending', 'path': [], 'notes': []})
    assert clicks(app.run, app.run) == 1

    def shown(id):
        return next(r for r in app.session_state[common.key(journal, 'trade-rows')][1] if r['id'] == id)

    # A measurement stored again. The open trade's, which the watcher stores at every poll and the page draws live,
    # reuses them; a closed trade's (prices that came in late) builds them once, with its new MAE.
    journal.record_metric(later, {'source': 'motivewave-bars:1m', 'precision': 'pending', 'path': [], 'notes': ['again']})
    assert clicks(app.run) == 0
    from mwjournal.metrics import excursions
    deeper = [dataclasses.replace(s, high=s.high - 3, low=s.low - 3) for s in journal.prices(trade.symbol, 'motivewave-ticks')]
    journal.record_metric(trade, excursions(trade, deeper, 'motivewave-ticks'))
    assert shown(trade.id)['mae'] != journal.best_metric(trade)['mae']
    assert clicks(app.run) == 1 and shown(trade.id)['mae'] == journal.best_metric(trade)['mae']
    # A multiplier set again to another value: the trade's result in the list follows it.
    journal.set_instrument('ESZ6', 50)
    assert clicks(app.run) == 1 and '242.00' in app.button(key=f'pick-{trade.id}-live').label
    journal.set_instrument('ESZ6', 5)
    assert clicks(app.run) == 1 and '17.00' in app.button(key=f'pick-{trade.id}-live').label


def test_stats_choices_are_still_chosen_after_another_tab_was_open(home):
    """Only the open Stats tab is drawn, and Streamlit forgets the value of a widget a run does not draw."""
    seed_preview(home)
    app = open_tab(open_app('Edge'), 'Compare')
    assert widget(app.selectbox, 'Compare by').value == 'Tags'
    widget(app.selectbox, 'Compare by').set_value('Playbooks').run()
    open_tab(app, 'Runners')
    assert widget(app.selectbox, 'Runner comparison horizon').value == 15
    assert widget(app.checkbox, 'Only winning trades').value
    widget(app.selectbox, 'Runner comparison horizon').set_value(120)
    widget(app.selectbox, 'Compare runners by').set_value('tag_categories')
    widget(app.checkbox, 'Only winning trades').uncheck()
    press(app, 'Compare Runners')
    open_tab(app, 'Overview')
    open_tab(app, 'Compare')
    assert widget(app.selectbox, 'Compare by').value == 'Playbooks'
    open_tab(app, 'Runners')
    assert (widget(app.selectbox, 'Runner comparison horizon').value, widget(app.selectbox, 'Compare runners by').value,
            widget(app.checkbox, 'Only winning trades').value) == (120, 'tag_categories', False)


def test_simple_navigation_and_optional_controls_do_not_remove_reviews(home):
    seed_ticks(seed_preview(home))
    app = open_app(review=False)
    assert app.segmented_control(key=common.VIEW_KEY).options == [common.NAV_LABELS.get(v, v) for v in common.PRIMARY_VIEWS]
    assert not any(s.label == 'Settings page' for s in app.selectbox)
    assert any(m.label == 'MAE' for m in app.metric)  # review is visible alongside the list immediately
    assert all(not e.proto.expanded for e in app.expander if e.label == 'More Filters')
    assert listed(app) and said(app.markdown, "class='mwj-day'")      # a scannable list, not a table
    assert not app.dataframe or not any('Opened (UTC)' in d.value.columns for d in app.dataframe)
    open_review(app)
    assert widget(app.metric, 'MAE').value != '—' and widget(app.metric, 'MFE').value != '—'
    assert [t.label for t in app.tabs] == [
        'Chart & Tags', 'Notes', 'Checklist', 'More', 'MAE / MFE', 'Executions', 'Runner']
    # The session tools, then the list's own menus, all in the row above the list.
    assert [b.proto.popover.label for b in app.main.get('popover')][:5] == [
        'Check-Ins', 'Drafts', 'Checklist', 'Filters', 'Options']
    assert not any(b.proto.popover.label in ('Chart settings', 'More', 'Trade filters') for b in app.get('popover'))
    assert not any(s.label == 'Review a trade' for s in app.selectbox)
    assert not any(b.label.startswith('Refresh') for b in app.button)
    assert any(s.label == 'Markets' for s in app.multiselect)  # filters live in a popover, not above the chart
    show(app, 'Connection')
    assert setup_page(app) == 'Data'
    assert not any(button.label == 'Arm recording' for button in app.button)
    assert not any('FFmpeg' in field.label for field in app.text_input)


def test_a_trade_on_a_later_day_is_not_hidden_by_the_date_range_of_the_day_before(home):
    journal = seed_preview(home)
    app = open_app(review=False)
    assert len(listed(app)) == 1
    journal.ingest([(Execution('motivewave', 'paper', 'ESZ6', 'buy', 1, 5010, '2026-10-08T14:10:00Z',
                              execution_id='next-day'), {})])
    app.run()
    assert not app.exception
    assert len(listed(app)) == 2
    assert chosen(app) == journal.trades()[0].id      # the selection is kept


def test_currencies_are_never_mixed_and_the_choice_appears_only_when_there_are_two():
    journal = Journal()
    round_trip(journal)
    app = open_app()
    assert not any(s.label == 'Journal currency' for s in app.selectbox)
    journal.ingest([(Execution('test', 'eurex', 'FDAX', 'buy', 1, 100, '2026-10-08T08:00:00Z', execution_id='e1',
                              point_value=25, currency='EUR'), {}),
                    (Execution('test', 'eurex', 'FDAX', 'sell', 1, 104, '2026-10-08T08:05:00Z', execution_id='e2',
                              point_value=25, currency='EUR'), {})])
    app.run()
    choice = widget(app.sidebar.selectbox, 'Journal currency')
    open_review(app)
    choice = widget(app.sidebar.selectbox, 'Journal currency')
    assert choice.options == ['EUR', 'USD'] and choice.value == 'EUR'
    assert widget(app.metric, 'Net P&L').value == '+€100.00'
    choice.set_value('USD').run()
    open_review(app)
    assert not app.exception and widget(app.metric, 'Net P&L').value == '+$242.00'
    show(app, 'Edge')
    assert widget(app.metric, 'Net P&L').value == '+$242.00'       # not 342 of mixed money


def test_unknown_instrument_gets_its_multiplier_where_the_trade_is_shown():
    journal = Journal()
    journal.ingest([(Execution('test', 'paper', 'XYZ', 'buy', 1, 100, '2026-10-07T14:00:00Z', execution_id='a'), {}),
                    (Execution('test', 'paper', 'XYZ', 'sell', 1, 103, '2026-10-07T14:03:00Z', execution_id='b'), {})])
    assert journal.trades()[0].net is None
    app = open_app()
    assert said(app.warning, 'XYZ has no dollars-per-point multiplier')
    widget(app.number_input, 'Currency per price point').set_value(10.)
    press(app, 'Set Multiplier and Recalculate')
    assert journal.trades()[0].net == 30
    assert widget(app.metric, 'Net P&L').value == '+$30.00' and not said(app.warning, 'multiplier')


# ---------------------------------------------------------------- edge

def test_edge_separates_gross_results_fees_and_planned_risk(home):
    seed_preview(home)
    app = open_app('Edge')
    assert widget(app.metric, 'Gross P&L').value == '+$250.00'
    assert widget(app.metric, 'Fees').value == '$8.00'
    assert widget(app.metric, 'Fees Per Trade').value == '$8.00'
    assert helped(app.metric, 'Recorded fees average 0.040R')
    assert helped(app.metric, 'not an estimate of slippage')

def test_edge_draws_excursions_for_captured_trades_nobody_has_reviewed(home):
    """Automatic capture produces measured trades with no setup, tags or risk; the statistics must not need them."""
    journal = Journal()
    round_trip(journal)
    seed_ticks(journal)
    # A second closed trade that has no prices at all: measured and unmeasured rows are mixed.
    journal.ingest([(Execution('motivewave', 'paper', 'NQZ6', 'buy', 1, 20000, '2026-10-08T14:10:00Z',
                              execution_id='n1'), {}),
                    (Execution('motivewave', 'paper', 'NQZ6', 'sell', 1, 19990, '2026-10-08T14:12:00Z',
                              execution_id='n2'), {})])
    assert not journal.annotations()
    app = open_app('Edge')
    assert widget(app.metric, 'Win Rate').value == '50.0%'
    assert len(app.get('plotly_chart')) == 2                  # Overview: the equity curve and the results
    open_tab(app, 'MAE / MFE')
    assert helped(app.markdown, 'Colour shows how the move was measured') and not helped(app.markdown, 'Giveback')
    assert len(app.get('plotly_chart')) == 1


# ---------------------------------------------------------------- import

def test_csv_fills_are_mapped_previewed_and_imported_once():
    app = open_app('Import')
    widget(app.file_uploader, 'Executions').upload('fills.csv', FILLS_CSV.encode(), 'text/csv').run()
    assert not app.exception
    assert next(s for s in app.selectbox if s.label == 'price').value == 'Avg Price'
    assert said(app.markdown, '2 records ready')
    press(app, 'Import Into My Journal')
    assert said(app.success, 'Imported 2 new executions')
    journal = Journal()
    trade = journal.trades()[0]
    assert trade.closed and trade.net == 96 and trade.provider == 'csv'
    press(app, 'Import Into My Journal')
    assert said(app.success, 'Skipped 2 existing fills') and len(journal.executions()) == 2
    show(app, 'Trades')
    assert chosen(app) == trade.id and listed(app) == [trade.id]


def test_csv_with_a_missing_required_column_is_an_error_message():
    app = open_app('Import')
    widget(app.file_uploader, 'Executions').upload('fills.csv', b'Instrument,Side\nESZ6,Buy\n', 'text/csv').run()
    assert not app.exception and said(app.error, 'Map these required columns')
    assert not Journal().executions()


def test_price_csv_import_calculates_excursions():
    journal = Journal()
    trade = round_trip(journal)
    app = open_app('Import')
    widget(app.text_input, 'Export time zone').set_value('UTC')
    widget(app.selectbox, 'Price file resolution').set_value(60)
    prices = 'Date,Time,Open,High,Low,Close,Volume\n' + ''.join(
        f'2026-10-07,14:0{m}:00,5000,5004,4999,5002,123\n' for m in range(4))
    widget(app.file_uploader, 'Prices CSV').upload('bars.csv', prices.encode(), 'text/csv').run()
    press(app, 'Import Prices')
    assert said(app.success, '4 prices')
    assert journal.price_sources('ESZ6') == ['my-data']
    assert journal.best_metric(trade)['precision'] == 'estimated-bars'


def test_bridge_file_import_by_hand_refuses_the_other_environment():
    app = open_app('Import')
    widget(app.selectbox, 'Import format').set_value('bridge')
    widget(app.file_uploader, 'Executions').upload('replay.ndjson', lines([fill('a')]).encode(), 'text/plain').run()
    assert not app.exception and said(app.error, 'other trading environment')
    assert widget(app.button, 'Import Into My Journal').disabled
    assert not Journal().executions()


def test_manual_fill_keeps_the_time_that_was_typed_and_is_measured_against_stored_prices():
    journal = Journal()
    journal.ingest_prices([bar(0), bar(1), bar(2)])                # what the study recorded for that contract
    app = open_app('Import')
    widget(app.text_input, 'Fill timestamp').set_value('2026-10-07T10:00:23-04:00')
    widget(app.number_input, 'Fill price').set_value(5000.25)
    press(app, 'Record Fill')
    assert said(app.success, 'Recorded 1 new fill')
    recorded = journal.executions()[0]
    assert recorded.ts == '2026-10-07T14:00:23+00:00' and recorded.price == 5000.25 and recorded.provider == 'manual'
    widget(app.text_input, 'Fill timestamp').set_value('yesterday at ten')
    press(app, 'Record Fill')
    assert said(app.error, 'Invalid timestamp') and len(journal.executions()) == 1
    widget(app.text_input, 'Fill timestamp').set_value('2026-10-07T14:02:10Z')
    widget(app.selectbox, 'Side').set_value('sell')
    press(app, 'Record Fill')
    trade = journal.trades()[0]
    assert trade.closed and journal.best_metric(trade)['precision'] == 'estimated-bars'


# ---------------------------------------------------------------- daily review and export

def test_daily_review_opens_on_the_latest_trading_day_and_is_saved(home):
    journal = seed_preview(home)
    journal.save_preferences(display_timezone='America/New_York')      # the trade list's days, and the card's
    app = open_app('Daily review')
    assert widget(app.date_input, 'Trading day').value == dt.date(2026, 10, 7)
    assert widget(app.metric, 'Trades').value == '1' and widget(app.metric, 'Net P&L').value == '+$242.00'
    widget(app.checkbox, 'I had a plan').set_value(True).run()                # each box saves when it changes
    assert journal.get('sessions')['2026-10-07:America/New_York']['planned']
    widget(app.text_area, 'What I learned').set_value('Sized correctly.').run()
    saved = journal.get('sessions')['2026-10-07:America/New_York']
    assert saved['planned'] and saved['notes'] == 'Sized correctly.' and saved['date'] == '2026-10-07'
    assert 'rating' not in saved                                               # untouched: nothing is made up
    widget(app.slider, 'Session process score').set_value(4).run()
    assert journal.get('sessions')['2026-10-07:America/New_York']['rating'] == 4
    app.run()
    assert widget(app.text_area, 'What I learned').value == 'Sized correctly.'
    assert not any(b.label == 'Save Daily Review' for b in app.button)


def test_the_backup_holds_both_environments_and_the_csv_this_one(home):
    from views import export
    journal = seed_preview(home)
    table = export.trades_csv(common.rows_for(journal), 'UTC')
    assert table.splitlines()[0].startswith('id,account,symbol') and len(table.splitlines()) == 2
    assert 'VISUAL TEST ONLY' in table.splitlines()[1]
    zipped = export.backup_zip()                                   # built only when Back Up Journal is pressed
    assert backup.read(io.BytesIO(zipped))['trades'] == {'live': 1, 'replay': 0}
    app = open_app('Export', 'Replay')
    assert backup_controls(app) and app.download_button[0].proto.type == 'primary'


# ---------------------------------------------------------------- the study connection

def test_nothing_is_typed_the_app_watches_the_default_bridge_and_waits_for_the_study():
    app = open_app('Connection')
    assert states(app.main) == ['Waiting For Study'] and states(app.sidebar) == ['Waiting For Study']
    for dataset in paths.DATASETS:
        assert Journal(dataset=dataset).get('profiles') == {DEFAULT_CONNECTION: {
            'path': str(paths.bridge_file(dataset)), 'mode': 'bridge', 'enabled': True}}
    assert not paths.bridge_file().exists()
    assert any(c.value == str(paths.bridge_file('live')) for c in app.main.code)
    show(app, 'Setup')                              # the step that is missing is on Setup's first page
    assert states(app.main) == ['Waiting For Study'] and said(app.main.markdown, '**Install the Study**')
    assert any(b.label == 'Install MotiveWave Study' for b in app.button)
    assert not any(str(paths.bridge_file('live')) in m.value for m in [*app.main.markdown, *app.main.code])


def shape(app):
    return [(len(block.markdown), len(block.caption), len(block.warning), len(block.metric), len(block.code))
            for block in (app.sidebar, app.main)]


def test_connection_turns_connected_once_the_study_writes_a_fresh_heartbeat():
    app = open_app('Connection')
    assert states(app.main) == ['Waiting For Study']
    waiting = shape(app)
    study_writes([heartbeat()])
    wait_until(lambda: status_of().get('heartbeat'))
    app.run()
    assert not app.exception
    assert states(app.main) == ['Connected'] and states(app.sidebar) == ['Connected']
    assert said(app.main.markdown, f'Connected · study {paths.STUDY_VERSION}')
    assert not said(app.sidebar.markdown, 'study ' + paths.STUDY_VERSION)   # too wide for the sidebar badge; Setup shows it
    assert said(app.main.markdown, 'The study says: Read-only capture · workspace paper')
    assert not said(app.sidebar.markdown, 'has not appeared yet')
    connected = shape(app)
    show(app, 'Setup')                              # one green line and no step
    assert states(app.main) == ['Connected'] and said(app.main.markdown, f'Connected · study {paths.STUDY_VERSION}')
    assert not any(b.label == 'Install MotiveWave Study' for b in app.button)
    assert not any(f'**{title}**' in m.value for m in app.main.markdown for title, _, _ in setup.STEPS.values())
    show(app, 'Connection')
    assert shape(app) == connected
    # What the study could not link or read is said there too, beside Connected, and so are the events of a
    # kind this app does not read, which are skipped and counted.
    warned = {**heartbeat(), 'warnings': '1 working stop order not linked to a position: no position on that '
                                         'account and contract (orders: 3 read, 1 stop, 0 new, 0 without instrument, '
                                         '0 without state, 0 with a position, 0 on the closing side, 0 linked)'}
    study_writes([{'version': 1, 'type': 'position', 'mode': 'live', 'provider': 'motivewave-native:paper:live'}, warned])
    wait_until(lambda: status_of().get('heartbeat', {}).get('warnings'))
    app.run()
    assert states(app.main) == ['Connected'] and shape(app) == connected
    assert said(app.main.markdown, 'The study also says: 1 working stop order not linked to a position: no position '
                                   'on that account and contract (orders: 3 read')
    assert said(app.main.markdown, 'Events of a kind this version does not read, skipped: 1.')
    # The status redraws itself in the browser every few seconds. Seen in a real browser: when such a
    # redraw has fewer elements than the one before, the old ones can stay on screen. So every state
    # draws the same elements and only their text changes.
    assert shape(app) == waiting
    with paths.bridge_file().open('a') as fh:
        fh.write('{"type":"exec')                    # cut off by a crash; the restarted study appends to that line
    study_writes([heartbeat(), heartbeat('error', detail='Unsupported MotiveWave version')])
    wait_until(lambda: status_of().get('error'))
    app.run()
    assert states(app.main) == ['Needs Attention'] and said(app.main.markdown, '1 damaged lines')
    assert shape(app) == waiting
    show(app, 'Setup')                              # one line saying what to do, without the raw error
    assert states(app.main) == ['Needs Attention'] and said(app.main.markdown, 'Open Data → Connection')
    assert not said(app.main.markdown, 'Unsupported MotiveWave version')


def test_trades_the_study_writes_show_up_without_any_setup():
    app = open_app()
    assert said(app.info, 'No trades yet')
    study_writes([heartbeat(), fill('a', mode='live', ts='2026-10-07T14:00:23Z'),
                  {'version': 1, 'type': 'bar', 'provider': 'motivewave-bars:1m', 'symbol': 'ESZ6',
                   'ts': '2026-10-07T14:00:00Z', 'resolution_seconds': 60,
                   'open': 5000, 'high': 5004, 'low': 4999, 'close': 5002, 'volume': 123},
                  {'version': 1, 'type': 'tick', 'mode': 'live', 'provider': 'motivewave-ticks', 'symbol': 'ESZ6',
                   'ts': '2026-10-07T14:00:30Z', 'price': 4999},
                  fill('b', 'sell', 5002, mode='live', ts='2026-10-07T14:00:50Z')])
    study_writes([heartbeat(mode='replay'), fill('r1'), tick(5001)], 'replay')
    wait_until(lambda: Journal().trades() and Journal().best_metric(Journal().trades()[0]))
    wait_until(lambda: status_of('replay').get('heartbeat'))         # read while Live is on screen
    app.run()
    open_review(app)
    assert not app.exception
    assert widget(app.metric, 'Net P&L').value == '+$96.00'
    assert widget(app.selectbox, 'Chart price source').value == 'motivewave-bars:1m'
    assert widget(app.metric, 'Maximum adverse excursion').value == '$50.00'      # a magnitude: no sign
    assert len(Journal().executions()) == 2 and len(Journal(dataset='replay').executions()) == 1
    app.radio(key='journal-environment').set_value('Replay').run()
    assert not app.exception and states(app.sidebar) == ['Connected']
    open_review(app)
    # An open position: bought at 5000 with a $2 fee, and the replay's last print is 5001, one ES point up.
    result = widget(app.metric, 'Open P&L')
    assert result.value == '+$48.00' and 'closed at 5,001,' in result.help and 'Realised so far: -$2.00' in result.help


def test_group_tag_clicks_autosave_and_preserve_legacy_tags_and_plan(home):
    journal = seed_preview(home)
    trade = journal.trades()[0]
    app = open_app()
    original = journal.annotations()[trade.id]
    widget(app.toggle, 'Show starter examples').set_value(True).run()
    widget(app.pills, 'Setup').set_value(['Breakout', 'Pullback']).run()
    widget(app.pills, 'Mindset').set_value(['Patient']).run()
    assert set(journal.annotations()[trade.id]['tags']) == {
        *original['tags'], 'Breakout', 'Pullback', 'Patient'}
    widget(app.pills, 'Setup').set_value(['Pullback']).run()
    saved = journal.annotations()[trade.id]
    assert 'Breakout' not in saved['tags'] and 'Patient' in saved['tags']
    assert saved['notes'] == original['notes'] and saved['initial_risk'] == original['initial_risk']
    widget(app.pills, 'Process rating').set_value(5).run()
    assert journal.annotations()[trade.id]['rating'] == 5
    assert not any(b.label == 'Save review' for b in app.button)
    app.run()
    assert widget(app.pills, 'Setup').value == ['Pullback']
    assert listed(app) == [trade.id]  # list and tagging remain together
    open_review(app)
    assert widget(app.pills, 'Mindset').value == ['Patient']


def test_new_tag_and_copy_tags_preserve_notes_and_are_available_on_other_trades(home):
    journal = seed_preview(home)
    journal.ingest([(Execution('test', 'paper', 'NQZ6', 'buy', 1, 20000,
                               '2026-10-08T14:00:00Z', execution_id='copy-target'), {})])
    app = open_app()
    target = journal.trades()[-1]
    donor = journal.trades()[0]
    widget(app.text_area, 'Review notes').set_value('Do not overwrite this.').run()
    widget(app.selectbox, 'Tag category').set_value('＋ New category').run()
    widget(app.text_input, 'Tag name').set_value('VWAP reclaim')
    widget(app.text_input, 'New category name').set_value('My setups')
    press(app, 'Create and Apply Tag')
    assert widget(app.pills, 'My setups').value == ['VWAP reclaim']
    press(app, 'Apply Copied Tags')
    assert set(journal.annotations()[target.id]['tags']) == {'VWAP reclaim', 'breakout', 'PAPER FIXTURE'}
    assert journal.annotations()[target.id]['notes'] == 'Do not overwrite this.'
    assert journal.annotations()[donor.id]['notes'] == 'Synthetic fixture, not an actual account trade.'
    pick(app, donor.id)
    assert 'VWAP reclaim' in widget(app.pills, 'My setups').options
    assert widget(app.pills, 'My setups').value == []


def test_return_from_detail_preserves_market_filters_and_tag_removal_does_not_jump_trades(home):
    journal = seed_preview(home)
    app = open_app(review=False)
    widget(app.multiselect, 'Markets').set_value(['ES']).run()
    widget(app.multiselect, 'Tags').set_value(['breakout']).run()
    open_review(app)
    selected = chosen(app)
    widget(app.pills, 'Other tags').set_value(['PAPER FIXTURE']).run()
    assert chosen(app) == selected
    assert 'breakout' not in journal.annotations()[selected]['tags']
    app.run()
    assert widget(app.multiselect, 'Markets').value == ['ES']
    assert widget(app.multiselect, 'Tags').value == ['breakout']
    assert said(app.info, 'No trades match these filters')


def test_connection_says_stale_when_the_study_has_gone_quiet():
    study_writes([heartbeat(age=600)])
    app = open_app('Connection')
    wait_until(lambda: status_of().get('heartbeat'))
    app.run()
    assert states(app.main) == ['Stale'] and states(app.sidebar) == ['Stale']
    assert said(app.main.markdown, 'last heard from 10 min ago')


def test_connection_needs_attention_shows_the_error_the_study_reported():
    study_writes([heartbeat('error', detail='Unsupported MotiveWave version')])
    app = open_app('Connection')
    wait_until(lambda: status_of().get('error'))
    app.run()
    assert states(app.main) == ['Needs Attention'] and states(app.sidebar) == ['Needs Attention']
    assert said(app.main.markdown, 'Unsupported MotiveWave version')
    assert not said(app.sidebar.markdown, 'Unsupported MotiveWave version')    # the badge is one word


def test_capture_can_be_paused_and_resumed_from_the_connection_view():
    app = open_app('Connection')
    widget(app.toggle, 'Read Trades From MotiveWave').set_value(False).run()
    assert not app.exception and states(app.main) == ['Paused']
    assert Journal().get('profiles')[DEFAULT_CONNECTION]['enabled'] is False
    assert Journal(dataset='replay').get('profiles')[DEFAULT_CONNECTION]['enabled'] is True
    study_writes([heartbeat()])
    time.sleep(1.5)                                  # a reader that ignored the switch would have imported by now
    assert not status_of().get('heartbeat')
    widget(app.toggle, 'Read Trades From MotiveWave').set_value(True).run()
    wait_until(lambda: status_of().get('heartbeat'))
    app.run()
    assert states(app.main) == ['Connected']


def test_advanced_watches_a_different_bridge_file_in_both_environments(home):
    app = open_app('Connection')
    other = home / 'elsewhere' / 'mw.ndjson'
    widget(app.text_input, 'Live bridge file').set_value('relative/mw.ndjson')
    press(app, 'Watch This File')
    assert said(app.error, 'full path')
    widget(app.text_input, 'Live bridge file').set_value(str(other.with_name('mw.replay.ndjson')))
    press(app, 'Watch This File')
    assert said(app.error, 'That is a replay file')
    assert Journal().get('profiles')[DEFAULT_CONNECTION]['path'] == str(paths.bridge_file())
    widget(app.text_input, 'Live bridge file').set_value(str(other))
    press(app, 'Watch This File')
    assert Journal().get('profiles')[DEFAULT_CONNECTION] == {'path': str(other), 'mode': 'bridge', 'enabled': True}
    assert Journal(dataset='replay').get('profiles')[DEFAULT_CONNECTION]['path'] == str(other.with_name('mw.replay.ndjson'))
    assert any(c.value == str(other) for c in app.main.code) and states(app.main) == ['Waiting For Study']
    other.parent.mkdir()
    other.write_text(lines([heartbeat()]))
    wait_until(lambda: status_of().get('heartbeat'))
    app.run()
    assert states(app.main) == ['Connected']
    paths.bridge_file().parent.mkdir()
    paths.bridge_file().write_text('')                             # the default file exists but the study is not writing it
    press(app, 'Use the Default File')
    assert Journal().get('profiles')[DEFAULT_CONNECTION]['path'] == str(paths.bridge_file())
    assert Journal(dataset='replay').get('profiles')[DEFAULT_CONNECTION]['path'] == str(paths.bridge_file('replay'))
    assert states(app.main) == ['Waiting For Study']               # the other file's heartbeat no longer counts


def test_another_watched_csv_can_be_added_paused_and_removed(home):
    exported = home / 'fills.csv'
    exported.write_text(FILLS_CSV)
    app = open_app('Connection')
    widget(app.text_input, 'Name').set_value('worker')
    widget(app.text_input, 'File to watch').set_value(str(exported))
    press(app, 'Add This File')
    assert said(app.error, 'not in use') and list(Journal().get('profiles')) == [DEFAULT_CONNECTION]
    widget(app.text_input, 'Name').set_value('Broker export')
    widget(app.text_input, 'File to watch').set_value(str(home / 'missing.csv'))
    press(app, 'Add This File')
    assert said(app.error, 'existing file')
    widget(app.text_input, 'File to watch').set_value(str(exported))
    press(app, 'Add This File')
    assert Journal().get('profiles')['Broker export']['mode'] == 'executions'
    wait_until(lambda: len(Journal().executions()) == 2)
    app.run()
    assert not app.exception and said(app.main.markdown, 'Broker export')
    assert states(app.main) == ['Waiting For Study']               # the study's own state is not the CSV's
    press(app, 'Remove')
    assert list(Journal().get('profiles')) == [DEFAULT_CONNECTION]
    assert 'Broker export' not in Journal().get('sync_status')


def test_changing_bridge_checks_both_journals_before_changing_either(home):
    from views import connection
    live = Journal()
    from mwjournal.sync import ensure_default_connection
    ensure_default_connection(live)
    before = live.get('profiles')
    replay = Journal(dataset='replay')
    with replay.connect() as db:
        db.execute('PRAGMA user_version = 99')
    error = connection.watch(home / 'elsewhere.ndjson')
    assert 'Neither connection was changed' in error
    assert live.get('profiles') == before


def test_connection_view_sets_an_instrument_multiplier():
    journal = Journal()
    journal.ingest([(Execution('test', 'paper', 'XYZ', 'buy', 1, 100, '2026-10-07T14:00:00Z', execution_id='a'), {}),
                    (Execution('test', 'paper', 'XYZ', 'sell', 1, 103, '2026-10-07T14:03:00Z', execution_id='b'), {})])
    app = open_app('Connection')
    assert widget(app.selectbox, 'Instrument').value == 'XYZ'
    widget(app.number_input, 'Currency per price point').set_value(2.5)
    press(app, 'Set Multiplier and Recalculate')
    assert journal.trades()[0].net == 7.5


def test_excursion_unit_picker_updates_detail_list_plot_and_persists_without_rewriting_data(home):
    journal = seed_ticks(seed_preview(home))
    trade = journal.trades()[0]
    before = journal.best_metric(trade)
    reviews = journal.annotations()
    app = open_app('General')
    assert widget(app.selectbox, 'MAE / MFE Units').options == [
        'Dollars / account currency', 'Points', 'Ticks', 'R · risk to your stop']
    display_setting(app, 'MAE / MFE Units', 'points')
    assert widget(app.metric, 'MFE').value == '3.50 pts'
    path = next(json.loads(x.proto.spec) for x in app.get('plotly_chart') if 'Price move' in x.proto.spec)
    assert path['layout']['yaxis']['title']['text'].endswith('(pts)')
    assert common.display_excursion_rows(journal, common.rows_for(journal))[0]['display_mfe'] == pytest.approx(3.5)
    assert 'held average entry' in widget(app.metric, 'MAE').help     # the unit is explained on hover
    open_review(app)
    display_setting(app, 'MAE / MFE Units', 'ticks')
    assert widget(app.metric, 'MFE').value == '14.00 ticks'
    # In dollars and R the most was with both contracts held, 2.5 points up: $250, over $200 at risk to 4998.
    display_setting(app, 'MAE / MFE Units', 'r')
    assert widget(app.metric, 'MFE').value == '1.25R'
    display_setting(app, 'MAE / MFE Units', 'currency')
    assert widget(app.metric, 'MFE').value == '$250.00'
    path = next(json.loads(x.proto.spec) for x in app.get('plotly_chart') if 'Move of the position held' in x.proto.spec)
    assert path['layout']['yaxis']['tickformat'].startswith('$')        # its axis reads like every other amount
    assert journal.best_metric(trade) == before
    assert journal.annotations() == reviews
    display_setting(app, 'MAE / MFE Units', 'points')
    fresh = open_app('General')
    assert widget(fresh.selectbox, 'MAE / MFE Units').value == 'points'
    fresh.radio(key='journal-environment').set_value('Replay').run()
    assert widget(fresh.selectbox, 'MAE / MFE Units').value == 'currency'
    fresh.radio(key='journal-environment').set_value('Live').run()
    assert widget(fresh.selectbox, 'MAE / MFE Units').value == 'points'


def test_missing_risk_and_unknown_tick_size_show_unavailable_with_one_time_setup(home):
    journal = Journal()
    journal.ingest([(Execution('test', 'paper', 'CUSTOM', 'buy', 2, 100, '2026-10-07T14:00:00Z', execution_id='a', point_value=1), {}),
                    (Execution('test', 'paper', 'CUSTOM', 'sell', 2, 102, '2026-10-07T14:03:00Z', execution_id='b', point_value=1), {})])
    trade = journal.trades()[0]
    samples = [PriceSample('CUSTOM', '2026-10-07T14:01:00Z', 'custom', 103, 103),
               PriceSample('CUSTOM', '2026-10-07T14:01:30Z', 'custom', 99, 99)]
    journal.ingest_prices(samples)
    for metric in measurements(trade, samples, 'custom'):
        journal.record_metric(trade, metric)
    app = open_app()
    display_setting(app, 'MAE / MFE Units', 'r')
    assert widget(app.metric, 'MFE').value == '—' and said(app.info, 'No stop order 10 seconds after entry')
    assert any(b.label == 'Recalculate From Stored Prices' for b in app.button)
    display_setting(app, 'MAE / MFE Units', 'ticks')
    assert widget(app.metric, 'MFE').value == '—'
    widget(app.number_input, 'Minimum price increment').set_value(.5)
    press(app, 'Save Tick Size')
    assert widget(app.metric, 'MFE').value == '6.00 ticks'
    assert journal.display_settings()['tick_sizes'] == {'CUSTOM': .5}
    show(app, 'Export')
    from views import export
    table = export.trades_csv(common.rows_for(journal), 'UTC').splitlines()
    assert float(table[1].split(',')[table[0].split(',').index('mfe')]) == 6  # account currency, not ticks


def test_old_tick_metrics_get_points_for_display_and_platform_reports_are_never_shown(home):
    journal = seed_ticks(seed_preview(home))
    trade = journal.trades()[0]
    legacy = next(m for m in journal.metric_options(trade) if m['source'] == 'motivewave-ticks')
    for name in ('mae_points', 'mfe_points', 'point_definition', 'point_precision'):
        legacy.pop(name, None)
    journal.record_metric(trade, legacy)
    report = {'source': 'platform-report-only', 'precision': 'platform-reported', 'mae': 999, 'mfe': 9999,
              'notes': [], 'path': []}
    journal.record_metric(trade, report)
    assert journal.best_metric(trade)['mfe'] == 9999
    app = open_app()
    # Only recorded ticks measure a trade: the report's dollars are never shown, whatever their rank.
    assert widget(app.metric, 'MFE').value == '$250.00'
    display_setting(app, 'MAE / MFE Units', 'points')
    assert widget(app.metric, 'MFE').value == '3.50 pts'          # worked out again from the stored prints
    assert not said(app.caption, 'OHLC estimate') and not any('9,999' in m.value for m in app.metric)
    stored_legacy = next(m for m in journal.metric_options(trade) if m['source'] == legacy['source'])
    assert 'mfe_points' not in stored_legacy
    assert not any(s.label == 'Measurement' for s in app.selectbox)


def test_analytics_is_question_based_and_selected_units_change_only_excursion_axes(home):
    seed_ticks(seed_preview(home))
    app = open_app('Edge')
    assert [t.label for t in app.tabs] == ['Overview', 'Compare', 'MAE / MFE', 'Runners', 'Dashboards']
    assert widget(app.metric, 'Average Trade').value == '+$242.00'
    # Only the open tab is drawn: Overview's equity curve, and none of Compare's controls yet.
    assert widget(app.metric, 'Gross P&L') and not any(s.label == 'Compare by' for s in app.selectbox)
    open_tab(app, 'Compare')
    assert widget(app.selectbox, 'Compare by').value == 'Tags' and not any(m.label == 'Gross P&L' for m in app.metric)
    # The tag tables: Totals first, each a compact table of Trades, Net P&L, Win % and Average Trade.
    tables = [m.value for m in app.markdown if "class='mwj-edge-table'" in m.value]
    assert '>Totals</th><th>Trades</th><th>Net P&amp;L</th><th>Win %</th><th>Average Trade</th>' in tables[0]
    display_setting(app, 'MAE / MFE Units', 'ticks', back='Edge')
    assert not app.exception and widget(app.metric, 'Net P&L').value == '+$242.00'
    open_tab(app, 'MAE / MFE')
    excursion = next(json.loads(x.proto.spec) for x in app.get('plotly_chart') if 'One dot per measured trade' in x.proto.spec)
    assert excursion['layout']['xaxis']['title']['text'] == 'Against me · MAE (ticks)'
    assert excursion['layout']['yaxis']['title']['text'] == 'In my favour · MFE (ticks)'


def test_reuse_tag_groups_and_instruments_with_no_extra_name_typing(home):
    journal = seed_preview(home)
    app = open_app()
    assert not any(x.label == 'New group name' for x in app.text_input)
    widget(app.selectbox, 'Tag category').set_value('Other tags').run()
    widget(app.text_input, 'Tag name').set_value('VWAP reclaim')
    press(app, 'Create and Apply Tag')
    assert journal.tag_catalog()['VWAP reclaim'] == 'Other tags'
    assert 'VWAP reclaim' in widget(app.pills, 'Other tags').value
    show(app, 'Connection')
    assert widget(app.selectbox, 'Instrument').value == 'ESZ6'
    assert not any(x.label == 'Instrument' for x in app.text_input)
    assert widget(app.number_input, 'Tick size').value == .25
    widget(app.selectbox, 'Instrument').set_value('＋ Other instrument').run()
    assert widget(app.number_input, 'Tick size').value == 0
    widget(app.text_input, 'Other instrument symbol').set_value('CUSTOM')
    widget(app.number_input, 'Currency per price point').set_value(2)
    press(app, 'Set Multiplier and Recalculate')
    assert 'CUSTOM' not in journal.display_settings()['tick_sizes']


def test_saved_setup_picker_applies_without_retyping_or_overwriting_review(home):
    journal = seed_preview(home)
    journal.ingest([(Execution('test', 'paper', 'NQZ6', 'buy', 1, 20000,
                              '2026-10-08T14:00:00Z', execution_id='new-setup-target'), {})])
    target = journal.trades()[-1]
    journal.annotate(target.id, {'notes': 'Keep this review', 'tags': ['Patient'], 'initial_risk': 100})
    app = open_app()
    widget(app.selectbox, 'Use saved setup').set_value('VISUAL TEST ONLY').run()
    assert not app.exception
    saved = journal.annotations()[target.id]
    assert saved['setup'] == 'VISUAL TEST ONLY' and saved['notes'] == 'Keep this review'
    assert saved['tags'] == ['Patient'] and saved['initial_risk'] == 100
    assert widget(app.text_input, 'Setup / playbook').value == 'VISUAL TEST ONLY'
    assert widget(app.selectbox, 'Use saved setup').value is None


# ---------------------------------------------------------------- status logic and the background readers

NOW = dt.datetime(2026, 10, 7, 14, 0, tzinfo=dt.timezone.utc)


def beat(seconds_ago, **fields):
    return {'ts': (NOW - dt.timedelta(seconds=seconds_ago)).isoformat(), 'status': 'connected', **fields}


def test_link_state_reads_the_stored_status_and_the_heartbeat_age(tmp_path):
    there = tmp_path / 'motivewave.ndjson'
    there.write_text('')
    on, missing = {'path': str(there), 'enabled': True}, {'path': str(tmp_path / 'none.ndjson'), 'enabled': True}
    state = lambda profile, status, **more: common.link_state(profile, status, now=NOW, **more)
    assert state(None, {}).state == 'Needs Attention'
    assert state({**on, 'enabled': False}, {'status': 'synced', 'heartbeat': beat(1)}).state == 'Paused'
    assert state(missing, {}).state == 'Waiting For Study'
    assert state(on, {'status': 'waiting for study'}).state == 'Waiting For Study'
    assert state(on, {}).state == 'Waiting For Study' and 'no events' in state(on, {}).detail
    assert state(on, {'status': 'synced'}).state == 'No Heartbeat'
    assert state(on, {'status': 'synced', 'heartbeat': {'ts': 'not a time'}}).state == 'No Heartbeat'
    fresh = state(on, {'status': 'synced', 'heartbeat': beat(12, connector_version='0.1.0')})
    assert (fresh.state, fresh.tone, fresh.label) == ('Connected', 'good', 'Connected · study 0.1.0')
    assert state(on, {'status': 'synced', 'heartbeat': beat(12)}).label == 'Connected'      # an older study: no version
    assert state(on, {'status': 'synced', 'heartbeat': beat(common.STALE_AFTER)}).state == 'Connected'
    assert state(on, {'status': 'synced', 'heartbeat': beat(-5)}).state == 'Connected'      # clocks a little apart
    stale = state(on, {'status': 'synced', 'heartbeat': beat(common.STALE_AFTER + 1, connector_version='0.1.0')})
    assert stale.state == 'Stale' and stale.label == 'Stale' and '1 min ago' in stale.detail
    failed = state(on, {'status': 'needs attention', 'error': 'Line 3: Unknown event type', 'heartbeat': beat(2)})
    assert failed.state == 'Needs Attention' and failed.detail == 'Line 3: Unknown event type'
    assert state(on, {'status': 'synced', 'heartbeat': beat(1)}, worker_error='OperationalError').state == 'Needs Attention'
    assert [common.ago(s) for s in (0, 59, 60, 3599, 3600, 90000)] == ['0 s ago', '59 s ago', '1 min ago',
                                                                     '59 min ago', '1 h ago', '1 d ago']


def test_only_enabled_connections_count_as_problems_and_the_stamp_follows_fills_and_excursions(tmp_path):
    journal = Journal(tmp_path / 'trades.sqlite3')
    journal.put('profiles', 'csv', {'path': '/nowhere.csv', 'mode': 'executions', 'enabled': True})
    journal.put('profiles', 'old', {'path': '/gone.csv', 'mode': 'executions', 'enabled': False})
    for name in ('csv', 'old', DEFAULT_CONNECTION):
        journal.put('sync_status', name, {'status': 'needs attention', 'error': 'The watched file does not exist.'})
    assert common.other_problems(journal, DEFAULT_CONNECTION) == 1
    assert common.study_connection(journal) == (None, None)
    journal.put('profiles', 'mine', {'path': '/x.ndjson', 'mode': 'bridge', 'enabled': True})
    assert common.study_connection(journal)[0] == 'mine'           # a bridge under another name is the study's
    before = common.stamp(journal)
    trade = round_trip(journal)
    filled = common.stamp(journal)
    for metric in measurements(trade, [bar(0)], 'motivewave-bars:1m'):
        journal.record_metric(trade, metric)
    measured = common.stamp(journal)
    assert before == (0, 0, 0) and filled == (3, 0, 0) and measured == (3, 1, 0)
    for metric in measurements(trade, [bar(0), bar(1)], 'motivewave-bars:1m'):
        journal.record_metric(trade, metric)                       # revised in place: nothing new to draw
    assert common.stamp(journal) == measured


def test_one_reader_per_database_is_shared_by_reruns_and_sessions_and_stopped_at_the_end():
    alive = lambda: [t for t in threading.enumerate() if t.name == 'mwj-sync' and t.is_alive()]
    assert not alive()
    app = open_app()
    app.run()
    show(app, 'Connection')
    open_app('Edge')                                               # a second browser tab
    assert len(alive()) == 2
    assert set(runtime._running) == {str(paths.database('live')), str(paths.database('replay'))}
    runtime.stop_all()
    assert not alive() and not runtime._running
    app.run()                                                      # a stopped reader is replaced, not handed back dead
    assert not app.exception and len(alive()) == 2
