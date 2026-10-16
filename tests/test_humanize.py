"""Money, times and the display time zone written the way a person reads them. No account data."""
import datetime as dt
from zoneinfo import ZoneInfo

import pytest

import common
from mwjournal import Journal, humanize

NY = 'America/New_York'
NOW = dt.datetime(2026, 10, 16, 12, tzinfo=dt.timezone.utc)


def test_money_has_its_currency_sign_and_its_sign_first():
    assert humanize.money(-152.48) == '-$152.48' and humanize.money(810.02) == '+$810.02'
    assert humanize.money(0) == '$0.00' and humanize.money(-0.001) == '$0.00'
    assert humanize.money(1234567.891, 'EUR') == '+€1,234,567.89' and humanize.money(12, 'chf') == '+CHF 12.00'
    assert humanize.money(375, signed=False) == '$375.00' and humanize.money(-2, signed=False) == '-$2.00'
    assert humanize.money(809.6, decimals=0) == '+$810' and humanize.money(None) == '—'
    assert humanize.money(float('nan')) == '—'
    assert common.money(-152.48) == '-$152.48' and common.amount(375, 'USD') == '$375.00'
    assert common.amount(375.5) == '375.50'                     # another unit carries no currency
    assert common.excursion_amount(375, 'currency', 'USD') == '$375.00'
    assert common.excursion_amount(3, 'points') == '3.00 pts' and common.excursion_amount(1.25, 'r') == '1.25R'
    assert common.excursion_amount(None, 'currency') == '—'
    assert common.md('+$1.00 to $2.00') == '+\\$1.00 to \\$2.00'


def test_times_are_written_in_the_display_zone_like_a_person_writes_them():
    entry, exit_ = '2026-10-15T15:52:42+00:00', '2026-10-15T16:01:38Z'
    assert humanize.moment(entry, NY, now=NOW) == 'Oct 15, 11:52:42 AM'
    assert humanize.moment(entry, NY, seconds=False, now=NOW) == 'Oct 15, 11:52 AM'
    assert humanize.clock(entry, NY) == '11:52 AM' and humanize.clock('2026-10-15T04:05:00Z', NY) == '12:05 AM'
    assert humanize.clock(entry, 'Asia/Tokyo') == '12:52 AM'
    assert humanize.span(entry, exit_, NY, now=NOW) == 'Oct 15 · 11:52 AM to 12:01 PM · 8m 56s'
    assert humanize.span('2026-10-15T03:52:00Z', '2026-10-15T04:10:00Z', NY, now=NOW) == \
        'Oct 14, 11:52 PM to Oct 15, 12:10 AM · 18m 00s'
    assert humanize.span(entry, None, NY, now=NOW) == 'Oct 15 · 11:52 AM · Still Open'
    assert humanize.date_label('2025-10-15T15:52:42Z', NY, weekday=True, now=NOW) == 'Wed, Oct 15, 2025'
    assert humanize.date_label(dt.date(2026, 10, 15), NY, weekday=True, now=NOW) == 'Thu, Oct 15'
    assert humanize.moment(None, NY) == '—' and humanize.moment('not a time', NY) == 'not a time'
    assert [humanize.duration(s) for s in (0, 45, 536, 3900, 2 * 86400 + 3 * 3600)] == ['0s', '45s', '8m 56s',
                                                                                       '1h 05m', '2d 3h']
    assert humanize.wall_clock([entry, None], NY) == [dt.datetime(2026, 10, 15, 11, 52, 42), None]
    assert humanize.zone_name(NY, entry) == 'EDT' and humanize.zone_name(NY, '2026-01-15T15:00:00Z') == 'EST'


def test_the_computers_own_zone_is_found_on_every_system(monkeypatch):
    humanize.local_zone.cache_clear()
    try:
        monkeypatch.setenv('TZ', ':Europe/Berlin')
        assert humanize.local_zone() == 'Europe/Berlin'
        humanize.local_zone.cache_clear()
        monkeypatch.setenv('TZ', 'EST')                          # an abbreviation is not a place
        assert humanize.local_zone() != 'EST'
    finally:
        humanize.local_zone.cache_clear()
    assert humanize._from_path('/private/var/db/timezone/zoneinfo/America/Los_Angeles') == 'America/Los_Angeles'
    assert humanize._from_path('/usr/share/zoneinfo/posix/Europe/London') == 'Europe/London'
    assert humanize._from_path('/etc/localtime') is None
    assert all(humanize.valid_zone(zone) for zone in humanize.WINDOWS_ZONES.values())
    assert humanize._offset_zone(dt.timedelta(hours=-5)) == 'Etc/GMT+5'          # Etc names invert the sign
    assert humanize._offset_zone(dt.timedelta(hours=9)) == 'Etc/GMT-9'
    assert humanize._offset_zone(dt.timedelta(hours=5, minutes=30)) == 'UTC'      # no Etc zone for a half hour
    assert ZoneInfo('Etc/GMT+5').utcoffset(dt.datetime(2026, 1, 15)) == dt.timedelta(hours=-5)


def test_one_display_zone_defaults_to_this_computer_and_is_saved_for_live_and_replay(tmp_path, monkeypatch):
    monkeypatch.setenv('MWJ_HOME', str(tmp_path))
    monkeypatch.setattr(humanize, 'local_zone', lambda: 'Europe/Berlin')
    live, replay = Journal(), Journal(dataset='replay')
    assert common.display_zone(live) == common.display_zone(replay) == 'Europe/Berlin'
    assert common.display_zone(None) == 'Europe/Berlin'
    common.set_display_zone(live, 'America/Chicago')
    assert common.display_zone(live) == common.display_zone(replay) == 'America/Chicago'
    choices = common.timezone_choices('Asia/Kathmandu')
    assert choices[:4] == ['America/New_York', 'America/Chicago', 'America/Los_Angeles', 'UTC']
    assert 'Europe/Berlin' in choices and choices[-1] == 'Asia/Kathmandu' and len(choices) == len(set(choices))
    assert common.zone_label('Europe/Berlin') == 'Europe/Berlin · this computer'
    with pytest.raises(ValueError):
        live.save_preferences(display_timezone='Not/AZone')
