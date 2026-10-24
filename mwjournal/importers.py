"""Preview before commit: nothing reaches the journal until a whole file or batch has parsed.

A file is a bridge file the study wrote (is_bridge) or a CSV of fills, one row per fill, its columns found by their
names (ALIASES) or chosen by the trader under their plain names (LABELS)."""

from __future__ import annotations

import csv
import io
import json
import re
from dataclasses import dataclass, field

from .model import Execution, PriceSample, ProtectionEvent, finite, instant, timestamp
from .scope import event_mode

# Each field of a fill, by the name a trader knows it by, in the order Column Mapping asks for them.
# A Time value that is only a time of day takes the row's Date, whatever either column is called.
TIME_OF_DAY = re.compile(r'\d{1,2}:\d{2}(:\d{2}([.,]\d+)?)?(\s*[AaPp][Mm])?$')
LABELS = {'ts': 'Time', 'date': 'Date', 'symbol': 'Contract', 'side': 'Side', 'qty': 'Contracts', 'price': 'Price',
          'commission': 'Fees', 'account': 'Account', 'execution_id': 'Fill ID', 'order_id': 'Order ID',
          'point_value': 'Dollars per point', 'currency': 'Currency'}
REQUIRED = ('ts', 'symbol', 'side', 'qty', 'price')
# The column names each field is found by, without asking.
ALIASES = {
    'symbol': ('symbol', 'instrument', 'ticker'),
    'side': ('side', 'buy/sell', 'action', 'direction'),
    'qty': ('qty', 'quantity', 'size', 'qty filled', 'filled qty'),
    'price': ('price', 'fill price', 'avg fill price', 'average fill price', 'avg price', 'average price'),
    'ts': ('ts', 'timestamp', 'datetime', 'date/time', 'fill time', 'time'),
    'date': ('date', 'fill date'),
    'execution_id': ('execution_id', 'execution id', 'fill id', 'trade id'),
    'order_id': ('order_id', 'order id', 'order number'),
    'account': ('account', 'account id'),
    'commission': ('commission', 'fees'),
    'point_value': ('point_value', 'point value', 'multiplier'),
    'currency': ('currency',),
}


@dataclass
class Preview:
    fills: list = field(default_factory=list)
    prices: list = field(default_factory=list)
    gaps: list = field(default_factory=list)
    protection: list = field(default_factory=list)
    heartbeats: list = field(default_factory=list)
    dataset: str | None = None
    errors: list[str] = field(default_factory=list)
    skipped: int = 0
    damaged: int = 0                      # bridge lines that were not JSON at all, when the caller skips them
    unknown: int = 0                      # bridge events of a type this version does not read, skipped

    @property
    def count(self):
        return len(self.fills) + len(self.prices) + len(self.gaps) + len(self.protection)


def csv_rows(text: str) -> tuple[list[str], list[dict]]:
    try:
        dialect = csv.Sniffer().sniff(text[:8192], delimiters=',;\t')
    except csv.Error:
        dialect = csv.excel
    reader = csv.DictReader(io.StringIO(text.lstrip('\ufeff')), dialect=dialect)
    try:
        if not reader.fieldnames or any(h is None or not str(h).strip() for h in reader.fieldnames):
            raise ValueError('The CSV needs a header row with named columns.')
        return reader.fieldnames, list(reader)
    except csv.Error as exc:               # e.g. an unbalanced quote swallowing the rest of the file
        raise ValueError(f'The CSV could not be read: {exc}. Check its quoting and delimiter.') from exc


def is_bridge(name: str, text: str) -> bool:
    """A bridge file: named .ndjson or .jsonl, or its first line a JSON object. Anything else is read as a CSV."""
    if name.lower().endswith(('.ndjson', '.jsonl')):
        return True
    first = next((line for line in text.split('\n') if line.strip()), '')
    try:
        return isinstance(json.loads(first), dict)
    except ValueError:
        return False


def mapping_for(headers: list[str]) -> dict:
    lower = {h.strip().lower(): h for h in headers}
    return {field: next((lower[a] for a in aliases if a in lower), '') for field, aliases in ALIASES.items()}


def missing_columns(mapping: dict) -> list[str]:
    """The plain names of the required fields no column is chosen for."""
    return [LABELS[name] for name in REQUIRED if not mapping.get(name)]


def preview_csv(text: str, *, timezone='America/New_York', mapping=None,
                provider='csv', account='default', point_value=None) -> Preview:
    """One row per individual fill. Cumulative per-order exports are not fills and do not belong here."""
    headers, rows = csv_rows(text)
    columns = mapping if mapping is not None else mapping_for(headers)
    missing = missing_columns(columns)
    if missing:
        raise ValueError('Choose a column for ' + ', '.join(missing) + ' under Column Mapping.')
    result = Preview()
    for n, raw in enumerate(rows, start=2):
        if not any(v for v in raw.values()):
            continue
        try:
            if None in raw:
                raise ValueError('The row has extra fields; check the CSV delimiter.')
            def value(key, default=''):
                return raw.get(columns.get(key), default) or default
            pv = value('point_value', point_value)
            stamp = value('ts')
            if value('date') and TIME_OF_DAY.match(stamp.strip()):
                stamp = value('date') + ' ' + stamp
            fill = Execution(provider, value('account', account), value('symbol'), value('side'),
                             finite(value('qty'), 'Contracts'), finite(value('price'), 'Price'),
                             timestamp(stamp, timezone), value('execution_id'), value('order_id'),
                             finite(value('commission', 0), LABELS['commission']),
                             finite(pv, LABELS['point_value'], True) if pv is not None and pv != '' else None,
                             value('currency', 'USD'), commission_known=value('commission') != '')
            result.fills.append((fill, raw))
        except (ValueError, TypeError) as exc:
            result.errors.append(f'Row {n}: {exc}')
    return result


# The event types this version reads (docs/protocol.md); any other is skipped and counted (Preview.unknown),
# a 'metric' (MAE / MFE a platform reported) included: only recorded ticks measure a trade.
KNOWN = ('execution', 'tick', 'quote', 'bar', 'protection', 'gap', 'heartbeat')


def preview_bridge(text: str, *, timezone='UTC', allow_partial_tail=False, dataset=None,
                   skip_damaged=False) -> Preview:
    if dataset is not None and dataset not in ('live', 'replay'):
        raise ValueError('Journal dataset must be live or replay.')
    result = Preview(dataset=dataset)
    # Only a newline ends an event. splitlines() would also cut at U+2028 and its kin, which JSON
    # allows unescaped inside a string such as an account name or an error detail.
    lines = text.split('\n')
    if allow_partial_tail and text and not text.endswith('\n'):
        lines = lines[:-1]                  # a writer has not committed its final event yet
    for n, line in enumerate(lines, 1):
        if not line.strip():
            continue
        try:
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                if not skip_damaged:
                    raise
                # A writer killed mid-line leaves a fragment that its next run appends to. The unattended
                # watcher counts such a line and moves on; one bad line must not stop capture for good.
                result.damaged += 1
                continue
            if not isinstance(event, dict):
                raise ValueError('Each bridge line must be a JSON object.')
            if event.get('version', 1) != 1:
                raise ValueError('Unsupported bridge protocol version.')
            kind = event.get('type')
            if kind not in KNOWN:
                # A newer study may write events this version does not know: skipped and counted, so the
                # fills, stops and heartbeats around them still arrive.
                result.unknown += 1
                continue
            mode = event_mode(event)
            if dataset is not None and mode != dataset:
                raise ValueError('Bridge event belongs to the other trading environment; choose its matching file.')
            if kind == 'execution':
                fields = {k: event[k] for k in Execution.__dataclass_fields__ if k in event}
                fields['ts'] = timestamp(event['ts'], timezone)
                fields.setdefault('commission_known', 'commission' in event)
                result.fills.append((Execution(**fields), event))
            elif kind in ('tick', 'quote', 'bar'):
                duration = event.get('resolution_seconds', 0)
                if kind == 'bar' and finite(duration, 'Bar length') <= 0:
                    raise ValueError('A bar needs a positive resolution_seconds.')
                high = event.get('high', event.get('last', event.get('price')))
                low = event.get('low', high)
                result.prices.append(PriceSample(event['symbol'], timestamp(event['ts'], timezone),
                                                event['provider'], high, low, duration,
                                                event.get('bid'), event.get('ask'), event.get('proxy', False),
                                                event.get('open'), event.get('close'), event.get('volume')))
            elif kind == 'protection':
                fields = {k: event[k] for k in ProtectionEvent.__dataclass_fields__ if k in event}
                fields['ts'] = timestamp(event['ts'], timezone)
                result.protection.append(ProtectionEvent(**fields))
            elif kind == 'gap':
                if not isinstance(event.get('provider'), str) or not event['provider'].strip():
                    raise ValueError('A recording gap needs its provider.')
                missing = finite(event.get('dropped'), 'Dropped ticks', True)
                if not missing.is_integer():
                    raise ValueError('Dropped ticks must be a whole number.')
                gap = {**event, 'dropped': int(missing)}
                if gap.get('ts'):
                    gap['ts'] = timestamp(gap['ts'], timezone)
                else:
                    gap.pop('ts', None)
                if event.get('start_ts') or event.get('end_ts'):
                    gap['start_ts'] = timestamp(event['start_ts'], timezone)
                    gap['end_ts'] = timestamp(event['end_ts'], timezone)
                    if instant(gap['end_ts']) < instant(gap['start_ts']):
                        raise ValueError('A recording gap ends before it starts.')
                result.gaps.append(gap)
            else:
                heartbeat = dict(event, ts=timestamp(event['ts'], timezone))
                if heartbeat.get('status') not in (None, 'connected', 'error'):
                    raise ValueError('Unknown connector heartbeat status.')
                result.heartbeats.append(heartbeat)
                result.skipped += 1
        except (ValueError, KeyError, TypeError) as exc:
            result.errors.append(f'Line {n}: {exc}')
    return result


def apply_preview(journal, preview: Preview) -> dict:
    if preview.errors:
        raise ValueError('Import rejected. Fix the rows shown in the preview; nothing was imported.')
    if preview.dataset is not None and preview.dataset != journal.dataset:
        raise ValueError('Preview belongs to the other trading environment.')
    with journal.connect() as db:
        db.execute('BEGIN IMMEDIATE')          # one import, and a relabel count no other writer can add to
        before = journal.fills_revision(db)
        added = journal.ingest(preview.fills, db=db) if preview.fills else 0
        # Fills already held whose writer described them again: a reworded quality label, or the
        # platform's position id. Their trades are rebuilt, so the caller is told.
        relabelled = journal.fills_revision(db) - before
        prices = journal.ingest_prices(preview.prices, db=db)
        gaps = journal.ingest_gaps(preview.gaps, db=db)
        protection = journal.ingest_protection(preview.protection, db=db)
    return {'executions': added, 'prices': prices, 'gaps': gaps, 'protection': protection,
            'duplicates': len(preview.fills) - added, 'relabelled': relabelled}
