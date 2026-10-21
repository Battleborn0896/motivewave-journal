"""What more than one view needs: trade rows, the recalculation pass, the study-connection status.

Views take (journal, rows) and draw; anything two of them share lives here so they can change
independently. Widget keys are scoped to the trading environment with key(), so Live and Replay
never show each other's selections.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import sqlite3
from dataclasses import dataclass, fields
from pathlib import Path

import streamlit as st

from mwjournal import Journal, paths
from mwjournal.metrics import measurements
from mwjournal.model import (ENTRY_MISSING, EXIT_MISSING, NO_FILL_PRICE, Trade, instant, instrument_market,
                             point_value)
from mwjournal.scope import replay_run
from mwjournal import humanize, risk, units, tagging
from mwjournal.sync import DEFAULT_CONNECTION

VIEWS = ('Trades', 'Edge', 'Import', 'Connection', 'Daily review', 'Export')
PRIMARY_VIEWS = ('Trades', 'Analytics', 'Settings')
NAV_LABELS = {'Trades': 'Trades', 'Analytics': 'Stats', 'Settings': 'Setup'}
# Every page that is not one of the three, by the name navigation() takes: (its Setup page, the tab it opens).
# The Daily Report Card, the session tools and running a checklist are on Trades; dashboards are a Stats tab.
SETUP_ROUTES = {'Setup': ('Setup', None), 'General': ('Setup', None),
                'Tags': ('Tags', None), 'Checklists': ('Tags', 'Checklists'), 'Ratings': ('Ratings', None),
                'Data': ('Data', None), 'Import': ('Data', 'Import Trades'), 'Export': ('Data', 'Export & Backup'),
                'Journals': ('Data', 'Journals & Accounts'), 'Connection': ('Data', 'Connection')}
TOOLS = tuple(view for view in SETUP_ROUTES if view != 'Setup')
VIEW_KEY = 'journal-view'
TOOL_KEY = 'journal-settings-page'
# Requests for the page drawn next, taken by that page as it is drawn: which tab to open (tabs()), and
# whether Trades opens the Daily Report Card instead of a trade.
TAB_KEY = 'journal-open-tab'
REPORT_KEY = 'journal-open-report-card'
MAPPING_KEY = 'journal-import-mapping'      # the column mapping of the last CSV previewed in Import
# The first four keep their places: older selectors default to the first one.
TIMEZONES = ['America/New_York', 'America/Chicago', 'America/Los_Angeles', 'UTC',
             'America/Denver', 'America/Phoenix', 'America/Anchorage', 'Pacific/Honolulu', 'America/Toronto',
             'America/Vancouver', 'America/Mexico_City', 'America/Sao_Paulo', 'Europe/London', 'Europe/Dublin',
             'Europe/Lisbon', 'Europe/Paris', 'Europe/Berlin', 'Europe/Madrid', 'Europe/Rome', 'Europe/Amsterdam',
             'Europe/Zurich', 'Europe/Stockholm', 'Europe/Warsaw', 'Europe/Athens', 'Europe/Istanbul',
             'Europe/Moscow', 'Africa/Johannesburg', 'Asia/Dubai', 'Asia/Kolkata', 'Asia/Singapore',
             'Asia/Hong_Kong', 'Asia/Shanghai', 'Asia/Tokyo', 'Asia/Seoul', 'Australia/Perth',
             'Australia/Brisbane', 'Australia/Sydney', 'Pacific/Auckland']
PRECISION = {'platform-reported': 'Platform reported', 'observed-ticks': 'Observed ticks',
             'estimated-bars': 'OHLC estimate', 'proxy-estimate': 'Proxy estimate',
             'replay-simulated': 'Replay simulated / source unverified', 'pending': 'Waiting for data'}
STALE_AFTER = 60           # seconds without a heartbeat; the study sends one every 10
# One line under a replay trade that has no real ticks yet: the study loads them once the trade is flat.
REPLAY_TICKS_LATER = ('The study fills in this replay trade\'s real ticks after it closes, while the study is on '
                      'the replay chart.')
# The chart library's licence asks for this notice and link on a page its users can see.
CREDIT = '[TradingView Lightweight Charts™, Copyright (c) 2025 TradingView, Inc.](https://www.tradingview.com/)'


def key(journal, name: str) -> str:
    return f'{name}-{getattr(journal, "ui_scope", journal.dataset)}'


def open_journal(dataset: str):
    """The journal of one environment, or None after saying why it cannot be opened."""
    try:
        return Journal(dataset=dataset)
    except (ValueError, OSError, sqlite3.Error) as exc:
        st.error(f'The {dataset} journal could not be opened. {exc}')
        return None


def go(view: str):
    """An on_click handler that opens another view."""
    return lambda: st.session_state.update(navigation(view))


def navigation(view: str) -> dict:
    """The session-state changes that show a view, by any name it has had: a Setup page and its tab, the
    Daily Report Card on Trades, the Dashboards tab of Stats, or one of the three pages."""
    if view in SETUP_ROUTES:
        page, tab = SETUP_ROUTES[view]
        return {VIEW_KEY: 'Settings', TOOL_KEY: page, TAB_KEY: tab}
    if view == 'Dashboard':
        return {VIEW_KEY: 'Analytics', TAB_KEY: 'Dashboards'}
    if view == 'Daily review':
        return {VIEW_KEY: 'Trades', REPORT_KEY: True}
    if view == 'Live session':              # the session tools are the bar at the top of Trades
        return {VIEW_KEY: 'Trades'}
    return {VIEW_KEY: 'Analytics' if view in ('Edge', 'Analytics') else view}


def tabs(labels, key: str):
    """Tabs whose `.open` says which one is showing, that open the one navigation() asked for, and that come
    back on the tab last opened. The choice is kept under its own key as well as the widget's, because
    Streamlit forgets a widget's value on a page that does not draw it. A tab that is no longer there (Stats
    tabs can be removed) gives way to the first."""
    chosen = key + '-chosen'
    wanted = st.session_state.pop(TAB_KEY, None)
    if wanted in labels:
        st.session_state[chosen] = wanted
    if st.session_state.get(chosen) in labels:
        st.session_state[key] = st.session_state[chosen]
    else:
        st.session_state.pop(chosen, None)
        st.session_state.pop(key, None)

    def remember():
        st.session_state[chosen] = st.session_state.get(key)

    return st.tabs(list(labels), key=key, on_change=remember)


def keep(defaults: dict):
    """Gives each widget key in `defaults` that Streamlit has forgotten the value its widget last had (else its
    default), before the widget is drawn, and returns the function that keeps new values: the widget's on_change,
    or the on_click of the submit button of the form the widgets sit in. As in tabs(), the values are kept under
    keys of their own, because Streamlit forgets the value of a widget a run does not draw, and a Stats tab is
    drawn only while it is open. The widgets themselves take no default (Streamlit warns about a widget given
    both)."""
    for name, default in defaults.items():
        if name not in st.session_state:
            st.session_state[name] = st.session_state.get(name + '-chosen', default)

    def remember():
        for name in defaults:
            st.session_state[name + '-chosen'] = st.session_state.get(name, defaults[name])
    return remember


def stamp(journal):
    """Changes when there is something new to show: a fill, or a trade's first excursions. Streaming
    prices revise excursions in place and leave it alone, so an open position does not redraw the page."""
    try:
        with journal.connect() as db:
            return tuple(db.execute('SELECT (SELECT count(*) FROM executions), '
                                    '(SELECT count(*) FROM metrics), (SELECT count(*) FROM media)').fetchone())
    except sqlite3.Error:
        return None


def changes(journal):
    """Moves whenever the background watcher changes what the trade list shows: a new fill, a fill
    the platform described again, a linked order, a multiplier (a new one, or one set to another value) or a
    trade's first measurement."""
    try:
        with journal.connect() as db:
            return tuple(db.execute('SELECT (SELECT count(*) FROM executions), '
                                    '(SELECT total(length(payload)) FROM executions), (SELECT count(*) FROM metrics), '
                                    '(SELECT count(*) FROM protection), '
                                    "(SELECT group_concat(symbol || '=' || point_value) FROM instruments)").fetchone())
    except sqlite3.Error:
        return None


# ---------------------------------------------------------------- how things are written
# Every page formats money and time through these, so the journal reads one way everywhere.

def money(value, currency='USD', *, signed=True, decimals=2) -> str:
    """A result with its sign first: '+$810.02', '-$152.48'. signed=False for an amount that is not
    a result, such as fees: '$8.00'. decimals=0 for a tight space such as a calendar cell: '+$810'."""
    return humanize.money(value, currency, signed=signed, decimals=decimals)


def amount(value, currency=None) -> str:
    """A magnitude such as MAE or MFE, which carries no sign: '$375.00' in a currency, '375.00' in
    any other unit."""
    if currency is not None:
        return humanize.money(value, currency, signed=False)
    return humanize.number(value)


def md(text: str) -> str:
    """Text for a Markdown element (caption, markdown, metric): two '$' would start a formula."""
    return str(text).replace('$', '\\$')


def display_zone(journal=None) -> str:
    """The one time zone every time is shown in: the Time Zone chosen on Setup, else this computer's own
    zone. Stored times stay UTC."""
    try:
        chosen = journal.workspace()['preferences'].get('display_timezone') if journal is not None else None
    except (sqlite3.Error, ValueError, KeyError):
        chosen = None
    return chosen if chosen and humanize.valid_zone(chosen) else humanize.local_zone()


def set_display_zone(journal, zone: str) -> None:
    """Saves the display time zone for this journal and for both environments, so Live and Replay
    always show the same clock."""
    journal.save_preferences(display_timezone=zone)
    for dataset in paths.DATASETS:
        try:
            other = Journal(dataset=dataset)
            if other.path != getattr(journal, 'path', None):
                other.save_preferences(display_timezone=zone)
        except (ValueError, OSError, sqlite3.Error):
            pass                               # that journal cannot be opened; it keeps its own setting


def timezone_choices(*include) -> list[str]:
    """TIMEZONES plus this computer's zone and any zone already in use, so a saved choice is listed."""
    return list(dict.fromkeys([*TIMEZONES, humanize.local_zone(), *[z for z in include if z and humanize.valid_zone(z)]]))


def zone_label(zone: str) -> str:
    """'America/Los_Angeles · this computer' for the local zone, else the zone's own name."""
    return zone + (' · this computer' if zone == humanize.local_zone() else '')


def moment(value, zone: str, *, seconds=True) -> str:
    """'Oct 15, 11:52:42 AM' in the zone. The year is added when it is not this year."""
    return humanize.moment(value, zone, seconds=seconds)


def clock(value, zone: str, *, seconds=False) -> str:
    """'11:52 AM' in the zone."""
    return humanize.clock(value, zone, seconds=seconds)


def day_label(value, zone: str, *, weekday=False) -> str:
    """'Oct 15' or 'Thu, Oct 15' for a time in the zone or for a date."""
    return humanize.date_label(value, zone, weekday=weekday)


def span(opened, closed, zone: str) -> str:
    """'Oct 15 · 11:52 AM to 12:01 PM · 8m 56s'; an open trade reads '... · Still Open'."""
    return humanize.span(opened, closed, zone)


def duration(seconds) -> str:
    """'45s', '8m 56s', '1h 05m', '2d 3h'."""
    return humanize.duration(seconds)


def local_day(value, zone: str):
    """The calendar date of a stored time in the zone."""
    return humanize.to_zone(value, zone).date()


def wall_clock(values, zone: str) -> list:
    """Stored times as naive wall-clock datetimes in the zone: chart axes print exactly that."""
    return humanize.wall_clock(values, zone)


def money_column(label: str, currency='USD', **options):
    """A dataframe column for an amount that is not a result, such as a fee: '$8.00', '-$2.00'.
    It stays a number, so it sorts as one, and any other column keeps its own precision."""
    preset = {'USD': 'dollar', 'EUR': 'euro', 'JPY': 'yen'}.get(str(currency).upper())
    return st.column_config.NumberColumn(label, format=preset or f'%.2f {currency}', **options)


def money_table(frame, currency='USD', *, results=()):
    """A table for st.dataframe whose result columns read '+$810.02' / '-$152.48' while the numbers
    underneath still sort as numbers. Other numbers show two decimals, so keep prices out of it."""
    import pandas as pd
    frame = frame if isinstance(frame, pd.DataFrame) else pd.DataFrame(frame)
    formats = {name: (lambda v: money(v, currency)) for name in results if name in frame}
    if not formats:
        return frame
    others = [name for name in frame.columns if name not in formats]
    styled = frame.style.format(precision=2, na_rep='—', thousands=',', subset=others) if others else frame.style
    return styled.format(formats, subset=list(formats))


def unit_selector(journal, container=st):
    """MAE / MFE Units, saved for this journal the moment it is chosen."""
    widget_key = key(journal, 'excursion-unit')
    st.session_state.setdefault(widget_key, journal.display_settings()['excursion_unit'])
    def save():
        journal.set_excursion_unit(st.session_state[widget_key])
    container.selectbox('MAE / MFE Units', units.UNITS, format_func=units.LABELS.get, key=widget_key, on_change=save,
                        help='How MAE and MFE are shown everywhere. Display only: fills and the raw currency '
                             'measurements stay unchanged. Points and ticks measure price movement, not '
                             'quantity-weighted position profit.')


def excursion_unit(journal):
    widget_key = key(journal, 'excursion-unit')
    return st.session_state[widget_key] if widget_key in st.session_state else journal.display_settings()['excursion_unit']


def excursion_amount(value, unit, currency='USD'):
    """MAE / MFE in the chosen unit: '$375.00', '3.00 pts', '12.00 ticks', '1.25R'."""
    if value is None:
        return '—'
    if unit == 'currency':
        return amount(value, currency)
    return amount(value) + ('' if unit == 'r' else ' ') + units.suffix(unit, currency)


def display_metric(journal, trade, metric, unit=None):
    """Old stored reports need matching prices for points; never divide size-weighted currency blindly."""
    unit = unit or excursion_unit(journal)
    if getattr(trade, 'merge_overlapping', False) or unit not in ('points', 'ticks') or not metric or 'mae_points' in metric:
        return metric
    from mwjournal.metrics import excursions
    samples = journal.prices(trade.symbol, metric['source'], start=trade.opened, end=trade.closed)
    calculated = excursions(trade, samples, metric['source'], now=journal.trade_clock(trade))
    return {**metric, **{k: calculated.get(k) for k in ('mae_points', 'mfe_points', 'point_definition', 'point_precision')},
            'path': calculated.get('path', []), 'point_notes': calculated.get('notes', [])}


def best_display_metric(journal, trade, *, unit=None):
    """The measurement a trade is shown with: from recorded ticks only (metric_options). The unit is read from
    the journal unless given: rows_for reads it once for all the trades."""
    options = metric_options(journal, trade)
    rank = {'platform-reported': 4, 'observed-ticks': 3, 'estimated-bars': 2,
            'replay-simulated': 1, 'proxy-estimate': 1, 'pending': 0}
    best = max(options, key=lambda m: (rank.get(m.get('precision'), 0), m.get('basis', 'last') == 'last',
                                       m.get('observations', 0))) if options else {}
    unit = unit or excursion_unit(journal)
    if unit not in ('points', 'ticks'):
        return best
    options = [display_metric(journal, trade, m, unit) for m in options]
    available = [m for m in options if m.get('mae_points') is not None and m.get('mfe_points') is not None]
    rank = {'platform-reported': 4, 'observed-ticks': 3, 'estimated-bars': 2,
            'replay-simulated': 1, 'proxy-estimate': 1, 'pending': 0}
    return max(available, key=lambda m: (rank.get(m.get('point_precision'), 0),
                                        m.get('basis', 'last') == 'last', m.get('observations', 0))) if available else display_metric(journal, trade, best, unit)


def replay_ticks_pending(journal, trade) -> bool:
    """A replay trade without a measurement from real ticks yet. The study
    backfills one from MotiveWave's history once the trade is flat and the study is on the replay chart."""
    if getattr(journal, 'dataset', None) != 'replay' or not replay_run(getattr(trade, 'provider', '') or ''):
        return False
    if not getattr(trade, 'complete', True):
        return False                       # fills are missing: its excursions stay unknown, ticks or not
    return not any(m.get('precision') == 'observed-ticks' for m in journal.metric_options(trade))


def measured(metric) -> bool:
    """Whether a measurement has MAE and MFE: a stored one can still be waiting for its first price."""
    return bool(metric) and metric.get('precision') != 'pending'


# Why a trade has no result, as the line under its numbers says it.
UNKNOWN = {ENTRY_MISSING: "Unknown: the entry fill is no longer in MotiveWave's history.",
           EXIT_MISSING: "Unknown: the exit fill is not in MotiveWave's history.",
           NO_FILL_PRICE: 'Unknown: MotiveWave stored no price for one of the fills.'}


def no_excursions(journal, trade) -> str:
    """Why a trade shows no MAE / MFE, in one line: what happened to its prices, never a setting to change.
    The causes, first to last: fills missing, a replay trade's ticks still to come, prices that do not
    measure it (a gap in the ticks, only candles or other estimates), an open trade with no price yet, no
    prices at all."""
    if trade.incomplete:
        return UNKNOWN[trade.incomplete[0]]
    if trade.closed is not None and trade.closed == trade.opened:
        return 'Entered and exited at the same moment: there is no price path to measure.'
    if replay_ticks_pending(journal, trade):
        return REPLAY_TICKS_LATER
    kinds = {m.get('precision') for m in journal.metric_options(trade)} - {'pending', None}
    market = instrument_market(trade.symbol)
    if 'observed-ticks' in kinds:
        return 'Some of this trade\'s ticks were not recorded, so MAE / MFE are not measured.'
    if 'estimated-bars' in kinds:
        return 'Only 1-minute candles were recorded, so MAE / MFE are not measured.'
    if kinds:
        return 'Only estimated prices were recorded, so MAE / MFE are not measured.'
    if trade.status == 'open':
        return f'Waiting for the first {market} price.'
    # 'an MNQ chart', 'a CL chart': the article goes by how the first letter is said.
    article = 'an' if market[:1] in 'AEFHILMNORSX' else 'a'
    return (f'No prices were recorded for {market} during this trade. Keep the study on {article} {market} chart '
            'while you trade it.')


def open_pnl(trade, metric):
    """An open trade's P&L at the newest price its MAE and MFE were measured with, so the three agree: the
    header's and the trade list's. None until a price has been measured."""
    return trade.net_at(metric.get('last_price')) if trade.status == 'open' and metric else None


def row_trade(row) -> Trade:
    """The Trade a trade-list row was made from (rows_for)."""
    trade = Trade(**{f.name: row[f.name] for f in fields(Trade)})
    if row.get('merge_leg_ids'):
        trade.merge_leg_ids = row['merge_leg_ids']
        trade.merge_overlapping = row.get('merge_overlapping', False)
    return trade


def trade_name(row, zone: str) -> str:
    """A trade as a trader recognises it, in place of its id: 'Oct 20, 5:05 PM · NQZ6 Short 2'."""
    return (f"{moment(row['opened'], zone, seconds=False)} · {row['symbol']} {row['direction'].title()} "
            f"{row['max_qty']:g}")


def account_name(journal, provider, account) -> str:
    """An account as the trader knows it: the name given to it under Journals & Accounts, else the platform's
    own ('SIM'). Never the provider code it is stored under."""
    from mwjournal import journals
    return journals.alias(getattr(journal, 'capture_journal', journal), (provider, account))


def metric_options(journal, trade):
    """The measurements a trade can be shown with: from recorded ticks only, and none whose ticks the study
    reported a gap in. Candles, proxies, simulated replay prints and platform reports never measure a trade."""
    return [m for m in journal.metric_options(trade) if m.get('precision') == 'observed-ticks'
            and not journal.capture_gaps(m['source'], start=trade.opened, end=trade.closed, symbol=trade.symbol)]


def excursion_values(journal, trade, metric, risk=None):
    metric = display_metric(journal, trade, metric)
    unit = excursion_unit(journal)
    tick = units.tick_size(trade.symbol, journal.display_settings()['tick_sizes'])
    return tuple(units.value(metric, name, unit, risk=risk, tick=tick) for name in ('mae', 'mfe'))


def display_excursion_rows(journal, rows):
    """MAE and MFE of trade rows (rows_for) in the chosen unit. Each row carries the measurement it is shown with,
    chosen when the rows were built for this unit, so no trade is read again here."""
    result = []
    unit = excursion_unit(journal)
    sizes = journal.display_settings()['tick_sizes']
    for row in rows:
        metric = row['metric']
        mae, mfe = (units.value(metric, name, unit, risk=row.get('initial_risk'), tick=units.tick_size(row['symbol'], sizes))
                    for name in ('mae', 'mfe'))
        precision = metric.get('point_precision') if unit in ('points', 'ticks') else metric.get('precision')
        result.append({**row, 'display_mae': mae, 'display_mfe': mfe,
                       'display_precision': PRECISION.get(precision, 'Waiting for prices')})
    return result


def excursion_help(journal, trade, metric, at_risk):
    unit = excursion_unit(journal)
    if unit == 'r' and not units.positive(at_risk):
        # Why there is no risk, as the Stop box under Notes says it: no stop reported, a stop on the wrong side...
        st.info(risk.plan_for(journal, trade).why or 'R needs a stop: the stop order you place with the trade '
                'counts, or type your stop under Notes → Stop.')
    if unit == 'ticks' and units.tick_size(trade.symbol, journal.display_settings()['tick_sizes']) is None:
        with st.popover('Set Tick Size for ' + trade.symbol):
            with st.form(key(journal, 'tick-size-' + trade.id)):
                size = st.number_input('Minimum price increment', min_value=0., value=0., step=.000001, format='%.8f',
                                        help='Use the confirmed minimum price increment for this exact contract; '
                                             'this starting value is not an inferred specification.')
                if st.form_submit_button('Save Tick Size'):
                    if size <= 0:
                        st.error('Enter a confirmed tick size greater than zero.')
                        return
                    journal.set_tick_size(trade.symbol, size)
                    st.rerun()
    if unit in ('points', 'ticks') and metric.get('mae') is not None and metric.get('mae_points') is None:
        st.info('This report has currency excursions but no matching price path for points/ticks. '
                'It cannot be converted safely from whole-position dollars alone.')


# What the trade rows show besides fills, measurements, linked orders and multipliers (changes()): the journal_meta
# entries holding the tag library, ratings, display time zone and settings, and merged trades.
ROW_SETTINGS = ('workspace', 'tag_library', 'tag_catalog', 'display_settings', 'trade_merges')


def rows_signature(journal) -> tuple | None:
    """(what changes whenever a trade row could, the newest measurement of each trade), or None when the journal
    cannot be read. The first: changes() (a fill, a trade's first measurement, a linked order, a multiplier), a
    review, a day's tags, the tag library, a rating, a setting, a merge or an account's name. The second, by trade
    id: a measurement stored again (prices that came in late, a gap the study reported) leaves changes() as it
    was, but it replaces its row, and the new row has a higher rowid."""
    capture = getattr(journal, 'capture_journal', journal)
    found = hashlib.sha256()
    try:
        with journal.connect() as db:
            for (text,) in db.execute(
                    "SELECT 'a ' || trade_id || ' ' || payload FROM annotations UNION ALL "
                    "SELECT 's ' || key || ' ' || payload FROM sessions UNION ALL "
                    "SELECT 'm ' || key || ' ' || value FROM journal_meta WHERE key IN (%s)" % ','.join('?' * len(ROW_SETTINGS)),
                    ROW_SETTINGS):
                found.update(text.encode())
            measured = dict(db.execute('SELECT trade_id, max(rowid) FROM metrics GROUP BY trade_id'))
        with capture.connect() as db:
            names = db.execute("SELECT value FROM journal_meta WHERE key='journal_registry'").fetchone()
    except sqlite3.Error:
        return None                            # never equal to a signature read before: the rows are built again
    # A journal shown with its merged trades (merging.ReviewJournal) has other rows than the same journal without.
    return (changes(journal), found.hexdigest(), names and names[0], excursion_unit(journal), humanize.local_zone(),
            hasattr(journal, 'original')), measured


def rows(journal) -> list[dict]:
    """rows_for(journal), built again only when rows_signature says something they show has changed: a click
    that changes nothing reuses the rows of the click before. An open trade's measurement stored again is left
    out: the watcher stores it again at every poll, and the open trade's numbers are drawn live from the journal
    (trades.py LIVE), not from these rows; its fills and its first measurement still count (changes())."""
    cache = key(journal, 'trade-rows')
    read = rows_signature(journal)             # read first: a change during the build builds them again next time
    held = st.session_state.get(cache)

    def signature(rows):
        """What `rows` were built from, with the newest measurement of the trades not open in them."""
        if read is None:
            return None
        found, measured = read
        live = {r['id'] for r in rows if r['status'] == 'open'}
        return found, max((n for id, n in measured.items() if id not in live), default=None)

    if read is None or held is None or held[0] != signature(held[1]):
        built = rows_for(journal)
        held = st.session_state[cache] = (signature(built), built)
    return held[1]


def rows_for(journal) -> list[dict]:
    """One row per trade with its review, tags, risk and measurement. The journal's settings are read once."""
    annotations = journal.annotations()
    library = journal.tag_library()
    sessions = journal.get('sessions')
    from mwjournal.workspace import tag_score, rating
    workspace = journal.workspace()
    from mwjournal import journals
    aliases = journals.registry(getattr(journal, 'capture_journal', journal))['account_aliases']
    import json
    zone = display_zone(journal)
    unit = excursion_unit(journal)
    rows = []
    trades = journal.trades()
    plans = risk.plans(journal, trades, annotations)
    for trade in trades:
        a = annotations.get(trade.id, {})
        m = best_display_metric(journal, trade, unit=unit)
        tag_ids = tagging.effective(library, a, sessions.get(tagging.session_key(library, trade.opened), {}))
        # The risk to the trade's stop (risk.py): every R the app shows divides by it.
        at_risk = plans[trade.id].risk
        # setup is always present: a captured trade nobody has reviewed yet still has to chart and group.
        rows.append({**trade.row(), 'setup': '', **a, 'initial_risk': at_risk, 'stop': plans[trade.id].stop,
                     'market': instrument_market(trade.symbol),
                     'merge_leg_ids': getattr(trade, 'merge_leg_ids', []),
                     'merge_overlapping': getattr(trade, 'merge_overlapping', False),
                     'account_label': aliases.get(json.dumps([trade.provider, trade.account], sort_keys=True), trade.account),
                     'tags': tagging.names(library, tag_ids), 'tag_ids': sorted(tag_ids),
                     'playbooks': tagging.matched_playbooks(library, tag_ids), 'metric': m,
                     # Every playbook this trade's tags complete, retired ones too: what a filter's playbook asks.
                     'playbook_ids': [id for id, book in library['playbooks'].items()
                                      if tagging.matches(library, tag_ids, book)],
                     'tag_score': tag_score(library, tag_ids),
                     'ratings': {id: rating(library, tag_ids, definition) for id, definition in workspace['ratings'].items()},
                     'mae': m.get('mae'), 'mfe': m.get('mfe'),
                     'precision': PRECISION.get(m.get('precision'), 'Waiting for data'),
                     # An open trade's net is only its fees so far: it has no R until it is closed.
                     'r': trade.net / at_risk if trade.status == 'closed' and trade.net is not None and at_risk else None,
                     'mae_r': m['mae'] / at_risk if m.get('mae') is not None and at_risk else None,
                     'mfe_r': m['mfe'] / at_risk if m.get('mfe') is not None and at_risk else None,
                     # The calendar day of entry in the display time zone: what date filters compare.
                     'entry_day': local_day(trade.opened, zone).isoformat()})
    return rows


def recalculate(journal) -> None:
    """MAE/MFE for every trade from the prices already stored. The watcher does this for what it
    imports; fills and prices added by hand in the app go through here."""
    for trade in journal.trades():
        for source in journal.price_sources(trade.symbol, trade):
            samples = journal.prices(trade.symbol, source, start=trade.opened, end=trade.closed)
            for metric in measurements(trade, samples, source, now=journal.trade_clock(trade)):
                journal.record_metric(trade, metric)


def show_result(result: dict) -> None:
    st.success(f"Imported {result['executions']} new executions, {result['prices']} prices, "
               f"{result['metrics']} platform metrics, {result.get('protection', 0)} protective-order events. "
               f"Skipped {result['duplicates']} existing fills.")
    for warning in result['unresolved']:
        st.warning(warning)
    if result['gaps']:
        st.warning('The file reports dropped ticks. The warning is kept with the affected trades.')


# The number limits the Filters menu offers, by their name in a filter rule.
NUMBER_LIMITS = {'net': 'Net P&L', 'r': 'R', 'mae': 'MAE', 'mfe': 'MFE', 'quantity': 'Size',
                 'duration': 'Minutes Held', 'score': 'Tag Score'}


def filter_keys(journal) -> list[str]:
    """The session keys of the Filters menu's own filters in this environment: each one's key starts 'filter-'."""
    scope = key(journal, '')
    return [name for name in st.session_state if name.startswith('filter-') and name.endswith(scope)]


def filter_generation(journal) -> str:
    """'' until a filter is saved here, then '-1', '-2' and so on: part of the key of every Filters-menu widget,
    so a save gives the menu new, empty widgets. A browser keeps showing what a widget held as long as its key
    is the same, whatever the session says."""
    saved = st.session_state.get(key(journal, 'filters-saved'), 0)
    return f'-{saved}' if saved else ''


def filter_rule(journal, rows: list[dict]) -> dict:
    """Draws the Filters menu's own filters and returns them as a rule for workspace.filter_sample: the same
    kind of rule a saved filter is, so saving one keeps exactly the trades these show."""
    if not rows:
        return {}
    fresh = filter_generation(journal)
    library = journal.tag_library()
    c1, c2 = st.columns([1, 1])
    markets = c1.multiselect('Markets', sorted({r['market'] for r in rows}),
                             key=key(journal, 'filter-markets' + fresh),
                             placeholder='All markets · expiries grouped',
                             help='NQZ6, NQM6 and other NQ expiries share NQ. Micros stay separate. '
                                  'Charts and MAE/MFE still use the exact traded contract.')
    zone = display_zone(journal)
    days = [local_day(r['opened'], zone) for r in rows]
    first, last = min(days), max(days)
    # The span is part of the key: a trade on a new day widens the filter instead of hiding behind
    # the range that was right yesterday.
    period = c2.date_input('Entry Dates', (first, last), key=key(journal, f'filter-dates-{first}-{last}' + fresh),
                           help=f'Calendar days in your display time zone, {zone}.')
    with st.expander('More Filters'):
        c1, c2, c3 = st.columns(3)
        names = {r['account']: r['account_label'] for r in rows}         # by the name given under Journals & Accounts
        accounts = c1.multiselect('Accounts', sorted(names), format_func=lambda a: names.get(a, a),
                                  key=key(journal, 'filter-accounts' + fresh))
        symbols = c2.multiselect('Exact Contracts', sorted({r['symbol'] for r in rows}), key=key(journal, 'filter-symbols' + fresh))
        available_tags = {t for r in rows for t in r['tags']}
        # A removed tag can still be an active filter; do not silently broaden the list on return.
        available_tags.update(st.session_state.get(key(journal, 'filter-tags' + fresh), []))
        tags = c3.multiselect('Tags', sorted(available_tags), key=key(journal, 'filter-tags' + fresh),
                              help='Trades with all of these tags, or any of them: choose under Tag Match.')
        match = c3.radio('Tag Match', ['All selected', 'Any selected'], horizontal=True, key=key(journal, 'filter-tag-match' + fresh))
        excluded = c3.multiselect('Exclude Tags', sorted(available_tags), key=key(journal, 'filter-exclude-tags' + fresh))
        books = [p for p in library['playbooks'].values() if not p.get('retired')]
        book = c1.selectbox('Playbook', [None, *[p['id'] for p in books]],
                            format_func=lambda id: next(p['name'] for p in books if p['id'] == id) if id else 'Any playbook',
                            key=key(journal, 'filter-playbook' + fresh), help='Trades whose tags complete this playbook.')
        status = c1.multiselect('Trade Status', ['closed', 'open', 'incomplete'], format_func=str.title,
                                key=key(journal, 'filter-status' + fresh))
        directions = c2.multiselect('Directions', ['long', 'short'], format_func=str.title,
                                    key=key(journal, 'filter-directions' + fresh))
        ratings = c2.multiselect('Process Ratings', [1, 2, 3, 4, 5], key=key(journal, 'filter-ratings' + fresh))
        numbers = st.multiselect('Number Ranges', list(NUMBER_LIMITS), format_func=NUMBER_LIMITS.get,
                                 key=key(journal, 'filter-numbers' + fresh),
                                 help='Each one adds a minimum and a maximum; an empty box is no limit. MAE and MFE are '
                                      'in account currency, not the display unit. A trade without a value never '
                                      'passes a limit; R needs a stop.')
        limits = {}
        for name in numbers:
            low, high = st.columns(2)
            for column, end, word in [(low, '_min', 'Min '), (high, '_max', 'Max ')]:
                limits[name + end] = column.number_input(word + NUMBER_LIMITS[name], value=None, step=1.0,
                                                         key=key(journal, f'filter-{name}{end}' + fresh), placeholder='No limit')

    def ids(names):
        # A renamed tag keeps its identity; a name no tag has any more matches no trade.
        return [next(iter(tagging.ids(library, [name])), name) for name in names]

    rule = {'markets': markets, 'accounts': accounts, 'symbols': symbols, 'directions': directions,
            'status': status, 'ratings': ratings, 'playbook': book, 'tags_none': ids(excluded),
            'tags_all' if match == 'All selected' else 'tags_any': ids(tags), **limits}
    if isinstance(period, (tuple, list)) and len(period) == 2:
        # Only a narrowed side limits: the full span is no limit, so a new day's trades are never hidden by it.
        if period[0] > first:
            rule['start'] = period[0].isoformat()
        if period[1] < last:
            rule['end'] = period[1].isoformat()
    return rule


def multiplier_form(journal, form_key: str, symbol: str | None = None) -> None:
    """Dollars per full price point per contract. Known futures roots come with one; anything else
    stays without P&L until it is set here."""
    trades = journal.trades()
    symbols = sorted({t.symbol for t in trades})
    suggested = symbol or (symbols[0] if symbols else 'ESZ6')
    fixed_symbol = symbol is not None
    if not fixed_symbol:
        choice = st.selectbox('Instrument', [*(symbols or ['ESZ6']), '＋ Other instrument'],
                              format_func=lambda s: '+ Other instrument' if s == '＋ Other instrument' else s,
                              key=key(journal, form_key + '-instrument'))
        if choice != '＋ Other instrument':
            suggested = choice
        else:
            suggested = ''  # Never carry another instrument's multiplier/tick defaults into a new symbol.
    in_use = next((t.point_value for t in trades if t.symbol == suggested and t.point_value), None)
    with st.form(key(journal, form_key)):
        if fixed_symbol or choice != '＋ Other instrument':
            symbol = suggested
            st.caption('Instrument: ' + symbol)
        else:
            symbol = st.text_input('Other instrument symbol', key=key(journal, form_key + '-symbol'))
        # The starting value follows the suggested instrument, not the box beside it: both boxes are
        # sent together on submit, so it must not be able to change underneath the number just typed.
        pv = st.number_input('Currency per price point', min_value=.000001,
                             value=float(in_use or point_value(suggested) or 1.), key=key(journal, form_key + '-value-' + suggested),
                             help='ES 50, MES 5, NQ 20, MNQ 2, CL 1000; 1 for shares.')
        tick = st.number_input('Tick size · optional', min_value=0.,
                               value=float(units.tick_size(suggested, journal.display_settings()['tick_sizes']) or 0.),
                               format='%.6f', key=key(journal, form_key + '-tick-' + suggested),
                               help='One minimum price increment, not its dollar value. Zero leaves it unchanged. '
                                    'ES/MES/NQ/MNQ are detected automatically. Other contracts need a confirmed size.')
        if st.form_submit_button('Set Multiplier and Recalculate'):
            if not symbol.strip():
                st.error('Name the instrument.')
                return
            journal.set_instrument(symbol, pv)
            if tick > 0:
                journal.set_tick_size(symbol, tick)
            recalculate(journal)
            st.rerun()


# ---------------------------------------------------------------- the study connection

@dataclass(frozen=True)
class Link:
    state: str             # Connected, Waiting For Study, Stale, Needs Attention, No Heartbeat or Paused
    tone: str              # good, warning, critical or neutral: a key of theme.STATUS
    detail: str
    version: str = ''      # the study's version, when its heartbeat carries one
    heard: dt.datetime | None = None
    connector: str = ''    # which writer sent the heartbeat; the study says 'motivewave-journal-study'

    @property
    def label(self) -> str:
        return self.state + (f' · study {self.version}' if self.version and self.state == 'Connected' else '')


def version_parts(text) -> tuple[int, ...] | None:
    """'0.3.2' -> (0, 3, 2); None for anything that is not dotted whole numbers."""
    parts = str(text or '').strip().split('.')
    return tuple(int(p) for p in parts) if all(p.isdigit() for p in parts) else None


def study_outdated(link) -> bool:
    """The study that reported in is ours and older than the one this app bundles. Studies before 0.3.3
    never receive ticks, before 0.3.4 label replay prints simulated, and before 0.3.5 link no prop account's
    stops, so Setup asks for the update. Another writer's version numbers are its own."""
    if getattr(link, 'connector', '') != paths.STUDY_CONNECTOR:
        return False
    version = version_parts(getattr(link, 'version', ''))
    return version is not None and version < version_parts(paths.STUDY_VERSION)


def ago(seconds: float) -> str:
    seconds = max(0, int(seconds))
    for size, unit in ((86400, 'd'), (3600, 'h'), (60, 'min')):
        if seconds >= size:
            return f'{seconds // size} {unit} ago'
    return f'{seconds} s ago'


def study_connection(journal) -> tuple[str | None, dict | None]:
    """The connection that reads the study's bridge file: the default one, else any other bridge."""
    profiles = journal.get('profiles')
    if DEFAULT_CONNECTION in profiles:
        return DEFAULT_CONNECTION, profiles[DEFAULT_CONNECTION]
    bridges = sorted(((n, p) for n, p in profiles.items() if p.get('mode') == 'bridge'),
                     key=lambda item: (not item[1].get('enabled'), item[0]))
    return bridges[0] if bridges else (None, None)


def link_state(profile: dict | None, status: dict | None, *, worker_error: str | None = None, now=None) -> Link:
    """What the stored sync status and the last heartbeat say about the study, in one word.

    The watcher only rewrites the status when new lines arrive, so freshness comes from the
    heartbeat's own timestamp: a study that has gone quiet turns Stale without anything being written.
    """
    status = status or {}
    if profile is None:
        return Link('Needs Attention', 'critical', 'No bridge file is being watched. Choose one under Bridge File below.')
    if not profile.get('enabled'):
        return Link('Paused', 'neutral', 'Read Trades From MotiveWave is off, so nothing is read. The study keeps '
                    'writing, and reading carries on from the same place once it is on again.')
    if worker_error:
        return Link('Needs Attention', 'critical', f'The background reader failed ({worker_error}) and is retrying.')
    heartbeat = status.get('heartbeat') or {}
    try:
        heard = instant(heartbeat['ts']) if heartbeat.get('ts') else None
    except (ValueError, TypeError, AttributeError):
        heard = None
    now = now or dt.datetime.now(dt.timezone.utc)
    age = (now - heard).total_seconds() if heard else None
    version = str(heartbeat.get('connector_version') or '')
    connector = str(heartbeat.get('connector') or '')
    if status.get('error'):
        return Link('Needs Attention', 'critical', str(status['error']), version, heard, connector)
    try:
        exists = Path(profile.get('path') or '').expanduser().is_file()
    except OSError:
        exists = False
    if status.get('status') == 'waiting for study' or not exists:
        return Link('Waiting For Study', 'warning', 'The bridge file has not appeared yet. It is created when the '
                    'study is added to a chart in MotiveWave.')
    if not status:
        return Link('Waiting For Study', 'warning', 'The bridge file is there but holds no events yet.')
    if heard is None:
        return Link('No Heartbeat', 'warning', 'Events are being read, but the study has not reported in. Prices '
                    'still arrive; fills need Capture Fills switched on (the study\'s Advanced tab).')
    if age > STALE_AFTER:
        return Link('Stale', 'warning', f'The study was last heard from {ago(age)}. Check that MotiveWave is '
                    'running and the study is still on a chart.', version, heard, connector)
    return Link('Connected', 'good', f'Heard from the study {ago(age)}.', version, heard, connector)


def study_link(journal, *, now=None) -> tuple[str | None, dict | None, dict, Link]:
    """(connection name, its profile, its stored status, the one-word state) for this environment."""
    name, profile = study_connection(journal)
    statuses = journal.get('sync_status')
    status = statuses.get(name, {}) if name else {}
    worker = statuses.get('worker', {}).get('error')
    return name, profile, status, link_state(profile, status, worker_error=worker, now=now)


def other_problems(journal, study: str | None) -> int:
    """Enabled connections other than the study's whose last pass failed. A switched-off connection
    keeps its last error, so it is not counted."""
    profiles, statuses = journal.get('profiles'), journal.get('sync_status')
    return sum(1 for name, profile in profiles.items()
               if name != study and profile.get('enabled') and statuses.get(name, {}).get('error'))
