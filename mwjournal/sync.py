"""Background capture while the app runs, also usable without it: python -m mwjournal.sync.

Only the files named in saved connections are read. A bridge (NDJSON) is consumed in committed
lines, never a partially written tail, and the read position is kept in the journal so a restart
resumes where it stopped. A watched CSV is read whole and must be unchanged across that read.

A connection is a row in 'profiles': {'path', 'mode', 'enabled'} with mode one of MODES. The CSV
modes also read timezone, provider, account, point_value and mapping (executions) or symbol,
provider, timezone, resolution_seconds and proxy (prices). Its latest outcome is the row of the
same name in 'sync_status'.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import os
import threading
import time
from pathlib import Path

from . import paths
from .importers import apply_preview, preview_bridge, preview_csv, preview_prices_csv
from .metrics import measurements
from .model import heartbeat_report, instant, product
from .store import Journal
from .scope import allows_source

MODES = ('bridge', 'executions', 'prices')
DEFAULT_CONNECTION = 'MotiveWave'
CHECKPOINT = 256            # bytes remembered from just before the read position, to notice a rewrite in place


def _same_file(a, b) -> bool:
    a, b = (os.path.realpath(os.path.expanduser(str(p))) for p in (a, b))
    try:
        return os.path.samefile(a, b)
    except OSError:                                    # the study has not created the file yet
        return os.path.normcase(a) == os.path.normcase(b)


def ensure_default_connection(journal: Journal) -> bool:
    """Watch the study's default bridge file with no setup. True when the connection was just created.

    The list of connections stays the user's: one that already names that file is left exactly as
    it is, so a disabled one stays disabled, and a 'MotiveWave' connection pointed elsewhere is kept.
    """
    target = paths.bridge_file(journal.dataset)
    profiles = journal.get('profiles')
    if DEFAULT_CONNECTION in profiles or any(_same_file(p.get('path') or '', target) for p in profiles.values()):
        return False
    journal.put('profiles', DEFAULT_CONNECTION, {'path': str(target), 'mode': 'bridge', 'enabled': True})
    return True


class SyncWorker:
    def __init__(self, journal: Journal):
        self.journal = journal
        self.stop_event = threading.Event()
        self.hashes = {}
        self.metric_clock = 0
        self.dirty_executions = set()
        self.dirty_prices = {}      # (product, source): the time new prices span, for the trades of that product
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
        with path.open('rb') as fh:
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
            if offset:
                fh.seek(offset - len(checkpoint))
                if fh.read(len(checkpoint)) != checkpoint:
                    offset, checkpoint = 0, b''        # rewritten in place, even at the same size
            fh.seek(offset)
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
            mode = profile.get('mode')
            try:
                if mode not in MODES or not profile.get('path'):
                    raise ValueError('This connection needs a file path and a format: ' + ', '.join(MODES) + '.')
                path = Path(profile['path']).expanduser()
                if mode == 'bridge':
                    pending = self.bridge_lines(name, path)
                    if pending is None:
                        continue
                    blob, offset, position = pending
                    parsed = preview_bridge(blob.decode('utf-8-sig'), dataset=self.journal.dataset,
                                            skip_damaged=True)
                else:
                    stat = path.stat()
                    if stat.st_size > 32 * 1024 * 1024:
                        raise ValueError('CSV exceeds 32 MB. Split the export into smaller files.')
                    blob = path.read_bytes()
                    after = path.stat()
                    if (after.st_size, after.st_mtime_ns) != (stat.st_size, stat.st_mtime_ns):
                        continue
                    if not blob or not blob.endswith((b'\n', b'\r')):
                        continue                       # likely in the middle of an export rewrite
                    checksum = hashlib.sha256(blob + str(profile).encode()).hexdigest()
                    if self.hashes.get(name) == checksum:
                        continue
                    if mode == 'prices':
                        parsed = preview_prices_csv(blob.decode('utf-8-sig'), profile.get('symbol', ''),
                                                    profile.get('provider', 'my-bars'),
                                                    timezone=profile.get('timezone', 'UTC'),
                                                    resolution_seconds=profile.get('resolution_seconds', 60),
                                                    proxy=profile.get('proxy', False))
                    else:
                        parsed = preview_csv(blob.decode('utf-8-sig'),
                                             timezone=profile.get('timezone', 'America/New_York'),
                                             mapping=profile.get('mapping'), provider=profile.get('provider', 'csv'),
                                             account=profile.get('account', 'default'), point_value=profile.get('point_value'))
                if parsed.errors:
                    raise ValueError('; '.join(parsed.errors[:3]))
                result = apply_preview(self.journal, parsed)
                if result['unresolved']:
                    raise ValueError(result['unresolved'][0])
                if mode == 'bridge':
                    self.journal.set_bridge_offset(name, *position)   # only after its lines are in the journal
                else:
                    self.hashes[name] = checksum
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
                if mode == 'bridge':
                    # Counted over the part of this file read so far; visible, never silently dropped.
                    damaged = (previous.get('damaged_lines', 0) if offset else 0) + parsed.damaged
                    if damaged:
                        status['damaged_lines'] = damaged
                if heartbeat:
                    status['heartbeat'] = heartbeat
                    error, warnings = heartbeat_report(heartbeat)
                    if warnings:
                        status['warnings'] = warnings      # capture is working; these are shown beside it
                    if error:
                        status.update(status='needs attention', error=error)
                self.journal.put('sync_status', name, status)
            except FileNotFoundError:
                waiting = {'status': 'waiting for study', 'detail': 'Bridge file has not appeared yet. '
                           'Start the MotiveWave study.'} if mode == 'bridge' else {
                               'status': 'needs attention', 'error': 'The watched file does not exist.'}
                self.journal.put('sync_status', name, {'checked': now, **waiting})
            except (OSError, ValueError, UnicodeError) as exc:
                self.journal.put('sync_status', name, {'status': 'needs attention', 'checked': now, 'error': str(exc)})
        self.journal.reconcile_drafts()
        self.recalculate()

    def recalculate(self):
        """Late fills use cached prices; new live ticks do not rebuild every old closed trade."""
        clock = time.monotonic()
        if not self.rebuild and not self.dirty_executions and (not self.dirty_prices or clock - self.metric_clock < 5):
            return
        now = dt.datetime.now(dt.timezone.utc)
        for trade in self.journal.trades():
            if not trade.complete:
                continue                               # its excursions are unknown, whatever prices exist
            fills_changed = self.rebuild or (
                trade.provider, trade.account, trade.symbol, trade.currency) in self.dirty_executions
            if fills_changed:
                sources = self.journal.price_sources(trade.symbol, trade)
            else:
                sources = [source for (market, source), window in self.dirty_prices.items()
                           if market == product(trade.symbol) and allows_source(trade, source) and window[1] >= instant(trade.opened)
                           and window[0] < (instant(trade.closed) if trade.closed else now)]
            for source in sources:
                prices = self.journal.prices(trade.symbol, source, start=trade.opened, end=trade.closed)
                replay_clock = self.journal.trade_clock(trade)
                options = {'now': replay_clock} if replay_clock else {}
                for metric in measurements(trade, prices, source, **options):
                    self.journal.record_metric(trade, metric)
        if self.rebuild:
            self.journal.recalculation_done()
            self.rebuild = False
        self.dirty_executions.clear()
        self.dirty_prices.clear()
        self.metric_clock = clock

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
            self.stop_event.wait(1)


def main(argv=None):
    parser = argparse.ArgumentParser(prog='python -m mwjournal.sync',
                                     description='Capture into MotiveWave Journal without opening the app.')
    parser.add_argument('--once', action='store_true', help='one bounded pass over the saved connections, then exit')
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
