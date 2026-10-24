"""Deterministic average-cost round trips; reversals split a fill and its fee proportionately.

Fills that carry the platform's own position id are netted inside that position; the others pair
flat to flat. The platform only keeps part of an account's history, so a position can arrive without
its entry or without its exit. Such a trade keeps its fills and is flagged with why it has no
result (model.INCOMPLETE); nothing is netted against a fill that is not there.
"""

from dataclasses import asdict

from .model import ENTRY_MISSING, EXIT_MISSING, NO_FILL_PRICE, Execution, Trade, digest, instant, point_value

FLAT = 1e-9
# Commission rates are set in MotiveWave, so the fee the study reports is the fee, $0 included.
# Every study fill carries this source label; it names where the fill came from, not a problem.
PLATFORM = 'motivewave-native:'
STUDY_SOURCE = ('MotiveWave internal capture', 'fees may be estimated')


def _flag(trade: Trade, text: str):
    if text not in trade.flags:
        trade.flags.append(text)


def _quality_notes(fill: Execution, platform: bool) -> list[str]:
    if fill.quality == 'execution':
        return []
    if not platform:
        return [fill.quality]
    return [part for part in (p.strip() for p in fill.quality.split(';')) if part and part not in STUDY_SOURCE]


def _leg(trade: Trade, fill: Execution, take: float, entry: bool):
    """The part of one fill this trade takes, with its share of the fee and what it says about quality."""
    fee = fill.commission * take / fill.qty
    platform = fill.provider.startswith(PLATFORM)
    for note in _quality_notes(fill, platform):
        _flag(trade, note)
    if not fill.commission_known and not platform:
        _flag(trade, 'fees not provided')
    if not fill.execution_id:
        _flag(trade, 'no Fill ID')
    if not fill.priced:
        _flag(trade, NO_FILL_PRICE)
    if entry:
        trade.entry_qty += take
        trade.entry_value += fill.price * take
    else:
        trade.exit_qty += take
        trade.exit_value += fill.price * take
    trade.commission += fee
    trade.fills.append({**asdict(fill), 'key': fill.key, 'qty': take, 'commission': fee,
                        'role': 'entry' if entry else 'exit'})


def match(executions: list[Execution], instruments: dict | None = None) -> list[Trade]:
    instruments = instruments or {}
    books, settled, trades = {}, {}, []
    placed, newest = {}, {}       # the book each trade was built in; the last fill of each platform position

    def start(book, fill, direction):
        scope = book[0]
        multiplier = instruments.get(fill.symbol, fill.point_value)
        if multiplier is None:
            multiplier = point_value(fill.symbol)
        # The id names the trade's first fill and nothing else, so reviews stay attached to a trade
        # for as long as it still begins with that fill.
        trade = Trade(digest([scope, fill.key, fill.side])[:24], *scope[:3], direction, fill.currency, fill.ts,
                      point_value=multiplier)
        books[book] = trade
        placed[id(trade)] = book
        trades.append(trade)
        return trade

    # Equal-time fills retain their ingestion order (Python sort is stable), except that a fill the
    # platform calls an entry of its position comes before the others of that instant: a snapshot
    # that lists an exit ahead of its same-millisecond entry does not make the entry look missing.
    ordered = sorted(executions, key=lambda e: (instant(e.ts), not (e.native_position_id and e.native_entry is True)))
    for index, fill in enumerate(ordered):
        scope = (fill.provider, fill.account, fill.symbol, fill.currency)
        position = fill.native_position_id
        book = (scope, position)            # fills without a platform position share one flat-to-flat book
        said = fill.native_entry if position else None
        if position:
            newest.setdefault(scope, {})[position] = index
        trade = books.get(book)
        if position and (trade is None or ENTRY_MISSING not in trade.flags):
            adds = trade is not None and (trade.direction == 'long') == (fill.sign > 0)
            held = trade.open_qty if trade is not None else 0.
            # The platform says this fill reduced a position: it cannot add to one, and it cannot take
            # out more than the kept fills opened. It says it opened or added: it cannot reduce one
            # without reversing it. Either way the position did not begin where its kept fills begin.
            if (said is False and (adds or fill.qty > held + FLAT)) or (
                    said is True and trade is not None and not adds and fill.qty <= held + FLAT):
                if trade is None:
                    trade = settled.get(book) or start(book, fill, 'short' if fill.sign > 0 else 'long')
                    books[book] = trade
                _flag(trade, ENTRY_MISSING)
        if trade is not None and ENTRY_MISSING in trade.flags:
            # How much of this position is open is not known, so its remaining fills are kept
            # together and never netted, split or carried into another trade.
            entry = said if said is not None else (trade.direction == 'long') == (fill.sign > 0)
            _leg(trade, fill, fill.qty, entry)
            trade.open_qty = 0
            trade.max_qty = max(trade.max_qty, trade.entry_qty, trade.exit_qty)
            trade.closed = fill.ts
            continue
        remaining = fill.qty
        while remaining > FLAT:
            if trade is None:
                trade = start(book, fill, 'long' if fill.sign > 0 else 'short')
            sign = 1 if trade.direction == 'long' else -1
            entry = sign == fill.sign
            take = remaining if entry else min(remaining, trade.open_qty)
            if entry:
                trade.average_cost = (trade.average_cost * trade.open_qty + fill.price * take) / (trade.open_qty + take)
                trade.open_qty += take
                trade.max_qty = max(trade.max_qty, trade.open_qty)
            else:
                trade.realised_points += (fill.price - trade.average_cost) * sign * take
                trade.open_qty -= take
            _leg(trade, fill, take, entry)
            remaining -= take
            if trade.open_qty < FLAT:
                trade.open_qty = 0
                trade.closed = fill.ts
                settled[book] = books.pop(book)
                trade = None
    for trade in trades:
        scope, position = placed[id(trade)]
        if position and trade.open_qty > FLAT and ENTRY_MISSING not in trade.flags and any(
                other != position and index > newest[scope][position] for other, index in newest[scope].items()):
            # One account holds one position per instrument: the platform moved on to another, so
            # this one ended in a fill it did not keep. Only the newest position can still be open.
            _flag(trade, EXIT_MISSING)
            trade.closed = trade.fills[-1]['ts']
        if trade.incomplete:
            trade.realised_points = trade.average_cost = None
            for role, total in (('entry', 'entry_value'), ('exit', 'exit_value')):
                if any(f['role'] == role and f['price'] == 0 for f in trade.fills):
                    setattr(trade, total, None)
    return trades
