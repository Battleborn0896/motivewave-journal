"""Money, dates, times and durations the way a person writes them, in one display time zone.

Stored times stay UTC. Only what is shown changes: 'Oct 15, 11:52:42 AM', 'Oct 15 · 11:52 AM to
12:01 PM · 8m 56s', '+$810.02', '-$152.48'. Month and day names are English on every computer, so
the journal reads the same whatever the system locale is.
"""
from __future__ import annotations

import datetime as dt
import functools
import os
import re
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

MONTHS = ('Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec')
WEEKDAYS = ('Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun')
SIGNS = {'USD': '$', 'EUR': '€', 'GBP': '£', 'JPY': '¥', 'CAD': 'CA$', 'AUD': 'A$', 'NZD': 'NZ$',
         'HKD': 'HK$', 'SGD': 'S$', 'CNY': 'CN¥', 'INR': '₹', 'KRW': '₩', 'BRL': 'R$', 'MXN': 'MX$'}
# Windows names its time zones itself; these are the IANA zones of the ones traders use most.
WINDOWS_ZONES = {
    'Eastern Standard Time': 'America/New_York', 'Central Standard Time': 'America/Chicago',
    'Mountain Standard Time': 'America/Denver', 'US Mountain Standard Time': 'America/Phoenix',
    'Pacific Standard Time': 'America/Los_Angeles', 'Alaskan Standard Time': 'America/Anchorage',
    'Hawaiian Standard Time': 'Pacific/Honolulu', 'Atlantic Standard Time': 'America/Halifax',
    'Newfoundland Standard Time': 'America/St_Johns', 'Canada Central Standard Time': 'America/Regina',
    'Central Standard Time (Mexico)': 'America/Mexico_City', 'SA Pacific Standard Time': 'America/Bogota',
    'E. South America Standard Time': 'America/Sao_Paulo', 'Argentina Standard Time': 'America/Argentina/Buenos_Aires',
    'GMT Standard Time': 'Europe/London', 'Greenwich Standard Time': 'Atlantic/Reykjavik',
    'W. Europe Standard Time': 'Europe/Berlin', 'Romance Standard Time': 'Europe/Paris',
    'Central Europe Standard Time': 'Europe/Budapest', 'Central European Standard Time': 'Europe/Warsaw',
    'GTB Standard Time': 'Europe/Bucharest', 'FLE Standard Time': 'Europe/Helsinki',
    'E. Europe Standard Time': 'Europe/Chisinau', 'Russian Standard Time': 'Europe/Moscow',
    'Turkey Standard Time': 'Europe/Istanbul', 'Israel Standard Time': 'Asia/Jerusalem',
    'South Africa Standard Time': 'Africa/Johannesburg', 'Arabian Standard Time': 'Asia/Dubai',
    'Arab Standard Time': 'Asia/Riyadh', 'India Standard Time': 'Asia/Kolkata',
    'Pakistan Standard Time': 'Asia/Karachi', 'SE Asia Standard Time': 'Asia/Bangkok',
    'Singapore Standard Time': 'Asia/Singapore', 'China Standard Time': 'Asia/Shanghai',
    'Taipei Standard Time': 'Asia/Taipei', 'Tokyo Standard Time': 'Asia/Tokyo',
    'Korea Standard Time': 'Asia/Seoul', 'W. Australia Standard Time': 'Australia/Perth',
    'E. Australia Standard Time': 'Australia/Brisbane', 'Cen. Australia Standard Time': 'Australia/Adelaide',
    'AUS Eastern Standard Time': 'Australia/Sydney', 'New Zealand Standard Time': 'Pacific/Auckland',
    'UTC': 'UTC', 'Coordinated Universal Time': 'UTC',
}


# ---------------------------------------------------------------- money

def symbol(currency: str | None) -> str:
    """'$' for USD; an unfamiliar currency is written as its code and a space: 'CHF '."""
    code = str(currency or 'USD').strip().upper()
    return SIGNS.get(code, code + ' ')


def money(value, currency: str | None = 'USD', *, signed: bool = True, decimals: int = 2) -> str:
    """'+$810.02', '-$152.48', '$0.00'; '—' when unknown. signed=False drops the plus for
    amounts that are not a result, such as fees or a drawdown ('$8.00', '-$2.00' for a rebate)."""
    if value is None or value != value:
        return '—'
    shown = round(abs(value), decimals)
    sign = ('-' if value < 0 else '+' if signed else '') if shown else ''
    return f'{sign}{symbol(currency)}{abs(value):,.{decimals}f}'


def number(value, decimals: int = 2) -> str:
    return '—' if value is None or value != value else f'{value:,.{decimals}f}'


# ---------------------------------------------------------------- time zones

def valid_zone(name) -> bool:
    try:
        ZoneInfo(str(name))
        return bool(name)
    except (ZoneInfoNotFoundError, ValueError, TypeError):
        return False


def _from_path(path: str) -> str | None:
    match = re.search(r'zoneinfo/(?:posix/|right/)?(.+)$', path.replace('\\', '/'))
    return match.group(1) if match and valid_zone(match.group(1)) else None


def _windows_zone() -> str | None:
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                            r'SYSTEM\CurrentControlSet\Control\TimeZoneInformation') as key:
            name = winreg.QueryValueEx(key, 'TimeZoneKeyName')[0]
    except (ImportError, OSError):
        return None
    return WINDOWS_ZONES.get(str(name).strip('\x00').strip())


def _offset_zone(offset: dt.timedelta | None = None) -> str:
    """The computer's current whole-hour offset as an Etc zone (whose sign is inverted), else UTC."""
    if offset is None:
        offset = dt.datetime.now().astimezone().utcoffset() or dt.timedelta()
    hours, rest = divmod(offset.total_seconds(), 3600)
    if rest or not hours:
        return 'UTC'
    return f'Etc/GMT{-int(hours):+d}'


@functools.lru_cache(maxsize=1)
def local_zone() -> str:
    """The IANA name of this computer's time zone: TZ, then the system setting, else its offset."""
    candidates = [os.environ.get('TZ', '').lstrip(':').strip()]
    try:
        candidates.append(_from_path(os.path.realpath('/etc/localtime')) or '')
    except OSError:
        pass
    try:
        candidates.append(Path('/etc/timezone').read_text(encoding='utf-8').strip())
    except OSError:
        pass
    if os.name == 'nt':
        candidates.append(_windows_zone() or '')
    for name in candidates:
        # An abbreviation such as EST is a fixed offset with no daylight saving: not the computer's zone.
        if name and ('/' in name or name == 'UTC') and valid_zone(name):
            return name
    return _offset_zone()


def zone_name(zone: str, at=None) -> str:
    """The zone's short name at that moment where it has a familiar one ('EDT', 'PST'), else its
    city ('Berlin')."""
    short = to_zone(at or dt.datetime.now(dt.timezone.utc), zone).tzname() or ''
    if re.fullmatch(r'[A-Z]{2,5}', short):
        return short
    return zone.rsplit('/', 1)[-1].replace('_', ' ')


# ---------------------------------------------------------------- dates and times

def instant(value) -> dt.datetime:
    """A stored ISO time, a datetime or a pandas Timestamp as an aware datetime (naive means UTC)."""
    if hasattr(value, 'to_pydatetime'):
        value = value.to_pydatetime()
    if isinstance(value, str):
        value = dt.datetime.fromisoformat(value.strip().replace('Z', '+00:00'))
    if not isinstance(value, dt.datetime):
        raise ValueError(f'Not a time: {value!r}')
    return value if value.tzinfo else value.replace(tzinfo=dt.timezone.utc)


def to_zone(value, zone: str) -> dt.datetime:
    return instant(value).astimezone(ZoneInfo(zone))


def clock(value, zone: str, *, seconds: bool = False) -> str:
    """'11:52 AM', or '11:52:42 AM' with seconds."""
    t = to_zone(value, zone)
    return (f'{t.hour % 12 or 12}:{t.minute:02d}' + (f':{t.second:02d}' if seconds else '')
            + (' AM' if t.hour < 12 else ' PM'))


def _today(zone, now):
    return to_zone(now or dt.datetime.now(dt.timezone.utc), zone).date()


def date_label(value, zone: str | None = None, *, weekday: bool = False, now=None) -> str:
    """'Oct 15', 'Thu, Oct 15' with the weekday, and the year added when it is not this year.
    A date is taken as it is; a time is first moved into the zone."""
    day = value if isinstance(value, dt.date) and not isinstance(value, dt.datetime) else to_zone(value, zone).date()
    text = f'{MONTHS[day.month - 1]} {day.day}'
    if day.year != _today(zone or 'UTC', now).year:
        text += f', {day.year}'
    return (WEEKDAYS[day.weekday()] + ', ' + text) if weekday else text


def moment(value, zone: str, *, seconds: bool = True, now=None) -> str:
    """'Oct 15, 11:52:42 AM'; '—' when there is no time."""
    if value is None or value == '':
        return '—'
    try:
        return date_label(value, zone, now=now) + ', ' + clock(value, zone, seconds=seconds)
    except (ValueError, TypeError):
        return str(value)                      # something that is not a time is shown as it is


def duration(seconds) -> str:
    """'45s', '8m 56s', '1h 05m', '2d 3h'."""
    if seconds is None:
        return '—'
    seconds = max(0, int(round(float(seconds))))
    days, rest = divmod(seconds, 86400)
    hours, rest = divmod(rest, 3600)
    minutes, secs = divmod(rest, 60)
    if days:
        return f'{days}d {hours}h'
    if hours:
        return f'{hours}h {minutes:02d}m'
    if minutes:
        return f'{minutes}m {secs:02d}s'
    return f'{secs}s'


def held(opened, closed) -> float | None:
    """Seconds between two stored times, or None while the second is missing."""
    return (instant(closed) - instant(opened)).total_seconds() if opened and closed else None


def span(opened, closed, zone: str, *, now=None) -> str:
    """'Oct 15 · 11:52 AM to 12:01 PM · 8m 56s'; across midnight both dates are given; an open
    trade reads 'Oct 15 · 11:52 AM · Still Open'."""
    start = to_zone(opened, zone)
    if not closed:
        return f'{date_label(start, zone, now=now)} · {clock(start, zone)} · Still Open'
    end = to_zone(closed, zone)
    length = duration(held(opened, closed))
    if start.date() == end.date():
        return f'{date_label(start, zone, now=now)} · {clock(start, zone)} to {clock(end, zone)} · {length}'
    return (f'{date_label(start, zone, now=now)}, {clock(start, zone)} to '
            f'{date_label(end, zone, now=now)}, {clock(end, zone)} · {length}')


def wall_clock(values, zone: str):
    """Stored times as naive wall-clock datetimes in the zone, for chart axes that print what they get."""
    return [to_zone(v, zone).replace(tzinfo=None) if v not in (None, '') and v == v else None for v in values]


def price(value) -> str:
    """A price as the market quotes it, every decimal it has and no more: '19,990.25', '1.08515', '20,000'."""
    return f'{value:,.6f}'.rstrip('0').rstrip('.')
