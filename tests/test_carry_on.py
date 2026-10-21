"""An open trade's measurement carried on with the prices stored since (metrics.carry_on, Journal.measure) is
the measurement from entry, without reading the tape again. Checked on random tapes against measurements()."""
import dataclasses
import datetime as dt
import random

import pytest

from mwjournal import Execution, Journal, PriceSample
from mwjournal.metrics import PATH_POINTS, carry_on, measurements

T0 = dt.datetime(2026, 10, 7, 14, 0, tzinfo=dt.timezone.utc)
SOURCE = 'motivewave-ticks'
COMPARED = ('mae', 'mfe', 'mae_points', 'mfe_points', 'observations', 'last_price', 'last_ts', 'last_count',
            'contract', 'precision', 'through', 'source', 'basis')


def at(seconds):
    return (T0 + dt.timedelta(seconds=seconds)).isoformat()


def tape(rng, count, start, labels, quotes):
    """Prints in time order; about one in five shares the previous one's millisecond."""
    clock, price, prints = start, 20000.0, []
    for _ in range(count):
        if rng.random() > 0.2:
            clock += rng.choice([0.001, 0.25, 1.0, 3.0])
        price += rng.choice([-0.5, -0.25, 0, 0.25, 0.5])
        book = {'bid': price - 0.25, 'ask': price + 0.25} if quotes and rng.random() > 0.1 else {}
        prints.append(PriceSample(rng.choice(labels), at(clock), SOURCE, price, price, **book))
    return prints, clock


def fills(rng, direction, scale_in_at):
    side = 'buy' if direction == 'long' else 'sell'
    result = [Execution('t', 'a', 'NQZ6', side, rng.choice([1, 2]), 20000, at(0), commission=2, execution_id='in')]
    if scale_in_at is not None:
        result.append(Execution('t', 'a', 'NQZ6', side, 1, 20001.5, at(scale_in_at), commission=2, execution_id='add'))
    return result


def same(carried, full):
    assert {k: carried.get(k) for k in COMPARED} == {k: full.get(k) for k in COMPARED}
    for low, high in (('point_low', 'point_high'), ('low', 'high')):
        assert (min(p[low] for p in carried['path']) if carried['path'][0][low] is not None else None) == (
            min(p[low] for p in full['path']) if full['path'][0][low] is not None else None)
        assert (max(p[high] for p in carried['path']) if carried['path'][0][high] is not None else None) == (
            max(p[high] for p in full['path']) if full['path'][0][high] is not None else None)
    assert any('compacted' in n for n in carried['notes']) == any('compacted' in n for n in full['notes'])
    assert len(carried['path']) <= PATH_POINTS


@pytest.mark.parametrize('seed', range(40))
def test_carrying_on_is_measuring_from_entry(tmp_path, seed):
    rng = random.Random(seed)
    journal = Journal(tmp_path / 'trades.sqlite3')
    direction = rng.choice(['long', 'short'])
    labels = rng.choice([['NQZ6'], ['NQ'], ['NQ', 'NQ.CME']])
    quotes = rng.random() > 0.3
    first, clock = tape(rng, rng.randint(1, 1500), 0.5, labels, quotes)
    scale_in = rng.choice([None, round(rng.uniform(0.1, clock), 3)])
    journal.ingest([(fill, {}) for fill in fills(rng, direction, scale_in)])
    if journal.trades()[0].point_value is None and rng.random() > 0.5:
        journal.set_instrument('NQZ6', 20)
    trade, = journal.trades()
    journal.ingest_prices(first)
    stop = T0 + dt.timedelta(seconds=clock + 0.0005)
    previous = measurements(trade, journal.prices('NQZ6', SOURCE, start=trade.opened), SOURCE, now=stop)
    then, clock = tape(rng, rng.randint(0, 1500), clock, labels, quotes)
    journal.ingest_prices(then)
    now = T0 + dt.timedelta(seconds=clock + 0.5)
    full = measurements(trade, journal.prices('NQZ6', SOURCE, start=trade.opened), SOURCE, now=now)
    since = min(m['last_ts'] for m in previous if m.get('last_ts'))
    carried = carry_on(trade, previous, journal.prices('NQZ6', SOURCE, start=since), now=now)
    if carried is None:
        # Only for the documented reasons: the first quotes arrive after the split, or a measurement (the
        # quote-based one, when the last prints had no quote) stopped before the scale-in.
        last_fill = trade.fills[-1]['ts']
        assert len(full) != len(previous) or any(m['last_ts'] < last_fill for m in previous)
        return
    assert len(carried) == len(full)
    for got, want in zip(sorted(carried, key=lambda m: m['basis']), sorted(full, key=lambda m: m['basis'])):
        same(got, want)


def journal_with(tmp_path, prints, *, side='buy'):
    journal = Journal(tmp_path / 'trades.sqlite3')
    journal.ingest([(Execution('t', 'a', 'NQZ6', side, 1, 20000, at(0), execution_id='in'), {})])
    journal.set_instrument('NQZ6', 20)
    journal.ingest_prices(prints)
    return journal, journal.trades()[0]


def tick(label, seconds, price, **more):
    return PriceSample(label, at(seconds), SOURCE, price, price, **more)


def test_it_does_not_carry_on_what_would_differ_from_measuring_again(tmp_path):
    journal, trade = journal_with(tmp_path, [tick('NQ', 1, 19990), tick('NQ', 2, 20010)])
    previous = measurements(trade, journal.prices('NQZ6', SOURCE), SOURCE, now=T0 + dt.timedelta(seconds=3))
    later = T0 + dt.timedelta(seconds=9)

    def carry(*prints):
        journal.ingest_prices(list(prints))
        return carry_on(trade, previous, journal.prices('NQZ6', SOURCE, start=previous[0]['last_ts']), now=later)

    assert carry(tick('NQ', 4, 20020))[0]['mfe_points'] == 20
    # The contract's own prints appear: from then on the whole trade is measured with them alone.
    assert carry(tick('NQZ6', 5, 20030)) is None
    # A candle, a proxy price, and the first quote for a trade measured without quotes.
    (tmp_path / 'b').mkdir()
    journal2, trade2 = journal_with(tmp_path / 'b', [tick('NQ', 1, 19990)])
    previous2 = measurements(trade2, journal2.prices('NQZ6', SOURCE), SOURCE, now=T0 + dt.timedelta(seconds=3))
    for new in (PriceSample('NQ', at(60), SOURCE, 20010, 19990, 60, open=20000, close=20005),
                tick('NQ', 4, 20000, proxy=True), tick('NQ', 4, 20000, bid=19999.75, ask=20000.25)):
        assert carry_on(trade2, previous2, [new], now=later) is None
    # A closed trade, and a measurement that stopped before the last fill.
    assert carry_on(dataclasses.replace(trade, closed=at(8)), previous, [], now=later) is None
    assert carry_on(trade, [{**previous[0], 'last_ts': '2026-10-07T13:59:00+00:00'}], [], now=later) is None


def test_a_print_stored_late_with_the_time_already_measured_is_counted_once(tmp_path):
    journal, trade = journal_with(tmp_path, [tick('NQ', 1, 19990), tick('NQ', 2, 20001), tick('NQ', 2, 20002)])
    stop = T0 + dt.timedelta(seconds=2.5)
    previous = measurements(trade, journal.prices('NQZ6', SOURCE), SOURCE, now=stop)
    assert (previous[0]['last_ts'], previous[0]['last_count'], previous[0]['observations']) == (at(2), 2, 3)
    journal.ingest_prices([tick('NQ', 2, 20025)])          # same millisecond, arrived after the measurement
    now = T0 + dt.timedelta(seconds=5)
    carried, = carry_on(trade, previous, journal.prices('NQZ6', SOURCE, start=at(2)), now=now)
    full, = measurements(trade, journal.prices('NQZ6', SOURCE), SOURCE, now=now)
    same(carried, full)
    assert (carried['observations'], carried['last_count'], carried['last_price']) == (4, 3, 20025)


def test_the_watcher_carries_an_open_trade_on_and_reads_only_the_new_prices(tmp_path, monkeypatch):
    import json
    from mwjournal.sync import SyncWorker
    journal = Journal(tmp_path / 'trades.sqlite3')
    bridge = tmp_path / 'motivewave.ndjson'
    journal.put('profiles', 'native', {'mode': 'bridge', 'path': str(bridge), 'enabled': True})

    def write(*events):
        with bridge.open('a', encoding='utf-8', newline='') as fh:
            fh.write(''.join(json.dumps(e) + '\n' for e in events))

    def print_at(second, price):
        return {'version': 1, 'type': 'tick', 'mode': 'live', 'provider': SOURCE, 'symbol': 'NQ', 'price': price,
                'ts': (dt.datetime(2026, 10, 7, 14, 0, tzinfo=dt.timezone.utc) + dt.timedelta(seconds=second)).isoformat(),
                'proxy': False}
    write({'version': 1, 'type': 'execution', 'mode': 'live', 'provider': 'motivewave-native:ws:live',
           'account': 'SIM', 'symbol': 'NQZ6', 'side': 'buy', 'qty': 1, 'price': 20000, 'ts': at(0),
           'execution_id': 'in', 'order_id': 'o', 'commission': 2.0, 'point_value': 20},
          *[print_at(s, 20000 + s % 7) for s in range(1, 200)])
    worker = SyncWorker(journal)
    worker.poll()
    reads = []
    original = Journal.prices
    monkeypatch.setattr(Journal, 'prices', lambda self, *a, **k: reads.append(k.get('start')) or original(self, *a, **k))
    write(print_at(200, 19950))
    worker.poll()
    trade, = journal.trades()
    metric = journal.best_metric(trade)
    assert (metric['observations'], metric['mae_points'], metric['last_price']) == (200, 50, 19950)
    assert reads == [at(199)]                      # from where it stopped, not from entry


def test_prints_under_two_labels_at_one_time_keep_the_tapes_order(tmp_path):
    """Two continuous charts of one market, a print each in the same millisecond, the second stored after the
    trade was measured: prices() keeps the tape's order whatever the labels, so carrying on skips the print
    already measured, not the new one."""
    journal, trade = journal_with(tmp_path, [tick('NQ.CME', 1, 19990), tick('NQ.CME', 2, 20002.25)])
    previous = measurements(trade, journal.prices('NQZ6', SOURCE), SOURCE, now=T0 + dt.timedelta(seconds=2.5))
    journal.ingest_prices([tick('NQ', 2, 20000)])          # sorts before NQ.CME by name, stored after it
    assert [s.symbol for s in journal.prices('NQZ6', SOURCE)] == ['NQ.CME', 'NQ.CME', 'NQ']
    now = T0 + dt.timedelta(seconds=5)
    carried, = carry_on(trade, previous, journal.prices('NQZ6', SOURCE, start=at(2)), now=now)
    full, = measurements(trade, journal.prices('NQZ6', SOURCE), SOURCE, now=now)
    same(carried, full)
    assert carried['last_price'] == 20000 and min(p['point_low'] for p in carried['path']) == -10
