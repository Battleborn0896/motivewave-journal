"""What a trader reads and types on Trades: every review box saves by itself, an open trade has one P&L, a
trade without MAE / MFE says why, and the screen uses words instead of codes and ids. Disposable journals in a
temporary data home, under the production tick-only default."""
import datetime as dt

import pytest

import runtime
from mwjournal import Execution, Journal, PriceSample, journals
from mwjournal.metrics import measurements
from mwjournal.model import ProtectionEvent, instant
from test_app import chosen, listed, open_app, pick, press, said, show, widget
from test_chart import seed_preview
from test_moved_tools import menu
from views import checklists

NY = 'America/New_York'
TICKS = 'motivewave-ticks'


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    monkeypatch.setenv('MWJ_HOME', str(tmp_path))
    for dataset in ('live', 'replay'):
        Journal(dataset=dataset).save_preferences(display_timezone=NY)     # times below are New York times
    yield tmp_path
    runtime.stop_all()


def fill(id, side, price, ts, symbol='ESZ6', qty=1, **more):
    return Execution('motivewave', 'paper', symbol, side, qty, price, ts, execution_id=id, commission=2, **more), {}


def measure(journal, trade, prices, now=None):
    """Store these prints for the trade's contract and measure it from them, as the watcher does."""
    journal.ingest_prices([PriceSample(trade.symbol, ts, TICKS, price, price) for ts, price in prices])
    samples = journal.prices(trade.symbol, TICKS, start=trade.opened, end=trade.closed)
    for metric in measurements(trade, samples, TICKS, now=instant(now) if now else None):
        journal.record_metric(trade, metric)


def rows_html(app):
    return [m.value for m in app.main.markdown if "class='mwj-trade" in m.value]


def everything(app):
    """Every word on the page that is not a widget's own value: what a trader reads."""
    return ' '.join(str(e.value) for kind in ('markdown', 'caption', 'info', 'warning', 'error')
                    for e in getattr(app.main, kind))


def cells(app):
    """Every column name and cell of every table on the page, as text."""
    return [str(v) for d in app.dataframe for v in [*d.value.columns, *d.value.astype(str).to_numpy().ravel()]]


# ---------------------------------------------------------------- every review box saves by itself

def test_the_plan_typed_on_a_trade_is_kept_when_next_is_clicked_straight_away(home):
    journal = seed_preview(home)
    journal.ingest([fill('n-in', 'buy', 20000, '2026-10-08T14:10:00Z', symbol='NQZ6'),
                    fill('n-out', 'sell', 20010, '2026-10-08T14:12:00Z', symbol='NQZ6')])
    newer, older = sorted(journal.trades(), key=lambda t: t.opened, reverse=True)
    app = open_app()
    assert chosen(app) == newer.id and not any(b.label == 'Save Plan' for b in app.button)
    widget(app.text_input, 'Stop').set_value('19990')
    widget(app.selectbox, 'Followed my plan').set_value('No')
    widget(app.text_area, 'Review notes').set_value('Waited for the retest.')
    press(app, 'Next →')                                  # no box was left first: the click carries what was typed
    assert chosen(app) == older.id
    review = journal.annotations()[newer.id]
    assert (review['planned_stop'], review['adherence'], review['notes']) == (19990, 'No', 'Waited for the retest.')
    widget(app.selectbox, 'Followed my plan').set_value('Yes')
    pick(app, newer.id)                                  # a row click keeps it too
    assert journal.annotations()[older.id]['adherence'] == 'Yes'
    assert journal.annotations()[older.id]['planned_stop'] == 4998         # its saved stop, untouched
    assert widget(app.text_input, 'Stop').value == '19990'                 # and the first trade shows what was kept


def test_an_untouched_stop_box_filled_from_the_order_is_never_saved_as_the_traders_stop(home):
    journal = Journal()
    journal.ingest([fill('a-in', 'buy', 5000, '2026-10-07T14:00:00Z'), fill('a-out', 'sell', 5004, '2026-10-07T14:01:00Z'),
                    fill('b-in', 'buy', 5000, '2026-10-07T15:00:00Z'), fill('b-out', 'sell', 5004, '2026-10-07T15:01:00Z')])
    first, second = sorted(journal.trades(), key=lambda t: t.opened)
    journal.ingest_protection([ProtectionEvent('motivewave', 'paper', 'ESZ6', 'stop-9', '2026-10-07T15:00:00Z', 'stop',
                                               'working', 4996, entry_execution_id='b-in')])
    app = open_app()
    assert chosen(app) == second.id and widget(app.text_input, 'Stop').value == '4996'
    widget(app.selectbox, 'Followed my plan').set_value('Yes')
    press(app, 'Next →')
    assert journal.annotations()[second.id]['adherence'] == 'Yes'
    assert 'planned_stop' not in journal.annotations()[second.id]          # still the order's stop
    assert chosen(app) == first.id


def test_the_daily_report_card_keeps_what_was_typed_when_another_day_or_a_trade_is_clicked(home):
    journal = seed_preview(home)
    journal.ingest([fill('n-in', 'buy', 20000, '2026-10-08T14:10:00Z', symbol='NQZ6'),
                    fill('n-out', 'sell', 20010, '2026-10-08T14:12:00Z', symbol='NQZ6')])
    app = open_app()
    app.button(key='open-day-2026-10-07-live').click().run()
    assert not any(b.label == 'Save Daily Review' for b in app.button)
    widget(app.checkbox, 'I respected my risk limits').check()
    widget(app.text_area, 'What I learned').set_value('Too early on the first entry.')
    app.button(key='open-day-2026-10-08-live').click().run()             # another day, straight away
    assert widget(app.date_input, 'Trading day').value == dt.date(2026, 10, 8)
    oct7 = journal.get('sessions')['2026-10-07:' + NY]
    assert oct7['risk_limits'] and oct7['notes'] == 'Too early on the first entry.'
    assert widget(app.text_area, 'What I learned').value == ''             # each day its own card
    widget(app.text_area, 'What I learned').set_value('Patient.')
    pick(app, listed(app)[0])                                             # a trade, straight away
    assert journal.get('sessions')['2026-10-08:' + NY]['notes'] == 'Patient.'
    app.button(key='open-day-2026-10-07-live').click().run()
    assert widget(app.text_area, 'What I learned').value == 'Too early on the first entry.'
    assert widget(app.checkbox, 'I respected my risk limits').value


# ---------------------------------------------------------------- one P&L for an open trade

def test_an_open_trades_row_shows_the_same_open_pnl_as_its_header(home):
    journal = Journal()
    journal.ingest([fill('o-in', 'buy', 5000, '2026-10-07T14:00:00Z'),
                    fill('c-in', 'buy', 5000, '2026-10-07T13:00:00Z', symbol='MESZ6'),
                    fill('c-out', 'sell', 5001, '2026-10-07T13:05:00Z', symbol='MESZ6')])
    trade = next(t for t in journal.trades() if t.status == 'open')
    app = open_app()
    assert chosen(app) == trade.id
    # No price yet: a dash in the row and in the header, never the entry fee as a loss.
    row = next(r for r in rows_html(app) if 'ESZ6' in r and 'MESZ6' not in r)
    assert "title='Open P&amp;L'>—<" in row and "mwj-badge open'>Open<" in row and '-&#36;2.00' not in row
    result = widget(app.metric, 'Open P&L')
    assert result.value == '—' and 'Realised so far: -$2.00' in result.help
    assert not any(m.label in ('Realised P&L · still open', 'Recorded P&L') for m in app.metric)
    assert said(app.main.caption, 'Waiting for the first ES price.')
    # A print one point up: +$50 on the contract, less the $2 entry fee, in both places.
    measure(journal, trade, [('2026-10-07T14:00:30Z', 5001)], now='2026-10-07T14:01:00Z')
    app.run()
    row = next(r for r in rows_html(app) if 'ESZ6' in r and 'MESZ6' not in r)
    assert "class='p mwj-flat' title='Open P&amp;L'>+&#36;48.00<" in row and "mwj-badge open'>Open<" in row
    assert widget(app.metric, 'Open P&L').value == '+$48.00'
    assert not said(app.main.caption, 'Waiting for the first')
    pick(app, next(t.id for t in journal.trades() if t.status == 'closed'))
    assert widget(app.metric, 'Net P&L').value == '+$1.00'                  # a closed trade: Net P&L
    row = next(r for r in rows_html(app) if 'ESZ6' in r and 'MESZ6' not in r)
    assert '+&#36;48.00' in row                                            # the open row, whatever is selected


def test_an_open_trade_whose_prices_do_not_count_says_why_and_never_that_it_is_waiting(home):
    journal = Journal()
    journal.ingest([fill('o-in', 'buy', 5000, '2026-10-07T14:00:00Z')])
    trade = journal.trades()[0]
    measure(journal, trade, [('2026-10-07T14:00:30Z', 5001), ('2026-10-07T14:00:40Z', 5002)], now='2026-10-07T14:01:00Z')
    journal.ingest_gaps([{'type': 'gap', 'provider': TICKS, 'symbol': 'ESZ6', 'ts': '2026-10-07T14:00:35Z', 'dropped': 4}])
    app = open_app()
    result = widget(app.metric, 'Open P&L')
    # Prices were recorded, with a gap: no Open P&L at them, as for MAE / MFE, and the line under the numbers
    # gives the reason. Nothing claims the trade is still waiting for its first price.
    assert result.value == '—' and result.help == 'Realised so far: -$2.00.'
    assert said(app.main.caption, "Some of this trade's ticks were not recorded, so MAE / MFE are not measured.")
    assert not said(app.main.caption, 'Waiting for the first')
    assert "title='Open P&amp;L'>—<" in rows_html(app)[0]


def test_a_dashboard_lists_an_open_trade_without_its_fees_as_its_net_pnl(home):
    journal = Journal()
    journal.ingest([fill('o-in', 'buy', 5000, '2026-10-07T14:00:00Z'),
                    fill('c-in', 'buy', 5000, '2026-10-07T13:00:00Z', symbol='MESZ6'),
                    fill('c-out', 'sell', 5001, '2026-10-07T13:05:00Z', symbol='MESZ6')])
    page = journal.save_dashboard('Recent', [{'kind': 'Trades', 'title': 'Recent trades'}])
    app = open_app('Dashboard')
    widget(app.selectbox, 'Dashboard page').set_value(page).run()
    table = next(d.value for d in app.dataframe if 'Contract' in d.value.columns).set_index('Contract')
    assert table.loc['MESZ6', 'Net P&L'] == 1                              # +$5 less $4 of fees: closed
    assert table['Net P&L'].isna()['ESZ6']                                 # open: '—', never the -$2 entry fee


# ---------------------------------------------------------------- why MAE / MFE are missing

def causes(app):
    return [c.value for c in app.main.caption], [i.value for i in app.main.info]


@pytest.mark.parametrize('case', ['no prices', 'incomplete', 'candles only', 'ticks missed', 'waiting'])
def test_a_trade_without_mae_mfe_says_the_real_cause_in_the_header_and_under_more(home, case):
    journal = seed_preview(home) if case == 'candles only' else Journal()
    expected = {
        'no prices': 'No prices were recorded for MNQ during this trade. Keep the study on an MNQ chart while you '
                     'trade it.',
        'incomplete': "Unknown: the entry fill is no longer in MotiveWave's history.",
        'candles only': 'Only 1-minute candles were recorded, so MAE / MFE are not measured.',
        'ticks missed': "Some of this trade's ticks were not recorded, so MAE / MFE are not measured.",
        'waiting': 'Waiting for the first MNQ price.'}[case]
    if case == 'no prices':
        journal.ingest([fill('m-in', 'buy', 20000, '2026-10-07T14:00:00Z', symbol='MNQZ6'),
                        fill('m-out', 'sell', 20004, '2026-10-07T14:03:00Z', symbol='MNQZ6')])
    elif case == 'incomplete':                       # MotiveWave no longer has the fill that opened it
        journal.ingest([fill('x-out', 'sell', 5002, '2026-10-07T15:00:00Z', native_position_id='p9', native_entry=False)])
    elif case == 'ticks missed':
        journal.ingest([fill('t-in', 'buy', 5000, '2026-10-07T14:00:00Z'), fill('t-out', 'sell', 5002, '2026-10-07T14:03:00Z')])
        trade = journal.trades()[0]
        measure(journal, trade, [('2026-10-07T14:01:00Z', 4999), ('2026-10-07T14:02:00Z', 5003)])
        journal.ingest_gaps([{'type': 'gap', 'provider': TICKS, 'symbol': 'ESZ6', 'ts': '2026-10-07T14:01:30Z',
                              'dropped': 4}])
    elif case == 'waiting':
        journal.ingest([fill('w-in', 'buy', 20000, '2026-10-07T14:00:00Z', symbol='MNQZ6')])
    app = open_app()
    captions, infos = causes(app)
    assert expected in captions                                           # under the trade's numbers
    assert expected in infos                                              # and More → MAE / MFE, word for word
    said_ = everything(app)
    for pointer in ('Settings →', 'Accuracy', 'accuracy policy', 'Allow labelled estimates', 'need recorded ticks'):
        assert pointer not in said_                                       # a trade screen never points at a setting


def test_a_measured_trade_says_nothing_about_missing_mae_mfe(home):
    journal = Journal()
    journal.ingest([fill('t-in', 'buy', 5000, '2026-10-07T14:00:00Z'), fill('t-out', 'sell', 5002, '2026-10-07T14:03:00Z')])
    measure(journal, journal.trades()[0], [('2026-10-07T14:01:00Z', 4999), ('2026-10-07T14:02:00Z', 5003)])
    app = open_app()
    assert widget(app.metric, 'MAE').value == '$50.00' and widget(app.metric, 'Net P&L').value == '+$96.00'
    assert not any('MAE / MFE are not measured' in c or 'No prices were recorded' in c for c in causes(app)[0])


# ---------------------------------------------------------------- words instead of codes and ids

def test_orders_and_fills_read_as_time_stop_status_price_with_no_ids(home):
    journal = Journal()
    journal.ingest([fill('o-d5-in', 'buy', 5000, '2026-10-07T14:00:00Z'), fill('o-d5-out', 'sell', 4996, '2026-10-07T14:03:00Z')])
    trade = journal.trades()[0]
    measure(journal, trade, [('2026-10-07T14:01:00Z', 4999), ('2026-10-07T14:02:00Z', 4997)])   # a chart to draw
    stop = lambda id, ts, state, price: ProtectionEvent('motivewave', 'paper', 'ESZ6', id, ts, 'stop', state, price,
                                                        entry_execution_id='o-d5-in')
    journal.ingest_protection([stop('ord-77', '2026-10-07T14:00:01Z', 'working', 4995),
                               stop('ord-78', '2026-10-07T14:00:02Z', 'working', 4990),
                               stop('ord-78', '2026-10-07T14:00:03Z', 'cancelled', 4990),
                               stop('ord-77', '2026-10-07T14:01:00Z', 'working', 4996),
                               stop('ord-77', '2026-10-07T14:03:00Z', 'filled', 4996)])
    app = open_app()
    orders = next(d.value for d in app.dataframe if 'Status' in d.value.columns)
    assert list(orders.columns) == ['Time', 'Stop', 'Status', 'Price']
    assert list(orders['Stop']) == [1, 2, 2, 1, 1]                       # each order by its number
    assert list(orders['Status']) == ['Working', 'Working', 'Cancelled', 'Working', 'Filled']
    shown = cells(app) + [everything(app)]
    for code in ('ord-77', 'ord-78', 'o-d5-in', 'o-d5-out', trade.id, 'Execution', 'Stop order'):
        assert not any(code in text for text in shown), code
    fills = [d.value for d in app.dataframe if 'Role' in d.value.columns]
    assert len(fills) == 2 and all('Order' not in f.columns and 'Execution' not in f.columns for f in fills)


def test_accounts_are_shown_by_their_name_never_the_provider_code(home):
    capture = seed_preview(home)                         # paper · ESZ6 on Oct 7
    capture.ingest([(Execution('motivewave-native:workspace:live', 'SIM', 'NQZ6', 'buy', 1, 20000, '2026-10-08T14:10:00Z',
                               execution_id='s-in'), {}),
                    (Execution('motivewave-native:workspace:live', 'SIM', 'NQZ6', 'sell', 1, 20010, '2026-10-08T14:12:00Z',
                               execution_id='s-out'), {})])
    journals.account_alias(capture, 'motivewave', 'paper', 'Practice')
    app = open_app()
    menu(app, 'Drafts')
    assert widget(app.selectbox, 'Next-trade source').options == ['SIM · NQZ6', 'Practice · ESZ6']   # newest first
    widget(app.selectbox, 'Next-trade source').set_value(('motivewave', 'paper', 'ESZ6'))
    menu(app, 'Drafts', run=False)
    press(app, 'Arm Draft')
    menu(app, 'Drafts')
    drafts = next(d.value for d in app.dataframe if 'Plan' in d.value.columns)
    assert list(drafts['Account']) == ['Practice'] and list(drafts['Status']) == ['Waiting']
    show(app, 'Journals')
    assert widget(app.multiselect, 'Capture accounts').options == ['Practice', 'SIM']
    assert widget(app.selectbox, 'Account to rename').options == ['Practice', 'SIM']
    assert not any('motivewave-native' in text for text in [*cells(app), everything(app)])


def test_drafts_say_waiting_used_cancelled_or_needs_review_and_name_the_trade(home):
    journal = Journal()
    journal.ingest([fill('a-in', 'buy', 5000, '2026-10-07T14:00:00Z'), fill('a-out', 'sell', 5002, '2026-10-07T14:03:00Z')])
    where = dict(provider='motivewave', account='paper')
    used = journal.save_draft('Opening drive', symbol='ESZ6', start='2026-10-07T15:00:00Z', end='2026-10-07T16:00:00Z', **where)
    journal.save_draft('Later', symbol='ESZ6', start='2026-10-07T20:00:00Z', end='2026-10-07T21:00:00Z', **where)
    for name in ('Twin one', 'Twin two'):                 # two drafts for one trade: neither attaches
        journal.save_draft(name, symbol='NQZ6', start='2026-10-07T15:00:00Z', end='2026-10-07T16:00:00Z', **where)
    dropped = journal.save_draft('Dropped', symbol='ESZ6', start='2026-10-07T22:00:00Z', end='2026-10-07T23:00:00Z', **where)
    journal.cancel_draft(dropped)
    journal.ingest([fill('b-in', 'buy', 5000, '2026-10-07T15:10:00Z'), fill('b-out', 'sell', 5003, '2026-10-07T15:12:00Z'),
                    fill('n-in', 'buy', 20000, '2026-10-07T15:20:00Z', symbol='NQZ6'),
                    fill('n-out', 'sell', 20001, '2026-10-07T15:22:00Z', symbol='NQZ6')])
    journal.reconcile_drafts()
    attached = journal.workspace()['drafts'][used]['trade_id']
    app = open_app()
    menu(app, 'Drafts')
    drafts = next(d.value for d in app.dataframe if 'Plan' in d.value.columns).set_index('Plan')
    assert dict(drafts['Status']) == {'Opening drive': 'Used', 'Later': 'Waiting', 'Twin one': 'Needs review',
                                      'Twin two': 'Needs review', 'Dropped': 'Cancelled'}
    assert drafts.loc['Opening drive', 'Trade'] == 'Oct 7, 11:10 AM · ESZ6 Long 1'
    assert not any(attached in text for text in cells(app))
    assert not any(state in cells(app) for state in ('armed', 'ambiguous', 'attached', 'cancelled'))


def test_check_ins_and_checklist_runs_name_their_trade_instead_of_its_id(home):
    journal = Journal()
    journal.ingest([fill('a-in', 'sell', 5000, '2026-10-07T14:00:00Z', qty=2),
                    fill('a-out', 'buy', 4998, '2026-10-07T14:03:00Z', qty=2)])
    trade = journal.trades()[0]
    journal.record_checkin('Mindset', ['Patient'], trade_id=trade.id)
    journal.record_checkin('Mindset', ['Patient'])
    journal.start_checklist(journal.save_checklist('Plan', checklists.starter()), trade.id)
    app = open_app()
    menu(app, 'Check-Ins')
    checkins = next(d.value for d in app.dataframe if 'Group' in d.value.columns)
    assert sorted(checkins['Trade']) == ['Oct 7, 10:00 AM · ESZ6 Short 2', 'Session']
    show(app, 'Checklists')
    runs = next(d.value for d in app.dataframe if 'Answers' in d.value.columns)
    assert list(runs['Trade']) == ['Oct 7, 10:00 AM · ESZ6 Short 2']
    assert not any(trade.id in text for text in cells(app))


def test_a_stop_typed_with_a_thousands_comma_is_the_same_price(home):
    journal = Journal()
    journal.ingest([fill('a-in', 'buy', 19990, '2026-10-07T14:00:00Z', symbol='NQZ6'),
                    fill('a-out', 'sell', 20010, '2026-10-07T14:01:00Z', symbol='NQZ6')])
    trade, = journal.trades()
    app = open_app()
    widget(app.text_input, 'Stop').set_value('19,980.25').run()
    assert not app.exception and journal.annotations()[trade.id]['planned_stop'] == 19980.25
