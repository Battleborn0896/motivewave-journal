"""What moved off Settings in 0.4.5, where it is used: the Daily Report Card, a trade's checklist and the session
tools on Trades, and the dashboards on Stats. Disposable journals only, in a temporary data home."""

import datetime as dt
import time

import pytest

import common
import runtime
from mwjournal import Execution, Journal, tagging
from mwjournal.workspace import question
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
    Journal().tag_library()                    # a new journal's first read offers the starters, Day among them
    journal = two_days(home)
    app = open_app()
    selected = app.session_state['selected-trade-live']
    assert not report_open(app) and any(m.label == 'MAE' for m in app.metric)
    header = day_header(app, '2026-10-07')
    assert header.label == 'Daily Report Card for Wed, Oct 7'
    header.click().run()
    assert not app.exception and report_open(app)
    assert widget(app.date_input, 'Trading day').value == dt.date(2026, 10, 7)
    assert not any('zone' in s.label.lower() for s in app.selectbox)   # days follow Settings → Time Zone alone
    assert widget(app.metric, 'Net P&L').value == '+$242.00' and widget(app.metric, 'Trades').value == '1'
    assert not any(m.label == 'MAE' for m in app.metric)                     # the trade made room for it
    assert len(listed(app)) == 2                                              # the list stays beside it
    headers = [m.value for m in app.main.markdown if "class='mwj-day" in m.value]
    assert "mwj-day sel'" in headers[1] and "mwj-day sel'" not in headers[0]  # Oct 7 is the open day
    # Grade the day, write the note, tag the day: the card is the whole Daily Report Card. There is no Save:
    # going straight back to the trade after typing keeps what was typed.
    assert not any(b.label == 'Save Daily Review' for b in app.button)
    widget(app.pills, 'Day').set_value(['Had a Plan'])
    widget(app.pills, 'Rating').set_value(4)
    widget(app.text_area, 'What I learned').set_value('Waited for the open.')
    press(app, '← Back to Trades')
    saved = journal.get('sessions')['2026-10-07']
    assert saved['day_tag_ids'] == [tagging.identity('Had a Plan')] and saved['rating'] == 4
    assert saved['notes'] == 'Waited for the open.'
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
    app = open_app('Edge')                         # the calendar on Overview
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

def checklist_pills(app):
    return [p for p in app.pills if p.label.startswith('1 · ')]


def test_a_checklist_runs_in_the_selected_trades_checklist_section(home):
    journal = seed_preview(home)
    journal.save_checklist('Plan', checklists.starter())
    trade = journal.trades()[0]
    app = open_app()
    assert not app.tabs and said(app.main.markdown, '>Checklist<')     # a section of the trade's page
    pills, = checklist_pills(app)
    assert pills.label == '1 · ' + checklists.STARTER and pills.options == ['Yes', 'No']
    pills.set_value('q1-yes').run()
    runs = list(journal.workspace()['runs'].values())
    assert len(runs) == 1 and runs[0]['trade_id'] == trade.id and runs[0]['complete']


def test_a_trade_has_no_checklist_section_until_a_checklist_runs_on_each_trade(home):
    journal = seed_preview(home)
    app = open_app()
    assert not said(app.main.markdown, '>Checklist<') and not checklist_pills(app)
    journal.save_checklist('Morning', checklists.starter(), runs_on='session')
    app.run()
    assert not said(app.main.markdown, '>Checklist<') and not checklist_pills(app)   # it is on the day instead
    journal.save_checklist('Plan', checklists.starter())
    app.run()
    assert said(app.main.markdown, '>Checklist<') and len(checklist_pills(app)) == 1


def test_a_before_the_session_checklist_runs_on_the_daily_report_card_and_its_run_keeps_the_day(home):
    Journal().tag_library()                    # a new journal's first read offers the starters, Day among them
    journal = two_days(home)
    day_tag = next(t for t in journal.tag_library()['tags'].values() if 'day' in t['levels'])
    journal.save_checklist('Morning', [question('Slept well?', no={'tag_ids': [day_tag['id']]})], runs_on='session')
    app = open_app()
    day_header(app, '2026-10-07').click().run()
    assert report_open(app) and said(app.main.markdown, '>Checklist<')
    pills, = checklist_pills(app)
    pills.set_value('q1-no').run()
    assert not app.exception and said(app.success, 'Checklist complete')
    run, = journal.workspace()['runs'].values()
    assert run['day'] == '2026-10-07' and 'trade_id' not in run
    press(app, 'Apply Collected Tags')
    assert journal.get('sessions')['2026-10-07']['day_tag_ids'] == [day_tag['id']]
    # The next day has its own run: none yet.
    app.date_input(key='review-day-live').set_value(dt.date(2026, 10, 8)).run()
    assert checklist_pills(app)[0].value is None and len(journal.workspace()['runs']) == 1


# ---------------------------------------------------------------- the session tools above the list

def menu(app, *, run=True):
    """Open the Session menu. AppTest cannot click one open, so it is opened the way a click does; and as AppTest
    does not send a menu's state back, a click inside one opens it again first (run=False)."""
    app.session_state['session-menu-live'] = True
    if run:
        app.run()
        assert not app.exception
    return app


def timer_buttons(app):
    """The timer's buttons in the bar, by what they do: none while it is stopped."""
    prefix = 'session-timer-live-'
    return [b.key.removeprefix(prefix) for b in app.button if b.key and b.key.startswith(prefix)]


def test_the_check_in_timer_starts_from_the_session_menu_and_its_count_is_in_the_bar_only_while_it_runs(home):
    journal = seed_preview(home)
    app = open_app()
    assert not timer_buttons(app) and not said(app.main.markdown, "class='mwj-timer")   # stopped: nothing in the bar
    assert [p.proto.popover.label for p in app.main.get('popover')][:3] == ['Session', 'Filters', 'Merge Trades']
    assert not any(n.key == 'timer-minutes-live' for n in app.number_input)      # the menu draws only when open
    menu(app)
    duration = app.number_input(key='timer-minutes-live')
    assert duration.value == session.DEFAULT_MINUTES and duration.label == 'Minutes Between Check-Ins'
    menu(app, run=False)
    duration.set_value(2).run()
    assert journal.workspace()['preferences']['timer_minutes'] == 2
    menu(app, run=False)
    press(app, 'Start Check-In Timer')
    running = app.session_state['session-timer-live']
    assert not running.get('paused') and 110 < running['next'] - time.monotonic() <= 120
    assert app.session_state['session-menu-live'] is False                       # the menu closes on the count
    assert said(app.main.markdown, "class='mwj-timer on'") and timer_buttons(app) == ['pause', 'stop']
    menu(app)
    assert widget(app.button, 'Start Check-In Timer').disabled                   # it is running
    app.button(key='session-timer-live-pause').click().run()
    assert app.session_state['session-timer-live']['paused'] and said(app.main.markdown, "class='mwj-timer paused'")
    assert timer_buttons(app) == ['play', 'stop']
    app.button(key='session-timer-live-play').click().run()                      # resumes where it was
    assert not app.session_state['session-timer-live'].get('paused')
    app.button(key='session-timer-live-stop').click().run()
    assert 'session-timer-live' not in app.session_state and not timer_buttons(app)
    assert not said(app.main.markdown, "class='mwj-timer") and listed(app)              # the list is still there


def test_when_the_timer_runs_out_the_chosen_categories_check_in(home):
    journal = seed_preview(home)
    journal.save_preferences(timer_groups=['Mindset'])
    app = open_app()
    menu(app)
    press(app, 'Start Check-In Timer')
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
    menu(app)
    assert widget(app.multiselect, 'Check-In Categories').value == ['Mindset']
    assert any('Category' in d.value.columns for d in app.dataframe)


def test_tag_my_next_trade_is_in_the_session_menu_and_no_menu_runs_a_checklist(home):
    journal = seed_preview(home)
    journal.save_checklist('Morning', checklists.starter(), runs_on='session')
    app = open_app()
    assert not any(b.label == 'Tag My Next Trade' for b in app.button)         # the menu draws only when open
    menu(app)
    assert any(b.label == 'Tag My Next Trade' for b in app.button)
    assert widget(app.selectbox, 'Contract').value == ('motivewave', 'paper', 'ESZ6')
    assert not any(w.label.endswith(('date', 'time', 'time zone')) for w in [*app.date_input, *app.time_input,
                                                                            *app.selectbox])     # no window
    assert not any(t.label == 'Draft name' for t in app.text_input)
    assert not any(p.proto.popover.label == 'Checklist' for p in app.main.get('popover'))   # it is on the day


def test_an_empty_journal_shows_no_session_bar():
    app = open_app()
    assert said(app.info, 'No trades yet')
    assert not app.main.get('popover') and not timer_buttons(app)                 # no Session, Filters or Merge
    app.session_state[common.REPORT_KEY] = True                                  # a calendar day asks for its card
    app.run()
    assert not app.exception and report_open(app) and not timer_buttons(app)


# ---------------------------------------------------------------- the dashboards on Stats

def test_stats_has_the_same_tabs_every_time_and_one_customize_on_the_dashboards(home):
    seed_preview(home)
    app = open_app('Edge')
    assert [t.label for t in app.tabs] == [*edge.SECTIONS, edge.DASHBOARDS] == [
        'Overview', 'Compare', 'MAE / MFE', 'After Exit', 'Dashboards']
    assert not any(s.label == 'Dashboard page' for s in app.selectbox)          # drawn only when open
    assert not any(m.label == 'Tabs, in order' for m in app.multiselect)
    open_tab(app, 'Dashboards')
    assert any(s.label == 'Dashboard page' for s in app.selectbox)
    # One Customize: the dashboard page's own, beside its filters.
    assert [p.proto.popover.label for p in app.main.get('popover')] == ['Filters', 'Page Filters', 'Customize Page']
    assert not hasattr(edge, 'customize') and not hasattr(Journal, 'set_dashboard_sections')


def test_the_stats_filters_narrow_the_dashboards_too(home):
    two_days(home)
    Journal().save_dashboard('Glance', [{'kind': 'Summary', 'title': 'Results at a Glance'}])
    app = open_app('Dashboard')
    summary = lambda: widget(app.metric, 'Trades').value
    assert summary() == '2'
    widget(app.multiselect, 'Markets').set_value(['NQ']).run()
    assert not app.exception and summary() == '1'
    # The Stats button says a filter is on; the page's own count only the page's filters.
    assert [p.proto.popover.label for p in app.main.get('popover')] == ['Filters · 1', 'Page Filters', 'Customize Page']


def test_dashboards_open_with_no_closed_trade_yet():
    app = open_app('Edge')
    assert said(app.info, 'Statistics need') and [t.label for t in app.tabs] == ['Dashboards']
    open_tab(app, 'Dashboards')
    assert any(s.label == 'Dashboard page' for s in app.selectbox)


def test_a_tab_that_is_gone_gives_way_to_the_first(home):
    seed_preview(home)
    app = open_app('Edge')
    open_tab(app, 'After Exit')
    # Filters that keep no closed trade leave only Dashboards; with the trades back, Stats opens on Overview.
    widget(app.multiselect, 'Directions').set_value(['short']).run()
    assert not app.exception and [t.label for t in app.tabs] == ['Dashboards']
    widget(app.multiselect, 'Directions').set_value([]).run()
    assert not app.exception and [t.label for t in app.tabs][0] == 'Overview'
    assert app.session_state.get('analytics-tabs-live') in (None, 'Overview')
