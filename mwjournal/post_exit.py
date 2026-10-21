"""Hindsight runner analysis, NOT a strategy backtest or a promise of achievable profit.

Hypothesis: retain ONLY the quantity sold/bought in the FINAL exit fill, at that fill's price,
for a fixed horizon. Earlier partial exits remain unchanged. No perfect-exit claim, commissions,
spread/slippage, stop logic or reinvestment is silently invented.
"""
from __future__ import annotations

import datetime as dt
from collections import defaultdict

from . import risk
from .model import finite, instant, trade_prices
from .scope import allows_source, is_simulated, provenance


def post_exit(trade, samples, source, *, minutes=15, now=None, initial_risk=None, gaps=(),
              stop_price=None, stop_source='', require_stop=False):
    if type(minutes) is not int or not 1 <= minutes <= 120:
        raise ValueError('Post-exit horizon must be 1–120 whole minutes.')
    result = {'source': source, 'precision': 'pending', 'minutes': minutes, 'extra_mfe': None,
              'extra_mae': None, 'horizon_extra': None, 'minutes_to_peak': None,
              'extra_mfe_r': None, 'extra_mae_r': None, 'coverage': 'unavailable',
              'observations': 0, 'path': [], 'notes': [], 'stop_price': stop_price, 'stop_source': stop_source,
              'stop_touched': False, 'stop_ts': None, 'extra_mfe_upper': None, 'termination': 'unavailable',
              'provenance': provenance(source) if source else None}
    if not trade.complete:
        # Its last kept fill is not known to be the exit, or has no price to continue from.
        result['notes'] = ['Unavailable for this trade: ' + ', '.join(trade.incomplete) + '.']
        return result
    if not trade.closed or trade.point_value is None or not allows_source(trade, source):
        result['notes'] = ['Needs a closed trade, known multiplier and matching replay run.']
        return result
    exit_fill = trade.fills[-1]
    start = instant(trade.closed)
    horizon = start + dt.timedelta(minutes=minutes)
    through = min(horizon, now or dt.datetime.now(dt.timezone.utc))
    sign = 1 if trade.direction == 'long' else -1
    quantity, exit_price = exit_fill['qty'], exit_fill['price']
    result.update(exit_qty=quantity, exit_price=exit_price)
    if require_stop and stop_price is None:
        result['notes'] = ['No stop is available. Type your stop under Notes or select an explicit breakeven '
                           'hypothesis; a stop must not be guessed from MAE or price proximity.']
        return result
    if stop_price is not None:
        stop_price = finite(stop_price, 'Runner stop')
        if (exit_price - stop_price) * sign <= 0:
            result.update(extra_mfe=0., extra_mae=0., extra_mfe_upper=0., stop_touched=True,
                          stop_ts=trade.closed, termination='stop-already-touched-at-exit')
            result['notes'] = ['Selected stop is already reached at the actual exit price; no continuation is possible.']
            return result
    used, path = [], []
    hit, ambiguous_upper = None, None
    for sample in sorted(trade_prices(samples, trade.symbol, source), key=lambda s: instant(s.ts)):
        stamp = instant(sample.ts)
        end = stamp + dt.timedelta(seconds=sample.resolution_seconds)
        # Entire bars must be after exit and before the horizon. Never borrow pre-exit highs or
        # future highs from a boundary candle. Ticks at exit are ambiguous and excluded too.
        if end > through:
            continue
        if (sample.resolution_seconds and stamp < start) or (not sample.resolution_seconds and stamp <= start):
            continue
        values = [(p - exit_price) * sign * quantity * trade.point_value for p in (sample.low, sample.high)]
        touched = stop_price is not None and (sample.low <= stop_price if sign > 0 else sample.high >= stop_price)
        if touched:
            hit = sample
            used.append(sample)
            if sample.resolution_seconds:
                # Price order inside this candle is unknowable. Its high might be AFTER the stop.
                ambiguous_upper = max(0., max(values), max((p['high'] for p in path), default=0.))
            break  # NEVER credit favorable prices observed after the first stop touch
        endpoint = sample.close if sample.resolution_seconds else sample.high
        path.append({'ts': sample.ts, 'low': min(values), 'high': max(values),
                     'close': (endpoint - exit_price) * sign * quantity * trade.point_value if endpoint is not None else None})
        used.append(sample)
    if not used:
        result['notes'] = ['No recorded prices after exit in this window. Keep the study running, '
                           'increase After Exit (Minutes), or supply matching history.']
        return result
    bars = any(s.resolution_seconds for s in used)
    simulated = is_simulated(source)
    precision = ('replay-simulated' if simulated else 'proxy-estimate' if any(s.proxy for s in used)
                 else 'estimated-bars' if bars else 'observed-ticks')
    peak = max(path, key=lambda p: p['high']) if path else None
    earliest = instant(used[0].ts)
    latest = instant(used[-1].ts) + dt.timedelta(seconds=used[-1].resolution_seconds)
    tolerance = dt.timedelta(seconds=60 if bars else 1)
    covers = earliest <= start + tolerance and (hit is not None or through >= horizon and latest >= horizon - tolerance)
    if bars:
        cursor = earliest
        for sample in used:
            if instant(sample.ts) > cursor:
                covers = False
            cursor = max(cursor, instant(sample.ts) + dt.timedelta(seconds=sample.resolution_seconds))
    if gaps:
        covers = False
    extra_mfe = max(0., peak['high']) if peak else 0.
    adverse = [p['low'] for p in path]
    if hit is not None:
        adverse.append((stop_price - exit_price) * sign * quantity * trade.point_value)
    extra_mae = max(0., -min(adverse)) if adverse else 0.
    notes = ['Hindsight potential on the FINAL exit quantity only. Earlier partial exits stay unchanged. '
             'Last-traded prices, gross of additional costs. Stop TOUCH is observed, not a guaranteed stop fill; '
             'spread, slippage, order latency and dynamic stop changes are not simulated.',
             'Window endpoints are observed, but tick completeness is not guaranteed.']
    if bars:
        notes.append('OHLC estimate: boundary bars are excluded; the peak time is a bar window, not an exact tick.')
    if simulated:
        notes.append('Simulated replay prices, not a historical tick-accurate continuation.')
    if not covers:
        notes.append('Partial or gapped horizon: excluded from default tag comparisons.')
    if ambiguous_upper is not None:
        notes.append('Stop and potential peak share an OHLC bar: their order is unknown. '
                     'Report a conservative/possible peak range, and exclude it from default runner comparisons.')
    if stop_price is None:
        notes.append('No stop rule applied: this is unrestricted hindsight potential, not a stop-surviving runner.')
    risk = initial_risk if initial_risk is not None and initial_risk > 0 else None
    result.update(precision=precision, extra_mfe=extra_mfe, extra_mae=extra_mae,
                  horizon_extra=path[-1]['close'] if covers and hit is None and path else None,
                  minutes_to_peak=(instant(peak['ts']) - start).total_seconds() / 60 if peak and extra_mfe else None,
                  extra_mfe_r=extra_mfe / risk if risk else None, extra_mae_r=extra_mae / risk if risk else None,
                  coverage=('ambiguous-stop-bar' if ambiguous_upper is not None else
                            'stop-observed' if covers and hit is not None else
                            'window-observed' if covers else 'partial-window'), observations=len(used),
                  stop_touched=hit is not None, stop_ts=hit.ts if hit else None,
                  extra_mfe_upper=ambiguous_upper if ambiguous_upper is not None else extra_mfe,
                  termination='stop-touch' if hit is not None else 'still-running-at-cutoff' if covers else 'awaiting-prices',
                  exit_qty=quantity, exit_price=exit_price, through=latest.isoformat(), path=path, notes=notes)
    return result


def resolve_stop(journal, trade, mode='original'):
    if mode == 'breakeven':
        return trade.entry_price, 'Entry average · gross breakeven hypothesis, excludes fees'
    if mode == 'original':
        # The stop the trade's R uses, by the one rule in risk.py.
        plan = risk.plan_for(journal, trade)
        labels = {'saved': 'Your saved stop · the one R uses',
                  'order': f'Your stop order {risk.STOP_SETTLES} seconds after entry · the one R uses'}
        return plan.stop, labels.get(plan.source, '')
    events = [p for p in journal.protection_events(trade) if p.role == 'stop'
              and instant(p.ts) <= instant(trade.closed)]
    if mode == 'at-exit':
        last = {}
        for event in events:
            last[event.order_id] = event
        working = [p for p in last.values() if p.state == 'working' and p.price is not None]
        if len(working) == 1:
            return working[0].price, 'Recorded working stop before actual exit'
        return None, 'No unique working stop at exit; multiple stops require an explicit hypothesis'
    raise ValueError('Unknown stop hypothesis.')


def best_post_exit(journal, trade, minutes=15, initial_risk=None, *, stop_mode=None):
    if not trade.closed or not trade.complete:
        return post_exit(trade, [], '', minutes=minutes)
    end = (instant(trade.closed) + dt.timedelta(minutes=minutes)).isoformat()
    stop, stop_source = resolve_stop(journal, trade, stop_mode) if stop_mode else (None, '')
    options = []
    for source in journal.price_sources(trade.symbol, trade):
        samples = journal.prices(trade.symbol, source, start=trade.closed, end=end)
        options.append(post_exit(trade, samples, source, minutes=minutes, now=journal.trade_clock(trade),
                                 initial_risk=initial_risk,
                                 stop_price=stop, stop_source=stop_source, require_stop=stop_mode is not None,
                                 gaps=journal.capture_gaps(source, start=trade.closed, end=end, symbol=trade.symbol)))
    ranks = {'observed-ticks': 4, 'estimated-bars': 3, 'proxy-estimate': 2, 'replay-simulated': 1, 'pending': 0}
    if journal.display_settings()['excursion_policy'] == 'ticks-only':
        options = [p for p in options if p['precision'] == 'observed-ticks'
                   and not journal.capture_gaps(p['source'], start=trade.closed, end=end, symbol=trade.symbol)]
    return max(options, key=lambda p: (p['coverage'] in ('window-observed', 'stop-observed'), ranks[p['precision']], p['observations'])) \
        if options else post_exit(trade, [], '', minutes=minutes, stop_price=stop,
                                 stop_source=stop_source, require_stop=stop_mode is not None)


def runner_groups(rows, field='tags'):
    if len({r.get('currency', 'USD') for r in rows}) > 1:
        raise ValueError('Runner comparisons require one currency.')
    groups = defaultdict(list)
    for row in rows:
        if (row.get('coverage') not in ('window-observed', 'stop-observed') or row.get('extra_mfe') is None
                or row.get('stop_price') is None):
            continue  # partial windows are not cherry-picked into group averages
        if field in ('tags', 'tag_categories'):
            values = row.get(field) or ['Untagged' if field == 'tags' else 'Uncategorized']
        else:
            values = [row.get(field) or 'Unspecified']
        for value in set(values):
            groups[(value, row['precision'])].append(row)
    result = []
    for (name, quality), values in sorted(groups.items()):
        risk = [r for r in values if r.get('extra_mfe_r') is not None]
        result.append({'group': name, 'quality': quality, 'trades': len(values),
                       'avg_extra_peak': sum(r['extra_mfe'] for r in values) / len(values),
                       'avg_adverse_after_exit': sum(r['extra_mae'] for r in values) / len(values),
                       'avg_extra_peak_R': sum(r['extra_mfe_r'] for r in risk) / len(risk) if risk else None,
                       'additional_1R_reached_pct': sum(r['extra_mfe_r'] >= 1 for r in risk) / len(risk) * 100 if risk else None,
                       'trades_with_r': len(risk)})
    return result
