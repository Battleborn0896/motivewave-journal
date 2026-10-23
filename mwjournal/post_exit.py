"""What the price did after a trade's exit: hindsight, not a strategy backtest or a profit you could have made.

The contracts of the final exit fill are held on at that fill's price for AFTER_EXIT_MINUTES, earlier partial exits
staying as they were, until the trade's stop is touched: the stop R uses (risk.plan_for), else breakeven, the trade's
average entry before fees (resolve_stop). There is no take profit. Only recorded ticks count, as for MAE / MFE. A
stop touch is not a stop fill, and fees, spread, slippage and trailing stops are not modelled.
"""
from __future__ import annotations

import datetime as dt
from collections import defaultdict

from . import risk
from .model import finite, instant, trade_prices
from .scope import allows_source, is_simulated

# How long after an exit the price is followed, on the trade page and in Stats alike. The study loads the 30
# minutes after each exit, and the journal keeps the prints of 120 (store.PRINTS_AFTER).
AFTER_EXIT_MINUTES = 15
# The coverage of a result whose minutes after the exit were recorded to their end, or to the stop.
COMPLETE = ('window-observed', 'stop-observed')
# A tick this close after the exit, or before the end of the minutes after it, counts as recorded from or to there.
# At a moment taken at random over the day, a complete NQ or ES tape has had no print in the second before it about
# one time in three, but almost never none in the minute before it: a second would call many complete recordings
# incomplete.
TOLERANCE = dt.timedelta(seconds=60)


def post_exit(trade, samples, source, stop_price, *, now=None, initial_risk=None, gaps=()):
    """What `samples` of one `source` say about the AFTER_EXIT_MINUTES after `trade`'s exit, held to `stop_price`:

    - precision: 'observed-ticks' when they are recorded ticks; candles, another contract's prices and simulated
      replay prints are named ('estimated-bars', 'proxy-estimate', 'replay-simulated') and never measured;
    - extra_mfe / extra_mae: how far the price went for / against the final exit's contracts after the exit, in
      account currency, up to the first stop touch; extra_mfe_r / extra_mae_r: over `initial_risk`, when known;
    - stop_touched / stop_ts: whether, and when, the stop was first touched;
    - coverage: 'window-observed' or 'stop-observed' (COMPLETE) when the ticks cover the minutes to their end or to
      the stop, 'partial-window' when they do not or the study reported `gaps` in them, 'unavailable' with none;
    - termination: 'stop-already-touched-at-exit' (the exit was at or past the stop: nothing left to hold),
      'stop-touch', 'still-running-at-cutoff', 'not-over' (the minutes are still running and the stop untouched),
      'awaiting-prices' (over, but not all recorded) or 'unavailable'."""
    result = {'source': source, 'precision': 'pending', 'stop_price': stop_price, 'extra_mfe': None,
              'extra_mae': None, 'extra_mfe_r': None, 'extra_mae_r': None, 'stop_touched': False, 'stop_ts': None,
              'coverage': 'unavailable', 'termination': 'unavailable', 'observations': 0}
    if not trade.complete or not trade.closed or trade.point_value is None or not allows_source(trade, source):
        return result
    exit_fill, sign = trade.fills[-1], 1 if trade.direction == 'long' else -1
    quantity, exit_price, stop_price = exit_fill['qty'], exit_fill['price'], finite(stop_price, 'Stop')
    result.update(exit_qty=quantity, exit_price=exit_price, stop_price=stop_price)
    if (exit_price - stop_price) * sign <= 0:
        result.update(extra_mfe=0., extra_mae=0., stop_touched=True, stop_ts=trade.closed,
                      termination='stop-already-touched-at-exit')
        return result

    def move(price):
        return (price - exit_price) * sign * quantity * trade.point_value

    start = instant(trade.closed)
    end = start + dt.timedelta(minutes=AFTER_EXIT_MINUTES)
    through = min(end, now or dt.datetime.now(dt.timezone.utc))
    # After the exit, never at its moment: a print stamped with the exit's own time may be the exit itself.
    window = sorted((s for s in trade_prices(samples, trade.symbol, source) if start < instant(s.ts) <= through),
                    key=lambda s: instant(s.ts))
    peak = adverse = 0.
    hit, used = None, []
    for sample in window:
        used.append(sample)
        if sample.low <= stop_price if sign > 0 else sample.high >= stop_price:
            hit = sample
            adverse = min(adverse, move(stop_price))
            break                                  # nothing after the first stop touch counts
        worst, best = sorted((move(sample.low), move(sample.high)))
        peak, adverse = max(peak, best), min(adverse, worst)
    if not used:
        result['termination'] = 'not-over' if through < end else 'unavailable'
        return result
    result['precision'] = ('replay-simulated' if is_simulated(source) else
                           'proxy-estimate' if any(s.proxy for s in used) else
                           'estimated-bars' if any(s.resolution_seconds for s in used) else 'observed-ticks')
    if result['precision'] != 'observed-ticks':
        return result                              # only recorded ticks say what the price did
    recorded_from_exit = instant(window[0].ts) <= start + TOLERANCE
    to_the_end = through >= end and instant(window[-1].ts) >= end - TOLERANCE
    covers = recorded_from_exit and not gaps and (hit is not None or to_the_end)
    at_risk = initial_risk if initial_risk is not None and initial_risk > 0 else None
    against = max(0., -adverse)
    result.update(extra_mfe=peak, extra_mae=against, observations=len(used),
                  extra_mfe_r=peak / at_risk if at_risk else None, extra_mae_r=against / at_risk if at_risk else None,
                  coverage=('stop-observed' if covers and hit is not None else 'window-observed' if covers
                            else 'partial-window'),
                  stop_touched=hit is not None, stop_ts=hit.ts if hit else None,
                  termination=('stop-touch' if hit is not None else 'still-running-at-cutoff' if covers else
                               'not-over' if through < end else 'awaiting-prices'))
    return result


def resolve_stop(journal, trade):
    """The price the minutes after `trade`'s exit are held to, and which it is: ('stop') the stop R uses, the one saved
    for the trade, else its stop order as it stood risk.STOP_SETTLES seconds after entry; else ('breakeven') the
    trade's average entry, before fees."""
    stop = risk.plan_for(journal, trade).stop
    return (stop, 'stop') if stop is not None else (trade.entry_price, 'breakeven')


def best_post_exit(journal, trade, initial_risk=None):
    """post_exit of a closed `trade`, held to resolve_stop's price, from the source of recorded ticks that covers the
    minutes after its exit best, with 'held_to' saying which price that was ('stop' or 'breakeven'). As for MAE / MFE,
    candles, another contract's prices and simulated replay prints are never used, and ticks with a gap the study
    reported never cover the minutes."""
    if not trade.closed or not trade.complete:
        return {**post_exit(trade, [], '', None), 'held_to': None}
    stop, held_to = resolve_stop(journal, trade)
    # A replay trade's minutes go by as the replay plays them, not on the wall clock.
    now = journal.replay_clock(trade)
    best = post_exit(trade, [], '', stop, now=now, initial_risk=initial_risk)
    if best['termination'] == 'stop-already-touched-at-exit':
        return {**best, 'held_to': held_to}         # nothing left to hold: no prices to read
    end = (instant(trade.closed) + dt.timedelta(minutes=AFTER_EXIT_MINUTES)).isoformat()
    options = [post_exit(trade, journal.prices(trade.symbol, source, start=trade.closed, end=end), source, stop,
                         now=now, initial_risk=initial_risk,
                         gaps=journal.capture_gaps(source, start=trade.closed, end=end, symbol=trade.symbol))
               for source in journal.price_sources(trade.symbol, trade)]
    options = [p for p in options if p['precision'] == 'observed-ticks']
    if options:
        best = max(options, key=lambda p: (p['coverage'] in COMPLETE, p['observations']))
    return {**best, 'held_to': held_to}


def runner_groups(rows, field='tags'):
    """The after-exit results in `rows` (best_post_exit, all in one unit) averaged by each value of `field`, a row
    counting under each of its values. Only results whose minutes after the exit were all recorded count: a partial
    window is never averaged in as if it were whole."""
    if len({r.get('currency', 'USD') for r in rows}) > 1:
        raise ValueError('After-exit comparisons need one currency.')
    groups = defaultdict(list)
    for row in rows:
        if row.get('coverage') not in COMPLETE or row.get('extra_mfe') is None:
            continue
        for value in set(row.get(field) or ['Untagged' if field == 'tags' else 'Uncategorized']):
            groups[value].append(row)
    result = []
    for name, values in sorted(groups.items()):
        risk = [r for r in values if r.get('extra_mfe_r') is not None]
        result.append({'group': name, 'trades': len(values),
                       'avg_extra_peak': sum(r['extra_mfe'] for r in values) / len(values),
                       'avg_adverse_after_exit': sum(r['extra_mae'] for r in values) / len(values),
                       'avg_extra_peak_R': sum(r['extra_mfe_r'] for r in risk) / len(risk) if risk else None,
                       'additional_1R_reached_pct': sum(r['extra_mfe_r'] >= 1 for r in risk) / len(risk) * 100 if risk else None,
                       'trades_with_r': len(risk)})
    return result
