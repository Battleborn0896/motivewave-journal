"""A trade's stop and the risk R divides by: one rule, and the only place the app works out risk.

A trade's stop is one price: the stop the trader saved for it (Stop under Notes), otherwise the
linked stop order as it stood STOP_SETTLES seconds after the first fill. Its initial risk is what
that stop puts at risk on the position held at that moment. Where the trade was closed, and any
take profit, play no part: R is the result divided by this risk.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

from .humanize import price
from .model import instant

# Seconds after a trade's first fill at which its stop counts. With an OCO bracket the stop goes in
# with the entry, and traders often drag it to the structure a second or two later: that is still
# placing the stop. A stop moved after this is managing the trade and leaves its risk alone.
STOP_SETTLES = 10


@dataclass(frozen=True)
class Plan:
    stop: float | None      # the price R uses
    source: str             # 'saved' (typed under Notes), 'order' (the captured stop order), 'merged' or ''
    risk: float | None      # in account currency; None when it cannot be known
    order: float | None     # the captured stop order's price, which clearing a saved stop returns to
    why: str = ''           # why risk is None, in a trader's words


def settles_at(trade) -> dt.datetime:
    """The moment a trade's stop counts: its first fill plus STOP_SETTLES, or just before its first
    exit when that comes sooner, so a trade stopped out in five seconds has the stop that took it out."""
    moment = instant(trade.fills[0]['ts']) + dt.timedelta(seconds=STOP_SETTLES)
    exit = next((instant(f['ts']) for f in trade.fills if f['role'] == 'exit'), None)
    return moment if exit is None or exit > moment else exit - dt.timedelta(microseconds=1)


def position(trade) -> tuple[float, float] | None:
    """(average cost, quantity) of the position held when the stop settles: every entry fill until
    then, so an entry order filled in several parts counts whole."""
    if not trade.fills:
        return None
    moment = settles_at(trade)
    entries = [f for f in trade.fills if f['role'] == 'entry' and instant(f['ts']) <= moment]
    quantity = sum(f['qty'] for f in entries)
    if not quantity or any(not f['price'] for f in entries):
        return None
    return sum(f['price'] * f['qty'] for f in entries) / quantity, quantity


def working_stops(trade, events) -> set[float]:
    """The prices of the linked stop orders working when the stop settles. Take-profit orders are ignored."""
    if not trade.fills:
        return set()
    moment, last = settles_at(trade), {}
    for event in sorted(events, key=lambda e: instant(e.ts)):
        if event.role == 'stop' and instant(event.ts) <= moment:
            last[event.order_id] = event
    return {e.price for e in last.values() if e.state == 'working'}


def initial_risk(trade, stop) -> float | None:
    """|average cost - stop| x quantity x point value, of the position held when the stop settles.
    None without a stop or a multiplier, for a trade whose result is unknown, and for a stop that is
    not on the losing side of the entry."""
    held = position(trade)
    if stop is None or held is None or not trade.complete or trade.point_value is None:
        return None
    cost, quantity = held
    distance = cost - stop if trade.direction == 'long' else stop - cost
    return distance * quantity * trade.point_value if distance > 0 else None


def stop_for_risk(trade, risk) -> float | None:
    """The stop that gives `risk` with the position held when the stop settles: how a dollar risk
    saved by an older version, or an older pre-trade draft, becomes a stop with the same R."""
    held = position(trade)
    if not risk or risk <= 0 or held is None or not trade.complete or trade.point_value is None:
        return None
    cost, quantity = held
    distance = risk / (quantity * trade.point_value)
    return cost - distance if trade.direction == 'long' else cost + distance


def plan(trade, annotation: dict, events) -> Plan:
    """The stop and risk of one trade: the stop saved for it, else its stop order as it stood
    STOP_SETTLES seconds after entry. `events` are the trade's linked protective orders."""
    prices = working_stops(trade, events)
    order = next(iter(prices)) if len(prices) == 1 else None
    saved = annotation.get('planned_stop')
    stop, source = (saved, 'saved') if saved is not None else (order, 'order' if order is not None else '')
    risk = initial_risk(trade, stop)
    why = ''
    if risk is None:
        held = position(trade)
        if trade.incomplete:
            why = 'Unknown: ' + ', '.join(trade.incomplete) + '.'
        elif trade.point_value is None:
            why = 'R needs this contract\'s multiplier.'
        elif stop is None and len(prices) > 1:
            why = (f'Your stop orders had different prices {STOP_SETTLES} seconds after entry. '
                   'Type the stop R should use.')
        elif stop is None:
            why = f'No stop order {STOP_SETTLES} seconds after entry. Type your stop to get R.'
        elif held is not None:
            why = f'On the wrong side of your entry, {price(held[0])}, so R is unknown.'
    return Plan(stop, source, risk, order, why)


def combined(plans) -> Plan:
    """A merged trade risks what its original trades risked together, known only when every one is."""
    risks = [p.risk for p in plans]
    known = bool(risks) and all(r is not None for r in risks)
    return Plan(None, 'merged', sum(risks) if known else None, None,
                '' if known else 'R needs the stop of every trade merged here.')


def plans(journal, trades, annotations: dict | None = None) -> dict[str, Plan]:
    """plan() for each trade, reading the journal's reviews and linked orders once for all of them."""
    original = getattr(journal, 'original', journal)
    legs = {t.id: list(t.merge_leg_ids) for t in trades if getattr(t, 'merge_leg_ids', None)}
    singles = [t for t in trades if t.id not in legs]
    if legs:
        by_id = {t.id: t for t in original.trades()}
        singles += [by_id[id] for ids in legs.values() for id in ids if id in by_id]
    annotations = journal.annotations() if annotations is None else annotations
    events = original.protection_by_trade(singles)
    result = {t.id: plan(t, annotations.get(t.id, {}), events[t.id]) for t in singles}
    for id, ids in legs.items():
        result[id] = combined([result[i] for i in ids if i in result] if all(i in result for i in ids) else [])
    return {t.id: result[t.id] for t in trades}


def plan_for(journal, trade) -> Plan:
    return plans(journal, [trade])[trade.id]
