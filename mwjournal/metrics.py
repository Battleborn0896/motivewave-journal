"""MAE and MFE of a trade from the prices observed while it was held.

One definition in every unit: how far the position you held went against you (MAE) or for you (MFE), from its
average entry. The moment is the one where that price move times the contracts held was largest: the move then
is the points, and the points times the contracts held then and the point value are the dollars. Ticks and R
rescale these, so switching units changes nothing else. Profit already banked by a partial exit never enters:
long 2 NQ at 100, a dip to 97, sell 1 at 102, then the runner dips to 96 is an MAE of 3 points on the 2
contracts held then, $120, not 4 points. Bar extremes are estimates, especially boundary bars.
"""

from __future__ import annotations

import datetime as dt

from .model import PriceSample, Trade, instant, trade_prices
from .scope import allows_source, is_simulated, provenance


def measurements(trade: Trade, samples: list[PriceSample], source: str, *, now=None) -> list[dict]:
    """A trade's measurement from one source: from the traded price only (bid and ask play no part)."""
    return [excursions(trade, samples, source, now=now)]


def holding_states(trade: Trade, now: dt.datetime):
    """(from, to, contracts held, average cost) between consecutive fills, the last one until `now`."""
    qty, cost = 0., 0.
    states = []
    for i, fill in enumerate(trade.fills):
        amount, price = fill['qty'], fill['price']
        if fill['role'] == 'entry':
            cost = (cost * qty + price * amount) / (qty + amount)
            qty += amount
        else:
            qty = max(0., qty - amount)
        end = instant(trade.fills[i + 1]['ts']) if i + 1 < len(trade.fills) else now
        if qty > 1e-9 and end >= instant(fill['ts']):
            states.append((instant(fill['ts']), end, qty, cost))
    return states


def price_boundaries(trade):
    """The fills are prices too: (move from the average entry, contracts held) at each fill while held before
    it, and after an entry fill with the new average and size. The first, (0, 0), keeps both extremes >= 0."""
    qty, cost, values = 0., 0., [(0., 0.)]
    sign = 1 if trade.direction == 'long' else -1
    for fill in trade.fills:
        if qty > 1e-9:
            values.append(((fill['price'] - cost) * sign, qty))
        if fill['role'] == 'entry':
            cost = (cost * qty + fill['price'] * fill['qty']) / (qty + fill['qty'])
            qty += fill['qty']
            values.append(((fill['price'] - cost) * sign, qty))
        else:
            qty = max(0., qty - fill['qty'])
    return values


def walk(trade, samples, states, start, end, marks, path, used):
    """Each price's moves in the holding states it falls in, added to the lists given: the one loop
    excursions and carry_on both measure with. marks gets (move x contracts held x point value, move) for each,
    which extremes() takes MAE and MFE from."""
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
        prices = [sample.low, sample.high]
        for a, b, qty, cost in active:
            moves = [(p - cost) * sign for p in prices]
            values = [move * qty * multiplier for move in moves]
            marks.extend(zip(values, moves))
            path.append({'ts': sample.ts, 'low': min(values) if trade.point_value is not None else None,
                         'high': max(values) if trade.point_value is not None else None, 'held_qty': qty,
                         'point_low': min(moves), 'point_high': max(moves), 'average_cost': cost,
                         'price_low': min(prices), 'price_high': max(prices),
                         'resolution_seconds': sample.resolution_seconds})
        used.append(sample)


def extremes(trade, worst, best) -> dict:
    """MAE and MFE from the most adverse and the most favourable mark (walk): the move at that moment in points,
    and that move of the contracts held then in dollars, which need the point value. Of marks worth the same,
    the larger move counts."""
    priced = trade.point_value is not None
    return {'mae': max(0., -worst[0]) if priced else None, 'mfe': max(0., best[0]) if priced else None,
            'mae_points': max(0., -worst[1]), 'mfe_points': max(0., best[1])}


def bucket_size(count, max_path_points):
    """How many path points one stored point stands for: 1 up to max_path_points, then the smallest power of
    two that keeps the path within it. Buckets are counted from the first point, so a path carried on
    (carry_on) groups exactly as one compacted from entry."""
    size = 1
    while (count + size - 1) // size > max_path_points:
        size *= 2
    return size


def merge(trade, points):
    """One stored point for a bucket of path points, keeping its extremes."""
    return {'ts': points[0]['ts'], 'low': min(p['low'] for p in points) if trade.point_value is not None else None,
            'high': max(p['high'] for p in points) if trade.point_value is not None else None,
            'point_low': min(p['point_low'] for p in points), 'point_high': max(p['point_high'] for p in points)}


def compact(trade, path, max_path_points):
    """At most max_path_points entries, each bucket keeping its extremes: the chart only. Metric values use
    every observation."""
    size = bucket_size(len(path), max_path_points)
    return [merge(trade, path[i:i + size]) for i in range(0, len(path), size)]


PATH_POINTS = 1000            # a stored measurement's path, for the chart; the values use every price


def newest(trade, used) -> dict:
    """The newest price measured, which an open trade's result is shown at (Trade.net_at): of prints sharing
    the newest time, the last in tape order, as the chart's close is. last_count is how many share that time,
    and contract whether every price measured was recorded under the traded contract's own symbol: what
    carry_on needs to go on exactly where this stopped."""
    last = max(reversed(used), key=lambda s: s.ts)
    exact = str(trade.symbol).strip().upper()
    return {'last_price': last.last, 'last_ts': last.ts, 'last_count': sum(s.ts == last.ts for s in used),
            'contract': all(s.symbol == exact for s in used)}


def excursions(trade: Trade, samples: list[PriceSample], source: str, *, now=None,
               max_path_points: int | None = PATH_POINTS) -> dict:
    if max_path_points is not None and (type(max_path_points) is not int or max_path_points <= 0):
        raise ValueError('Path point limit must be positive, or None for the full recorded path.')
    end = instant(trade.closed) if trade.closed else (now or dt.datetime.now(dt.timezone.utc))
    start = instant(trade.opened)
    common = {'source': source, 'basis': 'last', 'precision': 'pending', 'mae': None, 'mfe': None,
              'mae_points': None, 'mfe_points': None, 'observations': 0, 'path': [], 'through': end.isoformat(),
              'provenance': provenance(source)}
    # Part of the position is not in the fills, or the prices are another replay run's: nothing is measured.
    # Why a trade has no MAE / MFE is said by the app (common.no_excursions), not stored here.
    if not trade.complete or not allows_source(trade, source):
        return common
    # Points remain calculable without a cash multiplier. Temporary scale 1 is internal only: the dollar
    # fields stay None until that multiplier is known.
    multiplier = trade.point_value if trade.point_value is not None else 1.
    states = holding_states(trade, end)
    marks = [(move * held * multiplier, move) for move, held in price_boundaries(trade)]
    path, used = [], []
    walk(trade, trade_prices(samples, trade.symbol, source), states, start, end, marks, path, used)
    if not used:
        return common
    bars = any(s.resolution_seconds > 0 for s in used)
    proxy = any(s.proxy for s in used)
    precision = ('replay-simulated' if is_simulated(source) else
                 'proxy-estimate' if proxy else 'estimated-bars' if bars else 'observed-ticks')
    if max_path_points is not None and len(path) > max_path_points:
        path = compact(trade, path, max_path_points)
    common.update(newest(trade, used))
    common.update(precision=precision, observations=len(used), path=path,
                  **extremes(trade, min(marks), max(marks)))
    return common


TICKS = ('observed-ticks', 'replay-simulated')     # precisions measured from prints alone


def carry_on(trade: Trade, previous: list[dict], samples: list[PriceSample], *, now=None) -> list[dict] | None:
    """An open trade's measurements from one source (`previous`) carried on with the prices
    stored since: the same as measuring again from entry (measurements), without reading the tape again. MAE
    and MFE are extremes, so new prices only need comparing with them. `samples` are the source's prices from
    the earliest previous last_ts on, as Journal.prices chooses them for that window.

    None when that would not be exactly the same, and the caller measures from entry: a closed or incomplete
    trade, one whose contract has no dollars per point (its measurement keeps no dollars to compare with), a
    measurement that is not from prints or has none yet, candles or proxy prices among the new ones, new prices
    before the last fill or under another kind of label than before (model.trade_prices prefers the contract's
    own)."""
    if trade.closed or not trade.complete or not previous or trade.point_value is None:
        return None
    end = now or dt.datetime.now(dt.timezone.utc)
    states = holding_states(trade, end)
    if not states:
        return None
    last_fill = instant(trade.fills[-1]['ts'])
    exact = str(trade.symbol).strip().upper()
    result = []
    for metric in previous:
        if metric.get('precision') not in TICKS or not metric.get('last_ts') or instant(metric['last_ts']) < last_fill:
            return None
        # The prints already measured: everything before last_ts, and the first last_count at it.
        skip, fresh = metric.get('last_count', 1), []
        for sample in samples:
            if sample.ts < metric['last_ts']:
                continue
            # Only prints of the kind measured (the contract's own, or not) were counted: the contract's first
            # print at that time is new, and below it stops the carrying on.
            if sample.ts == metric['last_ts'] and skip and (sample.symbol == exact) == metric.get('contract'):
                skip -= 1
                continue
            fresh.append(sample)
        if any(s.resolution_seconds or s.proxy or (s.symbol == exact) != metric.get('contract') for s in fresh):
            return None
        marks, path, used = [], [], []
        walk(trade, fresh, states[-1:], last_fill, end, marks, path, used)
        carried = {**metric, 'through': end.isoformat()}
        if used:
            # The stored MAE and MFE are the marks they were taken from (extremes): the new marks only need
            # comparing with them.
            carried.update(extremes(trade, min((-metric['mae'], -metric['mae_points']), *marks),
                                    max((metric['mfe'], metric['mfe_points']), *marks)))
            # One path point per print measured: the stored path stands for `observations` of them, in buckets
            # of bucket_size from the first, and the new prints follow. Grouping both by the new bucket size
            # gives the path compacted from entry.
            count = metric['observations'] + len(used)
            before, size = bucket_size(metric['observations'], PATH_POINTS), bucket_size(count, PATH_POINTS)
            if len(metric['path']) != -(-metric['observations'] // before):
                return None                            # stored by an earlier version, compacted another way
            if size == 1:
                path = metric['path'] + path
            else:
                buckets = {}
                for start, point in [*((i * before, p) for i, p in enumerate(metric['path'])),
                                     *((metric['observations'] + j, p) for j, p in enumerate(path))]:
                    buckets.setdefault(start // size, []).append(point)
                path = [merge(trade, points) for _, points in sorted(buckets.items())]
            carried['observations'] = count
            carried['path'] = path
            mark = newest(trade, used)
            if mark['last_ts'] == metric['last_ts']:
                mark['last_count'] += metric.get('last_count', 1)
            carried.update(mark, contract=metric.get('contract'))
        result.append(carried)
    return result
