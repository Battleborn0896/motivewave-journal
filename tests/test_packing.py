"""The study's prints are packed one to two hours after they are stored: one row per contract, source and minute,
which Journal.prices() unpacks into exactly the prints stored, in the order stored. Nothing a trader sees changes:
every price read, measurement, after-exit result and export is the same before and after, and every print is kept."""
import dataclasses
import datetime as dt
import json
import os
import random
import shutil
import sqlite3
import types
from pathlib import Path

import pytest

from mwjournal import Execution, Journal, PriceSample, journals, store
from mwjournal.importers import apply_preview, preview_bridge
from mwjournal.metrics import carry_on, excursions, measurements
from mwjournal.model import instant
from mwjournal.post_exit import best_post_exit
from mwjournal.sync import SyncWorker

DAY = '2026-10-21'
LIVE, HISTORY, BARS = 'motivewave-ticks', 'motivewave-ticks:history', 'motivewave-bars:1m'
STUDY, RUN = 'motivewave-native:ws', 'motivewave-native:ws:replay:r1'
REPLAY = ('motivewave-replay:r1:ticks', 'motivewave-replay:r1:history', 'motivewave-replay:r1:simulated')
START = dt.datetime(2026, 10, 21, 13, 50, tzinfo=dt.timezone.utc)


def fill(id, side, clock, price=20000., *, symbol='NQZ6', provider=STUDY, day=DAY):
    return Execution(provider, 'sim', symbol, side, 1, price, f'{day}T{clock}Z', execution_id=id)


def tape(seed, minutes=150, *, sources=(LIVE, HISTORY), labels=('NQZ6', 'NQ', 'NQZ6.CME'), start=START, step=.25,
         price=20000.):
    """NQ prints as the study records them, from `start`, in the order it writes them: one label or another of the
    product (a contract chart, a continuous one, an exchange's name), a print recorded by two charts under two labels,
    times in whole seconds, milliseconds and microseconds, prints sharing a time, bid and ask there or not, a volume or
    none, and the continuous chart's proxy prints. Seeded."""
    rng, prints = random.Random(seed), []
    for minute in range(minutes):
        for _ in range(rng.choice((0, 1, 3, 8))):
            if prints and rng.random() < .2:
                at = instant(prints[-1].ts)                      # the same time, to the microsecond, as the print before
            else:
                at = start + dt.timedelta(minutes=minute, microseconds=rng.choice(
                    (rng.randrange(60) * 1_000_000, rng.randrange(60_000) * 1000, rng.randrange(60_000_000))))
            # As the study writes them: the shortest decimal text of each price.
            price = round(round(price / step + rng.choice((-1, 0, 1))) * step, 10)
            label = rng.choice(labels)
            quoted = rng.random() < .5
            prints.append(PriceSample(label, at.isoformat(), rng.choice(sources), price, price,
                                      bid=round(price - step, 10) if quoted else None,
                                      ask=round(price + step, 10) if quoted else None,
                                      volume=float(rng.randrange(1, 20)) if rng.random() < .7 else None,
                                      proxy=label == 'NQ' and rng.random() < .5))
            if len(labels) > 1 and rng.random() < .1:
                prints.append(dataclasses.replace(prints[-1], symbol=rng.choice([s for s in labels if s != label])))
    return prints


def candles(source=BARS, minutes=150):
    return [PriceSample('NQZ6', (START + dt.timedelta(minutes=m)).isoformat(), source, 20010., 19990., 60,
                        open=20000., close=20005., volume=10.) for m in range(minutes)]


def day_journal(path, seed=1, *, dataset='live'):
    """A day of NQ: a round trip 14:00:10-14:10:20, another 15:01:00-15:02:30 under the continuous chart's label, and a
    position opened at 16:00:05 and still open; the study's prints of 13:50-16:20 under two labels, live first and the
    history backfill after, its candles, and prices the trader imported. A replay journal holds one run's of each."""
    journal = Journal(path, dataset=dataset)
    provider = RUN if dataset == 'replay' else STUDY
    journal.ingest([(fill('a-in', 'buy', '14:00:10', provider=provider), {}),
                    (fill('a-out', 'sell', '14:10:20', 20008., provider=provider), {}),
                    (fill('b-in', 'sell', '15:01:00', symbol='NQ', provider=provider), {}),
                    (fill('b-out', 'buy', '15:02:30', 19996., symbol='NQ', provider=provider), {}),
                    (fill('c-in', 'buy', '16:00:05', provider=provider), {})])
    sources = REPLAY if dataset == 'replay' else (LIVE, HISTORY)
    recorded = tape(seed, sources=sources[:1] if dataset == 'live' else sources)
    journal.ingest_prices([p for p in recorded if p.source == sources[0]])
    journal.ingest_prices(candles('motivewave-replay:bars' if dataset == 'replay' else BARS))
    journal.ingest_prices([p for p in recorded if p.source != sources[0]])
    journal.ingest_prices(tape(seed + 1, sources=sources[1:2]))            # the history backfill, loaded afterwards
    journal.ingest_prices([PriceSample('NQZ6', f'{DAY}T14:05:00Z', 'my-ticks', 20001., 20001.),
                           PriceSample('NQZ6', f'{DAY}T14:05:00Z', 'my-bars', 20004., 19998., 60, open=20000., close=20002.)])
    return journal


def windows(trade):
    """Every window a trade reads its prices over: none, its hold, after its exit to the longest runner horizon, its
    chart two hours either side, starts and ends in the middle of a minute and on one, and open at either end."""
    opened, closed = instant(trade.opened), instant(trade.closed or '2026-10-21T16:20:00+00:00')
    minute = dict(second=0, microsecond=0)
    for start, end in ((None, None), (opened, closed), (closed, closed + dt.timedelta(minutes=120)),
                       (opened - dt.timedelta(minutes=120), closed + dt.timedelta(minutes=120)),
                       (opened + dt.timedelta(seconds=17.25), closed - dt.timedelta(seconds=3.5)),
                       (opened.replace(**minute), closed.replace(**minute)), (opened.replace(**minute), None),
                       (None, opened + dt.timedelta(minutes=30)), (closed + dt.timedelta(minutes=30), None)):
        yield start and start.isoformat(), end and end.isoformat()


def text(samples):
    """Prices exactly as read, every field and its type: 0 and 0.0 differ here."""
    return [json.dumps(dataclasses.asdict(s), sort_keys=True) for s in samples]


def reads(journal, symbols=('NQZ6', 'NQ', 'NQZ6.CME', 'NQH7'), *, bounded=False):
    """What prices() gives for every symbol a trade can ask for (its contract, the continuous chart, an exchange's
    name, another month of the product; with none given, each trade's own), every source and every window of every
    trade; `bounded`, only the windows with both ends."""
    found = {}
    for trade in journal.trades():
        for symbol in symbols or (trade.symbol,):
            for source in journal.price_sources(symbol):
                for start, end in windows(trade):
                    if bounded and not (start and end):
                        continue
                    found[trade.id, symbol, source, start, end] = text(journal.prices(symbol, source, start=start, end=end))
    return found


def rows(path, where='1'):
    db = sqlite3.connect(path)
    try:
        return db.execute(f'SELECT rowid, key, symbol, source, ts, payload FROM prices WHERE {where} ORDER BY rowid').fetchall()
    finally:
        db.close()


LOOSE = ("source GLOB 'motivewave-*' AND json_extract(payload, '$.resolution_seconds')=0 "
         "AND json_extract(payload, '$.packed') IS NULL")
PACKED = "json_extract(payload, '$.packed') IS NOT NULL"
KEPT = "source NOT GLOB 'motivewave-*' OR json_extract(payload, '$.resolution_seconds')>0"


def meta(path, key):
    db = sqlite3.connect(path)
    try:
        row = db.execute('SELECT value FROM journal_meta WHERE key=?', (key,)).fetchone()
        return row[0] if row else None
    finally:
        db.close()


# ---------------------------------------------------------------- the packed row

@pytest.mark.parametrize('step', (.25, .01, .00005))
@pytest.mark.parametrize('source', (LIVE, HISTORY, *REPLAY))
def test_a_packed_minute_unpacks_to_exactly_the_prints_stored(step, source):
    prints = tape(5, minutes=1, sources=(source,), labels=('NQZ6',), step=step, price=1.08 if step < .01 else 20000.)
    low = round(prints[0].high - step, 10)
    prints += [PriceSample('NQZ6', f'{DAY}T13:50:{s}', source, prints[0].high, prints[0].high, bid=b, ask=a, volume=v,
                           proxy=x) for s, b, a, v, x in (('07Z', None, None, None, False), ('07Z', None, None, 3., True),
                                                          ('59.999999Z', low, None, .5, False))]
    stored = [(rowid, json.loads(store.encode(dataclasses.asdict(p)))) for rowid, p in enumerate(prints, start=40)]
    stored.sort(key=lambda found: (found[1]['ts'], found[0]))
    packed = store.pack('NQZ6', source, '2026-10-21T13:50:00+00:00', stored)
    assert [(rowid, store.encode(data)) for rowid, data in store.unpack(packed)] == [
        (rowid, store.encode(data)) for rowid, data in stored]
    assert packed['last'] == stored[-1][1]['ts'] and packed['prints'] == len(stored) and packed['resolution_seconds'] == 0
    assert len(store.encode(packed)) < sum(len(store.encode(data)) for _, data in stored) / 2


# ---------------------------------------------------------------- every read is the same

@pytest.mark.parametrize('dataset', ('live', 'replay'))
def test_every_price_a_trade_can_read_is_the_same_once_packed_and_candles_and_imports_stay_rows(tmp_path, dataset):
    path = tmp_path / 'trades.sqlite3'
    journal = day_journal(path, dataset=dataset)
    before, kept, loose = reads(journal), rows(path, KEPT), rows(path, LOOSE)
    assert len(before) > 300 and any(before.values())
    assert journal.pack_prices() == len(loose)
    assert not rows(path, LOOSE) and rows(path, PACKED) and rows(path, KEPT) == kept
    assert reads(journal) == before


def test_the_contract_seen_only_before_the_window_does_not_hide_the_continuous_charts_prints(tmp_path):
    path = tmp_path / 'trades.sqlite3'
    journal = Journal(path)
    journal.ingest_prices([PriceSample('NQZ6', f'{DAY}T14:00:05Z', LIVE, 20000., 20000.),
                           *[PriceSample('NQ', f'{DAY}T14:{m:02d}:40Z', LIVE, 20001. + m, 20001. + m) for m in range(5)],
                           PriceSample('NQZ6', f'{DAY}T14:04:50Z', LIVE, 20100., 20100.)])
    ask = [(f'{DAY}T14:00:30Z', f'{DAY}T14:04:45Z'), (f'{DAY}T14:00:06Z', f'{DAY}T14:04:49Z'),
           (f'{DAY}T14:00:30Z', f'{DAY}T14:04:50Z'), (None, f'{DAY}T14:00:04Z')]
    before = [text(journal.prices('NQZ6', LIVE, start=start, end=end)) for start, end in ask]
    # NQZ6 is in the packed minutes of 14:00 and 14:04, but in none of the first two windows: the continuous chart's
    # prints count there, and in the third NQZ6's own print alone.
    assert [len(found) for found in before] == [5, 5, 1, 0]
    journal.pack_prices()
    assert rows(path, PACKED) and [text(journal.prices('NQZ6', LIVE, start=start, end=end)) for start, end in ask] == before


@pytest.mark.parametrize('seed', range(8))
def test_packing_as_prints_arrive_reads_as_if_nothing_were_ever_packed(tmp_path, seed):
    """However the prints arrive and whenever passes run: some again, some late into a minute already packed."""
    rng = random.Random(seed)
    arriving = tape(seed, minutes=40)
    arriving += rng.sample(arriving, 10)                                   # read again: from a bridge read twice
    arriving += [dataclasses.replace(p, high=p.high + .25, low=p.high + .25) for p in rng.sample(arriving, 10)]   # late
    plain, packed = Journal(tmp_path / 'plain.sqlite3'), Journal(tmp_path / 'packed.sqlite3')
    assert plain.ingest_prices(arriving) > 0
    cuts = sorted(rng.sample(range(1, len(arriving)), 4))
    for first, last in zip([0, *cuts], [*cuts, len(arriving)]):
        packed.ingest_prices(arriving[first:last])
        for _ in range(rng.randrange(3)):
            packed.pack_prices()
    asks = [(None, None)] + [tuple(sorted((START + dt.timedelta(seconds=rng.uniform(-60, 2460))).isoformat()
                                          for _ in range(2))) for _ in range(30)]
    asks += [(start, None) for start, _ in asks[1:10]] + [(None, end) for _, end in asks[10:20]]
    assert rows(packed.path, PACKED)
    for symbol in ('NQZ6', 'NQ', 'NQZ6.CME', 'NQH7'):
        for source in (LIVE, HISTORY):
            for start, end in asks:
                assert (text(packed.prices(symbol, source, start=start, end=end))
                        == text(plain.prices(symbol, source, start=start, end=end))), (symbol, source, start, end)


def test_a_print_read_again_adds_nothing_and_a_late_one_is_read_at_once_and_packed_later(tmp_path):
    path = tmp_path / 'trades.sqlite3'
    journal = Journal(path)
    recorded = tape(3, minutes=10)
    journal.ingest_prices(recorded)
    journal.pack_prices()
    assert journal.ingest_prices(recorded) == 0 and not rows(path, LOOSE)
    # The same bridge lines imported again: the app reports no new prices.
    bridge = ''.join(json.dumps({'type': 'tick', 'provider': p.source, 'symbol': p.symbol, 'ts': p.ts, 'price': p.high,
                                 'bid': p.bid, 'ask': p.ask, 'volume': p.volume, 'proxy': p.proxy}) + '\n' for p in recorded)
    assert apply_preview(journal, preview_bridge(bridge, dataset='live'))['prices'] == 0
    late = PriceSample('NQZ6', (START + dt.timedelta(minutes=3, seconds=30.5)).isoformat(), LIVE, 20100., 20100.)
    expected = Journal(tmp_path / 'plain.sqlite3')
    expected.ingest_prices(recorded + [late])
    assert journal.ingest_prices([late]) == 1 and len(rows(path, LOOSE)) == 1
    for passes in (0, 1, 2):
        # Stored after the first pass: the next one leaves it, the one after packs it.
        assert len(rows(path, LOOSE)) == (1 if passes < 2 else 0)
        for source in (LIVE, HISTORY):
            assert text(journal.prices('NQZ6', source)) == text(expected.prices('NQZ6', source))
            assert text(journal.prices('NQZ6', source, start=late.ts)) == text(expected.prices('NQZ6', source, start=late.ts))
        journal.pack_prices()


def test_a_minute_that_cannot_be_packed_exactly_stays_as_it_was_stored(tmp_path):
    path = tmp_path / 'trades.sqlite3'
    journal = Journal(path)
    odd = [PriceSample('NQZ6', f'{DAY}T14:00:0{i}Z', LIVE, 1.000000000001 + i, 1.000000000001 + i) for i in range(3)]
    plain = [PriceSample('NQZ6', f'{DAY}T14:01:0{i}Z', LIVE, 20000. + i, 20000. + i) for i in range(3)]
    journal.ingest_prices(odd + plain)
    before = text(journal.prices('NQZ6', LIVE))
    assert journal.pack_prices() == 3
    assert [row[4][11:16] for row in rows(path, LOOSE)] == ['14:00'] * 3 and len(rows(path, PACKED)) == 1
    assert text(journal.prices('NQZ6', LIVE)) == before


# ---------------------------------------------------------------- what a trader sees

def everything(journal, *, now=dt.datetime(2026, 10, 21, 16, 25, tzinfo=dt.timezone.utc)):
    """Every number a trade shows from its prints: MAE / MFE from each source as measured afresh and as stored, the
    full recorded path the export writes, and what the price did in the minutes after the exit."""
    found = {}
    for trade in journal.trades():
        for source in journal.price_sources(trade.symbol, trade):
            samples = journal.prices(trade.symbol, source, start=trade.opened, end=trade.closed)
            found[trade.id, source] = (measurements(trade, samples, source, now=now),
                                       excursions(trade, samples, source, now=now, max_path_points=None)['path'])
        if trade.closed:
            found[trade.id, 'after exit'] = best_post_exit(journal, trade)
        found[trade.id] = journal.metric_options(trade)
    return found


def test_every_measurement_runner_result_and_full_path_is_the_same_once_packed(tmp_path):
    journal = day_journal(tmp_path / 'trades.sqlite3')
    trades = journal.trades()
    assert [t.status for t in trades] == ['closed', 'closed', 'open']
    journal.annotate(trades[0].id, {'planned_stop': 20001.})
    for trade in trades:
        for source in journal.price_sources(trade.symbol, trade):
            journal.measure(trade, source)
    before = everything(journal)
    # The first is held to its stop, the second to breakeven, and both are measured from recorded ticks.
    assert [before[t.id, 'after exit']['held_to'] for t in trades[:2]] == ['stop', 'breakeven']
    assert before[trades[1].id] and all(before[t.id, 'after exit']['precision'] == 'observed-ticks' for t in trades[:2])
    journal.pack_prices()
    assert everything(journal) == before
    # Measured again from what is stored (Recalculate, a change of measuring rules, a fill that regroups trades): the
    # same measurements.
    for trade in trades[:2]:
        for source in journal.price_sources(trade.symbol, trade):
            journal.measure(trade, source)
    assert everything(journal) == before


def test_an_open_trade_carried_on_from_a_packed_minute_is_measured_as_before(tmp_path):
    plain, packed = (day_journal(tmp_path / name) for name in ('plain.sqlite3', 'packed.sqlite3'))
    packed.pack_prices()

    def carried(journal):
        trade, found = journal.trades()[-1], {}
        stopped, now = (dt.datetime(2026, 10, 21, 16, m, tzinfo=dt.timezone.utc) for m in (10, 25))
        for source in (LIVE, HISTORY):          # recorded ticks, which carry on
            previous = measurements(trade, journal.prices(trade.symbol, source, start=trade.opened, end=stopped.isoformat()),
                                    source, now=stopped)
            found[source] = carry_on(trade, previous, journal.prices(trade.symbol, source, start=previous[0]['last_ts']),
                                     now=now)
        return found
    assert carried(packed) == carried(plain) and all(carried(packed).values())


def test_a_stop_typed_after_packing_gives_the_after_exit_result_it_gives_unpacked(tmp_path):
    plain, packed = (day_journal(tmp_path / name) for name in ('plain.sqlite3', 'packed.sqlite3'))
    packed.pack_prices()
    for journal in (plain, packed):
        for trade in journal.trades()[:2]:
            journal.annotate(trade.id, {'planned_stop': 20001. if trade.direction == 'long' else 19999.5})
    assert everything(packed) == everything(plain)


def test_the_newest_print_and_the_replay_run_are_found_once_packed(tmp_path):
    path = tmp_path / 'replay' / 'trades.sqlite3'
    journal = Journal(path, dataset='replay')
    journal.ingest([(fill('r-in', 'buy', '14:00:10', provider=RUN), {})])
    journal.ingest_prices(tape(4, minutes=40, sources=REPLAY[:1]))
    journal.ingest_prices([PriceSample('NQZ6', f'{DAY}T14:39:42.5Z', REPLAY[0], 20020., 20020.)])
    # A rewound replay, written after: its prices are of an earlier replay time.
    journal.ingest_prices(tape(6, minutes=20, sources=('motivewave-replay:r2:ticks',), labels=('ESZ6',),
                               start=START - dt.timedelta(hours=3), price=5000.))
    trade = journal.trades()[0]
    clock, recent = journal.trade_clock(trade), journal.recent_contracts()
    assert clock == instant('2026-10-21T14:39:42.501+00:00') and recent == ['ESZ6']
    for _ in range(2):
        journal.pack_prices()
        assert not rows(path, LOOSE)
        assert (journal.trade_clock(trade), journal.recent_contracts()) == (clock, recent)
    live = Journal(tmp_path / 'trades.sqlite3')
    live.ingest_prices(tape(4, minutes=40) + [PriceSample('NQZ6', f'{DAY}T14:39:42.5Z', LIVE, 20020., 20020.)])
    now = dt.datetime(2026, 10, 21, 15, 39, 42, 400000, tzinfo=dt.timezone.utc)
    assert live.recent_contracts(now=now) == ['NQZ6']                 # 100 ms inside the hour: its packed minute began before
    live.pack_prices()
    assert not rows(live.path, LOOSE) and live.recent_contracts(now=now) == ['NQZ6']


def test_a_pass_cut_short_leaves_every_print_readable_once_and_the_next_pass_finishes_it(tmp_path, monkeypatch):
    path = tmp_path / 'trades.sqlite3'
    journal = day_journal(path)
    before, calls = reads(journal), []
    pack_minute = Journal._pack_minute

    def dies_in_the_second_batch(db, *args):
        calls.append(args)
        if len(calls) == 150:
            raise KeyboardInterrupt
        return pack_minute(db, *args)
    monkeypatch.setattr(Journal, '_pack_minute', staticmethod(dies_in_the_second_batch))
    with pytest.raises(KeyboardInterrupt):
        journal.pack_prices()
    packed = rows(path, PACKED)
    assert len(packed) == 100 and rows(path, LOOSE) and meta(path, 'prices_packed') is None
    assert reads(journal) == before
    monkeypatch.undo()
    journal.pack_prices()
    assert not rows(path, LOOSE) and len(rows(path, PACKED)) > 200 and meta(path, 'prices_packed')
    assert reads(journal) == before


def test_no_trade_holds_packing_back_and_a_print_waits_one_pass(tmp_path):
    path = tmp_path / 'trades.sqlite3'
    journal = day_journal(path)
    assert journal.trades()[-1].status == 'open'
    journal.pack_prices()                       # the first pass: every print, the open trade's too
    assert not rows(path, LOOSE)
    journal.ingest_prices([PriceSample('NQZ6', f'{DAY}T16:21:00Z', LIVE, 20030., 20030.)])
    journal.pack_prices()                       # stored after the pass before: it stays a row for this one
    assert [row[4] for row in rows(path, LOOSE)] == ['2026-10-21T16:21:00+00:00']
    journal.ingest_prices([PriceSample('NQZ6', f'{DAY}T16:22:00Z', LIVE, 20031., 20031.)])
    journal.pack_prices()
    assert [row[4] for row in rows(path, LOOSE)] == ['2026-10-21T16:22:00+00:00']


def test_the_watcher_packs_as_it_starts_and_then_every_hour(tmp_path, monkeypatch):
    journal = day_journal(tmp_path / 'trades.sqlite3')
    passes = []
    monkeypatch.setattr(Journal, 'pack_prices', lambda self: passes.append(self.path))
    worker = SyncWorker(journal)
    worker.poll()
    worker.poll()
    assert passes == [journal.path]
    worker.pack_after -= 3600
    worker.poll()
    assert passes == [journal.path] * 2


def test_the_trade_page_shows_the_same_after_exit_and_full_path_once_packed(tmp_path, monkeypatch):
    import runtime
    from test_app import open_app, widget
    from test_chart import seed_preview
    monkeypatch.setenv('MWJ_HOME', str(tmp_path))
    journal = seed_preview(tmp_path)                   # long 2 ESZ6 14:00:23-14:03:11 with a saved stop
    trade, rng, price = journal.trades()[0], random.Random(2), 5000.
    prints = []
    for i in range(1200):                              # 13:58 to 16:18, live and history
        price += rng.choice((-.25, 0, .25))
        prints.append(PriceSample('ESZ6', (dt.datetime(2026, 10, 7, 13, 58, tzinfo=dt.timezone.utc)
                                           + dt.timedelta(seconds=7 * i)).isoformat(),
                                  rng.choice((LIVE, HISTORY)), price, price, volume=float(rng.randrange(1, 9))))
    journal.ingest_prices(prints)
    for source in journal.price_sources(trade.symbol, trade):
        journal.measure(trade, source)
    pack = Journal.pack_prices
    monkeypatch.setattr(Journal, 'pack_prices', lambda self: 0)       # the app's own watcher: not yet

    def shown():
        app = open_app()
        widget(app.checkbox, 'Show/export full recorded trade path').set_value(True).run()
        return {'path': [c.value for c in app.caption if 'recorded prices' in c.value],
                'after exit': [m.value for m in app.markdown if 'after you exited' in m.value],
                'numbers': [(m.label, m.value) for m in app.metric]}
    try:
        before = shown()
        assert before['path'] and int(before['path'][0].split()[0].replace(',', '')) > 10
        assert len(before['after exit']) == 1 and 'further your way' in before['after exit'][0]
        monkeypatch.setattr(Journal, 'pack_prices', pack)
        assert Journal().pack_prices() == 1200 and not rows(tmp_path / 'trades.sqlite3', LOOSE)
        assert shown() == before
    finally:
        runtime.stop_all()


# ---------------------------------------------------------------- the file on disk

def session(start, count, *, seed=0):
    rng = random.Random(seed)
    return [PriceSample('NQZ6', (start + dt.timedelta(seconds=i / 4)).isoformat(), LIVE, price, price,
                        volume=float(1 + i % 7)) for i in range(count) for price in [20000 + rng.randrange(40) / 4]]


def page_count(path):
    db = sqlite3.connect(path)
    try:
        return db.execute('PRAGMA page_count').fetchone()[0], db.execute('PRAGMA auto_vacuum').fetchone()[0]
    finally:
        db.close()


def test_the_first_pass_gives_the_space_back_and_says_so_and_each_later_pass_gives_back_what_it_frees(tmp_path):
    path = tmp_path / 'trades.sqlite3'
    journal = Journal(path)
    journal.ingest_prices(session(START, 16000))
    size = page_count(path)[0] * 4096
    assert size > 5e6 and page_count(path)[1] == 0
    assert journal.pack_prices() == 16000
    pages, mode = page_count(path)
    note = journal.upgrade_note()['packed']
    assert mode == 2 and pages * 4096 < size / 10 and path.stat().st_size == pages * 4096
    assert (note['prints'], note['compacted'], note['freed']) == (16000, True, size - pages * 4096)
    assert note['note'] == (f'Packed the recorded ticks: the journal went from {store.megabytes(size)} to '
                            f'{store.megabytes(pages * 4096)}, and every tick is kept.')
    # Another session's prints, stored as rows, then packed: the file shrinks back.
    journal.ingest_prices(session(START + dt.timedelta(hours=3), 16000))
    grown = page_count(path)[0]
    assert grown > 5 * pages
    journal.pack_prices()
    journal.pack_prices()
    assert page_count(path)[0] < grown / 5 and journal.upgrade_note()['packed'] == note


def test_without_room_on_the_disk_the_prints_are_packed_and_the_file_keeps_its_size(tmp_path, monkeypatch):
    path = tmp_path / 'trades.sqlite3'
    journal = Journal(path)
    journal.ingest_prices(session(START, 4000))
    before = text(journal.prices('NQZ6', LIVE))
    pages = page_count(path)[0]
    monkeypatch.setattr(store.shutil, 'disk_usage', lambda folder: types.SimpleNamespace(free=0))
    assert journal.pack_prices() == 4000
    note = journal.upgrade_note()['packed']
    assert page_count(path)[0] >= pages and page_count(path)[1] == 0 and not note['compacted']
    assert note['note'] == ('Packed the recorded ticks, and every tick is kept. The file keeps its size, as it was in use '
                            'or the disk was nearly full; new prices fill that space first.')
    assert text(journal.prices('NQZ6', LIVE)) == before


def test_the_one_time_trim_keeps_a_packed_minute_that_starts_before_a_trades_window(tmp_path):
    path = tmp_path / 'trades.sqlite3'
    journal = Journal(path)
    journal.ingest([(fill('a-in', 'buy', '14:00:30'), {}), (fill('a-out', 'sell', '14:05:00'), {})])
    journal.ingest_prices([PriceSample('NQZ6', f'{DAY}T13:58:40Z', LIVE, 19990., 19990.),       # 2 minutes before entry
                           PriceSample('NQZ6', f'{DAY}T14:01:00Z', LIVE, 20003., 20003.)])
    trade = journal.trades()[0]
    before = text(journal.prices('NQZ6', LIVE, start=(instant(trade.opened) - store.PRINTS_BEFORE).isoformat()))
    journal.pack_prices()
    db = sqlite3.connect(path)
    with db:
        db.execute("DELETE FROM journal_meta WHERE key='prices_trimmed'")          # as 0.4.13 left it
    db.close()
    journal = Journal(path)
    assert meta(path, 'prices_trimmed') == '0' and len(rows(path, PACKED)) == 2
    assert text(journal.prices('NQZ6', LIVE, start=(instant(trade.opened) - store.PRINTS_BEFORE).isoformat())) == before


# ---------------------------------------------------------------- named journals

def test_a_named_journal_takes_the_packed_minutes_and_its_copies_of_their_prints_go(tmp_path, monkeypatch):
    monkeypatch.setenv('MWJ_HOME', str(tmp_path))
    capture = day_journal(Journal().path)
    id = journals.create(capture, 'Sim only', [(STUDY, 'sim')])
    named = journals.journal_path(capture, id)
    assert len(rows(named, LOOSE)) == len(rows(capture.path, LOOSE))
    capture.pack_prices()
    journal = journals.open_named(capture, id)
    # The capture's packed minutes came over and every loose copy of their prints went: none is read twice.
    assert not rows(named, LOOSE) and len(rows(named, PACKED)) == len(rows(capture.path, PACKED))
    assert reads(journal) == reads(capture)
    # A late print: a row in both, until the capture packs it; then the named journal's copy goes as it comes.
    late = PriceSample('NQZ6', f'{DAY}T14:03:30.5Z', LIVE, 20100., 20100.)
    capture.ingest_prices([late])
    journal = journals.open_named(capture, id)
    assert len(rows(named, LOOSE)) == 1 and reads(journal) == reads(capture)
    capture.pack_prices()
    capture.pack_prices()
    journal = journals.open_named(capture, id)
    assert not rows(named, LOOSE) and reads(journal) == reads(capture)
    # An open trade's page brings it up to date the same way.
    trade = next(t for t in journal.trades() if t.status == 'open')
    capture.ingest_prices([PriceSample('NQZ6', f'{DAY}T16:21:10Z', LIVE, 20040., 20040.)])
    journals.follow(journal, trade)
    capture.pack_prices()
    capture.pack_prices()
    journals.follow(journal, trade)
    assert not rows(named, LOOSE) and reads(journal) == reads(capture)
    journals.open_named(capture, id)
    assert not rows(named, LOOSE) and reads(journal) == reads(capture)


# ---------------------------------------------------------------- a real journal, on a developer's machine

@pytest.mark.skipif(not os.environ.get('MWJ_REAL_JOURNAL_COPY'), reason='set MWJ_REAL_JOURNAL_COPY to a COPY of a journal')
def test_a_copy_of_a_real_journal_reads_the_same_once_packed(tmp_path):
    """Run by hand on a copy of a journal of real trades: MWJ_REAL_JOURNAL_COPY names the copy, which is copied again
    here and never written to."""
    original = Path(os.environ['MWJ_REAL_JOURNAL_COPY'])
    if not original.is_file():
        pytest.skip('MWJ_REAL_JOURNAL_COPY names no file')
    path = tmp_path / 'trades.sqlite3'
    shutil.copyfile(original, path)
    journal = Journal(path)
    now = dt.datetime.now(dt.timezone.utc)
    before = everything(journal, now=now), reads(journal, (), bounded=True)
    journal.pack_prices()
    assert not rows(path, LOOSE) and (everything(journal, now=now), reads(journal, (), bounded=True)) == before
