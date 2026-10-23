"""The prints the study recorded away from every trade, removed once when 0.4.14 first opens a journal: invented
fills and prices only. The window of a trade runs from 2 minutes before its first fill to 120 minutes after its last;
an open trade's runs on."""
import datetime as dt
import sqlite3
import types

import pytest

from mwjournal import Execution, Journal, PriceSample, journals, store
from mwjournal.metrics import measurements
from mwjournal.post_exit import best_post_exit
from mwjournal.sync import SyncWorker

DAY, NEXT = '2026-10-21', '2026-10-22'
LIVE, HISTORY, BARS = 'motivewave-ticks', 'motivewave-ticks:history', 'motivewave-bars:1m'
STUDY = 'motivewave-native:ws'


def fill(id, side, clock, price=20000., *, symbol='NQZ6', account='sim', provider=STUDY, day=DAY):
    return Execution(provider, account, symbol, side, 1, price, f'{day}T{clock}Z', execution_id=id)


def tick(clock, price=20000., source=LIVE, *, symbol='NQZ6', day=DAY):
    return PriceSample(symbol, f'{day}T{clock}Z', source, price, price)


def bar(clock, source=BARS, *, symbol='NQZ6', day=DAY):
    return PriceSample(symbol, f'{day}T{clock}Z', source, 20010., 19990., 60, open=20000., close=20005., volume=10)


def stored(path):
    """Every price as (rowid, source, symbol, time), in the order stored."""
    db = sqlite3.connect(path)
    try:
        return db.execute('SELECT rowid, source, symbol, ts FROM prices ORDER BY rowid').fetchall()
    finally:
        db.close()


def meta(path, key):
    db = sqlite3.connect(path)
    try:
        row = db.execute('SELECT value FROM journal_meta WHERE key=?', (key,)).fetchone()
        return row[0] if row else None
    finally:
        db.close()


def as_0_4_13(path):
    """The journal as 0.4.13 left it: no record that its prices were trimmed."""
    db = sqlite3.connect(path)
    with db:
        db.execute("DELETE FROM journal_meta WHERE key='prices_trimmed'")
    db.close()


def without(rows, *gone):
    """`rows` less those of the (source, symbol, time) given."""
    gone = {(source, symbol, f'{day}T{clock}+00:00') for source, symbol, day, clock in gone}
    return [row for row in rows if row[1:] not in gone]


def live_day(path):
    """A live journal of one day: an NQ round trip 14:00-14:10, an ES one 18:00-18:05, an NQ position opened at 21:00
    and still open. The study recorded every print all day, its candles and some history; the trader imported prints
    and candles of their own. Prices move, so a print used that should not be would change a measurement."""
    journal = Journal(path)
    journal.ingest([(fill('a-in', 'buy', '14:00:00'), {}), (fill('a-out', 'sell', '14:10:00', 20008.), {}),
                    (fill('b-in', 'sell', '18:00:00', 5000., symbol='ESZ6'), {}),
                    (fill('b-out', 'buy', '18:05:00', 4998., symbol='ESZ6'), {}),
                    (fill('c-in', 'buy', '21:00:00', 20050.), {})])
    journal.ingest_prices([
        bar('12:00:00'), tick('12:00:00', 21000., HISTORY), tick('12:00:00', 19000., 'my-ticks'),
        tick('13:57:59', 25000.), tick('13:58:00', 19990.), tick('14:04:00', 19995., HISTORY),
        tick('14:05:00', 20012.), tick('14:05:00', 19992.), tick('14:20:00', 20030.), tick('16:10:00', 19980.),
        tick('16:10:01', 15000.), bar('17:00:00'), bar('17:00:00', 'my-bars'), tick('17:00:00', 25000.),
        tick('17:59:00', 5001., symbol='ESZ6'), tick('18:03:00', 5003., symbol='ESZ6'), tick('19:00:00', 20020.),
        tick('20:30:00', 6000., symbol='ESZ6'), tick('20:58:00', 20040.), tick('21:30:00', 20070.),
        tick('03:00:00', 20100., day=NEXT)])
    return journal


AWAY = (('motivewave-ticks:history', 'NQZ6', DAY, '12:00:00'), (LIVE, 'NQZ6', DAY, '13:57:59'),
        (LIVE, 'NQZ6', DAY, '16:10:01'), (LIVE, 'NQZ6', DAY, '17:00:00'), (LIVE, 'ESZ6', DAY, '20:30:00'))


def test_prints_away_from_every_trade_go_and_candles_imports_and_prints_near_a_trade_stay(tmp_path):
    path = tmp_path / 'trades.sqlite3'
    live_day(path)
    before = stored(path)
    assert meta(path, 'prices_trimmed') == '0'           # a journal 0.4.14 makes has nothing recorded all day
    as_0_4_13(path)
    journal = Journal(path)
    # Kept: candles, imported prices, and prints inside a window, its edges and other markets' included, each with its
    # rowid (prices sharing a time are read in that order). An open trade's window runs on past midnight.
    assert stored(path) == without(before, *AWAY)
    assert meta(path, 'prices_trimmed') == '5'
    note = journal.upgrade_note()['prices']
    assert (note['removed'], note['compacted']) == (5, True)
    assert note['note'] == 'Freed under 1 MB of price data recorded away from your trades.'


def test_windows_that_overlap_or_nest_are_kept_whole(tmp_path):
    path = tmp_path / 'trades.sqlite3'
    journal = Journal(path)
    # 10:00-12:00 holds a trade of another account 10:30-10:31, and a third starts as the first window ends.
    journal.ingest([(fill('x-in', 'buy', '10:00:00', account='a'), {}), (fill('x-out', 'sell', '12:00:00', account='a'), {}),
                    (fill('y-in', 'buy', '10:30:00', account='b'), {}), (fill('y-out', 'sell', '10:31:00', account='b'), {}),
                    (fill('z-in', 'buy', '13:59:00', account='c'), {}), (fill('z-out', 'sell', '14:00:00', account='c'), {})])
    journal.ingest_prices([tick('09:57:59'), tick('09:58:00'), tick('13:00:00'), tick('15:00:00'), tick('16:00:00'),
                           tick('16:00:01')])
    as_0_4_13(path)
    Journal(path)
    assert [ts[11:19] for _, _, _, ts in stored(path)] == ['09:58:00', '13:00:00', '15:00:00', '16:00:00']


def test_a_position_a_fill_without_a_price_left_open_keeps_only_the_prints_near_its_fills(tmp_path):
    path = tmp_path / 'trades.sqlite3'
    journal = Journal(path)
    # The platform stored no price for this entry (it reports 0), so the position stays open in the journal for
    # good, months on, and is never measured: the owner's journal holds one from June. Its window ends as a closed
    # trade's would; running on to now, it would keep every print recorded since.
    journal.ingest([(fill('z-in', 'buy', '09:00:00', 0., account='old'), {}),
                    (fill('a-in', 'buy', '14:00:00'), {}), (fill('a-out', 'sell', '14:10:00', 20008.), {})])
    assert [(t.status, t.closed) for t in journal.trades()][0] == ('incomplete', None)
    journal.ingest_prices([tick(clock) for clock in ('08:57:59', '09:30:00', '11:00:00', '11:00:01', '13:00:00',
                                                     '14:05:00', '20:00:00')])
    as_0_4_13(path)
    Journal(path)
    assert [ts[11:19] for _, _, _, ts in stored(path)] == ['09:30:00', '11:00:00', '14:05:00']


def test_a_replay_journal_loses_every_runs_prints_away_from_its_trades_and_keeps_the_replayed_days_candles(tmp_path):
    path = tmp_path / 'replay' / 'trades.sqlite3'
    journal = Journal(path, dataset='replay')
    run = 'motivewave-native:ws:replay:r1'
    journal.ingest([(fill('r-in', 'buy', '14:00:00', provider=run), {}), (fill('r-out', 'sell', '14:10:00', provider=run), {})])
    prints = [f'motivewave-replay:r1:{kind}' for kind in ('ticks', 'simulated', 'history')] + ['motivewave-replay:r2:ticks']
    journal.ingest_prices([tick(clock, source=source) for source in prints for clock in ('09:00:00', '14:05:00', '17:00:00')]
                          + [bar(clock, source) for source in ('motivewave-replay:bars', 'motivewave-replay:r1:bars')
                             for clock in ('09:00:00', '17:00:00')])
    before = stored(path)
    as_0_4_13(path)
    Journal(path, dataset='replay')
    assert stored(path) == without(before, *[(source, 'NQZ6', DAY, clock) for source in prints for clock in ('09:00:00', '17:00:00')])


def test_no_measurement_changes_and_none_is_measured_again(tmp_path):
    path = tmp_path / 'trades.sqlite3'
    journal = live_day(path)
    trades = journal.trades()
    assert [t.status for t in trades] == ['closed', 'closed', 'open']
    for trade in trades:
        for source in journal.price_sources(trade.symbol, trade):
            journal.measure(trade, source)
    now = dt.datetime(2026, 10, 22, 4, tzinfo=dt.timezone.utc)

    def read():
        """What measures each trade: the prices it is measured from and its post-exit prices (to 120 minutes),
        measured afresh, its post-exit result, and the stored measurements with their rowids."""
        held = {}
        for trade in trades:
            for source in (LIVE, HISTORY):
                samples = journal.prices(trade.symbol, source, start=trade.opened, end=trade.closed)
                after = (journal.prices(trade.symbol, source, start=trade.closed,
                                        end=(dt.datetime.fromisoformat(trade.closed) + store.PRINTS_AFTER).isoformat())
                         if trade.closed else [])
                held[trade.id, source] = (samples, after, measurements(trade, samples, source, now=now))
            held[trade.id] = best_post_exit(journal, trade) if trade.closed else None
        with journal.connect() as db:
            held['metrics'] = [tuple(row) for row in db.execute('SELECT rowid, * FROM metrics ORDER BY rowid')]
        return held
    before = read()
    assert before[trades[0].id, LIVE][0] and before[trades[0].id, LIVE][1] and before[trades[0].id]['observations']
    as_0_4_13(path)
    journal = Journal(path)
    assert meta(path, 'prices_trimmed') == '5'
    assert read() == before
    # Measured again from what is stored: the same measurements, and nothing asks for another pass.
    for trade in trades[:2]:
        for source in journal.price_sources(trade.symbol, trade):
            options = journal.metric_options(trade)
            journal.measure(trade, source)
            assert journal.metric_options(trade) == options
    assert not journal.recalculation_due() and not SyncWorker(journal).rebuild


def test_it_runs_once_and_never_on_a_journal_this_version_made(tmp_path):
    path = tmp_path / 'trades.sqlite3'
    live_day(path)
    as_0_4_13(path)
    journal = Journal(path)
    after, note = stored(path), journal.upgrade_note()
    journal.ingest_prices([tick('10:00:00', day='2026-10-20')])       # far from any trade, recorded from now on
    for _ in range(2):
        assert Journal(path).upgrade_note() == note and meta(path, 'prices_trimmed') == '5'
    assert stored(path)[:-1] == after and len(stored(path)) == len(after) + 1
    fresh = Journal(tmp_path / 'new.sqlite3')
    fresh.ingest([(fill('a-in', 'buy', '14:00:00'), {}), (fill('a-out', 'sell', '14:10:00'), {})])
    fresh.ingest_prices([tick('20:00:00')])
    assert len(stored(Journal(fresh.path).path)) == 1 and fresh.upgrade_note() is None


def test_an_interrupted_trim_leaves_the_journal_as_it_was_and_completes_on_the_next_open(tmp_path, monkeypatch):
    path = tmp_path / 'trades.sqlite3'
    live_day(path)
    as_0_4_13(path)
    before = stored(path)
    trim = store._prints_away_from_trades

    def cut_short(db):
        trim(db)                                           # every print away from a trade is gone, then the process dies
        raise KeyboardInterrupt
    monkeypatch.setattr(store, '_prints_away_from_trades', cut_short)
    with pytest.raises(KeyboardInterrupt):
        Journal(path)
    assert stored(path) == before and meta(path, 'prices_trimmed') is None
    monkeypatch.undo()
    Journal(path)
    assert stored(path) == without(before, *AWAY) and meta(path, 'prices_trimmed') == '5'


def test_the_file_shrinks_by_what_the_note_says(tmp_path):
    path = tmp_path / 'trades.sqlite3'
    journal = Journal(path)
    journal.ingest([(fill('a-in', 'buy', '14:00:00'), {}), (fill('a-out', 'sell', '14:10:00'), {})])
    start = dt.datetime(2026, 10, 21, 16, 30, tzinfo=dt.timezone.utc)      # after the window: a session of prints
    journal.ingest_prices([PriceSample('NQZ6', (start + dt.timedelta(seconds=i / 4)).isoformat(), LIVE,
                                       20000 + i % 40 / 4, 20000 + i % 40 / 4, volume=1 + i % 7) for i in range(16000)])
    journal.ingest_prices([tick('14:05:00')])
    as_0_4_13(path)
    size = path.stat().st_size
    assert size > 5e6
    note = Journal(path).upgrade_note()['prices']
    assert len(stored(path)) == 1 and path.stat().st_size < size / 5
    assert note['freed'] == size - path.stat().st_size and note['compacted']
    assert note['note'] == f"Freed {note['freed'] / 1e6:,.0f} MB of price data recorded away from your trades."


def test_without_room_on_the_disk_it_waits_or_leaves_the_file_its_size(tmp_path, monkeypatch):
    path = tmp_path / 'trades.sqlite3'
    live_day(path)
    as_0_4_13(path)
    before, size = stored(path), path.stat().st_size
    monkeypatch.setattr(store.shutil, 'disk_usage', lambda folder: types.SimpleNamespace(free=0))
    assert Journal(path).upgrade_note() is None
    assert stored(path) == before and meta(path, 'prices_trimmed') is None      # nothing yet: tried at the next open
    room = iter([10 ** 12, 0])                  # room to remove the prints, none to compact the file after
    monkeypatch.setattr(store.shutil, 'disk_usage', lambda folder: types.SimpleNamespace(free=next(room)))
    note = Journal(path).upgrade_note()['prices']
    assert stored(path) == without(before, *AWAY) and path.stat().st_size == size
    assert (note['removed'], note['compacted']) == (5, False)
    assert note['note'].startswith('Removed ') and 'The file keeps its size' in note['note']
    monkeypatch.undo()
    assert Journal(path).upgrade_note()['prices'] == note                     # once, even so


def test_a_named_journal_trims_its_own_copies_by_its_own_trades(tmp_path, monkeypatch):
    monkeypatch.setenv('MWJ_HOME', str(tmp_path))
    capture = Journal()
    capture.ingest([(fill('a-in', 'buy', '14:00:00'), {}), (fill('a-out', 'sell', '14:10:00'), {}),
                    (fill('d-in', 'buy', '18:00:00', account='other'), {}),
                    (fill('d-out', 'sell', '18:05:00', account='other'), {})])
    capture.ingest_prices([tick('14:05:00'), tick('19:00:00'), tick('23:00:00'), bar('23:00:00')])
    id = journals.create(capture, 'Sim only', [(STUDY, 'sim')])
    named = journals.journal_path(capture, id)
    assert len(stored(named)) == 4                         # the mirror copies every price of the traded market
    as_0_4_13(capture.path)
    as_0_4_13(named)
    capture = Journal()
    assert [ts[11:19] for _, _, _, ts in stored(capture.path)] == ['14:05:00', '19:00:00', '23:00:00']   # print, print, bar
    journals.open_named(capture, id, refresh=False)
    # Its one trade's window: the other account's print goes from this copy, and the candle stays.
    assert [(source, ts[11:19]) for _, source, _, ts in stored(named)] == [(LIVE, '14:05:00'), (BARS, '23:00:00')]
    assert meta(named, 'prices_trimmed') == '2'
    journals.open_named(capture, id)
    # The capture journal's prints still come across, all of them near one of its trades.
    assert sorted(ts[11:19] for _, _, _, ts in stored(named)) == ['14:05:00', '19:00:00', '23:00:00']
