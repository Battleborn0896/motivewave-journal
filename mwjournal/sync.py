"""Background recording while the app runs, also usable without it: python -m mwjournal.sync.

The journal reads one file, the study's bridge file (paths.bridge_file), named in its one saved connection. It is
consumed in committed lines, never a partially written tail, and the read position is kept in the journal so a
restart resumes where it stopped; it is also written beside the file (write_receipt), so the study empties a file
read to its end.

A connection is a row in 'profiles': {'path', 'mode', 'enabled'} with mode 'bridge' (MODES). Its latest outcome
is the row of the same name in 'sync_status'.
"""

from __future__ import annotations

import argparse
import base64
import datetime as dt
import os
import threading
import time
from pathlib import Path

from . import paths
from .importers import apply_preview, preview_bridge
from .model import heartbeat_report, instant, product
from .store import DEFAULT_CONNECTION, Journal
from .scope import allows_source

MODES = ('bridge',)
# How often the watcher reads the files. The study writes the bridge file at least every quarter second,
# and an open trade's chart is drawn again every second (app/views/trades.py LIVE).
POLL_SECONDS = 1
# How often the watcher packs the prints stored before its previous pass (Journal.pack_prices): as it starts, then
# hourly, so a print stays a row for one to two hours.
PACK_SECONDS = 3600
CHECKPOINT = 256            # bytes remembered from just before the read position, to notice a rewrite in place


def write_receipt(path: Path, position: int, checkpoint: bytes):
    """Tells the study how far the journal has read and stored its bridge file: `<file>.read`, two lines, the
    position and the CHECKPOINT bytes just before it in base64. The study empties a file read to its end
    (docs/protocol.md, Read receipt). Written whole or not at all, and a receipt that cannot be written only means
    the file is not emptied yet."""
    receipt = path.with_name(path.name + '.read')
    partial = receipt.with_name(f'{receipt.name}.{os.getpid()}.partial')
    try:
        partial.write_bytes(b'%d\n%s\n' % (position, base64.b64encode(checkpoint)))
        os.replace(partial, receipt)
    except OSError:
        try:
            partial.unlink(missing_ok=True)
        except OSError:
            pass


def ensure_default_connection(journal: Journal) -> bool:
    """Watch the study's bridge file with no setup, and keep reading it: reading trades is always on. True when the
    connection was just created.

    The connection is the 'MotiveWave' one, switched on and pointed at the file the study writes on this computer
    (paths.bridge_file). A journal that points elsewhere (an earlier version's own choice of file, or a backup
    restored from another computer or data folder) is pointed back at it, and what the other file said is cleared.
    Run at every start, so a second run changes nothing.
    """
    profile = journal.get('profiles').get(DEFAULT_CONNECTION)
    study = {'path': str(paths.bridge_file(journal.dataset)), 'mode': 'bridge', 'enabled': True}
    if profile != study:
        journal.put('profiles', DEFAULT_CONNECTION, study)
        if profile is not None and profile.get('path') != study['path']:
            journal.clear_status(DEFAULT_CONNECTION)
    return profile is None


class SyncWorker:
    def __init__(self, journal: Journal):
        self.journal = journal
        self.stop_event = threading.Event()
        self.dirty_executions = set()
        self.dirty_prices = {}      # (product, source): the time new prices span, for the trades of that product
        self.measure_after = 0      # time.monotonic() before which new prices alone are not measured (recalculate)
        self.pack_after = 0         # time.monotonic() at which prices are packed next: at the first poll, then hourly
        self.trade_list, self.trades_version = [], None
        # Measured under older rules, or regrouped by an upgrade: measure every trade again, once.
        self.rebuild = journal.recalculation_due()
        self.thread = threading.Thread(target=self.run, name='mwj-sync', daemon=True)

    def start(self):
        self.thread.start()
        return self

    def stop(self):
        self.stop_event.set()
        self.thread.join(timeout=3)

    def bridge_lines(self, name: str, path: Path):
        """(new committed lines, where they start, the position to store once imported), or None."""
        with path.open('rb', buffering=0) as fh:          # each read is of the file as it is then
            stat = os.fstat(fh.fileno())               # the file being read, not a later one at this path
            # The device number is left out: it can change across a reboot, which would re-read the
            # whole file, and the checkpoint bytes already tell two files apart.
            identity = str(stat.st_ino)
            stored = self.journal.bridge_offset(name)
            offset, checkpoint = stored[1:] if stored and stored[0] == identity else (0, b'')
            # A stored position is used only while it describes this file: a replaced or truncated
            # file, or a damaged record, starts again from zero. Reading lines twice is harmless.
            if not (0 < offset <= stat.st_size and len(checkpoint) == min(offset, CHECKPOINT)
                    and checkpoint.endswith(b'\n')):
                offset, checkpoint = 0, b''
            fh.seek(offset)
            blob = fh.read(8 * 1024 * 1024)
            # The bytes before the position are checked after the read, not before: the study empties a file read
            # to its end (docs/protocol.md, Read receipt) and can write past the position again before the read
            # above, which then gave the middle of the new lines. Rewritten in place, even at the same size, too.
            if offset:
                fh.seek(offset - len(checkpoint))
                if fh.read(len(checkpoint)) != checkpoint:
                    offset, checkpoint = 0, b''
                    fh.seek(0)
                    blob = fh.read(8 * 1024 * 1024)
        blob = blob[:blob.rfind(b'\n') + 1]
        if not blob:
            return None
        # The checkpoint comes from the bytes just read, so it cannot describe a later rewrite.
        return blob, offset, (identity, offset + len(blob), (checkpoint + blob)[-CHECKPOINT:])

    def poll(self):
        for name, profile in self.journal.get('profiles').items():
            if not profile.get('enabled'):
                continue
            now = dt.datetime.now(dt.timezone.utc).isoformat()
            try:
                if profile.get('mode') not in MODES or not profile.get('path'):
                    raise ValueError('This connection needs the path of a bridge file.')
                path = Path(profile['path']).expanduser()
                pending = self.bridge_lines(name, path)
                if pending is None:
                    continue
                blob, offset, position = pending
                parsed = preview_bridge(blob.decode('utf-8-sig'), dataset=self.journal.dataset, skip_damaged=True)
                if parsed.errors:
                    raise ValueError('; '.join(parsed.errors[:3]))
                result = apply_preview(self.journal, parsed)
                if result['unresolved']:
                    raise ValueError(result['unresolved'][0])
                self.journal.set_bridge_offset(name, *position)   # only after its lines are in the journal
                write_receipt(path, *position[1:])                # and only once that is stored
                if result['executions'] or result['relabelled']:
                    self.dirty_executions.update((e.provider, e.account, e.symbol, e.currency) for e, _ in parsed.fills)
                if result['prices']:
                    for sample in parsed.prices:
                        key = (product(sample.symbol), sample.source)
                        start = instant(sample.ts)
                        stop = start + dt.timedelta(seconds=sample.resolution_seconds)
                        old = self.dirty_prices.get(key, (start, stop))
                        self.dirty_prices[key] = (min(start, old[0]), max(stop, old[1]))
                if result['gaps']:
                    for gap in parsed.gaps:
                        start = instant(gap.get('start_ts', gap.get('ts'))) if gap.get('start_ts') or gap.get('ts') else dt.datetime.min.replace(tzinfo=dt.timezone.utc)
                        stop = instant(gap['end_ts']) if gap.get('end_ts') else start if gap.get('ts') else dt.datetime.max.replace(tzinfo=dt.timezone.utc)
                        for trade in self.journal.trades():
                            key = (product(trade.symbol), gap['provider'])
                            old = self.dirty_prices.get(key, (start, stop))
                            self.dirty_prices[key] = (min(start, old[0]), max(stop, old[1]))
                previous = self.journal.get('sync_status').get(name, {})
                heartbeat = parsed.heartbeats[-1] if parsed.heartbeats else previous.get('heartbeat')
                status = {'status': 'synced', 'checked': now, **result}
                # Counted over the part of this file read so far; visible, never silently dropped.
                damaged = (previous.get('damaged_lines', 0) if offset else 0) + parsed.damaged
                if damaged:
                    status['damaged_lines'] = damaged
                unknown = (previous.get('unknown_events', 0) if offset else 0) + parsed.unknown
                if unknown:
                    status['unknown_events'] = unknown
                if heartbeat:
                    status['heartbeat'] = heartbeat
                    error, warnings = heartbeat_report(heartbeat)
                    if warnings:
                        status['warnings'] = warnings      # capture is working; these are shown beside it
                    if error:
                        status.update(status='needs attention', error=error)
                self.journal.put('sync_status', name, status)
            except FileNotFoundError:
                self.journal.put('sync_status', name, {'checked': now, 'status': 'waiting for study',
                                                       'detail': 'Bridge file has not appeared yet. '
                                                                 'Start the MotiveWave study.'})
            except (OSError, ValueError, UnicodeError) as exc:
                # A file that cannot be read does not undo the study's last report: Settings still knows it was
                # heard from, and does not ask to install the study or add it to a chart again.
                heard = self.journal.get('sync_status').get(name, {}).get('heartbeat')
                self.journal.put('sync_status', name, {'status': 'needs attention', 'checked': now, 'error': str(exc),
                                                       **({'heartbeat': heard} if heard else {})})
        # Drafts are matched at every poll, as cancelling one can settle another; from the kept trades.
        self.journal.reconcile_drafts(self.trades())
        self.recalculate()
        if time.monotonic() >= self.pack_after:        # at the first poll, then once an hour
            self.pack_after = time.monotonic() + PACK_SECONDS
            self.journal.pack_prices()

    def trades(self):
        """The journal's trades, built again only when its fills or multipliers changed: new prices arrive at
        nearly every poll and change no trade."""
        version = self.journal.fills_version()
        if version != self.trades_version:
            self.trade_list, self.trades_version = self.journal.trades(), version
            self.journal.reattach_reviews(self.trade_list)      # regrouped fills keep their reviews
        return self.trade_list

    def recalculate(self):
        """New fills measure only the trades whose fills they changed: a trade's basis is a fingerprint of its
        fills, so those are the trades with no measurement stored for their basis from a source, and each is
        measured from entry with the prices already stored. New prices measure only the trades they overlap,
        which while trading is the open one, so its MAE / MFE follow the market at every poll (POLL_SECONDS).
        New prices alone wait at least as long as the last measurement took, so a long-held trade with a long
        tape keeps the watcher busy half the time at most and the file is still read every poll. After an
        upgrade (rebuild) every trade is measured again from entry, once."""
        fills = bool(self.rebuild or self.dirty_executions)    # a bool: the set is cleared below
        if not fills and (not self.dirty_prices or time.monotonic() < self.measure_after):
            return
        began = time.monotonic()
        now = dt.datetime.now(dt.timezone.utc)
        measured = set() if self.rebuild else self.journal.measured() if fills else None
        sources = {}                                   # every source of a contract's prices, read once per contract
        for trade in self.trades():
            if not trade.complete:
                continue                               # its excursions are unknown, whatever prices exist
            fresh = []
            if fills:
                if trade.symbol not in sources:
                    sources[trade.symbol] = self.journal.price_sources(trade.symbol)
                fresh = [source for source in sources[trade.symbol] if allows_source(trade, source)
                         and (trade.id, trade.basis, source) not in measured]
            for source in fresh:
                self.journal.measure(trade, source)
            for (market, source), window in self.dirty_prices.items():
                # New prices alone: from where the trade's measurement stopped, when they all come after it.
                if (source not in fresh and market == product(trade.symbol) and allows_source(trade, source)
                        and window[1] >= instant(trade.opened)
                        and window[0] < (instant(trade.closed) if trade.closed else now)):
                    self.journal.measure(trade, source, since=window[0])
        if self.rebuild:
            self.journal.recalculation_done()
            self.rebuild = False
        self.dirty_executions.clear()
        self.dirty_prices.clear()
        # Only a measurement of new prices alone holds back the next one; after fills, prices are measured at once.
        self.measure_after = 0 if fills else time.monotonic() + (time.monotonic() - began)

    def run(self):
        failed = True                                  # a failure recorded by an earlier run is cleared too
        while not self.stop_event.is_set():
            try:
                self.poll()
                if failed:
                    self.journal.clear_status('worker')
                    failed = False
            except Exception as exc:
                failed = True
                try:
                    self.journal.put('sync_status', 'worker', {'status': 'needs attention',
                                                            'error': type(exc).__name__})
                except Exception:
                    pass                               # the database is unavailable; keep trying, do not end capture
            self.stop_event.wait(POLL_SECONDS)


def main(argv=None):
    parser = argparse.ArgumentParser(prog='python -m mwjournal.sync',
                                     description='Capture into MotiveWave Journal without opening the app.')
    parser.add_argument('--once', action='store_true', help='one bounded pass over the bridge file, then exit')
    parser.add_argument('--dataset', choices=paths.DATASETS, default='live')
    args = parser.parse_args(argv)
    journal = Journal(dataset=args.dataset)
    ensure_default_connection(journal)
    worker = SyncWorker(journal)
    if args.once:
        worker.poll()
        worker.poll()
    else:
        try:
            worker.run()
        except KeyboardInterrupt:
            worker.stop_event.set()


if __name__ == '__main__':
    main()
