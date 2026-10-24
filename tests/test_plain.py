"""What a trader reads and types on Trades: every review box saves by itself, an open trade has one P&L, a
trade without MAE / MFE says why, and the screen uses words instead of codes and ids. Then, for every page, one
word for each thing and a ? of one short sentence. Disposable journals in a temporary data home, under the
production tick-only default."""
import ast
import datetime as dt
import re
from pathlib import Path

import pytest

import runtime
from mwjournal import Execution, Journal, PriceSample, journals, tagging, units
from mwjournal.metrics import measurements
from mwjournal.model import ProtectionEvent, instant
from mwjournal.workspace import question
from test_app import chosen, details, listed, open_app, open_details, pick, press, said, show, widget
from test_chart import seed_preview
from test_moved_tools import menu

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
    widget(app.text_area, 'Review notes').set_value('Waited for the retest.')
    press(app, 'Next →')                                  # no box was left first: the click carries what was typed
    assert chosen(app) == older.id
    review = journal.annotations()[newer.id]
    assert (review['planned_stop'], review['notes']) == (19990, 'Waited for the retest.')
    widget(app.text_area, 'Review notes').set_value('Entered early.')
    pick(app, newer.id)                                  # a row click keeps it too
    assert journal.annotations()[older.id]['notes'] == 'Entered early.'
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
    widget(app.text_area, 'Review notes').set_value('Kept the order.')
    press(app, 'Next →')
    assert journal.annotations()[second.id]['notes'] == 'Kept the order.'
    assert 'planned_stop' not in journal.annotations()[second.id]          # still the order's stop
    assert chosen(app) == first.id


def test_the_daily_report_card_keeps_what_was_typed_when_another_day_or_a_trade_is_clicked(home):
    journal = seed_preview(home)
    journal.ingest([fill('n-in', 'buy', 20000, '2026-10-08T14:10:00Z', symbol='NQZ6'),
                    fill('n-out', 'sell', 20010, '2026-10-08T14:12:00Z', symbol='NQZ6')])
    app = open_app()
    app.button(key='open-day-2026-10-07-live').click().run()
    assert not any(b.label == 'Save Daily Review' for b in app.button)
    widget(app.pills, 'Rating').set_value(2)
    widget(app.text_area, 'What I learned').set_value('Too early on the first entry.')
    app.button(key='open-day-2026-10-08-live').click().run()             # another day, straight away
    assert widget(app.date_input, 'Trading day').value == dt.date(2026, 10, 8)
    oct7 = journal.get('sessions')['2026-10-07']
    assert oct7['rating'] == 2 and oct7['notes'] == 'Too early on the first entry.'
    assert widget(app.text_area, 'What I learned').value == ''             # each day its own card
    assert widget(app.pills, 'Rating').value is None
    widget(app.text_area, 'What I learned').set_value('Patient.')
    pick(app, listed(app)[0])                                             # a trade, straight away
    assert journal.get('sessions')['2026-10-08']['notes'] == 'Patient.'
    app.button(key='open-day-2026-10-07-live').click().run()
    assert widget(app.text_area, 'What I learned').value == 'Too early on the first entry.'
    assert widget(app.pills, 'Rating').value == 2


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
def test_a_trade_without_mae_mfe_says_the_real_cause_once_under_its_numbers(home, case):
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
    app = open_details(open_app())
    captions, infos = causes(app)
    assert captions.count(expected) == 1 and expected not in infos        # under the trade's numbers, once
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


@pytest.mark.parametrize('source,said_', [(TICKS, None), ('motivewave-ticks:history', "From MotiveWave's tick history"),
                                         ('my-prices', 'From your imported prices')])
def test_where_the_prices_came_from_is_said_once_and_only_when_they_were_not_recorded_live(home, source, said_):
    journal = Journal()
    journal.ingest([fill('t-in', 'buy', 5000, '2026-10-07T14:00:00Z'), fill('t-out', 'sell', 5002, '2026-10-07T14:03:00Z')])
    trade = journal.trades()[0]
    journal.ingest_prices([PriceSample('ESZ6', '2026-10-07T14:01:00Z', source, 4999, 4999)])
    for metric in measurements(trade, journal.prices('ESZ6', source), source):
        journal.record_metric(trade, metric)
    app = open_app()
    # The trade's line under its title: when, and nothing more when its ticks were recorded live.
    header = next(c.value for c in app.main.caption if c.value.startswith('Oct 7'))
    assert header.endswith(' · ' + said_) if said_ else ' · From ' not in header
    assert [c.value for c in app.main.caption].count(said_) == 0           # said in that line only, never alone
    for name in ('MAE', 'MFE'):
        assert 'Measured from' not in widget(app.metric, name).help and 'Observed ticks' not in everything(app)


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
    app = open_details(open_app())
    orders = next(d.value for d in details(app).dataframe if 'Status' in d.value.columns)
    assert list(orders.columns) == ['Time', 'Stop', 'Status', 'Price']
    assert list(orders['Stop']) == [1, 2, 2, 1, 1]                       # each order by its number
    assert list(orders['Status']) == ['Working', 'Working', 'Cancelled', 'Working', 'Filled']
    shown = cells(app) + [everything(app)]
    for code in ('ord-77', 'ord-78', 'o-d5-in', 'o-d5-out', trade.id, 'Execution', 'Stop order'):
        assert not any(code in text for text in shown), code
    fills = [d.value for d in app.dataframe if 'Role' in d.value.columns]                 # listed once
    assert [list(f.columns) for f in fills] == [['Time', 'Role', 'Side', 'Contracts', 'Price', 'Fee']]


def test_accounts_are_shown_by_their_name_never_the_provider_code(home):
    capture = seed_preview(home)                         # paper · ESZ6 on Oct 7
    capture.ingest([(Execution('motivewave-native:workspace:live', 'SIM', 'NQZ6', 'buy', 1, 20000, '2026-10-08T14:10:00Z',
                               execution_id='s-in'), {}),
                    (Execution('motivewave-native:workspace:live', 'SIM', 'NQZ6', 'sell', 1, 20010, '2026-10-08T14:12:00Z',
                               execution_id='s-out'), {})])
    journals.account_alias(capture, 'motivewave', 'paper', 'Practice')
    app = open_app()
    menu(app)
    assert widget(app.selectbox, 'Contract').options == ['SIM · NQZ6', 'Practice · ESZ6']   # newest first
    widget(app.selectbox, 'Contract').set_value(('motivewave', 'paper', 'ESZ6'))
    widget(app.text_area, 'Note').set_value('Wait for the pullback.')
    menu(app, run=False)
    press(app, 'Tag My Next Trade')
    menu(app)
    drafts = next(d.value for d in app.dataframe if 'Contract' in d.value.columns)
    assert list(drafts['Contract']) == ['Practice · ESZ6'] and list(drafts['Status']) == ['Waiting']
    show(app, 'Journals')
    assert widget(app.multiselect, 'Accounts').options == ['Practice', 'SIM']
    assert widget(app.selectbox, 'Account to rename').options == ['Practice', 'SIM']
    assert not any('motivewave-native' in text for text in [*cells(app), everything(app)])


def test_tags_for_the_next_trade_say_waiting_used_cancelled_or_needs_review_and_name_the_trade(home):
    journal = Journal()
    journal.ingest([fill('a-in', 'buy', 5000, '2026-10-07T14:00:00Z'), fill('a-out', 'sell', 5002, '2026-10-07T14:03:00Z')])
    where = dict(provider='motivewave', account='paper')
    library = journal.tag_library()
    journal.save_draft(symbol='ESZ6', tags=['FOMO'], **where)                 # replaced by the next: Cancelled
    used = journal.save_draft(symbol='ESZ6', tags=['Patient'], stop=4995, **where)
    journal.save_draft(symbol='NQZ6', tags=['Calm'], **where)
    journal.edit_tag(next(iter(tagging.ids(library, ['Calm']))), retired=True)  # retired since: Needs review
    journal.ingest([fill('b-in', 'buy', 5000, '2026-10-07T15:10:00Z'), fill('b-out', 'sell', 5003, '2026-10-07T15:12:00Z'),
                    fill('n-in', 'buy', 20000, '2026-10-07T15:20:00Z', symbol='NQZ6'),
                    fill('n-out', 'sell', 20001, '2026-10-07T15:22:00Z', symbol='NQZ6')])
    journal.reconcile_drafts()
    journal.save_draft(symbol='ESZ6', notes='Only A setups', **where)          # nothing yet: Waiting
    attached = journal.workspace()['drafts'][used]['trade_id']
    app = open_app()
    menu(app)
    drafts = next(d.value for d in app.dataframe if 'Contract' in d.value.columns)
    assert list(drafts.columns) == ['Contract', 'Tags', 'Stop', 'Status', 'Trade']
    assert [tuple(row) for row in drafts[['Contract', 'Tags', 'Status', 'Trade']].to_numpy()] == [   # the latest first
        ('paper · ESZ6', '', 'Waiting', ''),
        ('paper · NQZ6', 'Calm', 'Needs review', 'Oct 7, 11:20 AM · NQZ6 Long 1'),
        ('paper · ESZ6', 'Patient', 'Used', 'Oct 7, 11:10 AM · ESZ6 Long 1'),
        ('paper · ESZ6', 'FOMO', 'Cancelled', '')]
    assert widget(app.selectbox, 'Tags to cancel').options == ['paper · ESZ6 · Waiting',
                                                               'paper · NQZ6 · Needs review · Calm']
    assert not any(attached in text for text in cells(app))
    assert not any(state in cells(app) for state in ('armed', 'ambiguous', 'attached', 'cancelled'))
    session = next(p for p in app.main.get('popover') if p.proto.popover.label == 'Session')
    words = ' '.join([*(e.value for kind in ('markdown', 'caption') for e in getattr(session, kind)),
                      *(w.label + ' ' + (w.help or '') for kind in ('button', 'selectbox', 'text_input', 'text_area')
                        for w in getattr(session, kind))])
    assert 'Tag My Next Trade' in words and not any(word in words.lower() for word in ('window', 'draft'))
    menu(app, run=False)
    press(app, 'Cancel Tags')
    assert [d['state'] for d in journal.workspace()['drafts'].values()].count('armed') == 0


def test_check_ins_name_their_trade_and_checklist_settings_show_no_ids(home):
    journal = Journal()
    journal.ingest([fill('a-in', 'sell', 5000, '2026-10-07T14:00:00Z', qty=2),
                    fill('a-out', 'buy', 4998, '2026-10-07T14:03:00Z', qty=2)])
    trade = journal.trades()[0]
    journal.record_checkin('Mindset', ['Patient'], trade_id=trade.id)
    journal.record_checkin('Mindset', ['Patient'])
    definition = journal.save_checklist('Plan', [question('Patient?', yes={'go': 'finish', 'tag_ids': ['Patient']})])
    journal.start_checklist(definition, trade.id)
    app = open_app()
    menu(app)
    checkins = next(d.value for d in app.dataframe if 'Category' in d.value.columns)
    assert sorted(checkins['Trade']) == ['Oct 7, 10:00 AM · ESZ6 Short 2', 'Session']
    show(app, 'Checklists')
    assert not app.dataframe                                    # no run history: each run is on its trade or day
    assert said(app.caption, 'Yes collects Patient')            # a tag by its name
    shown = [*cells(app), *(w.label for w in app.text_input), *(s.label for s in app.selectbox)]
    assert not any(id in text for text in shown for id in (trade.id, definition, tagging.identity('Patient')))


def test_a_stop_typed_with_a_thousands_comma_is_the_same_price(home):
    journal = Journal()
    journal.ingest([fill('a-in', 'buy', 19990, '2026-10-07T14:00:00Z', symbol='NQZ6'),
                    fill('a-out', 'sell', 20010, '2026-10-07T14:01:00Z', symbol='NQZ6')])
    trade, = journal.trades()
    app = open_app()
    widget(app.text_input, 'Stop').set_value('19,980.25').run()
    assert not app.exception and journal.annotations()[trade.id]['planned_stop'] == 19980.25


def test_check_this_combination_says_which_earlier_trades_it_counts(home):
    """Only trades closed before this entry count: one entered earlier but still open at this entry does not."""
    journal = Journal()
    journal.ingest([fill('a-in', 'buy', 5000, '2026-10-07T14:00:00Z'), fill('a-out', 'sell', 5002, '2026-10-07T14:05:00Z'),
                    fill('b-in', 'buy', 20000, '2026-10-07T14:10:00Z', symbol='NQZ6'),
                    fill('b-out', 'sell', 20010, '2026-10-07T14:40:00Z', symbol='NQZ6'),
                    fill('c-in', 'buy', 5000, '2026-10-07T14:20:00Z'), fill('c-out', 'sell', 5001, '2026-10-07T14:25:00Z')])
    for trade in journal.trades():
        journal.annotate(trade.id, {'tags': ['Breakout']})
    this = next(t for t in journal.trades() if t.opened.startswith('2026-10-07T14:20'))
    app = pick(open_app(), this.id)
    assert said(app.markdown, r'1 trade closed before this entry · Win Rate 100.0% · Average Trade +\$96.00')


# ---------------------------------------------------------------- one word for each thing, and a short ?

APP = Path(__file__).resolve().parents[1] / 'app'
PAGES = sorted([*APP.glob('*.py'), *(APP / 'views').glob('*.py')])
# Words the screen no longer uses: record, Contracts, Minutes held, Win Rate, Category, Rating and Setup tags
# took their place; so did Trades with a stop, further your way, the path's Best and Worst move, the study's file
# (or a file the study wrote) and After Exit's Compare.
RETIRED = ('captur', 'Peak size', 'Qty', 'Quantity', 'Duration', 'Win %', 'tag group', 'Process rating', 'Setup →',
           '(Setup)', 'Trades With Risk', 'extra peak', 'Upper bound', 'Lower bound', 'Bridge File', 'Compare Runners')
LONGEST_HELP = 25          # words in a ?, which says in one sentence what its control does


def modules():
    """(file name, syntax tree, module-level constants) of every page and shared module of the app."""
    for path in PAGES:
        tree = ast.parse(path.read_text(encoding='utf-8'))
        constants = {target.id: node.value for node in tree.body if isinstance(node, ast.Assign)
                     for target in node.targets if isinstance(target, ast.Name)}
        yield path.relative_to(APP.parent).as_posix(), tree, constants


def written(tree) -> list[str]:
    """Every piece of text a module writes, but its docstrings: labels, helps, captions, notes and warnings, and
    its text constants. A lower-case word with no space is a key, such as the filter rules' 'quantity' and
    'duration', which stay as they are stored."""
    docstrings = {id(node.body[0].value) for node in ast.walk(tree)
                  if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
                  and node.body and isinstance(node.body[0], ast.Expr) and isinstance(node.body[0].value, ast.Constant)}
    return [node.value for node in ast.walk(tree) if isinstance(node, ast.Constant) and isinstance(node.value, str)
            and id(node) not in docstrings and not re.fullmatch(r'[a-z0-9_.:\-]+', node.value)]


def standing_for(node, constants) -> list:
    """The expressions `node` can be, through the module's constants: a dict of them by any key or .get(), one by
    its key, an element of a (line, help) pair by its index, either side of a choice, and md()'s text."""
    if isinstance(node, ast.Name):
        return standing_for(constants[node.id], constants) if node.id in constants else []
    if isinstance(node, ast.IfExp):
        return standing_for(node.body, constants) + standing_for(node.orelse, constants)
    if isinstance(node, ast.Call) and isinstance(node.func, (ast.Name, ast.Attribute)):
        name = node.func.id if isinstance(node.func, ast.Name) else node.func.attr
        if name == 'md' and node.args:
            return standing_for(node.args[0], constants)
        if name == 'get' and isinstance(node.func, ast.Attribute):
            return [found for table in standing_for(node.func.value, constants) if isinstance(table, ast.Dict)
                    for value in table.values for found in standing_for(value, constants)]
        return []
    if isinstance(node, ast.Subscript):
        key = node.slice.value if isinstance(node.slice, ast.Constant) else None
        found = []
        for base in standing_for(node.value, constants):
            if isinstance(base, ast.Dict):
                found += [value for name, value in zip(base.keys, base.values)
                          if key is None or isinstance(name, ast.Constant) and name.value == key]
            elif isinstance(base, (ast.Tuple, ast.List)) and isinstance(key, int):
                found.append(base.elts[key])
        return [each for value in found for each in standing_for(value, constants)]
    return [node]


def wording(node, constants) -> list[str]:
    """Every text an expression can be when it is a literal or a module constant, each value of an f-string
    standing for one word; nothing when any part of it is worked out as the page runs."""
    found = []
    for each in standing_for(node, constants):
        if isinstance(each, ast.Constant) and isinstance(each.value, str):
            found.append(each.value)
        elif isinstance(each, ast.JoinedStr):
            found.append(''.join(part.value if isinstance(part, ast.Constant) else 'X' for part in each.values))
        elif isinstance(each, ast.BinOp) and isinstance(each.op, ast.Add):
            found += [left + right for left in wording(each.left, constants) for right in wording(each.right, constants)]
    return found


def helps():
    """(where, text) of every ? the app writes as a literal or a module constant."""
    for name, tree, constants in modules():
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                for keyword in node.keywords:
                    if keyword.arg == 'help':
                        for text in wording(keyword.value, constants):
                            yield f'{name}:{keyword.value.lineno}', text


def too_long(text) -> bool:
    words = [word for word in text.split() if any(c.isalnum() for c in word)]
    sentences = [s for s in re.split(r'(?<=[.!?])\s+', text.strip()) if s]
    return len(words) > LONGEST_HELP or len(sentences) > 1


def test_the_screen_uses_one_word_for_each_thing():
    said_ = {name: written(tree) for name, tree, _ in modules()}
    assert len(said_) > 15 and sum(map(len, said_.values())) > 500             # every page was read
    found = [(name, word, text) for name, texts in said_.items() for text in texts for word in RETIRED
             if word.casefold() in text.casefold()]
    assert found == []


def test_every_question_mark_is_one_sentence_of_at_most_25_words():
    shown = list(helps())
    assert len(shown) > 100                                                    # every ? the app writes was read
    assert any(text == 'Opens the newer trade above in the list.' for _, text in shown)     # a literal
    assert any(text.startswith('One zip of Live and Replay') for _, text in shown)          # a dict's, by its key
    assert any(text.startswith('Copies the study into') for _, text in shown)               # a (line, ?) pair's
    assert [(where, text) for where, text in shown if too_long(text)] == []
    # The ? beside MAE and MFE is worked out as the page runs: one sentence in each unit.
    assert [unit for unit in units.UNITS if too_long(units.description(unit))] == []
