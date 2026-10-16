"""Preview before commit: nothing reaches the journal until a whole file or batch has parsed."""

from __future__ import annotations

import csv
import io
import json
from dataclasses import dataclass, field

from .model import Execution, PriceSample, ProtectionEvent, finite, instant, timestamp
from .scope import event_mode

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
    reports: list = field(default_factory=list)
    gaps: list = field(default_factory=list)
    protection: list = field(default_factory=list)
    heartbeats: list = field(default_factory=list)
    dataset: str | None = None
    errors: list[str] = field(default_factory=list)
    skipped: int = 0
    damaged: int = 0                      # bridge lines that were not JSON at all, when the caller skips them

    @property
    def count(self):
        return len(self.fills) + len(self.prices) + len(self.reports) + len(self.gaps) + len(self.protection)


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


def mapping_for(headers: list[str]) -> dict:
    lower = {h.strip().lower(): h for h in headers}
    return {field: next((lower[a] for a in aliases if a in lower), '') for field, aliases in ALIASES.items()}


def preview_csv(text: str, *, timezone='America/New_York', mapping=None,
                provider='csv', account='default', point_value=None) -> Preview:
    """One row per individual fill. Cumulative per-order exports are not fills and do not belong here."""
    headers, rows = csv_rows(text)
    columns = mapping if mapping is not None else mapping_for(headers)
    missing = [name for name in ('symbol', 'side', 'qty', 'price', 'ts') if not columns.get(name)]
    if missing:
        raise ValueError('Map these required columns: ' + ', '.join(missing))
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
            if value('date') and str(columns.get('ts', '')).strip().lower() in ('time', 'fill time'):
                stamp = value('date') + ' ' + stamp
            fill = Execution(provider, value('account', account), value('symbol'), value('side'),
                             finite(value('qty'), 'Quantity'), finite(value('price'), 'Price'),
                             timestamp(stamp, timezone), value('execution_id'), value('order_id'),
                             finite(value('commission', 0), 'Commission'),
                             finite(pv, 'Point value', True) if pv is not None and pv != '' else None,
                             value('currency', 'USD'), commission_known=value('commission') != '')
            result.fills.append((fill, raw))
        except (ValueError, TypeError) as exc:
            result.errors.append(f'Row {n}: {exc}')
    return result


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
            mode = event_mode(event)
            if dataset is not None and mode != dataset:
                raise ValueError('Bridge event belongs to the other trading environment; choose its matching file.')
            kind = event.get('type')
            if kind == 'execution':
                fields = {k: event[k] for k in Execution.__dataclass_fields__ if k in event}
                fields['ts'] = timestamp(event['ts'], timezone)
                fields.setdefault('commission_known', 'commission' in event)
                result.fills.append((Execution(**fields), event))
            elif kind in ('tick', 'quote', 'bar'):
                duration = event.get('resolution_seconds', 0)
                if kind == 'bar' and finite(duration, 'Bar duration') <= 0:
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
            elif kind == 'metric':
                # Reported values are currency magnitudes using the same gross-equity definition.
                if event.get('units') != 'currency' or event.get('definition') != 'position-equity':
                    raise ValueError('Metrics need units=currency and definition=position-equity.')
                if not isinstance(event.get('provider'), str) or not event['provider'].strip():
                    raise ValueError('A metric needs its provider identity.')
                if not event.get('trade_id') and not all(event.get(k) for k in ('account', 'symbol', 'entry_execution_id')):
                    raise ValueError('A metric needs trade_id or account/symbol/entry_execution_id.')
                if event.get('basis', 'last') not in ('last', 'liquidation'):
                    raise ValueError('Metric basis must be last or liquidation.')
                observations = finite(event.get('observations', 0), 'Observation count')
                if observations < 0 or not observations.is_integer():
                    raise ValueError('Observation count must be a non-negative whole number.')
                report = dict(event, mae=finite(event['mae'], 'MAE'), mfe=finite(event['mfe'], 'MFE'),
                              observations=int(observations))
                if report['mae'] < 0 or report['mfe'] < 0:
                    raise ValueError('MAE and MFE must be non-negative magnitudes.')
                result.reports.append(report)
            elif kind == 'gap':
                if not isinstance(event.get('provider'), str) or not event['provider'].strip():
                    raise ValueError('A capture gap needs its provider identity.')
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
                        raise ValueError('Capture gap end precedes its start.')
                result.gaps.append(gap)
            elif kind == 'heartbeat':
                heartbeat = dict(event, ts=timestamp(event['ts'], timezone))
                if heartbeat.get('status') not in (None, 'connected', 'error'):
                    raise ValueError('Unknown connector heartbeat status.')
                result.heartbeats.append(heartbeat)
                result.skipped += 1
            else:
                raise ValueError(f'Unknown event type {kind!r}.')
        except (ValueError, KeyError, TypeError) as exc:
            result.errors.append(f'Line {n}: {exc}')
    return result


def apply_preview(journal, preview: Preview) -> dict:
    if preview.errors:
        raise ValueError('Import rejected. Fix the rows shown in the preview; nothing was imported.')
    if preview.dataset is not None and preview.dataset != journal.dataset:
        raise ValueError('Preview belongs to the other trading environment.')
    with journal.connect() as db:
        before = db.total_changes
        added = journal.ingest(preview.fills, db=db) if preview.fills else 0
        # Fills already held whose writer described them again: a reworded quality label, or the
        # platform's position id. Their trades are rebuilt, so the caller is told.
        relabelled = db.total_changes - before - added
        prices = journal.ingest_prices(preview.prices, db=db)
        gaps = journal.ingest_gaps(preview.gaps, db=db)
        protection = journal.ingest_protection(preview.protection, db=db)
    reports = 0
    unresolved = []
    trades = journal.trades() if preview.reports else []
    for report in preview.reports:
        candidates = [t for t in trades if t.id == report.get('trade_id')] if report.get('trade_id') else [
            t for t in trades if t.provider == report.get('provider') and t.account == report.get('account')
            and t.symbol == str(report.get('symbol', '')).upper()
            and t.fills[0]['execution_id'] == report.get('entry_execution_id')]
        if len(candidates) == 1 and not candidates[0].complete:
            continue    # the fills held here are not the whole position, so no excursion is attached to them
        if len(candidates) != 1 or not candidates[0].closed:
            unresolved.append('Platform metric needs one matching closed trade; import its executions first.')
            continue
        trade = candidates[0]
        if report.get('currency', 'USD') != trade.currency:
            unresolved.append('Platform metric currency differs from the trade currency.')
            continue
        journal.record_metric(trade, {'source': report['provider'] + ':reported', 'precision': 'platform-reported',
                                     'mae': report['mae'], 'mfe': report['mfe'], 'basis': report.get('basis', 'last'),
                                     'observations': report.get('observations', 0), 'path': [],
                                     'notes': ['Reported by the platform; definition: gross realised + open P&L.'],
                                     'through': trade.closed})
        reports += 1
    return {'executions': added, 'prices': prices, 'metrics': reports, 'gaps': gaps, 'protection': protection,
            'duplicates': len(preview.fills) - added, 'relabelled': relabelled, 'unresolved': unresolved}


def preview_prices_csv(text: str, symbol: str, source: str, *, timezone='UTC', resolution_seconds=60,
                       proxy=False) -> Preview:
    headers, rows = csv_rows(text)
    columns = {h.strip().lower(): h for h in headers}
    def get(row, names, default=None):
        return next((row[columns[n]] for n in names if n in columns and row[columns[n]] != ''), default)
    result = Preview()
    for n, row in enumerate(rows, 2):
        try:
            stamp = get(row, ['ts', 'timestamp', 'datetime', 'date'])
            # A MotiveWave data export may split Date and Time into separate columns.
            if 'date' in columns and 'time' in columns and not any(k in columns for k in ('ts', 'timestamp', 'datetime')):
                stamp = f"{get(row, ['date'], '')} {get(row, ['time'], '')}"
            if stamp is None:
                raise ValueError('Need a timestamp/datetime column.')
            row_symbol = get(row, ['symbol'], symbol)
            price = get(row, ['last', 'price', 'close'])
            high, low = get(row, ['high'], price), get(row, ['low'], price)
            result.prices.append(PriceSample(row_symbol, timestamp(stamp, timezone), source, high, low,
                                            resolution_seconds, get(row, ['bid']), get(row, ['ask']), proxy,
                                            get(row, ['open']) if resolution_seconds else None,
                                            get(row, ['close']) if resolution_seconds else None,
                                            get(row, ['volume'])))
        except (ValueError, TypeError) as exc:
            result.errors.append(f'Row {n}: {exc}')
    return result
