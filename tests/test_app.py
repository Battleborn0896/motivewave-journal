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
import theme
import runtime
from views import setup, trades
from mwjournal import Execution, Journal, PriceSample, backup, paths, tagging, units
from mwjournal.importers import apply_preview, preview_bridge
from mwjournal.metrics import excursions, measurements
from mwjournal.sync import DEFAULT_CONNECTION
from mwjournal.workspace import GROUPS
from test_capture import fill, lines, tick
from test_chart import bar, round_trip, seed_preview, seed_ticks

APP = Path(__file__).resolve().parent.parent / 'app' / 'app.py'
VIEWS = ['Trades', 'Edge', 'Import', 'Connection', 'Daily review', 'Export']
CREDIT = 'TradingView Lightweight Charts™, Copyright (c) 2025 TradingView, Inc.'
FILLS_CSV = ('Account,Instrument,Side,Quantity,Avg Price,Date,Time,Trade ID,Commission\n'
             'paper,ESZ6,Buy,1,5000,2026-10-07,10:00:23,entry,2\n'
             'paper,ESZ6,Sell,1,5002,2026-10-07,10:03:00,exit,2\n')
IMPORT_BOX = 'Fills File'


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
    """Open a view the way a person does: its page in the sidebar, then, for a page under Settings, its tile or
    the tile's link to one of its tabs on the first page of Settings; for the dashboards, the Dashboards tab of Stats;
    for the Daily Report Card, a day's header in the trade list. Two clicks at most."""
    route = common.navigation(view)
    app.segmented_control(key=common.VIEW_KEY).set_value(route[common.VIEW_KEY]).run()
    assert not app.exception
    if route[common.VIEW_KEY] == 'Settings':
        if app.session_state[common.TOOL_KEY] != setup.HOME:
            app.button(key='setup-back').click().run()        # already on Settings, on another of its pages
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


def details(app):
    """The selected trade's Details fold."""
    return next(e for e in app.main.expander if e.label == 'Details')


def open_details(app, scope='live'):
    """Click the trade's Details fold open. AppTest cannot click one, so this opens it the way the click does: its
    state, kept under one key for every trade."""
    app.session_state[f'{trades.DETAILS}-{scope}'] = True
    app.run()
    assert not app.exception and details(app).proto.expanded
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
    """The Settings page on screen."""
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


def menu_label(app, name='Filters'):
    """What the button of the menu `name` reads, with the number of filters on: 'Filters', 'Filters · 2'."""
    return next(label for label in (p.proto.popover.label for p in app.main.get('popover'))
                if label == name or label.startswith(name + ' · '))


def helped(elements, text):
    """The text is in the ? tooltip of one of the elements."""
    return any(text in (getattr(e.proto, 'help', '') or '') for e in elements)


def display_setting(app, label, value, back='Trades'):
    """Change one setting where it lives, on the first page of Settings, then go back to the page that shows it."""
    show(app, 'Settings')
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
    if view == 'Trades':                   # Replay's empty list says 'No replay trades yet'
        return (said(app.info, 'No trades yet') or said(app.info, common.NO_REPLAY_TRADES)) if empty else bool(listed(app))
    if view == 'Edge':
        return said(app.info, 'Stats need') if empty else any(m.label == 'Win Rate' for m in app.metric)
    if view == 'Import':                   # one box for any file, and no prices import
        return ([u.label for u in app.file_uploader] == [IMPORT_BOX]
                and any(e.label == 'Add One Fill by Hand' for e in app.expander))
    if view == 'Connection':
        return (any(e.label == 'Troubleshooting' for e in app.expander) and len(states(app.main)) == 1
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


def test_chart_library_notice_and_independence_are_on_settings_and_the_data_folder_under_troubleshooting():
    app = open_app('Settings')
    notice = [c.value for c in app.main.caption if CREDIT in c.value]
    assert len(notice) == 1 and '(https://www.tradingview.com/)' in notice[0]
    assert said(app.main.caption, 'not affiliated with or endorsed by MotiveWave')
    assert not said(app.main.caption, str(paths.data_home()))
    app = open_app('Connection')
    assert said(widget(app.expander, 'Troubleshooting').caption, str(paths.data_home()))


@pytest.mark.parametrize('view', VIEWS)
def test_replay_environment_views_render_separately(view):
    replay = Journal(dataset='replay')
    apply_preview(replay, preview_bridge(lines([fill('a'), tick(5001),
                           fill('b', 'sell', 5002, ts='2022-01-03T14:32:00Z')]), dataset='replay'))
    app = open_app(view, 'Replay')
    assert not said(app.info, 'Replay journal')                     # no banner: the sidebar says which journal
    assert app.segmented_control(key=common.VIEW_KEY).value == common.navigation(view)[common.VIEW_KEY]
    if view == 'Trades':
        assert listed(app, 'replay') == [replay.trades()[0].id] and chosen(app, 'replay') == replay.trades()[0].id
    if view == 'Connection':
        assert any(c.value == str(paths.bridge_file('replay')) for c in app.main.code)
    assert not Journal().executions()
    app.radio(key='journal-environment').set_value('Live').run()
    assert not app.exception
    assert not listed(app, 'replay') and not listed(app, 'live')


def test_a_replay_trade_without_ticks_says_the_study_fills_them_in_after_it_closes():
    replay = Journal(dataset='replay')
    apply_preview(replay, preview_bridge(lines([fill('a'), fill('b', 'sell', 5002, ts='2022-01-03T14:32:00Z')]),
                                         dataset='replay'))
    app = open_app('Trades', 'Replay')
    assert chosen(app, 'replay') == replay.trades()[0].id
    assert [c.value for c in app.main.caption].count(common.REPLAY_TICKS_LATER) == 1
    assert not said(app.main.caption, 'MAE / MFE need recorded ticks')
    open_details(app, 'replay')                     # said once, under the numbers: Details has no path to draw yet
    assert [c.value for c in app.main.caption].count(common.REPLAY_TICKS_LATER) == 1
    assert not said(app.info, common.REPLAY_TICKS_LATER)
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
    assert said(app.main.caption, "From MotiveWave's tick history")            # not the ticks recorded live
    assert not said(app.main.caption, 'Observed ticks') and 'Measured from' not in widget(app.metric, 'MAE').help
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
    press(app, 'Open Settings')                     # the one step that is missing, not the diagnostics
    assert app.segmented_control(key=common.VIEW_KEY).value == 'Settings'
    assert setup_page(app) == setup.HOME and said(app.markdown, 'then add the study to your chart')
    assert not said(app.markdown, 'Add **Journal → MotiveWave Journal**')


def test_a_journal_from_a_newer_version_is_refused_with_a_message_not_a_crash(home):
    db = sqlite3.connect(home / 'trades.sqlite3')
    db.execute('PRAGMA user_version = 99')
    db.commit()
    db.close()
    app = AppTest.from_file(str(APP), default_timeout=30).run()
    assert not app.exception and said(app.error, 'newer version of MotiveWave Journal')
    assert app.segmented_control(key=common.VIEW_KEY).options == [common.NAV_LABELS.get(v, v) for v in common.PRIMARY_VIEWS]
    # Replay keeps its tags and settings in the Live journal, so it waits for the same update, and says so.
    app.radio(key='journal-environment').set_value('Replay').run()
    assert not app.exception and said(app.error, 'newer version of MotiveWave Journal')


# ---------------------------------------------------------------- trades and reviews

def test_empty_journal_then_a_review_is_saved():
    app = open_app()
    assert said(app.info, 'No trades yet')
    journal = Journal()
    journal.ingest([(Execution('test', 'paper', 'ESZ6', 'buy', 1, 5000, '2026-10-07T14:00:00Z',
                              execution_id='a'), {})])
    app.run()
    open_review(app)
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
    assert annotation['tags'] == ['A+', 'Steady']
    assert annotation['notes'] == 'Waited for the retest.' and annotation.get('initial_risk') is None
    assert widget(app.text_area, 'Review notes').value == 'Waited for the retest.'
    # The review is the note and the Stop: a setup is a Setup tag, and how well the plan was traded its Rating.
    assert [w.label for w in app.text_input if w.key and w.key.startswith(('notes-', 'stop-', 'setup-'))] == ['Stop']
    assert not any(w.label in ('Use saved setup', 'Followed my plan') for w in app.selectbox)


def test_full_recorded_path_and_after_exit_render_without_claiming_missing_data(home):
    journal = seed_ticks(seed_preview(home))
    app = open_details(open_app())
    widget(app.checkbox, 'Show/export full recorded trade path').set_value(True).run()
    assert not app.exception
    assert any(b.label == 'Download Full Path CSV' for b in app.download_button)
    # After Exit is one sentence with nothing to pick, under Details: two prints after the exit, not all of its
    # 15 minutes.
    assert said(details(app).markdown, 'Not every price was recorded')
    assert said(app.markdown, 'Not every price was recorded in the 15 minutes after your exit. Keep the study on a '
                              'chart of this contract while you trade.')
    assert not any(s.label in ('Look after exit', 'Runner stop rule') for s in app.selectbox)
    assert not any(m.label.startswith(('Best extra', 'Extra after')) for m in app.metric)
    show(app, 'Edge')
    assert not any(s.label == 'Compare by' for s in app.selectbox)    # drawn only when its tab is open
    open_tab(app, 'After Exit')
    assert not app.exception and not said(app.info, 'None of these trades')   # not run until asked
    assert said(app.caption, 'The 15 minutes after each exit, from recorded ticks, held until your stop, or breakeven')
    assert said(app.caption, 'An exit at or past the stop counts as 0, as there was nothing left to hold.')
    assert not any(s.label.startswith('Runner comparison') for s in app.selectbox)
    assert not any(c.label.startswith(('Only winning', 'Only exits near')) for c in app.checkbox)
    press(app, 'Compare')
    assert said(app.info, 'None of these trades has all of the 15 minutes after its exit recorded')
    assert said(app.caption, '1 trade · 1 left out · a trade counts under each of its tags')
    assert widget(app.selectbox, 'Compare by').options == list(GROUPS)
    assert not any(b.label in ('Analyze recorded post-exit prices', 'Calculate post-exit tag analysis')
                   for b in app.checkbox)
    # An exit at its stop left nothing to hold: it counts as 0 further your way, and its prices are never read.
    journal.update_annotation(journal.trades()[0].id, {'planned_stop': 5003})
    app.run()
    widget(app.selectbox, 'Compare by').set_value('Direction')
    press(app, 'Compare')
    assert said(app.caption, '1 trade · 0 left out') and not said(app.info, 'None of these trades')
    table = next(d.value for d in app.dataframe if 'Direction' in d.value.columns)
    assert table.iloc[0][['Direction', 'Trades', 'Average further your way (USD)']].tolist() == ['long', 1, 0]


def test_measured_trade_risk_renders_from_the_traded_price_alone():
    journal = Journal()
    journal.ingest([(Execution('test', 'paper', 'ESZ6', 'buy', 1, 5000, '2026-10-07T14:00:00Z', execution_id='a'), {}),
                    (Execution('test', 'paper', 'ESZ6', 'sell', 1, 5002, '2026-10-07T14:03:00Z', execution_id='b'), {})])
    trade = journal.trades()[0]
    samples = [PriceSample('ESZ6', '2026-10-07T14:00:30Z', 'test-prices', 4999, 4999, bid=4998.75, ask=4999.25)]
    journal.annotate(trade.id, {'planned_stop': 4999})
    for metric in measurements(trade, samples, 'test-prices'):
        journal.record_metric(trade, metric)
    # One measurement, from the traded price: the bid of 4998.75 plays no part, and there is nothing to choose.
    assert [m['basis'] for m in journal.metric_options(trade)] == ['last']
    app = open_app()
    # +$100 over the $50 its stop at 4999 risked, in the trade's numbers.
    assert widget(app.metric, 'R').value == '+2.00R' and widget(app.metric, 'MAE').value == '$50.00'
    assert not any(s.label == 'Measurement' for s in app.selectbox)
    open_tab(show(app, 'Edge'), 'Compare')
    # The stars have a table under Tags, and a setup is a Setup tag: neither is a Compare by choice of its own.
    assert widget(app.selectbox, 'Compare by').options == [
        'Tags', 'Playbooks', 'Market', 'Exact contract', 'Direction', 'Entry hour', 'Weekday']
    widget(app.selectbox, 'Compare by').set_value('Direction').run()
    assert not app.exception


def test_chart_renders_and_planned_protection_persists_with_tags(home):
    journal = seed_preview(home)
    app = open_app()
    assert not any(x.label == 'Chart price source' for x in app.selectbox)    # MotiveWave's candles, always
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
    assert not app.tabs and not details(app).proto.expanded   # one page: the rest waits under a closed Details


def test_a_closed_trade_is_one_page_with_its_fills_once_under_a_closed_details(home):
    journal = seed_ticks(seed_preview(home))
    app = open_app()
    assert not app.tabs
    # The numbers: +$242 over the $200 its stop at 4998 risked on the 2 contracts held.
    assert [(m.label, m.value) for m in app.main.metric] == [
        ('Net P&L', '+$242.00'), ('MAE', '$100.00'), ('MFE', '$250.00'), ('R', '+1.21R'), ('Contracts', '2')]
    assert any('journal-candles' in c.proto.id for c in app.main.get('component_instance'))       # the chart
    assert set(widget(app.pills, 'Other tags').value) == {'PAPER FIXTURE', 'breakout'}              # beside it
    assert any(p.label == 'Rating' for p in app.pills)
    assert widget(app.text_area, 'Review notes').value == 'Synthetic fixture, not an actual account trade.'
    assert widget(app.text_input, 'Stop').value == '4998'
    # Then Details, closed: nothing in it is drawn until it is opened, and then the fills are listed once.
    assert [e.label for e in app.main.expander][-1] == 'Details' and not details(app).proto.expanded
    assert not any('Role' in d.value.columns for d in app.dataframe)
    open_details(app)
    fills = [d.value for d in app.dataframe if 'Role' in d.value.columns]
    assert len(fills) == 1 and list(fills[0]['Contracts']) == [2, 1, 1]
    assert said(details(app).markdown, 'After Exit')
    # The contract's dollars per point is a line, changed behind Change; its tick size is set in one place only.
    assert said(details(app).caption, 'ESZ6: \\$50 per point')
    assert widget(details(app).number_input, 'Dollars per point').value == 50.            # correctable here
    assert any(b.label == 'Save and Recalculate' for b in details(app).button)
    assert not any(n.label.startswith('Tick size') for n in app.number_input)


def test_r_shows_a_dash_and_why_without_a_stop(home):
    journal = Journal()
    round_trip(journal)
    app = open_app()
    r = widget(app.metric, 'R')
    assert r.value == '—' and r.help == 'No stop order 10 seconds after entry: type your stop to get R.'
    widget(app.text_input, 'Stop').set_value('4998').run()
    r = widget(app.metric, 'R')
    assert r.value == '+1.21R' and r.help == 'Net P&L divided by the risk to your stop, $200.00.'


def test_a_path_short_of_mae_because_of_a_fill_says_nothing_about_a_moment(home):
    """One contract stopped out at 4997.75, below every recorded print: MAE counts that fill, 2.25 pts, which the
    path does not draw, so the path stops at 4998.25. That is no moment of more contracts to explain, in any unit."""
    journal = Journal()
    journal.ingest([(Execution('motivewave', 'paper', 'ESZ6', side, 1, price, ts, execution_id=id), {})
                    for id, side, price, ts in (('in', 'buy', 5000, '2026-10-07T14:00:00Z'),
                                                ('out', 'sell', 4997.75, '2026-10-07T14:02:00Z'))])
    trade = journal.trades()[0]
    prints = [PriceSample('ESZ6', f'2026-10-07T14:{at}Z', 'motivewave-ticks', price, price)
              for at, price in (('00:30', 4999.5), ('01:00', 5001), ('01:30', 4998.25))]
    journal.ingest_prices(prints)
    journal.record_metric(trade, excursions(trade, prints, 'motivewave-ticks'))
    app = open_app()
    for unit, mae, low in (('points', '2.25 pts', -1.75), ('currency', '$112.50', -87.5)):
        display_setting(app, 'MAE / MFE Units', unit)
        open_details(app)
        path = next(json.loads(x.proto.spec) for x in app.get('plotly_chart') if 'Move from your average entry' in x.proto.spec)
        assert widget(app.metric, 'MAE').value == mae and min(path['data'][1]['y']) == low
        assert not said(details(app).caption, 'counts the moment')


def test_details_stays_open_while_an_open_trade_redraws(home):
    """A browser sends the Details fold's state with every run, under the id it was drawn with (conftest sends it as
    a browser does): the fold stays open while that id is the same, through an open trade's new prices, the redraw a
    new fill brings, and from trade to trade."""
    journal = seed_preview(home)
    journal.ingest([(Execution('motivewave', 'paper', 'ESZ6', 'buy', 1, 5010, '2026-10-08T14:00:00Z',
                               execution_id='open-in', commission=2), {})])
    live = next(t for t in journal.trades() if t.status == 'open')
    app = open_app()
    assert chosen(app) == live.id and not details(app).proto.expanded
    open_details(app)
    fold = details(app).proto.id
    assert fold.endswith(trades.DETAILS + '-live')
    for price in (5011, 5012):                       # prices recorded while it is open: the page drawn again
        journal.ingest_prices([PriceSample('ESZ6', f'2026-10-08T14:0{price - 5010}:00Z', 'motivewave-ticks', price,
                                           price)])
        journal.record_metric(live, excursions(live, journal.prices('ESZ6', 'motivewave-ticks'), 'motivewave-ticks'))
        app.run()
        assert not app.exception and details(app).proto.expanded and details(app).proto.id == fold
    # A fill the watcher adds redraws the whole page: still open, with the new fill listed.
    journal.ingest([(Execution('motivewave', 'paper', 'ESZ6', 'buy', 1, 5012, '2026-10-08T14:03:00Z',
                               execution_id='open-add', commission=2), {})])
    app.run()
    assert details(app).proto.expanded and details(app).proto.id == fold
    assert len(next(d.value for d in details(app).dataframe if 'Role' in d.value.columns)) == 2
    opened = chosen(app)
    press(app, 'Next →')                                                   # and on the next trade
    assert chosen(app) != opened and details(app).proto.expanded and details(app).proto.id == fold


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
    assert widget(app.text_area, 'Review notes').value == 'Synthetic fixture, not an actual account trade.'
    pick(app, trades[-1].id)
    assert widget(app.text_area, 'Review notes').value == ''


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
    assert widget(app.selectbox, 'Compare by').options[2:4] == ['Market', 'Exact contract']


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
    # Clicks that change nothing reuse the rows: a rerun, the open trade's row, its Details, Stats and back.
    assert clicks(app.run, lambda: pick(app, trade.id), lambda: open_details(app),
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
    deeper = [dataclasses.replace(s, high=s.high - 3, low=s.low - 3) for s in journal.prices(trade.symbol, 'motivewave-ticks')]
    journal.record_metric(trade, excursions(trade, deeper, 'motivewave-ticks'))
    assert shown(trade.id)['mae'] != common.best_display_metric(journal, trade)['mae']
    assert clicks(app.run) == 1 and shown(trade.id)['mae'] == common.best_display_metric(journal, trade)['mae']
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
    open_tab(app, 'After Exit')
    assert widget(app.selectbox, 'Compare by').value == 'Tags'
    # A form sends its choices with the click, as a browser does: a run between them drops the choice (Streamlit 1.66).
    widget(app.selectbox, 'Compare by').set_value('Weekday')
    press(app, 'Compare')
    open_tab(app, 'Overview')
    open_tab(app, 'Compare')
    assert widget(app.selectbox, 'Compare by').value == 'Playbooks'
    open_tab(app, 'After Exit')
    assert widget(app.selectbox, 'Compare by').value == 'Weekday'


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
    assert not app.tabs and [e.label for e in app.main.expander] == ['More Filters', 'More Tagging Tools', 'Details']
    # The Session menu, then the list's own menus, all in the row above the list.
    assert [b.proto.popover.label for b in app.main.get('popover')][:3] == ['Session', 'Filters', 'Merge Trades']
    assert not any(b.proto.popover.label in ('Chart settings', 'More', 'Trade filters', 'Options')
                   for b in app.get('popover'))
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
    assert said(app.warning, 'XYZ has no dollars per point yet')
    assert not details(app).proto.expanded                      # asked at the top, where it stops the P&L
    widget(app.number_input, 'Dollars per point').set_value(10.)
    press(app, 'Save and Recalculate')
    assert journal.trades()[0].net == 30
    assert widget(app.metric, 'Net P&L').value == '+$30.00' and not said(app.warning, 'dollars per point')
    # A wrong value is changed under Details, with the same form, and the trade follows it.
    open_details(app)
    assert widget(details(app).number_input, 'Dollars per point').value == 10.
    widget(app.number_input, 'Dollars per point').set_value(5.)
    press(app, 'Save and Recalculate')
    assert journal.trades()[0].net == 15 and widget(app.metric, 'Net P&L').value == '+$15.00'


# ---------------------------------------------------------------- edge

def test_edge_separates_gross_results_fees_and_planned_risk(home):
    seed_preview(home)
    app = open_app('Edge')
    assert widget(app.metric, 'Gross P&L').value == '+$250.00'
    assert widget(app.metric, 'Fees').value == '$8.00'
    assert widget(app.metric, 'Fees Per Trade').value == '$8.00'
    assert helped(app.metric, 'recorded fees average 0.040R a trade')
    assert helped(app.metric, 'Recorded commissions and fees, gross minus net.')

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
    assert helped(app.markdown, units.description('currency')) and not helped(app.markdown, 'Giveback')
    assert not helped(app.markdown, 'Colour shows how the move was measured')
    scatter, = (json.loads(x.proto.spec) for x in app.get('plotly_chart'))
    assert len(scatter['data']) == 1 and not scatter['data'][0].get('showlegend')    # one colour, no legend
    # In points a point of NQ is not a point of ES: the page says so once both markets are measured.
    journal.set_excursion_unit('points')
    app.run()
    assert said(app.caption, '1 of 2 closed trades measured') and not said(app.caption, 'differ by market')
    nq = next(t for t in journal.trades() if t.symbol == 'NQZ6')
    prints = [PriceSample('NQZ6', '2026-10-08T14:11:00Z', 'motivewave-ticks', 19995, 19995)]
    journal.ingest_prices(prints)
    for metric in measurements(nq, prints, 'motivewave-ticks'):
        journal.record_metric(nq, metric)
    app.run()
    assert said(app.caption, '2 of 2 closed trades measured · points and ticks differ by market: filter to one')


# ---------------------------------------------------------------- import

def mapping_open(app):
    """Whether Column Mapping is drawn open."""
    return next(e for e in app.expander if e.label == 'Column Mapping').proto.expanded


def test_csv_fills_are_mapped_previewed_and_imported_once():
    app = open_app('Import')
    widget(app.file_uploader, IMPORT_BOX).upload('fills.csv', FILLS_CSV.encode(), 'text/csv').run()
    assert not app.exception
    # Every column was found by its name: the mapping stays closed, its boxes named in plain words.
    assert not mapping_open(app) and next(s for s in app.selectbox if s.label == 'Price').value == 'Avg Price'
    assert next(s for s in app.selectbox if s.label == 'Contracts').value == 'Quantity'
    assert not any(s.label in ('Import format', 'price', 'qty') for s in app.selectbox)
    assert not any(t.label.startswith('Source identity') for t in app.text_input)
    assert said(app.markdown, '2 records ready')
    press(app, 'Import Into My Journal')
    assert said(app.success, 'Imported 2 new fills.')
    journal = Journal()
    trade = journal.trades()[0]
    assert trade.closed and trade.net == 96 and trade.provider == 'csv'
    press(app, 'Import Into My Journal')
    assert said(app.success, 'Skipped 2 existing fills') and len(journal.executions()) == 2
    show(app, 'Trades')
    assert chosen(app) == trade.id and listed(app) == [trade.id]


def test_csv_times_are_read_in_the_zone_chosen_for_the_file_which_opens_on_the_time_zone():
    Journal().save_preferences(display_timezone='America/Chicago')
    app = open_app('Import')
    widget(app.file_uploader, IMPORT_BOX).upload('fills.csv', FILLS_CSV.encode(), 'text/csv').run()
    zone = widget(app.selectbox, 'Time Zone of the File')
    assert zone.value == 'America/Chicago' and 'America/New_York' in zone.options
    zone.set_value('America/New_York').run()
    press(app, 'Import Into My Journal')
    assert Journal().executions()[0].ts == '2026-10-07T14:00:23+00:00'          # 10:00:23 in New York


def test_csv_with_a_missing_required_column_opens_the_mapping_in_plain_words():
    app = open_app('Import')
    widget(app.file_uploader, IMPORT_BOX).upload('fills.csv', b'Instrument,Side\nESZ6,Buy\n', 'text/csv').run()
    assert not app.exception and said(app.error, 'Choose a column for Time, Contracts, Price under Column Mapping')
    assert mapping_open(app)
    assert sorted(s.label for s in app.selectbox if s.key and s.key.startswith('map-')) == sorted([
        'Time', 'Date', 'Contract', 'Side', 'Contracts', 'Price', 'Fees', 'Account', 'Fill ID', 'Order ID',
        'Dollars per point', 'Currency'])                                   # in three columns, row by row
    assert not Journal().executions()


def test_a_bridge_file_and_a_fills_csv_dropped_in_the_same_box_are_each_recognised():
    app = open_app('Import')
    events = lines([fill('a', mode='live', ts='2026-10-07T14:00:00Z'),
                    fill('b', 'sell', 5002, mode='live', ts='2026-10-07T14:02:00Z')])
    widget(app.file_uploader, IMPORT_BOX).upload('events.txt', events.encode(), 'text/plain').run()
    assert not app.exception and said(app.markdown, '2 records ready')
    assert not any(e.label == 'Column Mapping' for e in app.expander)          # nothing to map in a bridge file
    press(app, 'Import Into My Journal')
    assert said(app.success, 'Imported 2 new fills.')
    assert {e.provider for e in Journal().executions()} == {'motivewave-native:workspace:live'}
    widget(app.file_uploader, IMPORT_BOX).upload('fills.csv', FILLS_CSV.encode(), 'text/csv').run()
    assert not app.exception and any(e.label == 'Column Mapping' for e in app.expander)
    press(app, 'Import Into My Journal')
    assert said(app.success, 'Imported 2 new fills.')
    assert sorted(e.provider for e in Journal().executions()) == ['csv', 'csv', 'motivewave-native:workspace:live',
                                                                  'motivewave-native:workspace:live']


def test_a_replay_bridge_file_is_refused_in_live():
    app = open_app('Import')
    widget(app.file_uploader, IMPORT_BOX).upload('replay.ndjson', lines([fill('a')]).encode(), 'text/plain').run()
    assert not app.exception and said(app.error, 'other trading environment')
    assert widget(app.button, 'Import Into My Journal').disabled
    assert not Journal().executions()


def record_by_hand(app, *, day, time, side='buy', price=5000.25, account='SIM', contract='ESZ6'):
    widget(app.text_input, 'Account').set_value(account)
    widget(app.text_input, 'Contract').set_value(contract)
    widget(app.selectbox, 'Side').set_value(side)
    widget(app.number_input, 'Price').set_value(price)
    widget(app.date_input, 'Date').set_value(day)
    widget(app.time_input, 'Time').set_value(time)
    return press(app, 'Record Fill')


def test_a_fill_typed_by_hand_is_read_in_the_settings_time_zone():
    journal = Journal()
    journal.save_preferences(display_timezone='America/Los_Angeles')
    app = open_app('Import')
    assert widget(app.time_input, 'Time').label == 'Time · America/Los_Angeles'
    assert widget(app.text_input, 'Account').value == '' and widget(app.text_input, 'Contract').value == ''
    assert not any(t.label.startswith('Fill timestamp') for t in app.text_input)
    assert not any(n.label == 'Quantity' for n in app.number_input) and widget(app.number_input, 'Contracts')
    record_by_hand(app, day=dt.date(2026, 7, 15), time=dt.time(9, 30), account='')
    assert said(app.error, 'Enter the account and the contract.') and not journal.executions()
    record_by_hand(app, day=dt.date(2026, 7, 15), time=dt.time(9, 30))
    assert said(app.success, 'Recorded 1 new fill')
    recorded, = journal.executions()
    assert recorded.ts == '2026-07-15T16:30:00+00:00'                           # 9:30 in Los Angeles in summer
    assert (recorded.provider, recorded.account, recorded.symbol, recorded.side, recorded.qty, recorded.price) == (
        'manual', 'SIM', 'ESZ6', 'buy', 1, 5000.25)


def test_a_fill_typed_by_hand_is_measured_against_stored_prices():
    journal = Journal()
    journal.save_preferences(display_timezone='America/New_York')
    journal.ingest_prices([bar(0), bar(1), bar(2)])                # what the study recorded for that contract
    app = open_app('Import')
    record_by_hand(app, day=dt.date(2026, 10, 7), time=dt.time(10, 0))
    record_by_hand(app, day=dt.date(2026, 10, 7), time=dt.time(10, 2), side='sell')
    trade = journal.trades()[0]
    assert (trade.opened, trade.closed) == ('2026-10-07T14:00:00+00:00', '2026-10-07T14:02:00+00:00')
    assert [m['precision'] for m in journal.metric_options(trade)] == ['estimated-bars']


# ---------------------------------------------------------------- daily review and export

def test_daily_review_opens_on_the_latest_trading_day_and_is_saved(home):
    Journal().tag_library()                    # a new journal's first read offers the starters, Day among them
    journal = seed_preview(home)
    journal.save_preferences(display_timezone='America/New_York')      # the trade list's days, and the card's
    app = open_app('Daily review')
    assert widget(app.date_input, 'Trading day').value == dt.date(2026, 10, 7)
    assert widget(app.metric, 'Trades').value == '1' and widget(app.metric, 'Net P&L').value == '+$242.00'
    # The Day tags replace the old checkboxes, and the day's Rating its slider: no stars until one is clicked.
    assert widget(app.pills, 'Day').options == ['Had a Plan', 'Kept Risk Limits', 'Respected My Stop']
    assert widget(app.pills, 'Rating').value is None and not app.slider
    assert not any(box.label.startswith('I ') for box in app.checkbox)
    widget(app.pills, 'Day').set_value(['Had a Plan']).run()                    # each box saves when it changes
    assert journal.get('sessions')['2026-10-07']['day_tag_ids'] == [tagging.identity('Had a Plan')]
    widget(app.text_area, 'What I learned').set_value('Sized correctly.').run()
    saved = journal.get('sessions')['2026-10-07']
    assert saved['notes'] == 'Sized correctly.' and saved['date'] == '2026-10-07'
    assert 'rating' not in saved                                               # untouched: nothing is made up
    widget(app.pills, 'Rating').set_value(4).run()
    assert journal.get('sessions')['2026-10-07']['rating'] == 4
    app.run()
    assert widget(app.text_area, 'What I learned').value == 'Sized correctly.'
    assert widget(app.pills, 'Rating').value == 4 and widget(app.pills, 'Day').value == ['Had a Plan']
    assert not any(b.label == 'Save Daily Review' for b in app.button)


def test_the_backup_holds_both_environments_and_the_csv_this_one(home):
    from views import export
    journal = seed_preview(home)
    table = export.trades_csv(common.rows_for(journal), 'UTC')
    assert table.splitlines()[0].startswith('id,account,symbol') and len(table.splitlines()) == 2
    assert 'setup' not in table.splitlines()[0].split(',') and 'PAPER FIXTURE' in table.splitlines()[1]
    zipped = export.backup_zip()                                   # built only when Back Up Journal is pressed
    assert backup.read(io.BytesIO(zipped))['trades'] == {'live': 1, 'replay': 0}
    app = open_app('Export', 'Replay')
    assert backup_controls(app) and app.download_button[0].proto.type == 'primary'


# ---------------------------------------------------------------- the study connection

def test_nothing_is_typed_the_app_watches_the_default_bridge_and_waits_for_the_study():
    app = open_app('Connection')
    assert states(app.main) == ['Not recording'] and states(app.sidebar) == ['Not recording']
    for dataset in paths.DATASETS:
        assert Journal(dataset=dataset).get('profiles') == {DEFAULT_CONNECTION: {
            'path': str(paths.bridge_file(dataset)), 'mode': 'bridge', 'enabled': True}}
    assert not paths.bridge_file().exists()
    assert any(c.value == str(paths.bridge_file('live')) for c in app.main.code)
    show(app, 'Settings')                           # the step that is missing is on the first page of Settings
    assert states(app.main) == ['Not recording'] and said(app.main.markdown, 'then add the study to your chart')
    assert any(b.label == 'Install MotiveWave Study' for b in app.button)
    assert not any(str(paths.bridge_file('live')) in m.value for m in [*app.main.markdown, *app.main.code])


def shape(app):
    return [(len(block.markdown), len(block.caption), len(block.warning), len(block.metric), len(block.code))
            for block in (app.sidebar, app.main)]


def test_connection_turns_to_recording_once_the_study_writes_a_fresh_heartbeat():
    app = open_app('Connection')
    assert states(app.main) == ['Not recording']
    waiting = shape(app)
    study_writes([heartbeat()])
    wait_until(lambda: status_of().get('heartbeat'))
    app.run()
    assert not app.exception
    assert states(app.main) == ['Recording'] and states(app.sidebar) == ['Recording']
    assert said(app.main.markdown, f'Recording · study {paths.STUDY_VERSION}')
    assert not said(app.sidebar.markdown, 'study ' + paths.STUDY_VERSION)   # too wide for the sidebar badge; Settings shows it
    assert said(app.main.markdown, 'The study says: Read-only capture · workspace paper')
    assert not said(app.sidebar.markdown, 'has not written anything yet')
    connected = shape(app)
    show(app, 'Settings')                           # one green line and no step
    assert states(app.main) == ['Recording'] and said(app.main.markdown, f'Recording · study {paths.STUDY_VERSION}')
    assert not any(b.label == 'Install MotiveWave Study' for b in app.button)
    assert not any(helped(app.main.markdown, explained) for _, explained in setup.STEPS.values())
    show(app, 'Connection')
    assert shape(app) == connected
    # What the study could not link or read is said there too, beside Recording, and so are the events of a
    # kind this app does not read, which are skipped and counted.
    warned = {**heartbeat(), 'warnings': '1 working stop order not linked to a position: no position on that '
                                         'account and contract (orders: 3 read, 1 stop, 0 new, 0 without instrument, '
                                         '0 without state, 0 with a position, 0 on the closing side, 0 linked)'}
    study_writes([{'version': 1, 'type': 'position', 'mode': 'live', 'provider': 'motivewave-native:paper:live'}, warned])
    wait_until(lambda: status_of().get('heartbeat', {}).get('warnings'))
    app.run()
    assert states(app.main) == ['Recording'] and shape(app) == connected
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
    assert states(app.main) == ['Needs attention'] and said(app.main.markdown, '1 damaged lines')
    assert shape(app) == waiting
    show(app, 'Settings')                           # one line saying what to do, without the raw error
    assert states(app.main) == ['Needs attention'] and said(app.main.markdown, 'Open Data → Connection')
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
    wait_until(lambda: Journal().trades() and Journal().metric_options(Journal().trades()[0]))
    wait_until(lambda: status_of('replay').get('heartbeat'))         # read while Live is on screen
    app.run()
    open_review(app)
    assert not app.exception
    assert widget(app.metric, 'Net P&L').value == '+$96.00'
    assert widget(app.metric, 'MAE').value == '$50.00'      # a magnitude: no sign
    assert len(Journal().executions()) == 2 and len(Journal(dataset='replay').executions()) == 1
    app.radio(key='journal-environment').set_value('Replay').run()
    assert not app.exception and states(app.sidebar) == ['Recording']
    open_review(app)
    # An open position: bought at 5000 with a $2 fee, and the replay's last print is 5001, one ES point up.
    result = widget(app.metric, 'Open P&L')
    assert result.value == '+$48.00' and 'closed at 5,001,' in result.help and 'Realised so far: -$2.00' in result.help
    assert 'the 1 contract still held' in result.help


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
    widget(app.pills, 'Rating').set_value(5).run()
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


def test_connection_says_not_recording_when_the_study_has_gone_quiet():
    study_writes([heartbeat(age=600)])
    app = open_app('Connection')
    wait_until(lambda: status_of().get('heartbeat'))
    app.run()
    assert states(app.main) == ['Not recording'] and states(app.sidebar) == ['Not recording']
    assert said(app.main.markdown, 'last heard from 10 min ago')
    assert f'color:{theme.MUTED}' in next(m.value for m in app.sidebar.markdown if 'data-state=' in m.value)  # grey


def test_connection_needs_attention_shows_the_error_the_study_reported():
    study_writes([heartbeat('error', detail='Unsupported MotiveWave version')])
    app = open_app('Connection')
    wait_until(lambda: status_of().get('error'))
    app.run()
    assert states(app.main) == ['Needs attention'] and states(app.sidebar) == ['Needs attention']
    assert said(app.main.markdown, 'Unsupported MotiveWave version')
    assert not said(app.sidebar.markdown, 'Unsupported MotiveWave version')    # the badge is one word


def test_reading_trades_is_always_on_and_a_connection_switched_off_before_is_switched_back_on():
    """Earlier versions had a switch that stopped the app reading the study's file. It is gone: at the next start
    the connection is read again, from where reading stopped."""
    for dataset in paths.DATASETS:
        Journal(dataset=dataset).put('profiles', DEFAULT_CONNECTION, {'path': str(paths.bridge_file(dataset)),
                                                                     'mode': 'bridge', 'enabled': False})
    study_writes([heartbeat()])
    app = open_app('Connection')
    assert not any('Read Trades' in t.label for t in app.toggle) and not said(app.main.markdown, '>Advanced<')
    for dataset in paths.DATASETS:
        assert Journal(dataset=dataset).get('profiles')[DEFAULT_CONNECTION]['enabled'] is True
    wait_until(lambda: status_of().get('heartbeat'))
    app.run()
    assert not app.exception and states(app.main) == ['Recording']


def test_connection_is_the_status_and_the_install_with_the_details_under_a_closed_troubleshooting():
    Journal().ingest_gaps([{'type': 'gap', 'provider': 'motivewave-ticks', 'ts': '2026-10-07T14:00:00Z',
                            'dropped': 3, 'symbol': 'ESZ6'}])
    app = open_app('Connection')
    assert [m.label for m in app.main.metric] == ['Last Heard From Study', 'Fills in This Journal']
    trouble = next(e for e in app.main.expander if e.label == 'Troubleshooting')
    assert not trouble.proto.expanded
    assert [c.value for c in trouble.code] == [str(paths.bridge_file())]          # the file being read
    assert said(trouble.caption, f'Journal database: {paths.database()}')
    assert said(trouble.markdown, 'No other messages from the study.')
    assert any('Ticks Dropped' in d.value.columns for d in trouble.dataframe)
    # Nothing to type or choose: no other bridge file, no watched CSVs, no multipliers (a trade asks for its own).
    assert not app.main.text_input and not app.main.selectbox and not app.main.number_input
    assert not said(app.main.markdown, 'Other Watched Files') and not said(app.main.markdown, 'Instrument Multiplier')


def test_excursion_unit_picker_updates_detail_list_plot_and_persists_without_rewriting_data(home):
    journal = seed_ticks(seed_preview(home))
    trade = journal.trades()[0]
    before = journal.metric_options(trade)
    reviews = journal.annotations()
    app = open_app('Settings')
    assert widget(app.selectbox, 'MAE / MFE Units').options == [
        'Dollars / account currency', 'Points', 'Ticks', 'R · risk to your stop']
    assert widget(app.selectbox, 'MAE / MFE Units').help == ('Changes how MAE / MFE are shown in Live and Replay, '
                                                             'and the unit their filter limits are in.')
    # The most in your favour was with both contracts held, 2.5 points up: $250, over $200 at risk to 4998. Every
    # unit is that one moment, never the 3.5 points the one contract left saw later.
    display_setting(app, 'MAE / MFE Units', 'points')
    assert widget(app.metric, 'MFE').value == '2.50 pts'
    open_details(app)
    path = next(json.loads(x.proto.spec) for x in app.get('plotly_chart') if 'Move from your average entry' in x.proto.spec)
    assert path['layout']['yaxis']['title']['text'] == 'Move from your average entry (pts)'
    # The path reaches the runner's 3.5 points up; the MFE shown is the moment with both contracts, and the chart says
    # so. MAE is the same moment in the path and the numbers, so it says nothing.
    assert max(path['data'][0]['y']) == 3.5
    assert [c.value for c in details(app).caption if 'counts the moment' in c.value] == [
        'MFE counts the moment the most money was in your favor: 2.50 pts with 2 contracts.']
    assert common.rows_for(journal)[0]['display_mfe'] == pytest.approx(2.5)
    assert widget(app.metric, 'MAE').help == units.description('points')    # the one definition, on hover
    open_review(app)
    display_setting(app, 'MAE / MFE Units', 'ticks')
    assert widget(app.metric, 'MFE').value == '10.00 ticks'
    display_setting(app, 'MAE / MFE Units', 'r')
    assert widget(app.metric, 'MFE').value == '1.25R'
    display_setting(app, 'MAE / MFE Units', 'currency')
    assert widget(app.metric, 'MFE').value == '$250.00'
    open_details(app)
    assert not said(details(app).caption, 'counts the moment')         # in dollars the path and MFE agree
    path = next(json.loads(x.proto.spec) for x in app.get('plotly_chart') if 'Move from your average entry' in x.proto.spec)
    assert path['layout']['yaxis']['title']['text'] == 'Move from your average entry (USD)'   # one title in every unit
    assert path['layout']['yaxis']['tickformat'].startswith('$')        # its axis reads like every other amount
    assert journal.metric_options(trade) == before
    assert journal.annotations() == reviews
    display_setting(app, 'MAE / MFE Units', 'points')
    fresh = open_app('Settings')
    assert widget(fresh.selectbox, 'MAE / MFE Units').value == 'points'
    fresh.radio(key='journal-environment').set_value('Replay').run()         # one choice for Live and Replay
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
    assert widget(app.metric, 'MFE').value == '—' and said(app.caption, 'No stop order 10 seconds after entry')
    assert not any(b.label == 'Recalculate From Stored Prices' for b in app.button)    # the watcher measures
    display_setting(app, 'MAE / MFE Units', 'ticks')
    assert widget(app.metric, 'MFE').value == '—'                     # set right under it, once
    widget(app.number_input, 'Minimum price increment').set_value(.5)
    press(app, 'Save Tick Size')
    assert widget(app.metric, 'MFE').value == '6.00 ticks'
    assert journal.display_settings()['tick_sizes'] == {'CUSTOM': .5}
    show(app, 'Export')
    from views import export
    table = export.trades_csv(common.rows_for(journal), 'UTC').splitlines()
    assert float(table[1].split(',')[table[0].split(',').index('mfe')]) == 6  # account currency, not ticks


def test_one_measurement_from_recorded_ticks_in_every_unit_and_no_word_about_how_it_was_measured(home):
    journal = seed_ticks(seed_preview(home))
    trade = journal.trades()[0]
    # A platform's own report, as 0.4.17 kept one until the measuring pass removes it: never shown.
    report = {'source': 'platform-report-only', 'precision': 'platform-reported', 'mae': 999, 'mfe': 9999,
              'notes': [], 'path': []}
    journal.record_metric(trade, report)
    app = open_app()
    assert widget(app.metric, 'MFE').value == '$250.00'
    display_setting(app, 'MAE / MFE Units', 'points')
    assert widget(app.metric, 'MFE').value == '2.50 pts'          # the same moment as the $250
    assert not any('9,999' in m.value for m in app.metric)
    assert not any(s.label == 'Measurement' for s in app.selectbox)
    # The trade's own recorded ticks need no label: no caption says how MAE / MFE were measured, nor any note.
    open_review(app)
    for words in ('Observed ticks', 'OHLC estimate', 'Measured from', 'can understate excursions',
                  'Chart path is compacted'):
        assert not said(app.caption, words), words
    assert 'Measured from' not in widget(app.metric, 'MAE').help


def test_analytics_is_question_based_and_selected_units_change_only_excursion_axes(home):
    seed_ticks(seed_preview(home))
    app = open_app('Edge')
    assert [t.label for t in app.tabs] == ['Overview', 'Compare', 'MAE / MFE', 'After Exit', 'Dashboards']
    assert widget(app.metric, 'Average Trade').value == '+$242.00'
    # Only the open tab is drawn: Overview's equity curve, and none of Compare's controls yet.
    assert widget(app.metric, 'Gross P&L') and not any(s.label == 'Compare by' for s in app.selectbox)
    open_tab(app, 'Compare')
    assert widget(app.selectbox, 'Compare by').value == 'Tags' and not any(m.label == 'Gross P&L' for m in app.metric)
    # The tag tables: Totals first, each a compact table of Trades, Net P&L, Win Rate and Average Trade.
    tables = [m.value for m in app.markdown if "class='mwj-edge-table'" in m.value]
    assert '>Totals</th><th>Trades</th><th>Net P&amp;L</th><th>Win Rate</th><th>Average Trade</th>' in tables[0]
    display_setting(app, 'MAE / MFE Units', 'ticks', back='Edge')
    assert not app.exception and widget(app.metric, 'Net P&L').value == '+$242.00'
    open_tab(app, 'MAE / MFE')
    excursion = next(json.loads(x.proto.spec) for x in app.get('plotly_chart') if 'One dot per measured trade' in x.proto.spec)
    assert excursion['layout']['xaxis']['title']['text'] == 'Against me · MAE (ticks)'
    assert excursion['layout']['yaxis']['title']['text'] == 'In my favour · MFE (ticks)'


def test_reuse_tag_groups_with_no_extra_name_typing(home):
    journal = seed_preview(home)
    app = open_app()
    assert not any(x.label == 'New group name' for x in app.text_input)
    widget(app.selectbox, 'Tag category').set_value('Other tags').run()
    widget(app.text_input, 'Tag name').set_value('VWAP reclaim')
    press(app, 'Create and Apply Tag')
    assert journal.tag_catalog()['VWAP reclaim'] == 'Other tags'
    assert 'VWAP reclaim' in widget(app.pills, 'Other tags').value


# ---------------------------------------------------------------- status logic and the background readers

NOW = dt.datetime(2026, 10, 7, 14, 0, tzinfo=dt.timezone.utc)


def beat(seconds_ago, **fields):
    return {'ts': (NOW - dt.timedelta(seconds=seconds_ago)).isoformat(), 'status': 'connected', **fields}


def test_link_state_reads_the_stored_status_and_the_heartbeat_age(tmp_path):
    there = tmp_path / 'motivewave.ndjson'
    there.write_text('')
    on, missing = {'path': str(there), 'enabled': True}, {'path': str(tmp_path / 'none.ndjson'), 'enabled': True}
    state = lambda profile, status, **more: common.link_state(profile, status, now=NOW, **more)
    words = lambda link: (link.state, link.tone)
    recording, quiet, attention = ('Recording', 'good'), ('Not recording', 'neutral'), ('Needs attention', 'critical')
    # Recording: a heartbeat within the last 60 s.
    fresh = state(on, {'status': 'synced', 'heartbeat': beat(12, connector_version='0.3.10')})
    assert words(fresh) == recording and fresh.label == 'Recording · study 0.3.10'
    assert fresh.heard == NOW - dt.timedelta(seconds=12) and fresh.version == '0.3.10'
    assert state(on, {'status': 'synced', 'heartbeat': beat(12)}).label == 'Recording'      # an older study: no version
    assert words(state(on, {'status': 'synced', 'heartbeat': beat(common.STALE_AFTER)})) == recording
    assert words(state(on, {'status': 'synced', 'heartbeat': beat(-5)})) == recording      # clocks a little apart
    # Not recording, grey: a 61 s old heartbeat, no bridge file, or a file with no events yet.
    old = state(on, {'status': 'synced', 'heartbeat': beat(61, connector_version='0.3.10')})
    assert words(old) == quiet and old.label == 'Not recording' and '1 min ago' in old.detail
    assert old.heard == NOW - dt.timedelta(seconds=61) and old.version == '0.3.10'      # heard from before
    assert words(state(missing, {})) == quiet and words(state(on, {'status': 'waiting for study'})) == quiet
    assert words(state(on, {})) == quiet and 'not written anything' in state(on, {}).detail
    # Needs attention: no study connection, a reader error, the study's own error, events with no heartbeat at all.
    assert words(state(None, {})) == attention
    assert words(state(on, {'status': 'synced', 'heartbeat': beat(1)}, worker_error='OperationalError')) == attention
    failed = state(on, {'status': 'needs attention', 'error': 'Line 3: Unknown event type', 'heartbeat': beat(2)})
    assert words(failed) == attention and failed.detail == 'Line 3: Unknown event type'
    assert words(state(on, {'status': 'synced'})) == attention
    assert words(state(on, {'status': 'synced', 'heartbeat': {'ts': 'not a time'}})) == attention
    # Reading is always on: a connection stored as switched off reads as any other.
    assert words(state({**on, 'enabled': False}, {'status': 'synced', 'heartbeat': beat(1)})) == recording
    every = [state(on, status) for status in ({}, {'status': 'synced'}, {'status': 'synced', 'heartbeat': beat(1)},
                                               {'status': 'synced', 'heartbeat': beat(600)})]
    assert {link.state for link in every} == {common.RECORDING, common.NOT_RECORDING, common.ATTENTION}
    assert not any('Capture Fills' in link.detail or 'Advanced' in link.detail for link in every)
    assert [common.ago(s) for s in (0, 59, 60, 3599, 3600, 90000)] == ['0 s ago', '59 s ago', '1 min ago',
                                                                     '59 min ago', '1 h ago', '1 d ago']


def test_the_study_connection_is_the_default_one_and_the_stamp_follows_fills_and_excursions(tmp_path):
    journal = Journal(tmp_path / 'trades.sqlite3')
    journal.put('profiles', 'mine', {'path': '/x.ndjson', 'mode': 'bridge', 'enabled': True})
    assert common.study_connection(journal) == (None, None)        # a bridge under another name is not the study's
    assert common.study_link(journal)[3].state == common.ATTENTION
    journal.put('profiles', DEFAULT_CONNECTION, {'path': '/y.ndjson', 'mode': 'bridge', 'enabled': True})
    assert common.study_connection(journal) == (DEFAULT_CONNECTION, journal.get('profiles')[DEFAULT_CONNECTION])
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
