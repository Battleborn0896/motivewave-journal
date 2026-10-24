"""Accounting, excursions and the file watcher against known trade paths; no platform or network involved."""

import json
import os
import sqlite3

import pytest

from mwjournal import Execution, Journal, PriceSample, store, sync
from mwjournal.analytics import grouped, summary
from mwjournal.importers import (ALIASES, LABELS, Preview, apply_preview, csv_rows, is_bridge, mapping_for,
                                 missing_columns, preview_bridge, preview_csv)
from mwjournal.matching import match
from mwjournal.metrics import excursions, measurements
from mwjournal.model import ProtectionEvent, timestamp
from mwjournal.store import ImportConflict
from mwjournal.sync import SyncWorker


def fill(id, side, qty, price, minute=0, fee=0, symbol='ESZ6', account='paper'):
    return Execution('test', account, symbol, side, qty, price, f'2026-10-07T14:{minute:02d}:00Z',
                     execution_id=id, commission=fee, point_value=50)


def tick(minute, price, *, bid=None, ask=None, symbol='ESZ6'):
    return PriceSample(symbol, f'2026-10-07T14:{minute:02d}:30Z', 'test-prices', price, price,
                       bid=bid, ask=ask)


def line(id, side='buy', minute=0):
    return json.dumps({**fill(id, side, 1, 100, minute).__dict__, 'type': 'execution', 'version': 1}) + '\n'


def watched(tmp_path, text):
    journal = Journal(tmp_path / 'trades.db')
    file = tmp_path / 'bridge.ndjson'
    file.write_text(newline='', data=text)
    journal.put('profiles', 'bridge', {'path': str(file), 'mode': 'bridge', 'enabled': True})
    return journal, file


def restarted(journal):
    """What an app restart amounts to: a new worker with no memory, on the same database."""
    return SyncWorker(Journal(journal.path))


def parsed_texts(monkeypatch):
    """Every chunk of bridge text a worker hands to the parser from here on."""
    seen, original = [], sync.preview_bridge
    monkeypatch.setattr(sync, 'preview_bridge', lambda text, **options: seen.append(text) or original(text, **options))
    return seen


def test_scale_in_out_average_cost_and_fee_allocation():
    trades = match([fill('1', 'buy', 2, 100, fee=2), fill('2', 'sell', 1, 102, 1, fee=1),
                    fill('3', 'buy', 1, 105, 2, fee=1), fill('4', 'sell', 2, 103, 3, fee=2)])
    trade = trades[0]
    assert len(trades) == 1 and trade.closed and trade.open_qty == 0
    assert trade.entry_qty == 3 and trade.max_qty == 2
    assert trade.gross == pytest.approx(150)
    assert trade.net == pytest.approx(144)
    assert trade.entry_price == pytest.approx(305 / 3)


def test_reversal_splits_quantity_and_fees_across_two_round_trips():
    trades = match([fill('a', 'buy', 1, 100, fee=2), fill('b', 'sell', 3, 105, 1, fee=6),
                    fill('c', 'buy', 2, 101, 2, fee=4)])
    assert [t.direction for t in trades] == ['long', 'short']
    assert [t.gross for t in trades] == [250, 400]
    assert sum(t.commission for t in trades) == 12
    assert trades[1].entry_qty == 2 and trades[1].net == 392


def test_accounts_contract_expiries_and_currency_stay_separate():
    trades = match([fill('1', 'buy', 1, 100), fill('2', 'sell', 1, 101, account='other'),
                    fill('3', 'sell', 1, 101, symbol='ESH7')])
    assert len(trades) == 3 and all(t.closed is None for t in trades)


def test_deduplication_and_conflict_roll_back_whole_execution_batch(tmp_path):
    journal = Journal(tmp_path / 'trades.db')
    a = fill('a', 'buy', 1, 100)
    assert journal.ingest([(a, {})]) == 1
    assert journal.ingest([(a, {})]) == 0
    with pytest.raises(ImportConflict):
        journal.ingest([(fill('b', 'sell', 1, 110), {}), (fill('a', 'buy', 1, 101), {})])
    assert len(journal.executions()) == 1


def test_unknown_multiplier_is_not_invented_and_override_rebuilds(tmp_path):
    journal = Journal(tmp_path / 'trades.db')
    rows = preview_csv('symbol,side,quantity,price,timestamp,id\nABC,buy,2,100,2026-10-07T14:00:00Z,a\n'
                       'ABC,sell,2,101,2026-10-07T14:01:00Z,b\n')
    apply_preview(journal, rows)
    assert journal.trades()[0].net is None
    journal.set_instrument('ABC', 1)
    assert journal.trades()[0].net == 2


def test_long_short_excursions_use_only_the_holding_window():
    long = match([fill('a', 'buy', 1, 100), fill('b', 'sell', 1, 102, 3)])[0]
    samples = [tick(0, 98), tick(1, 105), tick(2, 103), tick(4, 999)]
    metrics = excursions(long, samples, 'test-prices')
    assert metrics['mae'] == 100 and metrics['mfe'] == 250
    assert metrics['precision'] == 'observed-ticks' and metrics['observations'] == 3
    short = match([fill('s1', 'sell', 1, 100), fill('s2', 'buy', 1, 98, 3)])[0]
    metrics = excursions(short, samples, 'test-prices')
    assert metrics['mae'] == 250 and metrics['mfe'] == 100


def test_dollar_excursions_of_a_scale_out_are_the_moves_of_the_contracts_held():
    trade = match([fill('a', 'buy', 2, 100), fill('b', 'sell', 1, 104, 1),
                   fill('c', 'sell', 1, 99, 3)])[0]
    metrics = excursions(trade, [tick(0, 98), tick(1, 106), tick(2, 97)], 'test-prices')
    # Each move is weighed by what was held then: 2 against on 2 contracts ($200) beats 3 on 1 ($150), and 4 for
    # on 2 at the partial exit ($400) beats 6 on 1 ($300). The points are those moments' moves, 2 and 4, not the
    # 3 and 6 the one contract left saw. The 4 points banked by the partial exit are not added: 0.4.7 said $500.
    assert (metrics['mae_points'], metrics['mfe_points']) == (2, 4)
    assert metrics['mae'] == 200 and metrics['mfe'] == 400


def test_a_scale_out_gives_dollar_mae_of_points_times_held_times_point_value():
    """The owner's example: long 2 at 100, sell 1 at 104, then a low of 99 with one still held."""
    nq = 20
    trade = match([fill('a', 'buy', 2, 100), fill('b', 'sell', 1, 104, 1), fill('c', 'sell', 1, 102, 3)],
                  {'ESZ6': nq})[0]
    metrics = excursions(trade, [tick(1, 99)], 'test-prices')
    assert metrics['mae_points'] == 1
    assert metrics['mae'] == metrics['mae_points'] * 1 * nq == 20       # 0.4.7 said $0: 4 banked points hid it
    assert metrics['path'][0]['held_qty'] == 1 and metrics['path'][0]['low'] == -20


def test_bar_boundary_is_estimated_and_missing_data_stays_null():
    trade = match([fill('a', 'buy', 1, 100), fill('b', 'sell', 1, 102, 3)])[0]
    bar = PriceSample('ESZ6', '2026-10-07T13:59:30Z', 'bars', 110, 90, 60)
    m = excursions(trade, [bar], 'bars')
    assert m['mae'] == 500 and m['mfe'] == 500
    assert m['precision'] == 'estimated-bars' and 'notes' not in m
    absent = excursions(trade, [tick(4, 999)], 'test-prices')
    assert absent['mae'] is None and absent['mfe'] is None and absent['precision'] == 'pending'


def test_bid_and_ask_play_no_part_in_mae_mfe():
    a = match([fill('a', 'buy', 1, 100), fill('b', 'sell', 1, 100, 2)])[0]
    b = match([fill('a', 'sell', 1, 100), fill('b', 'buy', 1, 100, 2)])[0]
    for trade in (a, b):
        quoted, = measurements(trade, [tick(0, 100, bid=99.75, ask=100.25)], 'test-prices')
        plain, = measurements(trade, [tick(0, 100)], 'test-prices')
        assert quoted['basis'] == 'last' and (quoted['mae'], quoted['mfe']) == (plain['mae'], plain['mfe']) == (0, 0)


def test_multiple_ticks_in_one_millisecond_retain_both_extremes(tmp_path):
    journal = Journal(tmp_path / 'trades.db')
    a = tick(0, 90)
    b = tick(0, 110)
    assert journal.ingest_prices([a, b, a]) == 2
    assert len(journal.prices('ESZ6', 'test-prices')) == 2


def test_price_window_reads_only_held_ticks_and_overlapping_boundary_bars(tmp_path):
    journal = Journal(tmp_path / 'trades.db')
    overlap = PriceSample('ESZ6', '2026-10-07T13:59:30Z', 'test-prices', 110, 90, 60)
    before = PriceSample('ESZ6', '2026-10-07T13:58:00Z', 'test-prices', 120, 80, 60)
    stale_tick = PriceSample('ESZ6', '2026-10-07T13:59:59Z', 'test-prices', 999, 999)
    journal.ingest_prices([overlap, before, stale_tick, tick(0, 102), tick(4, 999)])
    samples = journal.prices('ESZ6', 'test-prices', start='2026-10-07T14:00:00Z', end='2026-10-07T14:03:00Z')
    assert samples == [overlap, tick(0, 102)]


def test_journals_from_0_4_7_are_measured_again_once_and_lose_their_bid_ask_rows(tmp_path):
    """MEASUREMENT_RULES 2 and on: every trade is measured again from its stored ticks under the new dollar
    definition, and the bid/ask measurements 0.4.7 stored are removed, of trades still there and of trades long
    gone."""
    journal = Journal(tmp_path / 'trades.db')
    journal.ingest([(fill('a', 'buy', 2, 100), {}), (fill('b', 'sell', 1, 104, 1), {}), (fill('c', 'sell', 1, 102, 3), {})])
    trade, = journal.trades()
    journal.ingest_prices([tick(1, 99, bid=98.75, ask=99.25)])
    old = {'source': 'test-prices', 'precision': 'observed-ticks', 'mae': 0., 'mfe': 150., 'mae_points': 1,
           'mfe_points': 2, 'point_definition': 'held-average-entry', 'path': [], 'notes': [], 'observations': 1}
    journal.record_metric(trade, {**old, 'basis': 'last'})
    journal.record_metric(trade, {**old, 'basis': 'liquidation', 'mae': 12.5})
    with journal.connect() as db:                         # a bid/ask row of a trade whose fills have since changed
        db.execute('INSERT INTO metrics VALUES(?,?,?,?)', ('gone', 'old-basis', json.dumps(['test-prices', 'liquidation']),
                                                           json.dumps({**old, 'basis': 'liquidation'})))
        db.execute("UPDATE journal_meta SET value='1' WHERE key='measurement_rules'")
    assert journal.recalculation_due()
    worker = SyncWorker(Journal(journal.path))
    worker.recalculate()
    assert not journal.recalculation_due()
    metric, = journal.metric_options(trade)
    assert metric['basis'] == 'last' and metric['mae'] == 50 and metric['mae_points'] == 1   # 1 point x 1 held x $50
    with journal.connect() as db:
        assert db.execute("SELECT count(*) FROM metrics WHERE json_extract(source, '$[1]')='liquidation'").fetchone()[0] == 0
    # Once only.
    journal.record_metric(trade, {**old, 'basis': 'liquidation'})
    SyncWorker(Journal(journal.path)).recalculate()
    assert {m['basis'] for m in journal.metric_options(trade)} == {'last', 'liquidation'}


def test_capture_gaps_are_persistent_deduplicated_and_leave_the_measurement_unshown(tmp_path):
    import common
    journal = Journal(tmp_path / 'trades.db')
    journal.ingest([(fill('a', 'buy', 1, 100), {}), (fill('b', 'sell', 1, 101, 2), {})])
    gap = {'type': 'gap', 'provider': 'test-prices', 'ts': '2026-10-07T14:01:00Z', 'dropped': 12}
    assert apply_preview(journal, preview_bridge(json.dumps(gap)))['gaps'] == 1
    assert apply_preview(journal, preview_bridge(json.dumps(gap)))['gaps'] == 0
    trade = journal.trades()[0]
    journal.record_metric(trade, excursions(trade, [tick(0, 100)], 'test-prices'))
    assert len(journal.metric_options(trade)) == 1 and common.best_display_metric(journal, trade) == {}
    assert Journal(journal.path).capture_gaps()[0]['dropped'] == 12


def test_mixed_import_rolls_back_fills_and_prices_when_a_later_part_is_refused(tmp_path):
    journal = Journal(tmp_path / 'trades.db')
    foreign = ProtectionEvent('motivewave-native:workspace:replay:run-a', 'paper', 'ESZ6', 'stop',
                              '2026-10-07T14:00:00Z', 'stop', 'working', 99, entry_execution_id='new')
    preview = Preview(fills=[(fill('new', 'buy', 1, 100), {})], prices=[tick(0, 100)], protection=[foreign])
    with pytest.raises(ValueError, match='other trading environment'):
        apply_preview(journal, preview)
    assert not journal.executions() and not journal.prices('ESZ6', 'test-prices')


def test_unreadable_csv_is_a_plain_import_error():
    # An unbalanced quote swallows the rest of the file into one oversized field.
    text = 'symbol,side,qty,price,ts,notes\nESZ6,buy,1,100,2026-10-07T14:00:00Z,"' + 'x' * 140000 + '\n'
    with pytest.raises(ValueError, match='could not be read'):
        csv_rows(text)
    with pytest.raises(ValueError, match='could not be read'):
        preview_csv(text)


def test_a_csvs_columns_are_named_in_plain_words_and_a_bridge_file_is_told_apart():
    assert list(LABELS.values()) == ['Time', 'Date', 'Contract', 'Side', 'Contracts', 'Price', 'Fees', 'Account',
                                     'Fill ID', 'Order ID', 'Dollars per point', 'Currency']
    assert set(LABELS) == set(ALIASES)                     # one plain name for each field the importer reads
    with pytest.raises(ValueError, match='Choose a column for Contract, Price under Column Mapping'):
        preview_csv('Date,Time,Side,Qty Filled\n2026-10-07,10:00:23,Buy,1\n')
    headers = ['When', 'Ticker', 'Action', 'Filled', 'Px']
    mapping = {**mapping_for(headers), 'ts': 'When', 'qty': 'Filled', 'price': 'Px'}
    assert missing_columns(mapping_for(headers)) == ['Time', 'Contracts', 'Price']
    chosen = preview_csv('When,Ticker,Action,Filled,Px\n2026-10-07T14:00:00Z,ESZ6,Buy,0,5000\n', mapping=mapping)
    assert chosen.errors == ['Row 2: Contracts must be finite and greater than zero.']
    assert is_bridge('events.ndjson', '') and is_bridge('events.jsonl', '')
    assert is_bridge('export.txt', '\n' + line('a'))                   # JSON-object lines, whatever the name
    assert not is_bridge('fills.csv', 'symbol,side,qty,price,ts\n') and not is_bridge('fills.txt', '[1, 2]\n')


def test_invalid_csv_cannot_partially_import_and_nan_is_rejected(tmp_path):
    journal = Journal(tmp_path / 'trades.db')
    preview = preview_csv('symbol,side,qty,price,ts\nESZ6,buy,1,100,2026-10-07T14:00:00Z\n'
                          'ESZ6,sell,NaN,101,2026-10-07T14:01:00Z\n')
    assert len(preview.errors) == 1
    with pytest.raises(ValueError):
        apply_preview(journal, preview)
    assert not journal.executions()
    with pytest.raises(ValueError):
        timestamp('2026-11-01 01:30:00', 'America/New_York')
    with pytest.raises(ValueError):
        timestamp('10:30:00')
    with pytest.raises(ValueError):
        timestamp('10:30:00-0500')
    with pytest.raises(ValueError):
        timestamp(1791372600)


def test_malformed_bridge_events_are_preview_errors_not_uncaught_exceptions():
    assert len(preview_bridge('[]\nnull\n{"type":"tick","price":1}').errors) == 3
    bad_symbol = {'type': 'tick', 'provider': 'test-prices', 'symbol': None,
                  'ts': '2026-10-07T14:00:00Z', 'price': 100}
    assert preview_bridge(json.dumps(bad_symbol)).errors
    blank_fee = preview_csv('symbol,side,qty,price,ts,commission\nESZ6,buy,1,100,2026-10-07T14:00:00Z,\n')
    assert not blank_fee.fills[0][0].commission_known
    absent_fee = {k: v for k, v in fill('a', 'buy', 1, 100).__dict__.items() if k not in ('commission', 'commission_known')}
    assert not preview_bridge(json.dumps({**absent_fee, 'type': 'execution'})).fills[0][0].commission_known


def test_a_platforms_own_mae_mfe_report_is_skipped_and_counted(tmp_path):
    """Only recorded ticks measure a trade: a 'metric' event (MAE / MFE a platform reported) is an event this
    version does not read, skipped and counted like any other."""
    journal = Journal(tmp_path / 'trades.db')
    journal.ingest([(fill('a', 'buy', 1, 100), {}), (fill('b', 'sell', 1, 101, 2), {})])
    trade = journal.trades()[0]
    journal.record_metric(trade, excursions(trade, [tick(0, 99)], 'test-prices'))
    event = {'type': 'metric', 'provider': 'test', 'account': 'paper', 'symbol': 'ESZ6',
             'entry_execution_id': 'a', 'units': 'currency', 'definition': 'position-equity',
             'mae': 25, 'mfe': 100}
    preview = preview_bridge(json.dumps(event))
    assert not preview.errors and preview.unknown == 1 and not preview.count
    apply_preview(journal, preview)
    assert [m['source'] for m in journal.metric_options(trade)] == ['test-prices']
    # Late executions/multiplier changes invalidate calculations bound to the former fill set.
    journal.set_instrument('ESZ6', 5)
    assert journal.metric_options(journal.trades()[0]) == []


def test_file_watcher_handles_rewrites_restart_duplicates_and_partial_ndjson(tmp_path):
    journal = Journal(tmp_path / 'trades.db')
    file = tmp_path / 'bridge.ndjson'
    e = {**fill('a', 'buy', 1, 100).__dict__, 'type': 'execution', 'version': 1}
    event = json.dumps(e) + '\n'
    file.write_text(newline='', data=event + '{"unfinished":')
    journal.put('profiles', 'bridge', {'path': str(file), 'mode': 'bridge', 'enabled': True})
    worker = SyncWorker(journal)
    worker.poll()
    assert len(journal.executions()) == 1
    assert journal.get('sync_status')['bridge']['status'] == 'synced'
    SyncWorker(journal).poll()
    assert len(journal.executions()) == 1
    file.write_text(newline='', data=json.dumps({**fill('b', 'sell', 1, 102, 2).__dict__, 'type': 'execution'}) + '\n')
    # Truncation resets the offset; an overwritten same-size file is covered in an additional regression.
    worker.poll()
    assert len(journal.executions()) == 2 and journal.trades()[0].closed


def test_ndjson_rewritten_in_place_at_the_same_size_does_not_lose_fills(tmp_path):
    journal = Journal(tmp_path / 'trades.db')
    file = tmp_path / 'bridge.ndjson'
    a = json.dumps({**fill('a', 'buy', 1, 100).__dict__, 'type': 'execution'}) + '\n'
    b = json.dumps({**fill('b', 'buy', 1, 100).__dict__, 'type': 'execution'}) + '\n'
    assert len(a) == len(b)
    file.write_text(newline='', data=a)
    journal.put('profiles', 'bridge', {'path': str(file), 'mode': 'bridge', 'enabled': True})
    worker = SyncWorker(journal)
    worker.poll()
    file.write_text(newline='', data=b)
    worker.poll()
    assert len(journal.executions()) == 2


def test_restarted_worker_resumes_after_the_lines_already_consumed(tmp_path, monkeypatch):
    journal, file = watched(tmp_path, line('a') + line('b', 'sell', 1))
    seen = parsed_texts(monkeypatch)
    first = SyncWorker(journal)
    assert first.thread.name == 'mwj-sync'
    first.poll()
    assert seen == [line('a') + line('b', 'sell', 1)] and len(journal.executions()) == 2
    restarted(journal).poll()
    assert len(seen) == 1                          # nothing new, so nothing is read or parsed again
    beat = json.dumps({'type': 'heartbeat', 'provider': 'test', 'ts': '2026-10-07T14:05:00Z',
                       'status': 'connected'}) + '\n'
    with file.open('a', newline='') as fh:
        fh.write(line('c', 'buy', 2) + beat[:20])
    restarted(journal).poll()
    assert seen[1:] == [line('c', 'buy', 2)]       # only the new committed line, not the partial tail
    assert len(journal.executions()) == 3
    with file.open('a', newline='') as fh:
        fh.write(beat[20:])                        # the writer finishes its line; it is shorter than a checkpoint
    restarted(journal).poll()
    with file.open('a', newline='') as fh:
        fh.write(line('d', 'sell', 3))
    restarted(journal).poll()
    assert seen[2:] == [beat, line('d', 'sell', 3)] and len(journal.executions()) == 4
    identity, position, checkpoint = journal.bridge_offset('bridge')
    assert position == file.stat().st_size and checkpoint == file.read_bytes()[-256:]
    assert identity == str(file.stat().st_ino)


def test_bridge_lines_ending_in_crlf_import_and_resume(tmp_path):
    # The study writes the platform line separator, so on Windows every event ends in CRLF.
    journal, file = watched(tmp_path, (line('a') + line('b', 'sell', 1)).replace('\n', '\r\n'))
    SyncWorker(journal).poll()
    assert len(journal.executions()) == 2
    with file.open('a', newline='') as fh:
        fh.write(line('c', 'buy', 2).replace('\n', '\r\n'))
    restarted(journal).poll()
    assert len(journal.executions()) == 3


def test_truncated_or_replaced_bridge_restarts_from_zero_across_a_restart(tmp_path, monkeypatch):
    journal, file = watched(tmp_path, line('a') + line('b', 'sell', 1))
    SyncWorker(journal).poll()
    seen = parsed_texts(monkeypatch)
    file.write_text(newline='', data=line('c', 'buy', 2))           # truncated in place: shorter than the stored position
    restarted(journal).poll()
    assert seen == [line('c', 'buy', 2)] and len(journal.executions()) == 3
    # Another file moved over the path. It even begins with the bytes already consumed, so only
    # its identity says it is not the file that was being read.
    replacement = tmp_path / 'new.ndjson'
    replacement.write_text(newline='', data=line('c', 'buy', 2) + line('d', 'sell', 3) + line('e', 'buy', 4))
    os.replace(replacement, file)
    restarted(journal).poll()
    assert seen[1:] == [file.read_bytes().decode()] and len(journal.executions()) == 5
    assert len(line('e')) == len(line('f'))
    file.write_text(newline='', data=line('c', 'buy', 2) + line('d', 'sell', 3) + line('f', 'buy', 4))    # same file and size, new last line
    restarted(journal).poll()
    assert seen[2:] == [file.read_bytes().decode()] and len(journal.executions()) == 6
    restarted(journal).poll()
    assert len(seen) == 3 and len(journal.executions()) == 6


@pytest.mark.parametrize('damage', [
    'position=position+100000',                    # beyond the end of the file
    'position=-5',
    "position='soon'",
    'position=position-7',                         # inside a line: the checkpoint no longer matches the file
    "checkpoint=x'0a'",
    "checkpoint='text'",
    "identity='another-file'",
])
def test_corrupted_stored_offset_rereads_the_file_instead_of_skipping_or_failing(tmp_path, monkeypatch, damage):
    journal, file = watched(tmp_path, line('a') + line('b', 'sell', 1))
    SyncWorker(journal).poll()
    with sqlite3.connect(journal.path) as db:
        assert db.execute('UPDATE bridge_offsets SET ' + damage).rowcount == 1
    with file.open('a', newline='') as fh:
        fh.write(line('c', 'buy', 2))
    seen = parsed_texts(monkeypatch)
    restarted(journal).poll()
    assert seen == [file.read_bytes().decode()]              # read again from the beginning; duplicates are harmless
    assert len(journal.executions()) == 3 and journal.get('sync_status')['bridge']['status'] == 'synced'
    assert journal.bridge_offset('bridge')[1] == file.stat().st_size    # and the stored record is repaired
    restarted(journal).poll()
    assert len(seen) == 1


def test_line_cut_short_by_a_crash_is_counted_and_capture_continues(tmp_path):
    # The writer died mid-line and its next run appended straight after the fragment.
    journal, file = watched(tmp_path, line('a') + '{"version":1,"type":"tick","pro' + line('lost'))
    SyncWorker(journal).poll()
    status = journal.get('sync_status')['bridge']
    assert [e.execution_id for e in journal.executions()] == ['a']
    assert status['status'] == 'synced' and status['damaged_lines'] == 1
    with file.open('a', newline='') as fh:
        fh.write(line('c', 'sell', 2))
    restarted(journal).poll()
    assert len(journal.executions()) == 2 and journal.get('sync_status')['bridge']['damaged_lines'] == 1
    assert preview_bridge(file.read_bytes().decode()).errors            # a manual import still refuses the file
    # Well-formed JSON that is wrong is a setup problem, not crash debris, and still stops the watcher.
    with file.open('a', newline='') as fh:
        fh.write('{"version":2,"type":"execution"}\n' + line('d', 'buy', 3))
    restarted(journal).poll()
    assert 'Unsupported bridge protocol version' in journal.get('sync_status')['bridge']['error']
    assert len(journal.executions()) == 2


def test_one_broken_connection_is_reported_without_starving_the_others(tmp_path):
    journal, _ = watched(tmp_path, line('a'))
    broken = tmp_path / 'broken.ndjson'
    broken.write_text(newline='', data='{"version":2,"type":"execution"}\n')
    journal.put('profiles', 'broken', {'path': str(broken), 'mode': 'bridge', 'enabled': True})
    # A watched CSV of an earlier version is no connection any more (store._one_connection removes it).
    journal.put('profiles', 'csv', {'path': str(tmp_path / 'fills.csv'), 'mode': 'executions', 'enabled': True})
    journal.put('profiles', 'pathless', {'mode': 'bridge', 'enabled': True})
    journal.put('profiles', 'missing', {'path': str(tmp_path / 'none.ndjson'), 'mode': 'bridge', 'enabled': True})
    journal.put('profiles', 'zz-last', {'path': str(tmp_path / 'bridge.ndjson'), 'mode': 'bridge', 'enabled': True})
    SyncWorker(journal).poll()
    status = journal.get('sync_status')
    assert 'Unsupported bridge protocol version' in status['broken']['error']
    assert 'bridge file' in status['csv']['error'] and 'bridge file' in status['pathless']['error']
    assert status['missing']['status'] == 'waiting for study'
    assert status['bridge']['status'] == status['zz-last']['status'] == 'synced'
    assert len(journal.executions()) == 1


def test_worker_failure_is_cleared_once_polling_works_again(tmp_path, monkeypatch):
    journal = Journal(tmp_path / 'trades.db')
    journal.put('sync_status', 'worker', {'status': 'needs attention', 'error': 'left by an earlier run'})
    worker, seen = SyncWorker(journal), []

    def poll():
        seen.append(journal.get('sync_status').get('worker'))
        if len(seen) == 1:
            raise RuntimeError('database is locked')
        if len(seen) == 3:
            worker.stop_event.set()
    monkeypatch.setattr(worker, 'poll', poll)
    monkeypatch.setattr(worker.stop_event, 'wait', lambda seconds: None)
    worker.run()
    assert seen[1]['error'] == 'RuntimeError' and seen[2] is None
    assert 'worker' not in journal.get('sync_status')


def test_watcher_recalculates_late_fills_and_only_price_windows_affected(tmp_path, monkeypatch):
    journal = Journal(tmp_path / 'trades.db')
    journal.ingest([(fill('a', 'buy', 1, 100), {}), (fill('b', 'sell', 1, 101, 2), {})])
    bridge = tmp_path / 'ticks.ndjson'
    journal.put('profiles', 'ticks', {'path': str(bridge), 'mode': 'bridge', 'enabled': True})
    event = {'type': 'tick', 'provider': 'test-prices', 'symbol': 'ESZ6', 'ts': tick(0, 98).ts, 'price': 98}
    bridge.write_text(newline='', data=json.dumps(event) + '\n')
    worker = SyncWorker(journal)
    worker.poll()
    assert journal.metric_options(journal.trades()[0])[0]['mae'] == 100
    called = []
    original = store.measurements
    monkeypatch.setattr(store, 'measurements', lambda *args, **kw: called.append(args[0].id) or original(*args, **kw))
    bridge.write_text(newline='', data=json.dumps(event) + '\n' + json.dumps({**event, 'ts': tick(10, 999).ts, 'price': 999}) + '\n')
    worker.poll()
    assert not called                      # current ticks must not rebuild historical closed trades
    bridge.write_text(newline='', data=bridge.read_bytes().decode() + json.dumps({**fill('c', 'buy', 1, 100, 10).__dict__, 'type': 'execution'}) + '\n')
    worker.poll()
    assert called                          # a late fill rebuilds its basis against already-cached ticks


def test_current_schema_and_a_newer_database_is_refused_untouched(tmp_path):
    journal = Journal(tmp_path / 'trades.db')
    journal.ingest([(fill('a', 'buy', 1, 100), {})])
    with sqlite3.connect(journal.path) as db:
        assert db.execute('PRAGMA user_version').fetchone()[0] == store.SCHEMA_VERSION == 4
        db.execute('PRAGMA user_version=5')
    with pytest.raises(ValueError, match='newer version of MotiveWave Journal'):
        Journal(journal.path)
    with sqlite3.connect(journal.path) as db:
        assert db.execute('PRAGMA user_version').fetchone()[0] == 5
        assert db.execute('SELECT count(*) FROM executions').fetchone()[0] == 1
    assert not [p.name for p in tmp_path.iterdir() if 'backup' in p.name]


def test_reviews_and_sqlite_backup_remain_portable(tmp_path):
    journal = Journal(tmp_path / 'trades.db')
    journal.ingest([(fill('a', 'buy', 1, 100), {})])
    trade = journal.trades()[0]
    journal.annotate(trade.id, {'tags': ['patient', 'patient', '  '], 'initial_risk': 100})
    backup = tmp_path / 'backup.sqlite3'
    backup.write_bytes(journal.backup())
    restored = Journal(backup)
    assert restored.annotations()[trade.id]['tags'] == ['patient']
    assert len(restored.executions()) == 1


def test_analytics_excludes_open_positions_and_missing_prices():
    trades = match([fill('a', 'buy', 1, 100), fill('b', 'sell', 1, 102, 1),
                    fill('c', 'buy', 1, 100, 2), fill('d', 'sell', 1, 99, 3),
                    fill('e', 'buy', 1, 100, 4)])
    rows = [{**t.row(), 'tags': ['A+']} for t in trades]
    stat = summary(rows)
    assert stat['count'] == 2 and stat['expectancy'] == 25
    assert stat['drawdown'] == 50 and stat['profit_factor'] == 2
    assert grouped(rows, 'tags')[0]['count'] == 2
    by_side = grouped([{**row, 'direction': 'short' if row['net'] and row['net'] < 0 else 'long'} for row in rows],
                      'direction')
    assert [(g['group'], g['net']) for g in by_side] == [('short', -50), ('long', 100)]     # the worst Net P&L first
