"""Version 1 of the local bridge protocol, with UTC timestamps and explicit units."""

from __future__ import annotations

import datetime as dt
import functools
import hashlib
import json
import math
import re
from dataclasses import asdict, dataclass, field

import pandas as pd

UTC = dt.timezone.utc
FUTURES = {'ES': 50, 'MES': 5, 'NQ': 20, 'MNQ': 2, 'YM': 5, 'MYM': .5,
           'RTY': 50, 'M2K': 5, 'CL': 1000, 'MCL': 100, 'GC': 100, 'MGC': 10,
           'ZN': 1000, 'ZB': 1000}
# Why a trade has no result. These exact texts are its flags, shown to the trader as they are.
ENTRY_MISSING = 'entry not in platform history'
EXIT_MISSING = 'exit not in platform history'
NO_FILL_PRICE = 'platform reported no fill price'
INCOMPLETE = (ENTRY_MISSING, EXIT_MISSING, NO_FILL_PRICE)
WHY_INCOMPLETE = {
    ENTRY_MISSING: 'The platform no longer had the fill that opened this position, so its entry and its result are unknown.',
    EXIT_MISSING: 'The platform has opened another position here since, but the fill that closed this one is not in its history.',
    NO_FILL_PRICE: 'The platform stored no price for one of these fills, so the result cannot be calculated.',
}
# The platform's own description of a fill. It groups fills into positions; it is never part of a
# fill's identity, and a fill that has none is stored exactly as older journals stored it.
NATIVE = ('native_position_id', 'native_entry')
# Study 0.1 and 0.2 reported fills they had to leave out as a heartbeat error, in these words, although
# everything else was being captured. One or more such sentences, and nothing else, is a warning.
PARTIAL_CAPTURE = re.compile(r'Some [A-Za-z ]+ unavailable; capture is partial\.(?:; Some [A-Za-z ]+ unavailable; '
                             r'capture is partial\.)*')


def timestamp(value, timezone: str = 'UTC') -> str:
    """Naive dates require an explicit source timezone; ambiguous DST times are rejected."""
    try:
        if isinstance(value, (int, float, bool)):
            raise ValueError('use an explicit calendar timestamp, not an ambiguous epoch number')
        if isinstance(value, str) and not re.search(
                r'\d{4}[-/]\d{1,2}[-/]\d{1,2}|\d{1,2}[-/]\d{1,2}[-/]\d{4}|(?<!\d)\d{8}(?!\d)', value):
            raise ValueError('a full calendar date is required; time-zone offsets are not dates')
        stamp = pd.Timestamp(value)
        if pd.isna(stamp):
            raise ValueError('missing timestamp')
        if stamp.tzinfo is None:
            stamp = stamp.tz_localize(timezone, ambiguous='raise', nonexistent='raise')
        return stamp.tz_convert('UTC').isoformat()
    except Exception as exc:
        raise ValueError(f'Invalid timestamp {value!r}; include a date and timezone/UTC offset.') from exc


def instant(value: str) -> dt.datetime:
    return dt.datetime.fromisoformat(value.replace('Z', '+00:00'))


def finite(value, name: str, positive: bool = False) -> float:
    try:
        result = float(value)
    except (ValueError, TypeError) as exc:
        raise ValueError(f'{name} must be a number.') from exc
    if not math.isfinite(result) or (positive and result <= 0):
        raise ValueError(f'{name} must be finite' + (' and greater than zero.' if positive else '.'))
    return result


def heartbeat_report(heartbeat: dict | None) -> tuple[str | None, str | None]:
    """(error, warnings) of a study heartbeat. An error means capture is failing and needs attention;
    warnings travel with a capture that is working."""
    if not isinstance(heartbeat, dict):
        return None, None
    warnings = heartbeat.get('warnings')
    if isinstance(warnings, (list, tuple)):
        warnings = '; '.join(w.strip() for w in warnings if isinstance(w, str) and w.strip())
    warnings = warnings.strip() if isinstance(warnings, str) and warnings.strip() else None
    if heartbeat.get('status') != 'error':
        return None, warnings
    detail = heartbeat.get('detail')
    detail = detail.strip() if isinstance(detail, str) and detail.strip() else 'The study reported an error.'
    if PARTIAL_CAPTURE.fullmatch(detail):
        return None, detail if not warnings else detail + '; ' + warnings
    return detail, warnings


def digest(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str, allow_nan=False).encode()).hexdigest()


@functools.lru_cache(maxsize=4096)
def product(symbol: str) -> str:
    """What is traded, whatever the contract month or chart: the symbol in capitals without a leading
    slash, an exchange suffix ('.CME', or Yahoo's '=F') or a trailing month code with its year digits.
    NQZ6, NQZ26, NQZ2026, /NQZ6, NQZ6.CME and a continuous NQ chart are all NQ; MNQZ6 is MNQ, another
    product at the same price. The letters stay as the connection sends them (CQG's ENQZ6 is ENQ): a
    trader's fills and chart prices come through the same connection, so they share them. A trade's
    prices are the prices of its product (trade_prices), and markets are grouped by it."""
    text = str(symbol or '').strip().upper().lstrip('/')
    text = re.sub(r'^([^.]+)\.[A-Z]{2,}$', r'\1', text).removesuffix('=F')
    month = re.fullmatch(r'(.+?)\s*[FGHJKMNQUVXZ]\d{1,4}', text)
    return month.group(1) if month else text


instrument_market = product     # the name app/ uses for the market filter


def point_value(symbol: str) -> float | None:
    return FUTURES.get(product(symbol))


def trade_prices(samples, symbol: str, source: str) -> list:
    """The prices of one source that measure a trade in `symbol`: those recorded under any symbol of
    its product, in their order. In roll week the next contract trades at another price, so when some
    were recorded under `symbol` itself only those are used. A print recorded under two labels (two
    charts of one market) counts once."""
    exact = str(symbol).strip().upper()
    wanted = product(exact)
    chosen = [s for s in samples if s.source == source and product(s.symbol) == wanted]
    if any(s.symbol == exact for s in chosen):
        return [s for s in chosen if s.symbol == exact]
    seen, result = set(), []
    for sample in chosen:
        # A bar is its slot, as in the journal; a tick is everything it says apart from its label.
        key = (sample.ts, sample.resolution_seconds) + (() if sample.resolution_seconds else (
            sample.high, sample.bid, sample.ask, sample.volume))
        if key not in seen:
            seen.add(key)
            result.append(sample)
    return result


@dataclass(frozen=True)
class Execution:
    provider: str
    account: str
    symbol: str
    side: str
    qty: float
    price: float
    ts: str
    execution_id: str = ''
    order_id: str = ''
    commission: float = 0
    point_value: float | None = None
    currency: str = 'USD'
    quality: str = 'execution'
    commission_known: bool = True
    native_position_id: str = ''          # the platform's id for the position this fill belongs to
    native_entry: bool | None = None      # True opens or adds, False reduces or closes, None when not said

    def __post_init__(self):
        for key in ('provider', 'account', 'symbol', 'currency'):
            raw = getattr(self, key)
            val = '' if raw is None else str(raw).strip()
            if not val:
                raise ValueError(f'{key} is required.')
            object.__setattr__(self, key, val.upper() if key in ('symbol', 'currency') else val)
        for key in ('execution_id', 'order_id', 'native_position_id'):
            object.__setattr__(self, key, str(getattr(self, key) or '').strip())
        if not isinstance(self.commission_known, bool):
            raise ValueError('commission_known must be true or false.')
        if self.native_entry is not None and not isinstance(self.native_entry, bool):
            raise ValueError('native_entry must be true or false.')
        if not isinstance(self.quality, str) or not self.quality.strip():
            raise ValueError('Execution quality must be a non-empty label.')
        side = str(self.side).strip().lower()
        side = {'b': 'buy', 's': 'sell', 'bot': 'buy', 'sld': 'sell'}.get(side, side)
        if side not in ('buy', 'sell'):
            raise ValueError('Side must be buy or sell.')
        object.__setattr__(self, 'side', side)
        object.__setattr__(self, 'qty', finite(self.qty, 'Contracts', True))
        object.__setattr__(self, 'price', finite(self.price, 'Price'))
        object.__setattr__(self, 'commission', finite(self.commission, 'Commission'))
        object.__setattr__(self, 'ts', timestamp(self.ts))
        if self.point_value is not None:
            object.__setattr__(self, 'point_value', finite(self.point_value, 'Point value', True))

    @property
    def key(self) -> str:
        # Fee corrections are conflicts on the same identity, not additional fills.
        identity = self.execution_id or digest([self.order_id, self.symbol, self.side, self.ts, self.qty, self.price])
        return digest([self.provider, self.account, identity])

    @property
    def sign(self) -> int:
        return 1 if self.side == 'buy' else -1

    @property
    def priced(self) -> bool:
        """False when the platform stored no price for this fill, which it reports as exactly 0."""
        return self.price != 0

    def payload(self) -> dict:
        """What the journal stores and compares when the same fill arrives again."""
        data = asdict(self)
        if not self.native_position_id:
            del data['native_position_id']
        if self.native_entry is None:
            del data['native_entry']
        return data


@dataclass(frozen=True)
class PriceSample:
    symbol: str
    ts: str
    source: str
    high: float
    low: float
    resolution_seconds: float = 0   # 0 = observed tick; bars have a start time and duration
    bid: float | None = None
    ask: float | None = None
    proxy: bool = False
    open: float | None = None
    close: float | None = None
    volume: float | None = None

    @classmethod
    def stored(cls, data: dict) -> 'PriceSample':
        """A price read back from a journal, which checked it when it was stored. Checking it again took
        most of the time of reading a long tape, and an open trade's prices are read every second."""
        sample = object.__new__(cls)
        for name, spec in cls.__dataclass_fields__.items():
            object.__setattr__(sample, name, data[name] if name in data else spec.default)
        return sample

    @property
    def last(self) -> float | None:
        """The price it ended at: a print's price, a bar's close; None for a bar with only a high and low."""
        return self.close if self.close is not None else self.high if not self.resolution_seconds else None

    def __post_init__(self):
        object.__setattr__(self, 'symbol', str(self.symbol or '').strip().upper())
        if not self.symbol or not isinstance(self.source, str) or not self.source.strip():
            raise ValueError('Prices need an exact symbol and a source label.')
        if not isinstance(self.proxy, bool):
            raise ValueError('Price proxy must be true or false.')
        object.__setattr__(self, 'ts', timestamp(self.ts))
        for key in ('high', 'low', 'resolution_seconds'):
            object.__setattr__(self, key, finite(getattr(self, key), key))
        if self.high < self.low or not 0 <= self.resolution_seconds <= 86400:
            raise ValueError('Price high must be >= low; bar length must be between 0 and one day.')
        for key in ('bid', 'ask'):
            if getattr(self, key) is not None:
                object.__setattr__(self, key, finite(getattr(self, key), key))
        if self.bid is not None and self.ask is not None and self.bid > self.ask:
            raise ValueError('Crossed bid/ask quote.')
        for key in ('open', 'close', 'volume'):
            if getattr(self, key) is not None:
                object.__setattr__(self, key, finite(getattr(self, key), key))
        if (self.open is None) != (self.close is None):
            raise ValueError('Supply both open and close, or neither.')
        if self.open is not None and not (self.low <= self.open <= self.high and self.low <= self.close <= self.high):
            raise ValueError('Open and close must lie within the high/low range.')
        if self.volume is not None and self.volume < 0:
            raise ValueError('Volume cannot be negative.')
        if self.resolution_seconds == 0 and self.high != self.low:
            raise ValueError('An observed tick must have a single price; use a bar length for ranges.')


@dataclass(frozen=True)
class ProtectionEvent:
    """An explicitly linked protective order, never inferred from a nearby stop/limit order."""
    provider: str
    account: str
    symbol: str
    order_id: str
    ts: str
    role: str                         # stop or target, asserted by the execution adapter
    state: str                        # working, cancelled, filled, rejected
    price: float | None = None
    entry_execution_id: str = ''
    trade_id: str = ''

    def __post_init__(self):
        for key in ('provider', 'account', 'symbol', 'order_id'):
            value = str(getattr(self, key) or '').strip()
            if not value:
                raise ValueError(f'Protection event needs {key}.')
            object.__setattr__(self, key, value.upper() if key == 'symbol' else value)
        for key in ('entry_execution_id', 'trade_id'):
            object.__setattr__(self, key, str(getattr(self, key) or '').strip())
        if not self.entry_execution_id and not self.trade_id:
            raise ValueError('Link protection to trade_id or entry_execution_id; proximity is not sufficient.')
        if self.role not in ('stop', 'target') or self.state not in ('working', 'cancelled', 'filled', 'rejected'):
            raise ValueError('Invalid protective order role/state.')
        object.__setattr__(self, 'ts', timestamp(self.ts))
        if self.price is not None:
            object.__setattr__(self, 'price', finite(self.price, 'Protective order price'))
        if self.state == 'working' and self.price is None:
            raise ValueError('A working protective order needs its price.')


@dataclass
class Trade:
    """One round trip. When `complete` is false its result is unknown: `incomplete` says why, P&L and
    average prices that cannot be known are None, and `opened`/`closed` are only the first and last
    fills the platform still has."""
    id: str
    provider: str
    account: str
    symbol: str
    direction: str
    currency: str
    opened: str
    closed: str | None = None
    entry_qty: float = 0
    exit_qty: float = 0
    entry_value: float | None = 0
    exit_value: float | None = 0
    open_qty: float = 0
    max_qty: float = 0
    average_cost: float | None = 0
    realised_points: float | None = 0
    commission: float = 0
    point_value: float | None = None
    fills: list[dict] = field(default_factory=list)
    flags: list[str] = field(default_factory=list)

    @property
    def incomplete(self) -> list[str]:
        """The reasons this trade has no result, in the order of INCOMPLETE; empty for a usable trade."""
        return [reason for reason in INCOMPLETE if reason in self.flags]

    @property
    def complete(self) -> bool:
        """The one question to ask before using P&L, excursions or statistics. A complete trade can
        still be open; an incomplete one is never counted."""
        return not self.incomplete

    @property
    def status(self) -> str:
        return 'incomplete' if self.incomplete else 'closed' if self.closed else 'open'

    @property
    def entry_price(self):
        return self.entry_value / self.entry_qty if self.entry_qty and self.entry_value is not None else None

    @property
    def exit_price(self):
        return self.exit_value / self.exit_qty if self.exit_qty and self.exit_value is not None else None

    @property
    def gross(self):
        if self.incomplete or self.realised_points is None or self.point_value is None:
            return None
        return self.realised_points * self.point_value

    @property
    def net(self):
        return self.gross - self.commission if self.gross is not None else None

    def net_at(self, price):
        """The net result if the contracts still held were closed at `price`, without that exit's fees."""
        if self.net is None or self.average_cost is None or price is None:
            return None
        sign = 1 if self.direction == 'long' else -1
        return self.net + sign * (price - self.average_cost) * self.open_qty * self.point_value

    @property
    def basis(self):
        # What a stored measurement was computed against. The platform's position labels are left
        # out, so a measurement survives a fill that only gained them.
        return digest([[{k: v for k, v in fill.items() if k not in NATIVE} for fill in self.fills],
                       self.point_value])

    def row(self):
        return {**asdict(self), 'entry_price': self.entry_price, 'exit_price': self.exit_price,
                'gross': self.gross, 'net': self.net, 'basis': self.basis,
                'status': self.status, 'complete': self.complete, 'incomplete': self.incomplete}
