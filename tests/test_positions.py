"""Trades built from the platform's own position ids, on invented fills that repeat the patterns of
a platform which only kept part of an account's history. No real account, fill or price is used."""
import json

import pytest

from mwjournal import Execution, Journal, PriceSample
from mwjournal.analytics import cost_summary, grouped, summary
from mwjournal.chart import trade_chart
from mwjournal.importers import apply_preview, preview_bridge, preview_csv
from mwjournal.matching import match
from mwjournal.metrics import excursions, measurements
from mwjournal.model import ENTRY_MISSING, EXIT_MISSING, INCOMPLETE, NO_FILL_PRICE, digest, instant
from mwjournal.post_exit import best_post_exit, post_exit
from mwjournal.store import ImportConflict
from mwjournal.workspace import question

PROVIDER = 'motivewave-native:workspace:live'
SCOPE = (PROVIDER, 'SIM-1', 'NQZ6', 'USD')
PLAIN = 'MotiveWave internal capture; fees may be estimated'


def native(id, side, qty, price, ts, position, entry, *, symbol='NQZ6', fee=0, **more):
    """One fill as the study writes it: the platform's position id and entry flag beside the usual fields."""
    return {'version': 1, 'type': 'execution', 'mode': 'live', 'provider': PROVIDER, 'account': 'SIM-1',
            'symbol': symbol, 'side': side, 'qty': qty, 'price': price, 'ts': ts, 'execution_id': id,
            'order_id': 'order-' + id, 'native_entry': entry, 'native_position_id': position,
            'commission': fee, 'commission_known': False, 'currency': 'USD', 'point_value': 20,
            'quality': PLAIN, 'observed_at': '2026-10-08T09:00:00Z', 'capture_kind': 'retained', **more}


def history():
    """Seven positions of one account in one contract, as a platform with a partial history hands them over."""
    return [
        # The entry of this long had already left the platform's history.
        native('x0', 'sell', 1, 20100, '2026-09-01T14:00:00Z', 'p0', False),
        # Twenty days later, an ordinary round trip: +10 points.
        native('a1', 'buy', 1, 20000, '2026-09-21T14:00:00Z', 'p1', True),
        native('a2', 'sell', 1, 20010, '2026-09-21T14:05:00Z', 'p1', False),
        # A short covered in two parts: +10 and +20 points.
        native('b1', 'sell', 2, 20050, '2026-09-22T14:00:00Z', 'p2', True),
        native('b2', 'buy', 1, 20040, '2026-09-22T14:02:00Z', 'p2', False),
        native('b3', 'buy', 1, 20030, '2026-09-22T14:04:00Z', 'p2', False),
        # The platform stored no price for this entry.
        native('c1', 'buy', 1, 0, '2026-09-23T14:00:00Z', 'p3', True),
        native('c2', 'sell', 1, 20200, '2026-09-23T14:03:00Z', 'p3', False),
        # Three contracts bought and never seen sold, although another position follows.
        native('d1', 'buy', 3, 20300, '2026-09-24T14:00:00Z', 'p4', True),
        native('e1', 'buy', 1, 20400, '2026-09-25T14:00:00Z', 'p5', True),
        native('e2', 'sell', 1, 20390, '2026-09-25T14:01:00Z', 'p5', False),
        # The newest position, still open.
        native('f1', 'sell', 1, 20500, '2026-09-26T14:00:00Z', 'p6', True),
    ]


def lines(events):
    return ''.join(json.dumps(event) + '\n' for event in events)


def fills(events):
    parsed = preview_bridge(lines(events), dataset='live')
    assert not parsed.errors
    return [fill for fill, _ in parsed.fills]


def unlabelled(events):
    """The same fills as a writer without position ids sends them, which is also how 0.3.0 read them."""
    return [{k: v for k, v in event.items() if k not in ('native_position_id', 'native_entry')} for event in events]


def hours(trade):
    return (instant(trade.closed) - instant(trade.opened)).total_seconds() / 3600


def test_partial_history_made_wrong_trades_without_position_ids_and_makes_none_with_them():
    old = match(fills(unlabelled(history())))
    # The lone exit opens a short that the next position's entry closes twenty days later, and
    # every later fill is paired with the wrong partner, down to a cover at a price of zero.
    assert old[0].direction == 'short' and hours(old[0]) == 480 and old[0].net == 2000
    assert [[f['execution_id'] for f in t.fills] for t in old] == [
        ['x0', 'a1'], ['a2', 'b1', 'b2', 'b3', 'c1'], ['c2', 'd1'], ['d1', 'e1', 'e2', 'f1']]
    assert old[1].incomplete == [NO_FILL_PRICE] and old[1].net is None      # 20,037 points of it were that zero
    trades = match(fills(history()))
    assert [(t.direction, t.status) for t in trades] == [
        ('long', 'incomplete'), ('long', 'closed'), ('short', 'closed'), ('long', 'incomplete'),
        ('long', 'incomplete'), ('long', 'closed'), ('short', 'open')]
    assert [t.incomplete for t in trades] == [[ENTRY_MISSING], [], [], [NO_FILL_PRICE], [EXIT_MISSING], [], []]
    assert [t.net for t in trades] == [None, 200, 600, None, None, -200, 0]     # the open one has realised nothing yet
    assert max(hours(t) for t in trades if t.complete and t.closed) < 1
    assert [[f['execution_id'] for f in t.fills] for t in trades] == [
        ['x0'], ['a1', 'a2'], ['b1', 'b2', 'b3'], ['c1', 'c2'], ['d1'], ['e1', 'e2'], ['f1']]


def test_incomplete_trades_keep_fills_and_times_and_report_every_result_as_unknown():
    lone_exit, _, _, unpriced, lone_entry, _, still_open = match(fills(history()))
    for trade in (lone_exit, unpriced, lone_entry):
        assert not trade.complete and trade.incomplete[0] in INCOMPLETE and trade.incomplete[0] in trade.flags
        assert trade.gross is None and trade.net is None and trade.realised_points is None and trade.average_cost is None
        row = trade.row()
        assert row['status'] == 'incomplete' and row['complete'] is False and row['incomplete'] == trade.incomplete
        assert row['net'] is None and row['gross'] is None and row['realised_points'] is None
        json.dumps(row, allow_nan=False)                    # exportable as it is
    # What the kept fills do say stays readable: the side that is there, and when.
    assert (lone_exit.entry_qty, lone_exit.exit_qty, lone_exit.entry_price, lone_exit.exit_price) == (0, 1, None, 20100)
    assert lone_exit.opened == lone_exit.closed == '2026-09-01T14:00:00+00:00' and lone_exit.fills[0]['role'] == 'exit'
    assert (lone_entry.entry_price, lone_entry.exit_price, lone_entry.open_qty, lone_entry.max_qty) == (20300, None, 3, 3)
    assert lone_entry.closed == '2026-09-24T14:00:00+00:00'     # its last kept fill, not an exit
    assert (unpriced.entry_price, unpriced.exit_price, unpriced.entry_value) == (None, 20200, None)
    assert unpriced.opened == '2026-09-23T14:00:00+00:00' and unpriced.closed == '2026-09-23T14:03:00+00:00'
    assert still_open.complete and still_open.status == 'open' and still_open.closed is None and still_open.open_qty == 1


def test_statistics_and_cost_attribution_count_only_complete_closed_trades():
    rows = [{**t.row(), 'tags': ['A+']} for t in match(fills(history()))]
    stats = summary(rows)
    assert stats['count'] == 3 and stats['net'] == 600 and stats['drawdown'] == 200
    assert stats['win_rate'] == pytest.approx(200 / 3) and stats['profit_factor'] == 4
    assert grouped(rows, 'tags')[0]['count'] == 3
    # The worst Net P&L first: the longs, +10 and -10 points, before the short's +30.
    assert [(g['group'], g['count']) for g in grouped(rows, 'direction')] == [('long', 2), ('short', 1)]
    costs = cost_summary(rows)
    assert costs['count'] == 3 and costs['gross'] == 600 and costs['fees'] == 0


def test_newest_open_position_becomes_exit_missing_only_when_another_position_follows():
    events = history()
    assert match(fills(events))[-1].status == 'open'
    later = native('g1', 'buy', 1, 20600, '2026-09-27T14:00:00Z', 'p7', True)
    *_, was_open, newest = match(fills(events + [later]))
    assert was_open.incomplete == [EXIT_MISSING] and was_open.net is None and was_open.closed == '2026-09-26T14:00:00+00:00'
    assert newest.status == 'open' and newest.complete
    # Another contract, another account or another currency says nothing about this position.
    for elsewhere in ({'symbol': 'NQH7'}, {'account': 'SIM-2'}, {'currency': 'EUR'}):
        assert match(fills(events + [{**later, **elsewhere}]))[-2].status == 'open'
    # Its own exit, arriving late, makes it an ordinary closed trade again.
    exit = native('f2', 'buy', 1, 20480, '2026-09-26T14:30:00Z', 'p6', False)
    closed = match(fills(events + [later, exit]))[-2]
    assert closed.complete and closed.net == 400 and closed.closed == '2026-09-26T14:30:00+00:00'


def test_position_ids_use_the_same_accounting_scale_ins_partial_exits_and_fees():
    events = [native('1', 'buy', 2, 100, '2026-10-07T14:00:00Z', 'p', True, fee=2),
              native('2', 'sell', 1, 102, '2026-10-07T14:01:00Z', 'p', False, fee=1),
              native('3', 'buy', 1, 105, '2026-10-07T14:02:00Z', 'p', True, fee=1),
              native('4', 'sell', 2, 103, '2026-10-07T14:03:00Z', 'p', False, fee=2)]
    trade, = match(fills(events))
    assert trade.complete and trade.closed and trade.open_qty == 0 and trade.entry_qty == 3 and trade.max_qty == 2
    assert trade.gross == pytest.approx(60) and trade.net == pytest.approx(54)       # 3 points at 20 a point
    assert trade.entry_price == pytest.approx(305 / 3)
    assert [f['role'] for f in trade.fills] == ['entry', 'exit', 'entry', 'exit']
    assert trade.fills[0]['native_position_id'] == 'p' and trade.fills[1]['native_entry'] is False
    plain, = match(fills(unlabelled(events)))
    assert (plain.id, plain.gross, plain.net, plain.basis) == (trade.id, trade.gross, trade.net, trade.basis)


def test_interleaved_position_ids_are_netted_apart_and_fills_without_one_stay_flat_to_flat():
    # Two position ids whose fills alternate: flat-to-flat pairing would make one four-lot trade.
    events = [native('1', 'buy', 1, 100, '2026-10-07T14:00:00Z', 'p', True),
              native('2', 'buy', 3, 101, '2026-10-07T14:01:00Z', 'q', True),
              native('3', 'sell', 1, 104, '2026-10-07T14:02:00Z', 'p', False),
              native('4', 'sell', 3, 102, '2026-10-07T14:03:00Z', 'q', False)]
    first, second = match(fills(events))
    assert (first.max_qty, first.gross, second.max_qty, second.gross) == (1, 80, 3, 60)
    assert len(match(fills(unlabelled(events)))) == 1
    # A writer that sends no position id is read exactly as before, even beside one that does.
    loose = unlabelled([native('5', 'sell', 2, 110, '2026-10-07T14:04:00Z', '', None),
                        native('6', 'buy', 2, 108, '2026-10-07T14:05:00Z', '', None)])
    *_, short = match(fills(events + loose))
    assert short.direction == 'short' and short.gross == 80 and short.complete
    # An entry flag without a position id is not read at all: this lone "exit" opens a short as it always did.
    lone, = match(fills([native('7', 'sell', 1, 110, '2026-10-07T14:06:00Z', '', False)]))
    assert lone.direction == 'short' and lone.complete and lone.status == 'open'


def test_entry_and_exit_of_one_instant_pair_whichever_the_platform_lists_first():
    stamp = '2026-10-07T14:00:00.250Z'
    entry, exit = native('in', 'sell', 2, 105, stamp, 'p', True), native('out', 'buy', 2, 103, stamp, 'p', False)
    for listed in ([entry, exit], [exit, entry]):
        trade, = match(fills(listed))
        assert trade.complete and trade.direction == 'short' and trade.gross == 80
        assert [f['execution_id'] for f in trade.fills] == ['in', 'out']
    assert len({match(fills(listed))[0].id for listed in ([entry, exit], [exit, entry])}) == 1
    # Without the platform's flag there is nothing to order by: ingestion order decides, as it always did.
    assert [t.direction for t in (match(fills(unlabelled(listed)))[0] for listed in ([entry, exit], [exit, entry]))] == [
        'short', 'long']


def test_reversal_inside_one_position_id_is_split_as_before():
    for flag in (None, True):          # the writer does not say, or calls the reversing fill an entry
        events = [native('a', 'buy', 1, 100, '2026-10-07T14:00:00Z', 'p', True, fee=2),
                  native('b', 'sell', 3, 105, '2026-10-07T14:01:00Z', 'p', flag, fee=6),
                  native('c', 'buy', 2, 101, '2026-10-07T14:02:00Z', 'p', False, fee=4)]
        long, short = match(fills(events))
        assert [t.direction for t in (long, short)] == ['long', 'short'] and long.complete and short.complete
        assert (long.gross, short.gross, short.entry_qty, long.commission + short.commission) == (100, 160, 2, 12)
        plain = match(fills(unlabelled(events)))
        assert [t.id for t in plain] == [long.id, short.id]


def test_exit_that_takes_out_more_than_the_kept_fills_opened_is_not_turned_into_a_reversal():
    # Two contracts were bought; the platform kept only the second buy. Its own flag says the sell
    # closed a position, so the extra contract is a missing entry, never a new short.
    events = [native('b', 'buy', 1, 100, '2026-10-07T14:00:00Z', 'p', True),
              native('s', 'sell', 2, 105, '2026-10-07T14:01:00Z', 'p', False),
              native('n1', 'buy', 1, 106, '2026-10-07T15:00:00Z', 'q', True),
              native('n2', 'sell', 1, 107, '2026-10-07T15:01:00Z', 'q', False)]
    partial, later = match(fills(events))
    assert partial.incomplete == [ENTRY_MISSING] and partial.net is None and partial.direction == 'long'
    assert (partial.entry_qty, partial.exit_qty, partial.max_qty, partial.open_qty) == (1, 2, 2, 0)
    assert [f['qty'] for f in partial.fills] == [1, 2] and partial.closed == '2026-10-07T14:01:00+00:00'
    assert later.complete and later.gross == 20
    # The same exit sent as two fills says the same thing: the second arrives with nothing left to close.
    halves = [events[0], native('s1', 'sell', 1, 105, '2026-10-07T14:01:00Z', 'p', False),
              native('s2', 'sell', 1, 105, '2026-10-07T14:01:30Z', 'p', False)]
    one, = match(fills(halves))
    assert one.id == partial.id and one.incomplete == [ENTRY_MISSING] and len(one.fills) == 3 and one.net is None
    assert one.closed == '2026-10-07T14:01:30+00:00'
    # And so does a fill the platform flags against what it does to the kept position.
    for odd in (native('o', 'buy', 1, 101, '2026-10-07T14:00:30Z', 'p', False),      # an "exit" that adds
                native('o', 'sell', 1, 101, '2026-10-07T14:00:30Z', 'p', True)):     # an "entry" that closes
        trade, = match(fills([events[0], odd]))
        assert trade.incomplete == [ENTRY_MISSING] and trade.net is None and len(trade.fills) == 2


def test_zero_price_is_no_price_for_every_writer_and_never_a_profit(tmp_path):
    journal = Journal(tmp_path / 'trades.db')
    apply_preview(journal, preview_csv('symbol,side,qty,price,ts,id\nNQZ6,buy,1,0,2026-10-07T14:00:00Z,a\n'
                                       'NQZ6,sell,1,20200,2026-10-07T14:01:00Z,b\n'
                                       'NQZ6,buy,1,20210,2026-10-07T14:02:00Z,c\n'
                                       'NQZ6,sell,1,20215,2026-10-07T14:03:00Z,d\n'))
    unpriced, priced = journal.trades()
    assert unpriced.incomplete == [NO_FILL_PRICE] and unpriced.net is None and unpriced.status == 'incomplete'
    assert priced.net == 100 and summary([t.row() for t in journal.trades()])['net'] == 100
    # A reversal through an unpriced fill leaves both of its halves without a result.
    events = [native('a', 'buy', 1, 100, '2026-10-07T14:00:00Z', 'p', None),
              native('b', 'sell', 2, 0, '2026-10-07T14:01:00Z', 'p', None),
              native('c', 'buy', 1, 99, '2026-10-07T14:02:00Z', 'p', None)]
    assert [t.incomplete for t in match(fills(events))] == [[NO_FILL_PRICE], [NO_FILL_PRICE]]
    # The study's wording for such a fill keeps only the real problem as a flag beside the reason;
    # its source label and the fee wording are not warnings (MotiveWave's fee is the fee).
    labelled = native('z', 'buy', 1, 0, '2026-10-07T16:00:00Z', 'r', True, price_known=False,
                      quality=PLAIN + '; platform reported no fill price')
    trade, = match(fills([labelled]))
    assert trade.incomplete == [NO_FILL_PRICE] and trade.flags == [NO_FILL_PRICE] and trade.status == 'incomplete'
    assert trade.closed is None                               # still a position the platform holds open


def test_trade_ids_are_what_they_were_and_change_only_with_the_first_fill(tmp_path):
    def plain(id, side, qty, price, minute=0, fee=0):
        return Execution('test', 'paper', 'ESZ6', side, qty, price, f'2026-10-07T14:{minute:02d}:00Z',
                         execution_id=id, commission=fee, point_value=50)
    # Ids and measurement bases as 0.3.0 produced them for these fills.
    long, short = match([plain('a', 'buy', 1, 100, fee=2), plain('b', 'sell', 3, 105, 1, fee=6),
                         plain('c', 'buy', 2, 101, 2, fee=4)])
    assert (long.id, short.id) == ('4bda8523d3bc88cae7c682ae', '3b27cea06fc4a2eaa6e03663')
    assert long.basis == '085721ccf4eeffa701469939f75cfbeb45b8f714a933080185f6bb02a44ba4d5'
    assert short.basis == 'a912f29af1a41b570447f46917c7f14bfd4b3a446b6f28d9491659ab2921166e'
    events = history()
    trades = match(fills(events))
    # The id names the scope and the first fill, with or without a position id.
    assert trades[1].id == '7d969cc96665e63fe9292197'
    assert trades[1].basis == '4b4a85e42b38df5f58c59ca2906fc25c2076b738f197049169d9deff4d005e13'
    for trade in trades:
        first = next(f for f in fills(events) if f.execution_id == trade.fills[0]['execution_id'])
        assert trade.id == digest([SCOPE, first.key, first.side])[:24]
    old = {t.fills[0]['execution_id']: t.id for t in match(fills(unlabelled(events)))}
    assert all(old[t.fills[0]['execution_id']] == t.id for t in trades if t.fills[0]['execution_id'] in old)
    assert trades[0].id == old['x0']             # the lone exit keeps the id of the false short it used to open
    # A review stays with its trade through later fills, and through other positions being regrouped.
    journal = Journal(tmp_path / 'trades.db')
    apply_preview(journal, preview_bridge(lines(events), dataset='live'))
    lone_exit, round_trip = journal.trades()[:2]
    journal.annotate(lone_exit.id, {'notes': 'held from August'})
    journal.annotate(round_trip.id, {'tags': ['patient']})
    journal.record_media('shot', lone_exit.id, {'kind': 'screenshot', 'state': 'ready'})
    journal.record_media('shot-2', round_trip.id, {'kind': 'screenshot', 'state': 'ready'})
    checklist = journal.save_checklist('Plan', [question('Was it planned?')])
    journal.start_checklist(checklist, lone_exit.id)
    journal.start_checklist(checklist, day='2026-08-28')      # a run that belongs to a day, not a trade
    journal.record_checkin('Mindset', ['Patient'], trade_id=lone_exit.id)
    assert journal.detached_reviews() == {'annotations': [], 'media': [], 'checklist_runs': [], 'checkins': [], 'drafts': []}
    # The id changes in one case only: an earlier fill of the same position arrives and becomes its first fill.
    entry = native('w9', 'buy', 1, 20080, '2026-08-28T14:00:00Z', 'p0', True)
    apply_preview(journal, preview_bridge(lines([entry]), dataset='live'))
    completed = journal.trades()[0]
    assert completed.complete and completed.net == 400 and completed.id != lone_exit.id
    assert journal.trades()[1].id == round_trip.id and journal.annotations()[round_trip.id]['tags'] == ['patient']
    assert journal.detached_reviews() == {'annotations': [lone_exit.id], 'media': [lone_exit.id],
                                          'checklist_runs': [lone_exit.id], 'checkins': [lone_exit.id], 'drafts': []}
    assert lone_exit.id in journal.annotations() and journal.trade_media(lone_exit.id)       # kept, not deleted


def test_incomplete_trades_have_no_excursions_stored_measurements_runner_or_zero_price_marker(tmp_path):
    journal = Journal(tmp_path / 'trades.db')
    apply_preview(journal, preview_bridge(lines(history()), dataset='live'))
    lone_exit, complete, _, unpriced, lone_entry, _, _ = journal.trades()
    samples = [PriceSample('NQZ6', f'2026-09-{day}T14:0{minute}:30Z', 'test-prices', price, price)
               for day, minute, price in ((21, 1, 19990), (21, 2, 20015), (23, 1, 20150), (23, 4, 20260), (24, 0, 20250))]
    journal.ingest_prices(samples)
    measured = excursions(complete, samples, 'test-prices')
    assert (measured['mae'], measured['mfe'], measured['precision']) == (200, 300, 'observed-ticks')
    for trade in (lone_exit, unpriced, lone_entry):
        result, = measurements(trade, samples, 'test-prices')
        assert result['precision'] == 'pending' and result['mae'] is None and result['mfe_points'] is None
        assert result['path'] == [] and trade.incomplete
        journal.record_metric(trade, {**measured, 'source': 'by-mistake'})
        assert journal.metric_options(trade) == []
        runner = post_exit(trade, samples, 'test-prices', 20000)
        assert runner['extra_mfe'] is None and runner['coverage'] == runner['termination'] == 'unavailable'
        assert best_post_exit(journal, trade, None)['extra_mfe'] is None
    with journal.connect() as db:
        assert db.execute('SELECT count(*) FROM metrics').fetchone()[0] == 0
    # A platform's own excursion report is an event this version skips and counts: it attaches nothing.
    report = {'type': 'metric', 'provider': PROVIDER, 'account': 'SIM-1', 'symbol': 'NQZ6', 'entry_execution_id': 'c1',
              'units': 'currency', 'definition': 'position-equity', 'mae': 25, 'mfe': 100}
    assert preview_bridge(json.dumps(report)).unknown == 1
    assert journal.metric_options(unpriced) == []
    chart = trade_chart(unpriced, samples, 'test-prices')
    assert [f['execution_id'] for f in chart['overlays']['fills']] == ['c2'] and chart['overlays']['fills'][0]['sequence'] == 1
    assert any('no price from the platform' in note for note in chart['notes'])
    assert len(trade_chart(complete, samples, 'test-prices')['overlays']['fills']) == 2


def test_bridge_carries_the_platform_position_fields_and_older_lines_read_as_before(tmp_path):
    event = history()[1]
    fill, raw = preview_bridge(json.dumps(event), dataset='live').fills[0]
    assert (fill.native_position_id, fill.native_entry) == ('p1', True) and raw == event
    assert fill.payload()['native_position_id'] == 'p1' and fill.payload()['native_entry'] is True
    # A fill without them is stored byte for byte as 0.3.0 stored it, so its re-import is still a duplicate.
    old, = fills(unlabelled([event]))
    assert (old.native_position_id, old.native_entry, old.key) == ('', None, fill.key)
    assert json.dumps(old.payload(), sort_keys=True) == (
        '{"account": "SIM-1", "commission": 0.0, "commission_known": false, "currency": "USD", '
        '"execution_id": "a1", "order_id": "order-a1", "point_value": 20.0, "price": 20000.0, '
        '"provider": "motivewave-native:workspace:live", "qty": 1.0, '
        '"quality": "MotiveWave internal capture; fees may be estimated", "side": "buy", "symbol": "NQZ6", '
        '"ts": "2026-09-21T14:00:00+00:00"}')
    # Empty or null values mean "not said"; fields this version does not know are ignored.
    blank, = fills([{**event, 'native_position_id': None, 'native_entry': None, 'price_known': True,
                     'currency_assumed': True, 'a_later_field': {'x': 1}}])
    assert blank.payload() == old.payload()
    assert preview_bridge(json.dumps({**event, 'native_entry': 'yes'}), dataset='live').errors
    journal = Journal(tmp_path / 'trades.db')
    assert apply_preview(journal, preview_bridge(lines([event, event]), dataset='live'))['executions'] == 1
    assert journal.executions()[0] == fill


def test_same_fill_described_again_is_relabelled_while_different_values_stay_a_conflict(tmp_path):
    journal = Journal(tmp_path / 'trades.db')
    first, second = unlabelled(history()[6:8])               # the unpriced round trip, sent without position fields
    assert apply_preview(journal, preview_bridge(lines([first, second]), dataset='live'))['executions'] == 2
    before = journal.trades()[0]
    # A newer writer sends the same fills with position fields and a reworded quality label.
    reworded = [{**history()[6], 'quality': PLAIN + '; platform reported no fill price', 'price_known': False}, history()[7]]
    result = apply_preview(journal, preview_bridge(lines(reworded), dataset='live'))
    assert (result['executions'], result['duplicates'], result['relabelled']) == (0, 2, 2)
    stored = journal.executions()
    assert [e.native_position_id for e in stored] == ['p3', 'p3'] and stored[0].quality.endswith('no fill price')
    assert journal.trades()[0].id == before.id and len(journal.executions()) == 2
    # The older lines read again do not take the position id away, and nothing is counted twice.
    again = apply_preview(journal, preview_bridge(lines([first, second]), dataset='live'))
    assert again['executions'] == 0 and [e.native_position_id for e in journal.executions()] == ['p3', 'p3']
    assert journal.executions()[0].quality == PLAIN
    for field, value in (('price', 20001), ('qty', 2), ('side', 'sell'), ('ts', '2026-09-23T14:00:01Z'),
                         ('commission', 1.5), ('point_value', 2), ('order_id', 'another-order')):
        with pytest.raises(ImportConflict):
            apply_preview(journal, preview_bridge(json.dumps({**history()[6], field: value}), dataset='live'))
    assert len(journal.executions()) == 2 and journal.executions()[0].price == 0


def test_watcher_rebuilds_trades_and_their_measurements_when_known_fills_gain_position_ids(tmp_path):
    from mwjournal.sync import SyncWorker
    journal = Journal(tmp_path / 'trades.db')
    path = tmp_path / 'bridge.ndjson'
    events = [native('1', 'buy', 1, 100, '2026-10-07T14:00:00Z', 'p', True),
              native('2', 'buy', 3, 101, '2026-10-07T14:01:00Z', 'q', True),
              native('3', 'sell', 1, 104, '2026-10-07T14:02:00Z', 'p', False),
              native('4', 'sell', 3, 102, '2026-10-07T14:03:00Z', 'q', False)]
    low = {'type': 'tick', 'provider': 'test-prices', 'symbol': 'NQZ6', 'ts': '2026-10-07T14:01:30Z', 'price': 99}
    path.write_text(lines(unlabelled(events) + [low]))
    journal.put('profiles', 'bridge', {'path': str(path), 'mode': 'bridge', 'enabled': True})
    worker = SyncWorker(journal)
    worker.poll()
    paired, = journal.trades()                                # flat to flat: one four-lot long
    assert paired.max_qty == 4 and journal.metric_options(paired)[0]['mae'] == 140
    with path.open('a') as fh:
        fh.write(lines(events))                               # the same fills, now with the platform's positions
    worker.poll()
    status = journal.get('sync_status')['bridge']
    assert (status['status'], status['executions'], status['relabelled']) == ('synced', 0, 4)
    first, second = journal.trades()
    assert first.id == paired.id and (first.max_qty, second.max_qty) == (1, 3)
    assert journal.metric_options(first)[0]['mae'] == 20 and journal.metric_options(second)[0]['mae'] == 120
    worker.poll()
    assert len(journal.executions()) == 4 and journal.metric_options(second)[0]['observations'] == 1
