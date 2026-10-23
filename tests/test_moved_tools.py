"""What moved off Settings in 0.4.5, where it is used: the Daily Report Card, a trade's checklist and the session
tools on Trades, and the dashboards on Stats. Disposable journals only, in a temporary data home."""

import datetime as dt
import time

import pytest

import common
import runtime
from mwjournal import Execution
from test_app import listed, open_app, open_tab, pick, press, said, show, widget
from test_chart import seed_preview
from views import checklists, edge, session

NY = 'America/New_York'


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    monkeypatch.setenv('MWJ_HOME', str(tmp_path))
    yield tmp_path
    runtime.stop_all()


def two_days(home):
    """The fixture trade on Oct 7 and a short NQ trade on Oct 8, both shown in New York time."""
    journal = seed_preview(home)
    journal.save_preferences(display_timezone=NY)
    journal.ingest([(Execution('motivewave', 'paper', 'NQZ6', 'buy', 1, 20000, '2026-10-08T14:10:00Z',
                               execution_id='n1'), {}),
                    (Execution('motivewave', 'paper', 'NQZ6', 'sell', 1, 20010, '2026-10-08T14:12:00Z',
                               execution_id='n2'), {})])
    return journal


def report_open(app):
    return any(t.label.startswith('What I learned') for t in app.text_area)


def day_header(app, day):
    return app.button(key=f'open-day-{day}-live')


# ---------------------------------------------------------------- the Daily Report Card on Trades

def test_a_days_header_opens_its_report_card_in_place_of_the_trade_and_back_returns_to_it(home):
    journal = two_days(home)
    app = open_app()
    selected = app.session_state['selected-trade-live']
    assert not report_open(app) and any(m.label == 'MAE' for m in app.metric)
    header = day_header(app, '2026-10-07')
    assert header.label == 'Daily Report Card for Wed, Oct 7'
    header.click().run()
    assert not app.exception and report_open(app)
    assert widget(app.date_input, 'Trading day').value == dt.date(2026, 10, 7)
    assert widget(app.selectbox, 'Session time zone').value == NY             # the zone the list groups days by
    assert widget(app.metric, 'Net P&L').value == '+$242.00' and widget(app.metric, 'Trades').value == '1'
    assert not any(m.label == 'MAE' for m in app.metric)                     # the trade made room for it
    assert len(listed(app)) == 2                                              # the list stays beside it
    headers = [m.value for m in app.main.markdown if "class='mwj-day" in m.value]
    assert "mwj-day sel'" in headers[1] and "mwj-day sel'" not in headers[0]  # Oct 7 is the open day
    # Grade the day, write the note, tag the day: the card is the whole Daily Report Card. There is no Save:
    # going straight back to the trade after typing keeps what was typed.
    assert not any(b.label == 'Save Daily Review' for b in app.button)
    widget(app.checkbox, 'I had a plan').check()
    widget(app.text_area, 'What I learned').set_value('Waited for the open.')
    assert any(p.label == 'Mindset' for p in app.pills) or said(app.caption, 'No day tags yet')
    press(app, '← Back to Trades')
    saved = journal.get('sessions')['2026-10-07:' + NY]
    assert saved['planned'] and saved['notes'] == 'Waited for the open.'
    assert not report_open(app) and app.session_state['selected-trade-live'] == selected
    assert any(m.label == 'MAE' for m in app.metric)


def test_clicking_a_trade_or_trades_in_the_sidebar_also_closes_the_report_card(home):
    journal = two_days(home)
    older = min(journal.trades(), key=lambda t: t.opened)
    app = open_app()
    day_header(app, '2026-10-08').click().run()
    assert report_open(app) and widget(app.date_input, 'Trading day').value == dt.date(2026, 10, 8)
    pick(app, older.id)
    assert not report_open(app) and app.session_state['selected-trade-live'] == older.id
    day_header(app, '2026-10-07').click().run()
    assert report_open(app)
    show(app, 'Edge')
    app.segmented_control(key=common.VIEW_KEY).set_value('Trades').run()
    assert not app.exception and not report_open(app)


def test_the_report_card_keeps_working_for_a_day_picked_in_its_own_date_box(home):
    two_days(home)
    app = open_app()
    day_header(app, '2026-10-08').click().run()
    assert widget(app.metric, 'Net P&L').value == '+$200.00'
    widget(app.date_input, 'Trading day').set_value(dt.date(2026, 10, 7)).run()
    assert not app.exception and widget(app.metric, 'Net P&L').value == '+$242.00'
    widget(app.date_input, 'Trading day').set_value(dt.date(2026, 10, 1)).run()   # a day without trades
    assert not app.exception and widget(app.metric, 'Trades').value == '0'


def test_a_calendar_day_on_stats_opens_that_days_report_card_on_trades(home):
    two_days(home)
    app = open_app('Dashboard')
    calendar_day = next(b for b in app.button if b.label.startswith('8  \n'))
    assert 'Daily Report Card on Trades' in calendar_day.help
    calendar_day.click().run()
    assert not app.exception and app.segmented_control(key=common.VIEW_KEY).value == 'Trades'
    assert report_open(app) and widget(app.date_input, 'Trading day').value == dt.date(2026, 10, 8)
    assert len(listed(app)) == 2


def test_the_report_card_opens_even_with_no_trades_yet():
    app = open_app()
    assert said(app.info, 'No trades yet')
    app.session_state[common.REPORT_KEY] = True                   # how a calendar day asks for it
    app.run()
    assert not app.exception and report_open(app) and widget(app.metric, 'Trades').value == '0'
    press(app, '← Back to Trades')
    assert said(app.info, 'No trades yet')


# ---------------------------------------------------------------- the checklist beside the trade

def test_a_checklist_runs_in_the_selected_trades_checklist_tab(home):
    journal = seed_preview(home)
    journal.save_checklist('Plan', checklists.starter())
    trade = journal.trades()[0]
    app = open_app()
    assert [t.label for t in app.tabs][:4] == ['Chart & Tags', 'Notes', 'Checklist', 'More']
    assert 'Checklists' not in [t.label for t in app.tabs]          # no longer under More
    press(app, 'Start New Run')
    press(app, 'Yes — continue my plan')
    runs = list(journal.workspace()['runs'].values())
    assert len(runs) == 1 and runs[0]['trade_id'] == trade.id and runs[0]['complete']


def test_with_no_checklist_yet_the_tab_links_to_the_editor_on_setup(home):
    seed_preview(home)
    app = open_app()
    press(app, 'Build a Checklist')
    assert app.session_state[common.TOOL_KEY] == 'Tags'
    assert app.session_state['tag-settings-tabs-live'] == 'Checklists'
    assert any(s.label == 'Checklist' for s in app.selectbox)          # the editor's own picker


# ---------------------------------------------------------------- the session tools above the list

def menu(app, label, *, run=True):
    """Open one of the session menus. AppTest cannot click one open, so it is opened the way a click does; and
    as AppTest does not send a menu's state back, a click inside one opens it again first (run=False)."""
    app.session_state[f'session-menu-{label.lower()}-live'] = True
    if run:
        app.run()
        assert not app.exception
    return app


def test_the_session_timer_sits_above_the_list_with_play_pause_stop_and_its_minutes_under_check_ins(home):
    journal = seed_preview(home)
    app = open_app()
    play, pause, stop = (app.button(key=f'session-timer-live-{name}') for name in ('play', 'pause', 'stop'))
    assert not play.disabled and pause.disabled and stop.disabled
    assert said(app.main.markdown, "class='mwj-timer off'") and said(app.main.markdown, '>05:00<')
    assert not any(n.key == 'timer-minutes-live' for n in app.number_input)   # the row holds the count and buttons
    menu(app, 'Check-Ins')
    duration = app.number_input(key='timer-minutes-live')
    assert duration.value == session.DEFAULT_MINUTES and duration.label == 'Minutes Between Check-Ins'
    menu(app, 'Check-Ins', run=False)
    duration.set_value(2).run()
    assert journal.workspace()['preferences']['timer_minutes'] == 2 and said(app.main.markdown, '>02:00<')
    app.button(key='session-timer-live-play').click().run()
    running = app.session_state['session-timer-live']
    assert not running.get('paused') and 110 < running['next'] - time.monotonic() <= 120
    assert said(app.main.markdown, "class='mwj-timer on'") and app.button(key='session-timer-live-play').disabled
    app.button(key='session-timer-live-pause').click().run()
    assert app.session_state['session-timer-live']['paused'] and said(app.main.markdown, "class='mwj-timer paused'")
    app.button(key='session-timer-live-play').click().run()                      # resumes where it was
    assert not app.session_state['session-timer-live'].get('paused')
    app.button(key='session-timer-live-stop').click().run()
    assert 'session-timer-live' not in app.session_state and said(app.main.markdown, '>02:00<')
    assert listed(app)                                                           # the list is still there


def test_when_the_timer_runs_out_the_chosen_groups_check_in(home):
    journal = seed_preview(home)
    journal.save_preferences(timer_groups=['Mindset'])
    app = open_app()
    app.button(key='session-timer-live-play').click().run()
    app.session_state['session-timer-live']['next'] = time.monotonic() - 1     # the interval is over
    app.run()
    assert any(p.label == 'Check In · Mindset' for p in app.pills) and said(app.main.markdown, "class='mwj-timer due'")
    assert not any(s.label == 'Attach Check-In To' for s in app.selectbox)     # no open position to attach to
    app.pills(key='session-timer-live-check-Mindset-0').set_value('Patient').run()   # one click is the check-in
    checkins = list(journal.workspace()['checkins'].values())
    assert [(c['group'], c['trade_id']) for c in checkins] == [('Mindset', None)]
    assert not any(p.label == 'Check In · Mindset' for p in app.pills)                   # answered: the timer starts over
    assert 50 < app.session_state['session-timer-live']['next'] - time.monotonic() <= 300
    app.run()                                                                   # the click arrives again
    assert len(journal.workspace()['checkins']) == 1
    menu(app, 'Check-Ins')
    assert widget(app.multiselect, 'Check-In Tag Groups').value == ['Mindset']
    assert any('Group' in d.value.columns for d in app.dataframe)


def test_pre_trade_drafts_and_a_session_checklist_are_menus_in_the_same_row(home):
    journal = seed_preview(home)
    journal.save_checklist('Plan', checklists.starter())
    app = open_app()
    assert not any(b.label == 'Arm Draft' for b in app.button)                 # menus draw only when open
    menu(app, 'Drafts')
    assert any(b.label == 'Arm Draft' for b in app.button)
    assert widget(app.selectbox, 'Next-trade source').value == ('motivewave', 'paper', 'ESZ6')
    menu(app, 'Checklist')
    assert [b.label for b in app.button].count('Start New Run') == 2            # the session's and the trade's
    session_run = app.button(key='session-checklist-start-' + next(iter(journal.workspace()['checklists'])) + '-live')
    menu(app, 'Checklist', run=False)
    session_run.click().run()
    runs = list(journal.workspace()['runs'].values())
    assert len(runs) == 1 and runs[0]['trade_id'] is None                     # for the session, not a trade


def test_the_session_tools_are_there_before_the_first_trade():
    app = open_app()
    assert said(app.info, 'No trades yet')
    assert any(b.key == 'session-timer-live-play' for b in app.button)
    menu(app, 'Drafts')
    assert said(app.info, 'Capture a trade first')


# ---------------------------------------------------------------- the dashboards on Stats

def test_dashboards_are_the_last_tab_of_stats_and_customize_cannot_remove_it(home):
    journal = seed_preview(home)
    app = open_app('Edge')
    assert [t.label for t in app.tabs][-1] == edge.DASHBOARDS
    assert not any(s.label == 'Dashboard page' for s in app.selectbox)          # drawn only when open
    open_tab(app, 'Dashboards')
    assert any(s.label == 'Dashboard page' for s in app.selectbox)
    assert [p.proto.popover.label for p in app.main.get('popover')][:4] == [
        'Filters', 'Customize', 'Page Filters', 'Customize Page']
    assert edge.DASHBOARDS not in widget(app.multiselect, 'Tabs, in order').options
    widget(app.multiselect, 'Tabs, in order').set_value(['Runners'])
    press(app, 'Save Tabs')
    assert [t.label for t in app.tabs][:2] == ['Runners', 'Dashboards']
    assert journal.display_settings()['dashboard_sections'] == ['Runners']


def test_the_stats_filters_narrow_the_dashboards_too(home):
    two_days(home)
    app = open_app('Dashboard')
    summary = lambda: widget(app.metric, 'Trades').value
    assert summary() == '2'
    widget(app.multiselect, 'Markets').set_value(['NQ']).run()
    assert not app.exception and summary() == '1'


def test_dashboards_open_with_no_closed_trade_yet():
    app = open_app('Edge')
    assert said(app.info, 'Statistics need') and [t.label for t in app.tabs] == ['Dashboards']
    open_tab(app, 'Dashboards')
    assert any(s.label == 'Dashboard page' for s in app.selectbox)


def test_a_tab_that_was_removed_gives_way_to_the_first(home):
    journal = seed_preview(home)
    app = open_app('Edge')
    open_tab(app, 'Runners')
    journal.set_dashboard_sections(['Overview'])
    app.run()
    assert not app.exception and [t.label for t in app.tabs] == ['Overview', 'Dashboards']
    assert app.session_state.get('analytics-tabs-live') in (None, 'Overview')
