"""Pure, UTC-based trade chart payloads. No made-up candles or guessed stop orders."""

from __future__ import annotations

import datetime as dt
from collections import defaultdict

from .model import PriceSample, Trade, instant, trade_prices
from .risk import settles_at
from .scope import allows_source, is_history, is_simulated, provenance

STOP = '#F05B70'
ENTRY = '#58A6FF'
EXIT = '#E8B45A'


def chart_window(trade: Trade, padding_minutes=15, *, now=None):
    """From padding_minutes before entry to padding_minutes after exit. An open trade's ends at `now`, its replay
    clock (Journal.trade_clock), when it has one: a replay's later minutes are already stored, and a chart drawn
    while the trade is open must not show them. A live one has none and runs on past the wall clock."""
    start = instant(trade.opened) - dt.timedelta(minutes=padding_minutes)
    if trade.closed:
        end = instant(trade.closed) + dt.timedelta(minutes=padding_minutes)
    elif now is not None:
        end = now
    else:
        end = dt.datetime.now(dt.timezone.utc) + dt.timedelta(minutes=padding_minutes)
    # Bound local reads/rendering even for a position left open for months.
    end = min(end, start + dt.timedelta(minutes=10000))
    return start, end


def minute_candles(samples: list[PriceSample], symbol: str, source: str) -> tuple[list[dict], list[str]]:
    buckets, notes = defaultdict(list), []
    if is_simulated(source):
        notes.append('Generated/unknown replay ticks: these candles are not a verified historical tick path.')
    unusable = False
    samples = trade_prices(samples, symbol, source)
    for sample in sorted(samples, key=lambda s: s.ts):           # UTC ISO text sorts in time order
        stamp = instant(sample.ts).timestamp()
        seconds = sample.resolution_seconds
        # Never divide a 5m bar into fake 1m bars or invent O/C from a high/low range.
        if seconds and (seconds > 60 or 60 % seconds or stamp % seconds
                        or sample.open is None or sample.close is None):
            unusable = True
            continue
        buckets[int(stamp // 60) * 60].append(sample)
    data, ticks, partial = [], False, False
    for minute, group in sorted(buckets.items()):
        bars = [s for s in group if s.resolution_seconds == 60]
        if bars:
            s = bars[-1]
            point = {'time': minute, 'open': s.open, 'high': s.high, 'low': s.low, 'close': s.close}
            volume = s.volume
        else:
            # Subminute OHLC or observed ticks are aggregated, never interpolated through a gap.
            first, last = group[0], group[-1]
            point = {'time': minute, 'open': first.open if first.open is not None else first.high,
                     'high': max(s.high for s in group), 'low': min(s.low for s in group),
                     'close': last.close if last.close is not None else last.high}
            ticks |= any(s.resolution_seconds == 0 for s in group)
            partial |= sum(s.resolution_seconds for s in group) < 60
            volume = sum(s.volume for s in group) if all(s.volume is not None for s in group) else None
        if volume is not None:
            point['volume'] = volume
        data.append(point)
    if unusable:
        notes.append('Some prices cannot draw 1m candles: provide minute-aligned OHLC, smaller OHLC bars, or ticks.')
    if ticks and is_history(source):
        notes.append("Candles aggregate real prints loaded from the platform's history afterwards (history "
                     'backfill); the data service decides how much of each minute it returns.')
    elif ticks:
        notes.append('Candles aggregate observed ticks; recorder downtime can leave incomplete minutes.')
    elif partial:
        notes.append('Some minute candles contain only partial subminute history.')
    if any(b['time'] - a['time'] > 60 for a, b in zip(data, data[1:])):
        notes.append('Missing minutes are left blank (market closures or missing data); no prices are fabricated.')
    if any(s.proxy for s in samples):
        notes.append('Proxy/continuous-contract candles: fills still show your actual execution prices.')
    return data, notes


def protection_segments(trade: Trade, events, stop=None, *, now=None) -> list[dict]:
    """The stop the trade's R uses (risk.py), solid across the trade, and its linked stop orders from the
    moment that stop settled on, dashed: the moves that managed the trade. Take-profit orders are not drawn."""
    start = instant(trade.opened).timestamp()
    end = (instant(trade.closed) if trade.closed else (now or dt.datetime.now(dt.timezone.utc))).timestamp()
    settled = settles_at(trade).timestamp() if trade.fills else start
    result = []
    if stop is not None:
        result.append({'from': start, 'to': end, 'price': stop, 'label': 'Stop · R', 'color': STOP, 'style': 'solid'})

    def moved(left, right, price):
        left = max(left, settled)
        if right > left and price != stop:       # the stop R uses is drawn already
            result.append({'from': left, 'to': right, 'price': price, 'label': 'Stop order', 'color': STOP,
                           'style': 'dashed'})

    working = {}
    for event in sorted((e for e in events if e.role == 'stop'), key=lambda e: instant(e.ts)):
        stamp = instant(event.ts).timestamp()
        if stamp > end:
            continue
        old = working.pop(event.order_id, None)
        if old:
            moved(old[0], stamp, old[1])
        if event.state == 'working':
            working[event.order_id] = (stamp, event.price)
    for left, price in working.values():
        moved(left, end, price)
    return result


def trade_chart(trade: Trade, samples: list[PriceSample], source: str, stop=None, events=(), *, now=None,
                prints: list[PriceSample] = (), prints_source: str = '') -> dict:
    """`stop` is the price the trade's R uses (risk.plan), `events` its linked protective orders. `prints`,
    of `prints_source`, draw the minutes after the last candle of `source`: the one in progress, before
    the platform has its candle. One chart, as on any trading platform."""
    if not allows_source(trade, source):
        samples = []
    data, notes = minute_candles(samples, trade.symbol, source)
    if prints and allows_source(trade, prints_source):
        after = data[-1]['time'] + 60 if data else 0
        forming, _ = minute_candles([p for p in prints if instant(p.ts).timestamp() >= after], trade.symbol, prints_source)
        if forming:
            data += forming
            notes.append('Minutes after the last platform candle, usually just the one in progress, are drawn from '
                         "the prints the study recorded; the platform's candle replaces each when it arrives.")
    bars = {b['time'] for b in data}
    fills = []
    for i, fill in enumerate(trade.fills):
        if not fill['price']:
            # Drawn at zero it would flatten every candle. The fill stays in the trade's own list.
            notes.append('One or more fills have no price from the platform; they are not drawn on the chart.')
            continue
        stamp = instant(fill['ts']).timestamp()
        # A minute chart places seconds proportionately within their actual candle. No nearest-bar snapping.
        if int(stamp // 60) * 60 not in bars:
            notes.append('One or more fills have no corresponding candle; their markers are not snapped to another minute.')
        fills.append({'time': stamp, 'price': fill['price'], 'qty': fill['qty'], 'side': fill['side'],
                      'role': fill['role'], 'ts': fill['ts'], 'execution_id': fill['execution_id'],
                      'label': f"{'Entry' if fill['role'] == 'entry' else 'Exit'} · {fill['qty']:g} @ {fill['price']:g}",
                      'color': ENTRY if fill['role'] == 'entry' else EXIT, 'sequence': i})
    segments = protection_segments(trade, events, stop, now=now)
    # Whitespace keeps clock spacing and exact fill placement through missing minutes.
    chart_data = data
    if data:
        lookup = {b['time']: b for b in data}
        chart_data = [lookup.get(t, {'time': t}) for t in range(data[0]['time'], data[-1]['time'] + 60, 60)]
    return {'series': {'type': 'candles', 'name': trade.symbol + ' · 1m', 'data': chart_data,
                       'volume': any(b.get('volume', 0) > 0 for b in data)},
            'overlays': {'fills': fills, 'levels': segments, 'opened': instant(trade.opened).timestamp(),
                         'closed': instant(trade.closed).timestamp() if trade.closed else None},
            'provenance': provenance(source), 'notes': list(dict.fromkeys(notes))}
