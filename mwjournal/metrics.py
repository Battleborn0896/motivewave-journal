"""Excursions from the observed marked P&L path, including realised P&L and changing inventory.

MAE/MFE are non-negative currency magnitudes. They refer to gross position equity since the
round trip began (realised + unrealised). Bar extremes are estimates, especially boundary bars.
"""

from __future__ import annotations

import datetime as dt

from .model import PriceSample, Trade, instant
from .scope import allows_source, is_history, is_simulated, provenance
from .units import POINT_DEFINITION


def measurements(trade: Trade, samples: list[PriceSample], source: str, *, now=None) -> list[dict]:
    result = [excursions(trade, samples, source, now=now)]
    if any(s.resolution_seconds == 0 and (s.bid is not None if trade.direction == 'long' else s.ask is not None)
           for s in samples):
        result.append(excursions(trade, samples, source, basis='liquidation', now=now))
    return result


def holding_states(trade: Trade, now: dt.datetime):
    qty, cost, realised = 0., 0., 0.
    sign = 1 if trade.direction == 'long' else -1
    states, boundary = [], [0.]
    for i, fill in enumerate(trade.fills):
        amount, price = fill['qty'], fill['price']
        if fill['role'] == 'entry':
            cost = (cost * qty + price * amount) / (qty + amount)
            qty += amount
        else:
            realised += (price - cost) * sign * amount
            qty = max(0., qty - amount)
        boundary.append(realised + (price - cost) * sign * qty)
        end = instant(trade.fills[i + 1]['ts']) if i + 1 < len(trade.fills) else now
        if qty > 1e-9 and end >= instant(fill['ts']):
            states.append((instant(fill['ts']), end, qty, cost, realised))
    return states, boundary


def price_boundaries(trade):
    """Confirmed fills also mark price excursions before/after average entry changes."""
    qty, cost, values = 0., 0., [0.]
    sign = 1 if trade.direction == 'long' else -1
    for fill in trade.fills:
        if qty > 1e-9:
            values.append((fill['price'] - cost) * sign)
        if fill['role'] == 'entry':
            cost = (cost * qty + fill['price'] * fill['qty']) / (qty + fill['qty'])
            qty += fill['qty']
            values.append((fill['price'] - cost) * sign)
        else:
            qty = max(0., qty - fill['qty'])
    return values


def excursions(trade: Trade, samples: list[PriceSample], source: str, *, basis: str = 'last', now=None,
               max_path_points: int | None = 1000) -> dict:
    if max_path_points is not None and (type(max_path_points) is not int or max_path_points <= 0):
        raise ValueError('Path point limit must be positive, or None for the full recorded path.')
    end = instant(trade.closed) if trade.closed else (now or dt.datetime.now(dt.timezone.utc))
    start = instant(trade.opened)
    common = {'source': source, 'basis': basis, 'precision': 'pending', 'mae': None, 'mfe': None,
              'mae_points': None, 'mfe_points': None, 'point_definition': POINT_DEFINITION,
              'observations': 0, 'notes': [], 'path': [], 'through': end.isoformat(),
              'provenance': provenance(source)}
    if not trade.complete:
        # Part of this position is not in the fills, so how far price went against or for it is not known.
        common['notes'] = ['Excursions are unknown for this trade: ' + ', '.join(trade.incomplete) + '.']
        return common
    if not allows_source(trade, source):
        common['notes'] = ['These prices belong to a different replay run.']
        return common
    # Price excursions remain calculable without a cash multiplier. Temporary scale 1 is internal
    # only: all currency fields stay None until that multiplier is known.
    multiplier = trade.point_value if trade.point_value is not None else 1.
    sign = 1 if trade.direction == 'long' else -1
    states, boundaries = holding_states(trade, end)
    values = [v * multiplier for v in boundaries]
    point_values = price_boundaries(trade)
    path, used, covered = [], [], []
    for sample in samples:
        if sample.symbol != trade.symbol or sample.source != source:
            continue
        stamp = instant(sample.ts)
        stop = stamp + dt.timedelta(seconds=sample.resolution_seconds)
        if stop < start or stamp > end:
            continue
        # A tick at the exit timestamp is excluded: relative tick/fill order is unknown.
        active = [state for state in states if
                  stamp < state[1] and stop > state[0]] if sample.resolution_seconds > 0 else [
                      state for state in states if state[0] <= stamp < state[1]]
        if not active:
            continue
        if basis == 'liquidation':
            quote = sample.bid if sign > 0 else sample.ask
            if sample.resolution_seconds or quote is None:
                continue
            prices = [quote]
        else:
            prices = [sample.low, sample.high]
        for a, b, qty, cost, realised in active:
            marks = [(realised + (p - cost) * sign * qty) * multiplier for p in prices]
            moves = [(p - cost) * sign for p in prices]
            values.extend(marks)
            point_values.extend(moves)
            path.append({'ts': sample.ts, 'low': min(marks) if trade.point_value is not None else None,
                         'high': max(marks) if trade.point_value is not None else None, 'held_qty': qty,
                         'point_low': min(moves), 'point_high': max(moves),
                         'average_cost': cost, 'realised_pnl': realised * trade.point_value if trade.point_value is not None else None,
                         'price_low': min(prices), 'price_high': max(prices),
                         'resolution_seconds': sample.resolution_seconds})
            covered.append((max(a, stamp), min(b, stop)))
        used.append(sample)
    if not used:
        common['notes'] = ['No matching prices inside the holding interval. Capture or import ticks/bars for this period.']
        return common
    bars = any(s.resolution_seconds > 0 for s in used)
    proxy = any(s.proxy for s in used)
    simulated = is_simulated(source)
    precision = ('replay-simulated' if simulated else
                 'proxy-estimate' if proxy else 'estimated-bars' if bars else 'observed-ticks')
    notes = []
    if simulated:
        notes.append('Replay generated/unknown ticks: synthetic price path, not historical tick-accurate MAE/MFE.')
    if bars:
        notes.append('OHLC estimate: boundary bars may include prices before entry/after exit; '
                     'bars spanning a size change cannot resolve the price path.')
        intervals = sorted(covered)
        cursor = start
        for left, right in intervals:
            if left > cursor + dt.timedelta(milliseconds=1):
                notes.append('The history has gaps in the holding window; extrema may be understated.')
                break
            cursor = max(cursor, right)
        if cursor < end:
            notes.append('History does not cover the end of the holding window.')
    else:
        notes.append('Observed ticks: completeness is not guaranteed; missing ticks can understate excursions.')
        if is_history(source):
            notes.append("History backfill: real prints loaded from the platform's history afterwards, "
                         'not recorded while the trade was open.')
    if proxy:
        notes.append('Uses an explicitly selected proxy/continuous contract; this is not the exact traded contract.')
    if trade.flags:
        notes.append('Execution timing/identity limitations: ' + ', '.join(trade.flags))
    if trade.point_value is None:
        notes.append('Currency/R excursions are unavailable until the instrument currency-per-point multiplier is set. '
                     'Price-based points remain available.')
    if max_path_points is not None and len(path) > max_path_points:
        # Compact the chart only, preserving each bucket's extrema. Metric values use every observation.
        size = (len(path) + max_path_points - 1) // max_path_points
        path = [{'ts': path[i]['ts'], 'low': min(p['low'] for p in path[i:i + size]) if trade.point_value is not None else None,
                 'high': max(p['high'] for p in path[i:i + size]) if trade.point_value is not None else None,
                 'point_low': min(p['point_low'] for p in path[i:i + size]),
                 'point_high': max(p['point_high'] for p in path[i:i + size])} for i in range(0, len(path), size)]
        notes.append('Chart path is compacted for display; original prices remain stored. '
                     'Use the full recorded path view/export for every observation.')
    common.update(precision=precision, mae=max(0., -min(values)) if trade.point_value is not None else None,
                  mfe=max(0., max(values)) if trade.point_value is not None else None,
                  mae_points=max(0., -min(point_values)), mfe_points=max(0., max(point_values)),
                  point_precision=precision,
                  observations=len(used), notes=notes, path=path)
    return common
