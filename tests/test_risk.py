"""A trade's stop and the risk R divides by (mwjournal/risk.py), on invented paper fills only."""
import datetime as dt
import json
import sqlite3

import pytest

import common
import runtime
from mwjournal import Execution, Journal, PriceSample, merging
from mwjournal.chart import trade_chart
from mwjournal.metrics import measurements
from mwjournal.model import ProtectionEvent, instant
from mwjournal.post_exit import best_post_exit
from mwjournal.risk import NOT_REPORTED, STOP_SETTLES, initial_risk, plan, plan_for, settles_at, stop_for_risk
from test_app import display_setting, open_app, open_details, press, said, widget
from test_moved_tools import menu

T0 = '2026-10-07T14:00:00+00:00'
ES = 50


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    monkeypatch.setenv('MWJ_HOME', str(tmp_path))
    yield tmp_path
    runtime.stop_all()


def at(seconds):
    return (instant(T0) + dt.timedelta(seconds=seconds)).isoformat()


def trade(journal, *fills, provider='motivewave', symbol='ESZ6'):
    """The newest trade after these fills: (execution id, side, quantity, price, seconds after T0)."""
    journal.ingest([(Execution(provider, 'paper', symbol, side, qty, price, at(s), execution_id=id, commission=2), {})
                    for id, side, qty, price, s in fills])
    return journal.trades()[-1]


def order(seconds, price, *, state='working', id='stop-1', role='stop', entry='in', provider='motivewave'):
    return ProtectionEvent(provider, 'paper', 'ESZ6', id, at(seconds), role, state, price, entry_execution_id=entry)


def long_one(journal, exit_price=5004, exit_at=60):
    return trade(journal, ('in', 'buy', 1, 5000, 0), ('out', 'sell', 1, exit_price, exit_at))


# ---------------------------------------------------------------- the stop and its risk

def test_without_a_stop_order_the_risk_is_unknown_and_says_why():
    journal = Journal()
    t = long_one(journal)
    result = plan(t, {}, [])
    assert (result.stop, result.source, result.risk) == (None, '', None)
    assert result.why == f'No stop order {STOP_SETTLES} seconds after entry. Type your stop to get R.'


def test_a_motivewave_trade_with_no_stop_reported_says_so_where_r_would_show(home):
    journal = Journal()
    native = 'motivewave-native:workspace:live'
    t = trade(journal, ('in', 'buy', 1, 5000, 0), ('out', 'sell', 1, 5004, 60), provider=native)
    assert plan(t, {}, []).why == NOT_REPORTED == 'No stop order was recorded for this trade. Type your stop to get R.'
    # A stop was reported but was not standing 10 seconds after entry: that is said instead.
    assert plan(t, {}, [order(30, 4996, provider=native)]).why.startswith('No stop order 10 seconds after entry')
    # Typed: R, and nothing to say.
    assert plan(t, {'planned_stop': 4996}, []).why == ''
    prints = [PriceSample('ESZ6', at(20), 'motivewave-ticks', 4998, 4998)]
    journal.ingest_prices(prints)
    for metric in measurements(t, prints, 'motivewave-ticks'):
        journal.record_metric(t, metric)
    app = open_app()
    assert [c.value for c in app.caption].count(NOT_REPORTED) == 1             # by the Stop box
    r = widget(app.metric, 'R')
    assert r.value == '—' and r.help == NOT_REPORTED                           # and on R, where it shows
    display_setting(app, 'MAE / MFE Units', 'r')
    assert widget(app.metric, 'MAE').value == '—'
    assert [c.value for c in app.caption].count(NOT_REPORTED) == 2             # and under MAE / MFE, in R
    open_details(app)
    assert not said(app.info, NOT_REPORTED)                                    # never a third time


def test_a_stop_placed_with_the_entry_gives_the_risk_and_take_profit_plays_no_part():
    journal = Journal()
    t = long_one(journal)
    # An OCO bracket: the stop and the target go in a moment before the entry fills.
    events = [order(-1, 4996), order(-1, 5010, id='target-1', role='target')]
    result = plan(t, {}, events)
    assert (result.stop, result.source, result.order, result.risk) == (4996, 'order', 4996, 4 * 1 * ES)
    assert t.net / result.risk == pytest.approx((4 * ES - 4) / 200)


def test_a_stop_moved_3_seconds_after_entry_counts_and_a_move_30_seconds_after_does_not():
    journal = Journal()
    t = long_one(journal)
    events = [order(0, 4990), order(3, 4996), order(30, 4999)]
    assert plan(t, {}, events).stop == 4996 and plan(t, {}, events).risk == 200
    assert plan(t, {}, [order(0, 4990), order(30, 4999)]).stop == 4990


def test_the_ten_seconds_at_their_edge():
    journal = Journal()
    t = long_one(journal)
    for seconds, stop in [(9.9, 4996), (STOP_SETTLES, 4996), (10.1, 4990)]:
        assert plan(t, {}, [order(0, 4990), order(seconds, 4996)]).stop == stop, seconds


def test_a_cancelled_stop_is_no_stop_but_one_cancelled_later_still_stood():
    journal = Journal()
    t = long_one(journal)
    assert plan(t, {}, [order(0, 4996), order(5, 4996, state='cancelled')]).stop is None
    assert plan(t, {}, [order(0, 4996), order(30, 4996, state='cancelled')]).stop == 4996
    assert plan(t, {}, [order(0, 4996, state='rejected')]).stop is None


def test_a_stop_cancelled_and_replaced_within_the_window_is_the_replacement():
    journal = Journal()
    t = long_one(journal)
    events = [order(0, 4990), order(3, 4990, state='cancelled'), order(3, 4994, id='stop-2'),
              order(30, 4998, id='stop-2')]
    result = plan(t, {}, events)
    assert (result.stop, result.risk) == (4994, 300)
    levels = trade_chart(t, [], 'none', result.stop, events)['overlays']['levels']
    assert [(l['price'], l['style']) for l in levels] == [(4994, 'solid'), (4998, 'dashed')]


def test_every_fill_of_the_entry_within_the_window_counts_and_a_later_add_does_not():
    journal = Journal()
    t = trade(journal, ('a', 'buy', 1, 5000, 0), ('b', 'buy', 1, 5001, 2), ('c', 'buy', 1, 5002, 20),
              ('out', 'sell', 3, 5006, 60))
    result = plan(t, {}, [order(0, 4995, entry='a')])
    # Two contracts at an average of 5000.50 when the stop settled, 5.5 points above the stop.
    assert result.risk == pytest.approx(5.5 * 2 * ES)


def test_short_and_long_need_the_stop_on_the_losing_side():
    journal = Journal()
    short = trade(journal, ('s-in', 'sell', 2, 5000, 0), ('s-out', 'buy', 2, 4995, 60))
    assert plan(short, {}, [order(0, 5003, entry='s-in')]).risk == 3 * 2 * ES
    wrong = plan(short, {}, [order(0, 4998, entry='s-in')])
    assert wrong.stop == 4998 and wrong.risk is None and 'wrong side' in wrong.why
    long = trade(journal, ('l-in', 'buy', 1, 5000, 120), ('l-out', 'sell', 1, 5002, 180))
    assert plan(long, {}, [order(120, 5002, entry='l-in')]).risk is None
    assert plan(long, {'planned_stop': 5000}, []).risk is None      # at the entry: nothing at risk


def test_a_trade_stopped_out_within_the_window_keeps_the_stop_that_took_it_out():
    journal = Journal()
    t = long_one(journal, exit_price=4996, exit_at=4)
    assert settles_at(t) < instant(at(4))
    result = plan(t, {}, [order(0, 4996), order(4, 4996, state='filled')])
    assert result.stop == 4996 and result.risk == 200 and t.net / result.risk == pytest.approx(-1.02)
    # The order can be stamped filled a moment before the exit fill is: it is still the stop that took it out.
    assert plan(t, {}, [order(0, 4996), order(3.8, 4996, state='filled')]).risk == 200


def test_several_stop_orders_at_different_prices_are_not_guessed_between():
    journal = Journal()
    t = long_one(journal)
    result = plan(t, {}, [order(0, 4996), order(0, 4990, id='stop-2')])
    assert result.stop is None and result.risk is None and 'different prices' in result.why
    assert plan(t, {}, [order(0, 4996), order(0, 4996, id='stop-2')]).risk == 200


def test_unknown_results_and_multipliers_have_no_risk():
    journal = Journal()
    custom = trade(journal, ('c-in', 'buy', 1, 100, 0), ('c-out', 'sell', 1, 101, 60), symbol='CUSTOM')
    assert custom.point_value is None
    result = plan(custom, {'planned_stop': 99}, [])
    assert result.risk is None and result.why == "R needs this contract's dollars per point."
    assert initial_risk(custom, 99) is None and stop_for_risk(custom, 100) is None


def test_a_replay_trade_reads_its_stop_order_on_the_replay_clock(tmp_path):
    journal = Journal(tmp_path / 'replay.db', dataset='replay')
    provider = 'motivewave-native:workspace:replay:run-a'
    t = trade(journal, ('in', 'sell', 1, 4800, 0), ('out', 'buy', 1, 4797, 90), provider=provider)
    journal.ingest_protection([order(0, 4806, provider=provider), order(4, 4804, provider=provider),
                               order(40, 4800, provider=provider)])
    result = plan_for(journal, t)
    assert (result.stop, result.source, result.risk) == (4804, 'order', 4 * ES)


# ---------------------------------------------------------------- one risk everywhere

def test_rows_carry_the_stop_risk_and_r_and_a_saved_stop_overrides_until_cleared():
    journal = Journal()
    t = long_one(journal)
    journal.ingest_protection([order(0, 4996), order(60, 5002)])
    row = common.rows_for(journal)[0]
    assert (row['stop'], row['initial_risk']) == (4996, 200) and row['r'] == pytest.approx(196 / 200)
    journal.update_annotation(t.id, {'planned_stop': 4990})
    saved = plan_for(journal, t)
    assert (saved.stop, saved.source, saved.order, saved.risk) == (4990, 'saved', 4996, 500)
    assert common.rows_for(journal)[0]['r'] == pytest.approx(196 / 500)
    journal.update_annotation(t.id, {'planned_stop': None})
    assert plan_for(journal, t).stop == 4996 and common.rows_for(journal)[0]['r'] == pytest.approx(196 / 200)


def test_a_dollar_risk_saved_before_becomes_the_stop_with_the_same_r_once(home):
    journal = Journal()
    two = trade(journal, ('a', 'buy', 1, 5000, 0), ('b', 'buy', 1, 5001, 1), ('out', 'sell', 2, 5006, 60))
    custom = trade(journal, ('c-in', 'buy', 1, 100, 120), ('c-out', 'sell', 1, 101, 180), symbol='CUSTOM')
    journal.ingest_protection([order(0, 4990, entry='a')])
    journal.annotate(two.id, {'notes': 'Kept', 'initial_risk': 175})
    journal.annotate(custom.id, {'initial_risk': 50})
    before = two.net / 175                                     # how 0.4.6 measured R: net over the typed risk
    with sqlite3.connect(journal.path) as db:                  # a journal 0.4.6 left: never converted
        db.execute("DELETE FROM journal_meta WHERE key='stops_from_risks'")
    db.close()
    journal = Journal()
    review = journal.annotations()[two.id]
    assert review['planned_stop'] == pytest.approx(4998.75) and review['initial_risk'] == 175
    assert review['notes'] == 'Kept'
    assert common.rows_for(journal)[0]['r'] == pytest.approx(before)
    assert 'planned_stop' not in journal.annotations()[custom.id]     # no multiplier: left as it was
    # Once: clearing the converted stop later returns to the captured stop order, and stays so.
    journal.update_annotation(two.id, {'planned_stop': None})
    assert plan_for(Journal(), two).stop == 4990


def test_a_take_profit_saved_before_stays_stored_and_plays_no_part(home):
    journal = Journal()
    t = long_one(journal)
    journal.annotate(t.id, {'planned_target': 5010})
    with sqlite3.connect(journal.path) as db:                  # a journal 0.4.6 left: never converted
        db.execute("DELETE FROM journal_meta WHERE key='stops_from_risks'")
    db.close()
    journal = Journal()
    review = journal.annotations()[t.id]
    assert review['planned_target'] == 5010 and 'planned_stop' not in review
    assert common.rows_for(journal)[0]['r'] is None            # as in 0.4.6: nothing says the risk
    journal.ingest_protection([order(0, 4996)])
    assert common.rows_for(journal)[0]['r'] == pytest.approx(196 / 200)
    levels = trade_chart(t, [], 'none', plan_for(journal, t).stop, journal.protection_events(t))['overlays']['levels']
    assert [level['price'] for level in levels] == [4996]


def test_the_trade_list_reads_linked_orders_once_for_all_its_trades(monkeypatch):
    journal = Journal()
    for n in range(3):
        trade(journal, (f'in-{n}', 'buy', 1, 5000, 300 * n), (f'out-{n}', 'sell', 1, 5004, 300 * n + 60))
        journal.ingest_protection([order(300 * n, 4996 - n, id=f'stop-{n}', entry=f'in-{n}')])
    reads = []
    whole = Journal.protection_by_trade
    monkeypatch.setattr(Journal, 'protection_by_trade', lambda self, trades: reads.append(len(trades)) or whole(self, trades))
    monkeypatch.setattr(Journal, 'protection_events', lambda self, trade: pytest.fail('read one trade at a time'))
    rows = common.rows_for(journal)
    assert reads == [3] and [row['initial_risk'] for row in rows] == [200, 250, 300]


def test_a_draft_carries_a_stop_and_an_older_draft_with_a_dollar_risk_still_attaches(home):
    journal = Journal()
    long_one(journal)
    draft = journal.save_draft('Plan', provider='motivewave', account='paper', symbol='ESZ6',
                               start=at(100), end=at(400), stop=4995)
    older = journal.save_draft('Older', provider='motivewave', account='paper', symbol='NQZ6',
                               start=at(100), end=at(400))
    with journal.connect() as db:                              # as 0.4.6 saved it: a dollar risk, no stop
        state = json.loads(db.execute("SELECT value FROM journal_meta WHERE key='workspace'").fetchone()[0])
        state['drafts'][older].pop('stop')
        state['drafts'][older]['risk'] = 100
        db.execute("UPDATE journal_meta SET value=? WHERE key='workspace'", (json.dumps(state),))
    assert journal.workspace()['drafts'][older]['risk'] == 100
    es = trade(journal, ('e-in', 'buy', 1, 5000, 200), ('e-out', 'sell', 1, 5003, 260))
    nq = trade(journal, ('n-in', 'sell', 1, 20000, 210), ('n-out', 'buy', 1, 19990, 270), symbol='NQZ6')
    journal.reconcile_drafts()
    drafts = journal.workspace()['drafts']
    assert drafts[draft]['state'] == drafts[older]['state'] == 'attached'
    assert journal.annotations()[es.id]['planned_stop'] == 4995 and plan_for(journal, es).risk == 250
    assert journal.annotations()[nq.id]['planned_stop'] == 20005 and plan_for(journal, nq).risk == 100


def test_a_merged_trade_risks_what_its_trades_risked_together(home):
    journal = Journal()
    first = long_one(journal)
    second = trade(journal, ('in-2', 'buy', 1, 5001, 300), ('out-2', 'sell', 1, 5003, 360))
    journal.ingest_protection([order(0, 4996), order(300, 4999, id='stop-2', entry='in-2')])
    merged = merging.create(journal, [first.id, second.id])
    view = merging.ReviewJournal(journal)
    row = common.rows_for(view)[0]
    assert row['id'] == merged.id and row['initial_risk'] == 200 + 100
    assert row['r'] == pytest.approx(merged.net / 300)
    journal.update_annotation(second.id, {'planned_stop': 5005})     # the wrong side: that risk is unknown
    assert common.rows_for(merging.ReviewJournal(journal))[0]['r'] is None


def test_after_exit_holds_to_the_stop_r_uses(home):
    journal = Journal()
    t = long_one(journal)
    journal.ingest_protection([order(0, 4996), order(30, 5003)])     # trailed above entry later
    assert plan_for(journal, t).stop == 4996
    journal.ingest_prices([PriceSample('ESZ6', at(60 + s), 'ticks', p, p) for s, p in [(5, 5005), (60, 4997)]])
    result = best_post_exit(journal, t, plan_for(journal, t).stop)
    assert result['stop_price'] == 4996 and result['held_to'] == 'stop'
    assert result['extra_mfe_r'] == pytest.approx(result['extra_mfe'] / plan_for(journal, t).risk)
    journal.update_annotation(t.id, {'planned_stop': 4994})
    assert best_post_exit(journal, t, plan_for(journal, t).stop)['stop_price'] == 4994


def test_the_chart_draws_the_stop_and_never_a_take_profit(home):
    journal = Journal()
    t = long_one(journal)
    journal.ingest_protection([order(0, 4996), order(0, 5010, id='target-1', role='target'), order(30, 4999),
                               order(31, 5008, id='target-1', role='target')])
    events = journal.protection_events(t)
    levels = trade_chart(t, [], 'none', plan_for(journal, t).stop, events)['overlays']['levels']
    assert [(l['price'], l['style'], l['label']) for l in levels] == [(4996, 'solid', 'Stop · R'),
                                                                     (4999, 'dashed', 'Stop order')]
    assert levels[1]['from'] == instant(at(30)).timestamp()


# ---------------------------------------------------------------- the app

def test_notes_has_one_stop_box_filled_from_the_stop_order_and_saying_its_risk(home):
    journal = Journal()
    t = long_one(journal)
    journal.ingest_protection([order(0, 4996)])
    app = open_app()
    stops = [w for w in app.text_input if w.label == 'Stop']
    assert len(stops) == 1 and stops[0].value == '4996'
    assert not any(w.label.startswith(('Initial planned risk', 'Planned take-profit', 'Planned stop-loss'))
                   for w in [*app.text_input, *app.number_input])
    assert said(app.caption, 'From your stop order, 10 seconds after entry · risk \\$200.00')
    assert not any(b.label == 'Save Plan' for b in app.button)              # every box saves by itself
    widget(app.text_area, 'Review notes').set_value('Kept the order.').run()   # another box saved: the stop stays the order's
    assert journal.annotations()[t.id]['notes'] == 'Kept the order.'
    assert 'planned_stop' not in journal.annotations()[t.id]
    widget(app.text_input, 'Stop').set_value('4990').run()
    assert journal.annotations()[t.id]['planned_stop'] == 4990
    assert said(app.caption, 'Your stop · risk \\$500.00. Clear it to use your stop order, 4,996.')
    widget(app.text_input, 'Stop').set_value('').run()
    assert journal.annotations()[t.id]['planned_stop'] is None
    assert widget(app.text_input, 'Stop').value == '4996'


def test_notes_without_a_stop_order_asks_for_one(home):
    journal = Journal()
    long_one(journal)
    app = open_app()
    assert widget(app.text_input, 'Stop').value == ''
    assert said(app.caption, 'No stop order 10 seconds after entry. Type your stop to get R.')


def test_the_drafts_menu_arms_a_draft_with_a_stop_price(home):
    journal = Journal()
    long_one(journal)
    app = open_app()
    menu(app, 'Drafts')
    assert not any(w.label.startswith('Planned total risk') for w in app.number_input)
    widget(app.text_input, 'Stop · optional').set_value('4,995')          # typed as prices are shown
    menu(app, 'Drafts', run=False)
    press(app, 'Arm Draft')
    (draft,) = journal.workspace()['drafts'].values()
    assert draft['stop'] == 4995 and 'risk' not in draft


def test_stats_average_r_comes_from_the_stop(home):
    journal = Journal()
    long_one(journal)
    journal.ingest_protection([order(0, 4996)])
    app = open_app('Edge')
    average = widget(app.metric, 'Average R')
    assert average.value == '+0.98R' and 'Across 1 trades with a stop.' in average.proto.help


def test_mae_and_mfe_in_r_divide_by_the_risk_to_the_stop_order(home):
    journal = Journal()
    t = long_one(journal)
    journal.ingest_protection([order(0, 4996)])
    ticks = [PriceSample('ESZ6', at(20), 'test-prices', 4998, 4998), PriceSample('ESZ6', at(40), 'test-prices', 5006, 5006)]
    journal.ingest_prices(ticks)
    for metric in measurements(t, ticks, 'test-prices'):
        journal.record_metric(t, metric)
    app = open_app()
    display_setting(app, 'MAE / MFE Units', 'r')
    # $100 against and $300 for the trade, over the $200 its stop order risked.
    assert widget(app.metric, 'MAE').value == '0.50R' and widget(app.metric, 'MFE').value == '1.50R'
    assert common.rows_for(journal)[0]['mae_r'] == pytest.approx(.5)


def test_a_trade_in_and_out_at_the_same_moment_keeps_its_stop_and_says_why_it_has_no_path(home):
    journal = Journal()
    t = trade(journal, ('in', 'buy', 1, 5000, 0), ('out', 'sell', 1, 4996, 0))
    assert settles_at(t) == instant(at(0))                       # never before the first fill
    assert plan(t, {}, [order(0, 4996)]).risk == 200
    assert common.no_excursions(journal, t) == 'Entered and exited at the same moment: there is no price path to measure.'


def test_an_open_trade_has_no_r_until_it_is_closed(home):
    journal = Journal()
    trade(journal, ('in', 'buy', 1, 5000, 0))
    journal.ingest_protection([order(0, 4996)])
    row, = common.rows_for(journal)
    assert row['status'] == 'open' and row['r'] is None          # its net so far is only the commission
