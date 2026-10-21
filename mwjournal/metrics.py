"""Excursions from the observed marked P&L path, including realised P&L and changing inventory.

MAE/MFE are non-negative currency magnitudes. They refer to gross position equity since the
round trip began (realised + unrealised). Bar extremes are estimates, especially boundary bars.
"""

from __future__ import annotations

import datetime as dt

from .model import PriceSample, Trade, instant, trade_prices
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


def walk(trade, samples, states, start, end, basis, values, point_values, path, used, covered):
    """Each price's marks in the holding states it falls in, added to the lists given: the one loop
    excursions and carry_on both measure with."""
    multiplier = trade.point_value if trade.point_value is not None else 1.
    sign = 1 if trade.direction == 'long' else -1
    for sample in samples:
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


def compact(trade, path, max_path_points):
    """At most max_path_points entries, each bucket keeping its extremes: the chart only. Metric values use
    every observation."""
    size = (len(path) + max_path_points - 1) // max_path_points
    return [{'ts': path[i]['ts'], 'low': min(p['low'] for p in path[i:i + size]) if trade.point_value is not None else None,
             'high': max(p['high'] for p in path[i:i + size]) if trade.point_value is not None else None,
             'point_low': min(p['point_low'] for p in path[i:i + size]),
             'point_high': max(p['point_high'] for p in path[i:i + size])} for i in range(0, len(path), size)]


PATH_POINTS = 1000            # a stored measurement's path, for the chart; the values use every price
COMPACTED = ('Chart path is compacted for display; original prices remain stored. '
             'Use the full recorded path view/export for every observation.')


def newest(trade, used) -> dict:
    """The newest price measured, which an open trade's result is shown at (Trade.net_at): of prints sharing
    the newest time, the last in tape order, as the chart's close is. last_count is how many share that time,
    and contract whether every price measured was recorded under the traded contract's own symbol: what
    carry_on needs to go on exactly where this stopped."""
    last = max(reversed(used), key=lambda s: s.ts)
    exact = str(trade.symbol).strip().upper()
    return {'last_price': last.last, 'last_ts': last.ts, 'last_count': sum(s.ts == last.ts for s in used),
            'contract': all(s.symbol == exact for s in used)}


def excursions(trade: Trade, samples: list[PriceSample], source: str, *, basis: str = 'last', now=None,
               max_path_points: int | None = PATH_POINTS) -> dict:
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
    states, boundaries = holding_states(trade, end)
    values = [v * multiplier for v in boundaries]
    point_values = price_boundaries(trade)
    path, used, covered = [], [], []
    walk(trade, trade_prices(samples, trade.symbol, source), states, start, end, basis,
         values, point_values, path, used, covered)
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
        path = compact(trade, path, max_path_points)
        notes.append(COMPACTED)
    common.update(newest(trade, used))
    common.update(precision=precision, mae=max(0., -min(values)) if trade.point_value is not None else None,
                  mfe=max(0., max(values)) if trade.point_value is not None else None,
                  mae_points=max(0., -min(point_values)), mfe_points=max(0., max(point_values)),
                  point_precision=precision,
                  observations=len(used), notes=notes, path=path)
    return common


TICKS = ('observed-ticks', 'replay-simulated')     # precisions measured from prints alone


def carry_on(trade: Trade, previous: list[dict], samples: list[PriceSample], *, now=None) -> list[dict] | None:
    """An open trade's measurements from one source (`previous`, every basis of it) carried on with the prices
    stored since: the same as measuring again from entry (measurements), without reading the tape again. MAE
    and MFE are extremes, so new prices only need comparing with them. `samples` are the source's prices from
    the earliest previous last_ts on, as Journal.prices chooses them for that window.

    None when that would not be exactly the same, and the caller measures from entry: a closed or incomplete
    trade, a measurement that is not from prints or has none yet, candles or proxy prices among the new ones,
    new prices before the last fill or under another kind of label than before (model.trade_prices prefers
    the contract's own), or quotes arriving for a trade that had no quote-based measurement yet."""
    if trade.closed or not trade.complete or not previous:
        return None
    end = now or dt.datetime.now(dt.timezone.utc)
    states, _ = holding_states(trade, end)
    if not states:
        return None
    last_fill = instant(trade.fills[-1]['ts'])
    exact = str(trade.symbol).strip().upper()
    bases = {m.get('basis', 'last') for m in previous}
    result = []
    sign = 1 if trade.direction == 'long' else -1
    for metric in previous:
        if metric.get('precision') not in TICKS or not metric.get('last_ts') or instant(metric['last_ts']) < last_fill:
            return None
        basis = metric.get('basis', 'last')
        # The prints already measured: everything before last_ts, and the first last_count at it of those this
        # basis measures (a quote-based measurement skips prints without the quote).
        skip, fresh = metric.get('last_count', 1), []
        for sample in samples:
            if sample.ts < metric['last_ts']:
                continue
            if sample.ts == metric['last_ts'] and skip and (
                    basis != 'liquidation' or (sample.bid if sign > 0 else sample.ask) is not None):
                skip -= 1
                continue
            fresh.append(sample)
        if any(s.resolution_seconds or s.proxy or (s.symbol == exact) != metric.get('contract') for s in fresh):
            return None
        if 'liquidation' not in bases and any((s.bid if sign > 0 else s.ask) is not None for s in fresh):
            return None
        values, point_values, path, used = [], [], [], []
        walk(trade, fresh, states[-1:], last_fill, end, basis, values, point_values, path, used, [])
        carried = {**metric, 'through': end.isoformat()}
        if used:
            if trade.point_value is not None:
                carried['mae'] = max(metric['mae'], -min(values))
                carried['mfe'] = max(metric['mfe'], max(values))
            carried['mae_points'] = max(metric['mae_points'], -min(point_values))
            carried['mfe_points'] = max(metric['mfe_points'], max(point_values))
            carried['observations'] = metric['observations'] + len(used)
            path = metric['path'] + path
            if len(path) > PATH_POINTS:
                path = compact(trade, path, PATH_POINTS)
                carried['notes'] = list(dict.fromkeys([*metric['notes'], COMPACTED]))
            carried['path'] = path
            mark = newest(trade, used)
            if mark['last_ts'] == metric['last_ts']:
                mark['last_count'] += metric.get('last_count', 1)
            carried.update(mark, contract=metric.get('contract'))
        result.append(carried)
    return result

