"""What more than one view needs: trade rows, the recalculation pass, the study-connection status.

Views take (journal, rows) and draw; anything two of them share lives here so they can change
independently. Widget keys are scoped to the trading environment with key(), so Live and Replay
never show each other's selections. The settings both share (tags, filters, Time Zone, Units) are
read from the journal on every run, so a change made in one environment shows in the other.
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
NAV_LABELS = {'Trades': 'Trades', 'Analytics': 'Stats', 'Settings': 'Settings'}
# Every page that is not one of the three, by the name navigation() takes: (its Settings page, the tab it opens).
# The Daily Report Card, the session tools and running a checklist are on Trades; dashboards are a Stats tab.
SETTINGS_ROUTES = {'Settings': ('Settings', None),
                   'Tags': ('Tags', None), 'Checklists': ('Tags', 'Checklists'),
                   'Data': ('Data', None), 'Import': ('Data', 'Import Trades'), 'Export': ('Data', 'Export & Backup'),
                   'Journals': ('Data', 'Journals & Accounts'), 'Connection': ('Data', 'Connection')}
TOOLS = tuple(view for view in SETTINGS_ROUTES if view != 'Settings')
VIEW_KEY = 'journal-view'
TOOL_KEY = 'journal-settings-page'
# Requests for the page drawn next, taken by that page as it is drawn: which tab to open (tabs()), and
# whether Trades opens the Daily Report Card instead of a trade.
TAB_KEY = 'journal-open-tab'
REPORT_KEY = 'journal-open-report-card'
# The first four keep their places: older selectors default to the first one.
TIMEZONES = ['America/New_York', 'America/Chicago', 'America/Los_Angeles', 'UTC',
             'America/Denver', 'America/Phoenix', 'America/Anchorage', 'Pacific/Honolulu', 'America/Toronto',
             'America/Vancouver', 'America/Mexico_City', 'America/Sao_Paulo', 'Europe/London', 'Europe/Dublin',
             'Europe/Lisbon', 'Europe/Paris', 'Europe/Berlin', 'Europe/Madrid', 'Europe/Rome', 'Europe/Amsterdam',
             'Europe/Zurich', 'Europe/Stockholm', 'Europe/Warsaw', 'Europe/Athens', 'Europe/Istanbul',
             'Europe/Moscow', 'Africa/Johannesburg', 'Asia/Dubai', 'Asia/Kolkata', 'Asia/Singapore',
             'Asia/Hong_Kong', 'Asia/Shanghai', 'Asia/Tokyo', 'Asia/Seoul', 'Australia/Perth',
             'Australia/Brisbane', 'Australia/Sydney', 'Pacific/Auckland']
# The study's three states, as the sidebar, Settings and Data → Connection all say them (link_state).
RECORDING, NOT_RECORDING, ATTENTION = 'Recording', 'Not recording', 'Needs attention'
NO_REPLAY_TRADES = 'No replay trades yet'          # the sidebar chip and the trade list of an empty Replay journal
STALE_AFTER = 60           # seconds after its last heartbeat that the study still counts as recording; it sends one every 10
# One line under a replay trade that has no real ticks yet: the study loads them once the trade is flat.
REPLAY_TICKS_LATER = ('The study fills in this replay trade\'s real ticks after it closes, while the study is on '
                      'the replay chart.')
# The chart library's licence asks for this notice and link on a page its users can see.
CREDIT = '[TradingView Lightweight Charts™, Copyright (c) 2025 TradingView, Inc.](https://www.tradingview.com/)'


def key(journal, name: str) -> str:
    return f'{name}-{journal.dataset}'


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
    """The session-state changes that show a view, by any name it has had: a Settings page and its tab, the
    Daily Report Card on Trades, the Dashboards tab of Stats, or one of the three pages."""
    if view in SETTINGS_ROUTES:
        page, tab = SETTINGS_ROUTES[view]
        return {VIEW_KEY: 'Settings', TOOL_KEY: page, TAB_KEY: tab}
    if view == 'Dashboard':
        return {VIEW_KEY: 'Analytics', TAB_KEY: 'Dashboards'}
    if view == 'Daily review':
        return {VIEW_KEY: 'Trades', REPORT_KEY: True}
    if view == 'Live session':              # the session tools are the Session menu at the top of Trades
        return {VIEW_KEY: 'Trades'}
    return {VIEW_KEY: 'Analytics' if view in ('Edge', 'Analytics') else view}


def tabs(labels, key: str):
    """Tabs whose `.open` says which one is showing, that open the one navigation() asked for, and that come
    back on the tab last opened. The choice is kept under its own key as well as the widget's, because
    Streamlit forgets a widget's value on a page that does not draw it. A tab that is no longer there (Stats has
    only Dashboards while the filters keep no closed trade) gives way to the first."""
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
    """The one time zone of the journal (Journal.time_zone): every time is shown in it and every trading day starts
    at its midnight. Without a journal, or one that cannot be read, this computer's own. Stored times stay UTC."""
    try:
        return journal.time_zone() if journal is not None else humanize.local_zone()
    except (sqlite3.Error, ValueError, KeyError):
        return humanize.local_zone()


def set_display_zone(journal, zone: str) -> None:
    """Saves the Time Zone, one for Live and Replay: it is kept with the settings they share."""
    journal.save_preferences(display_timezone=zone)


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
    """MAE / MFE Units, saved the moment it is chosen, one choice for Live and Replay. The box opens on the saved choice
    on every run, so a choice made in the other environment shows here."""
    widget_key = key(journal, 'excursion-unit')
    st.session_state[widget_key] = excursion_unit(journal)
    def save():
        journal.set_excursion_unit(st.session_state[widget_key])
    container.selectbox('MAE / MFE Units', units.UNITS, format_func=units.LABELS.get, key=widget_key, on_change=save,
                        help='Changes how MAE / MFE are shown in Live and Replay, and the unit their filter limits '
                             'are in.')


def excursion_unit(journal):
    """The MAE / MFE Units saved on Settings."""
    return journal.display_settings()['excursion_unit']


def excursion_amount(value, unit, currency='USD'):
    """MAE / MFE in the chosen unit: '$375.00', '3.00 pts', '12.00 ticks', '1.25R'."""
    if value is None:
        return '—'
    if unit == 'currency':
        return amount(value, currency)
    return amount(value) + ('' if unit == 'r' else ' ') + units.suffix(unit, currency)


def best_display_metric(journal, trade):
    """The one measurement a trade is shown with, in every unit: of those from recorded ticks (metric_options),
    the traded price's ('last'), then the one that saw the most prices. {} when there is none."""
    return max(metric_options(journal, trade), key=lambda m: (m.get('basis', 'last') == 'last',
                                                              m.get('observations', 0)), default={})


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
    return trade


def trade_name(row, zone: str) -> str:
    """A trade as a trader recognises it, in place of its id: 'Oct 20, 5:05 PM · NQZ6 Short 2'."""
    return (f"{moment(row['opened'], zone, seconds=False)} · {row['symbol']} {row['direction'].title()} "
            f"{row['max_qty']:g}")


def account_name(journal, provider, account) -> str:
    """An account as the trader knows it: the name given to it under Journals & Accounts, else the platform's
    own ('SIM'). Never the provider code it is stored under."""
    from mwjournal import journals
    return journals.alias(journal, (provider, account))


def metric_options(journal, trade):
    """The measurements a trade can be shown with: from recorded ticks only, and none whose ticks the study
    reported a gap in. Candles, proxies and simulated replay prints never measure a trade."""
    return [m for m in journal.metric_options(trade) if m.get('precision') == 'observed-ticks'
            and not journal.capture_gaps(m['source'], start=trade.opened, end=trade.closed, symbol=trade.symbol)]


def excursion_values(journal, trade, metric, risk=None):
    """MAE and MFE of a trade's measurement (best_display_metric) in the chosen unit."""
    unit = excursion_unit(journal)
    tick = units.tick_size(trade.symbol, journal.display_settings()['tick_sizes'])
    return tuple(units.value(metric, name, unit, risk=risk, tick=tick) for name in ('mae', 'mfe'))


def tick_size_menu(journal, trade):
    """The one place a contract's tick size is set, and changed when it was typed wrong: under the trade's MAE and
    MFE in ticks, for a contract whose tick size is not known (units.TICK_SIZES)."""
    given = units.tick_size(trade.symbol, journal.display_settings()['tick_sizes']) or 0.
    with st.popover('Set Tick Size for ' + trade.symbol):
        with st.form(key(journal, 'tick-size-' + trade.id)):
            size = st.number_input('Minimum price increment', min_value=0., value=float(given), step=.000001,
                                   format='%.8f',
                                   help='The smallest price step of this exact contract, such as 0.25 for ES.')
            if st.form_submit_button('Save Tick Size'):
                if size <= 0:
                    st.error('Enter a confirmed tick size greater than zero.')
                    return
                journal.set_tick_size(trade.symbol, size)
                st.rerun()


# What the trade rows show besides fills, measurements, linked orders and multipliers (changes()): the journal_meta
# entries of the settings Live and Replay share, read from the journal keeping them (Journal.shared): the tag library,
# the Time Zone in the workspace, and Units with the tick sizes. Merged trades are each journal's own.
ROW_SETTINGS = ('workspace', 'tag_library', 'tag_catalog', 'display_settings')


def rows_signature(journal) -> tuple | None:
    """(what changes whenever a trade row could, the newest measurement of each trade), or None when the journal
    cannot be read. The first: changes() (a fill, a trade's first measurement, a linked order, a multiplier), a
    review, a day's tags, a merge, the shared settings (a tag edited in Live rebuilds Replay's rows too) or an
    account's name. The second, by trade id: a measurement stored again (prices that came in late, a gap the study
    reported) leaves changes() as it was, but it replaces its row, and the new row has a higher rowid."""
    found = hashlib.sha256()
    try:
        with journal.connect() as db:
            for (text,) in db.execute(
                    "SELECT 'a ' || trade_id || ' ' || payload FROM annotations UNION ALL "
                    "SELECT 's ' || key || ' ' || payload FROM sessions UNION ALL "
                    "SELECT 'm ' || key || ' ' || value FROM journal_meta WHERE key='trade_merges'"):
                found.update(text.encode())
            measured = dict(db.execute('SELECT trade_id, max(rowid) FROM metrics GROUP BY trade_id'))
            # The accounts' names, not the views beside them: a view filters the rows and changes none.
            names = db.execute("SELECT json_extract(value, '$.account_aliases') FROM journal_meta "
                               "WHERE key='journal_registry'").fetchone()
        with journal.shared.connect() as db:
            for (text,) in db.execute("SELECT key || ' ' || value FROM journal_meta WHERE key IN (%s)"
                                      % ','.join('?' * len(ROW_SETTINGS)), ROW_SETTINGS):
                found.update(text.encode())
    except sqlite3.Error:
        return None                            # never equal to a signature read before: the rows are built again
    # A journal shown with its merged trades (merging.ReviewJournal) has other rows than the same journal without.
    return (changes(journal), found.hexdigest(), names and names[0], humanize.local_zone(),
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
    """One row per trade with its review, tags, risk and measurement, and its MAE and MFE in the chosen MAE / MFE
    Units (display_mae, display_mfe: what Stats shows and MAE / MFE filters compare). The journal's settings are
    read once."""
    annotations = journal.annotations()
    library = journal.tag_library()
    sessions = journal.get('sessions')
    from mwjournal.workspace import tag_score
    from mwjournal import journals
    aliases = journals.registry(journal)['account_aliases']
    zone = display_zone(journal)
    unit, sizes = excursion_unit(journal), journal.display_settings()['tick_sizes']
    rows = []
    trades = journal.trades()
    plans = risk.plans(journal, trades, annotations)
    for trade in trades:
        a = annotations.get(trade.id, {})
        m = best_display_metric(journal, trade)
        day = tagging.day_key(trade.opened, zone)     # its trading day: its day tags and what date filters compare
        tag_ids = tagging.effective(library, a, sessions.get(day, {}))
        # The risk to the trade's stop (risk.py): every R the app shows divides by it.
        at_risk = plans[trade.id].risk
        shown = {'display_' + name: units.value(m, name, unit, risk=at_risk, tick=units.tick_size(trade.symbol, sizes))
                 for name in ('mae', 'mfe')}
        rows.append({**trade.row(), **a, **shown, 'initial_risk': at_risk, 'stop': plans[trade.id].stop,
                     'market': instrument_market(trade.symbol),
                     'merge_leg_ids': getattr(trade, 'merge_leg_ids', []),
                     'account_label': aliases.get(journals.account_id(trade.provider, trade.account), trade.account),
                     'tags': tagging.names(library, tag_ids), 'tag_ids': sorted(tag_ids),
                     'playbooks': tagging.matched_playbooks(library, tag_ids), 'metric': m,
                     # Every playbook this trade's tags complete, retired ones too: what a filter's playbook asks.
                     'playbook_ids': [id for id, book in library['playbooks'].items()
                                      if tagging.matches(library, tag_ids, book)],
                     'tag_score': tag_score(library, tag_ids),
                     'mae': m.get('mae'), 'mfe': m.get('mfe'),
                     # An open trade's net is only its fees so far: it has no R until it is closed.
                     'r': trade.net / at_risk if trade.status == 'closed' and trade.net is not None and at_risk else None,
                     'mae_r': m['mae'] / at_risk if m.get('mae') is not None and at_risk else None,
                     'mfe_r': m['mfe'] / at_risk if m.get('mfe') is not None and at_risk else None,
                     'entry_day': day})
    return rows


def recalculate(journal) -> None:
    """MAE/MFE for every trade from the prices already stored. The watcher does this for what it
    imports; fills imported or added by hand in the app, and a multiplier set on a trade, go through here."""
    for trade in journal.trades():
        for source in journal.price_sources(trade.symbol, trade):
            samples = journal.prices(trade.symbol, source, start=trade.opened, end=trade.closed)
            for metric in measurements(trade, samples, source, now=journal.trade_clock(trade)):
                journal.record_metric(trade, metric)


def show_result(result: dict) -> None:
    """What an import added, in fills: the new ones and those the journal already held. A file the study wrote also
    brings prices and stops, which go with its trades uncounted, as when the study records them."""
    def count(n, word):
        return f'{n:,} {word}' + ('' if n == 1 else 's')
    st.success(f"Imported {count(result['executions'], 'new fill')}." + (
        f" Skipped {count(result['duplicates'], 'existing fill')}." if result['duplicates'] else ''))
    if result['gaps']:
        st.warning('The file reports dropped ticks: the trades they fall in show no MAE / MFE, and say why.')


# ---------------------------------------------------------------- filters
# One editor for every filter (filter_rule): the Filters menu of Trades and Stats, a dashboard page's Page Filters and
# each dashboard widget's. Each draws it under a key prefix of its own and gets back the kind of rule a saved filter is.

# The number limits a filter offers, by their name in a rule. MAE and MFE are in the chosen MAE / MFE Units.
NUMBER_LIMITS = {'net': 'Net P&L', 'r': 'R', 'mae': 'MAE', 'mfe': 'MFE', 'quantity': 'Contracts',
                 'duration': 'Minutes held', 'score': 'Tag score'}
TAG_MATCH = ('All selected', 'Any selected')


def filter_keys(journal) -> list[str]:
    """The session keys of the Filters menu's own filters in this environment: each one's key starts 'filter-'."""
    scope = key(journal, '')
    return [name for name in st.session_state if name.startswith('filter-') and name.endswith(scope)]


def filter_generation(journal) -> str:
    """'' until the Filters menu is saved or cleared here, then '-1', '-2' and so on: part of the key of every filter
    widget, so a save or a clear gives the menu new, empty widgets. A browser keeps showing what a widget held as
    long as its key is the same, whatever the session says."""
    done = st.session_state.get(key(journal, 'filters-generation'), 0)
    return f'-{done}' if done else ''


def empty_filters(journal) -> None:
    """Empties the Filters menu of Trades and Stats: its values go, and the next run draws new widgets."""
    for name in filter_keys(journal):
        st.session_state.pop(name, None)
    done = key(journal, 'filters-generation')
    st.session_state[done] = st.session_state.get(done, 0) + 1


def _editor(journal, rows, prefix, start) -> dict:
    """What a filter editor is drawn from: each field's widget key ('keys'), the value each widget opens on
    ('opening'), the first and last entry day of `rows` and the tag library. A field is named as its key ends; a
    number limit as in a rule, such as net_max."""
    library = journal.tag_library()
    first, last = (dt.date.fromisoformat(day) for day in (min(r['entry_day'] for r in rows),
                                                           max(r['entry_day'] for r in rows)))
    fresh = filter_generation(journal)
    limits = [name + end for name in NUMBER_LIMITS for end in ('_min', '_max')]
    keys = {field: key(journal, f'{prefix}-{field}{fresh}') for field in (
        'markets', 'accounts', 'symbols', 'tags', 'tag-match', 'exclude-tags', 'playbook', 'status', 'directions',
        'ratings', 'numbers', *limits)}
    # The span is part of the key: a trade on a new day widens the filter instead of hiding behind the range that
    # was right yesterday.
    keys['dates'] = key(journal, f'{prefix}-dates-{first}-{last}{fresh}')
    name = lambda id: library['tags'][id]['name'] if id in library['tags'] else id
    match = TAG_MATCH[1] if start.get('tags_any') and not start.get('tags_all') else TAG_MATCH[0]
    opening = {**{field: list(start.get(field, [])) for field in ('markets', 'accounts', 'symbols', 'status',
                                                                  'directions', 'ratings')},
               'tags': [name(id) for id in start.get('tags_all' if match == TAG_MATCH[0] else 'tags_any', [])],
               'tag-match': match, 'exclude-tags': [name(id) for id in start.get('tags_none', [])],
               'playbook': start.get('playbook'),
               'dates': tuple(dt.date.fromisoformat(start[side]) if start.get(side) else day
                              for side, day in (('start', first), ('end', last))),
               'numbers': [n for n in NUMBER_LIMITS
                           if start.get(n + '_min') is not None or start.get(n + '_max') is not None],
               **{limit: None if start.get(limit) is None else float(start[limit]) for limit in limits}}
    return {'keys': keys, 'opening': opening, 'first': first, 'last': last, 'library': library}


def _rule(editor, start) -> dict:
    """The rule an editor (_editor) holds: each widget's value, else the value it opens on."""
    value = {field: st.session_state.get(name, editor['opening'][field]) for field, name in editor['keys'].items()}

    def ids(names):
        # A renamed tag keeps its identity; a name no tag has any more matches no trade.
        return [next(iter(tagging.ids(editor['library'], [name])), name) for name in names]

    rule = {field: value[field] for field in ('markets', 'accounts', 'symbols', 'directions', 'status', 'ratings',
                                              'playbook')}
    rule['tags_none'] = ids(value['exclude-tags'])
    rule['tags_all' if value['tag-match'] == TAG_MATCH[0] else 'tags_any'] = ids(value['tags'])
    if start.get('tags_all') and start.get('tags_any'):
        rule.setdefault('tags_any', list(start['tags_any']))     # kept as it was saved (filter_rule says so)
    period = value['dates']
    if isinstance(period, (tuple, list)) and len(period) == 2:
        # Only a narrowed side limits: the full span is no limit, so a new day's trades are never hidden by it. A
        # side the rule was saved with stays until it is changed.
        for side, day, narrowed in (('start', period[0], period[0] > editor['first']),
                                    ('end', period[1], period[1] < editor['last'])):
            if narrowed or start.get(side) == day.isoformat():
                rule[side] = day.isoformat()
    for name in value['numbers']:
        for end in ('_min', '_max'):
            rule[name + end] = value[name + end]
    return rule


def filter_state(journal, rows, prefix='filter', start=None) -> dict:
    """The rule the filter editor under `prefix` holds (filter_rule), read without drawing it, so the button that
    opens it can say how many filters are on."""
    start = start or {}
    return _rule(_editor(journal, rows, prefix, start), start) if rows else dict(start)


def active_filters(rule: dict) -> int:
    """How many filters a rule holds: each choice made, each side of the entry dates and each number limit."""
    return sum(value not in (None, [], ()) for value in rule.values())


def filter_rule(journal, rows: list[dict], prefix='filter', start=None) -> dict:
    """Draws the one filter editor and returns its rule for workspace.filter_sample, the kind of rule a saved filter
    is: the Filters menu of Trades and Stats (prefix 'filter'), a dashboard page's ('page-filter-<page id>') or a
    dashboard widget's ('widget-<page id>-<widget id>', 'new' for a new one). It opens on `start`, a page's or
    widget's stored rule, and shows its tags by their names now. Without trades there is nothing to choose from, and
    `start` comes back as it is."""
    start = start or {}
    if not rows:
        return dict(start)
    editor = _editor(journal, rows, prefix, start)
    keys, opening, library = editor['keys'], editor['opening'], editor['library']

    def held(*fields):
        # What a widget opens on or holds stays offered, so no filter goes because no trade has that value now.
        return {value for field in fields for value in [*opening[field], *st.session_state.get(keys[field], [])]}

    c1, c2 = st.columns([1, 1])
    c1.multiselect('Markets', sorted({r['market'] for r in rows} | held('markets')), default=opening['markets'],
                   key=keys['markets'], placeholder='All markets · expiries grouped',
                   help='NQZ6, NQM6 and other NQ expiries share NQ; micros stay separate, and charts and MAE / MFE '
                        'use the exact contract.')
    c2.date_input('Entry Dates', opening['dates'], key=keys['dates'],
                  help=f'Calendar days in your Time Zone, {display_zone(journal)}.')
    with st.expander('More Filters'):
        c1, c2, c3 = st.columns(3)
        names = {r['account']: r['account_label'] for r in rows}         # by the name given under Journals & Accounts
        c1.multiselect('Accounts', sorted(set(names) | held('accounts')), default=opening['accounts'],
                       format_func=lambda a: names.get(a, a), key=keys['accounts'])
        c2.multiselect('Exact Contracts', sorted({r['symbol'] for r in rows} | held('symbols')),
                       default=opening['symbols'], key=keys['symbols'])
        tags = sorted({t for r in rows for t in r['tags']} | held('tags', 'exclude-tags'))
        c3.multiselect('Tags', tags, default=opening['tags'], key=keys['tags'],
                       help='Trades with all of these tags, or any of them: choose under Tag Match.')
        c3.radio('Tag Match', TAG_MATCH, index=TAG_MATCH.index(opening['tag-match']), horizontal=True,
                 key=keys['tag-match'])
        if start.get('tags_all') and start.get('tags_any'):
            any_of = ', '.join(library['tags'][id]['name'] if id in library['tags'] else id for id in start['tags_any'])
            c3.caption(md(f'Also only trades with any of {any_of}; Any selected replaces these.'))
        c3.multiselect('Exclude Tags', tags, default=opening['exclude-tags'], key=keys['exclude-tags'])
        books = library['playbooks']
        chosen = [opening['playbook'], st.session_state.get(keys['playbook'])]
        playbooks = [None, *[id for id, book in books.items() if not book.get('retired') or id in chosen],
                     *[id for id in dict.fromkeys(chosen) if id and id not in books]]
        c1.selectbox('Playbook', playbooks, index=playbooks.index(opening['playbook']),
                     format_func=lambda id: books[id]['name'] if id in books else id or 'Any playbook',
                     key=keys['playbook'], help='Trades whose tags complete this playbook.')
        c1.multiselect('Trade Status', ['closed', 'open', 'incomplete'], default=opening['status'],
                       format_func=str.title, key=keys['status'])
        c2.multiselect('Directions', ['long', 'short'], default=opening['directions'], format_func=str.title,
                       key=keys['directions'])
        c2.multiselect('Rating', [1, 2, 3, 4, 5], default=opening['ratings'], key=keys['ratings'])
        offered = [name for name in NUMBER_LIMITS
                   if name != 'score' or tagging.scored(library) or name in held('numbers')]
        numbers = st.multiselect('Number Ranges', offered, default=opening['numbers'], format_func=NUMBER_LIMITS.get,
                                 key=keys['numbers'],
                                 help='Each adds a Min and a Max; an empty box is no limit, and a trade without a '
                                      'value never passes one.')
        if {'mae', 'mfe'} & set(numbers):
            st.caption(f'MAE and MFE are in {units.LABELS[excursion_unit(journal)]}, your MAE / MFE Units on Settings.')
        for name in numbers:
            low, high = st.columns(2)
            for column, end, word in [(low, '_min', 'Min '), (high, '_max', 'Max ')]:
                column.number_input(word + NUMBER_LIMITS[name], value=opening[name + end], step=1.0,
                                    key=keys[name + end], placeholder='No limit')
    return _rule(editor, start)


# ---------------------------------------------------------------- a contract's multiplier

def multiplier_form(journal, form_key: str, symbol: str) -> None:
    """Dollars per full price point per contract: asked at the top of a trade whose contract has none (known futures
    roots come with one; anything else stays without P&L until it is set), and changed under the trade's Details
    when it is wrong (point_value_line). Saving measures every trade again, so that contract's trades follow it. A
    tick size is set in one place, under the trade's MAE and MFE when they are shown in ticks (tick_size_menu)."""
    in_use = next((t.point_value for t in journal.trades() if t.symbol == symbol and t.point_value), None)
    with st.form(key(journal, form_key)):
        st.caption('Contract: ' + symbol)
        pv = st.number_input('Dollars per point', min_value=.000001,
                             value=float(in_use or point_value(symbol) or 1.),
                             key=key(journal, form_key + '-value-' + symbol),
                             help='What one point is worth on one contract (ES 50, NQ 20, MNQ 2); saving '
                                  "recalculates this contract's trades.")
        if st.form_submit_button('Save and Recalculate'):
            journal.set_instrument(symbol, pv)
            recalculate(journal)
            st.rerun()


def point_value_line(journal, trade) -> None:
    """Under a trade's Details, what one point of its contract is worth, as a line ('NQZ6: $20 per point'), with the
    form that changes it behind Change."""
    worth = money(trade.point_value, trade.currency, signed=False,
                  decimals=0 if float(trade.point_value).is_integer() else 2)
    with st.container(horizontal=True, vertical_alignment='center', gap='small'):
        st.caption(md(f'{trade.symbol}: {worth} per point'), width='content')     # Change sits beside it
        with st.popover('Change'):
            multiplier_form(journal, 'trade-multiplier', trade.symbol)


# ---------------------------------------------------------------- the study connection

@dataclass(frozen=True)
class Link:
    state: str             # RECORDING, NOT_RECORDING or ATTENTION
    tone: str              # good, neutral or critical: a key of theme.STATUS
    detail: str
    version: str = ''      # the study's version, when its heartbeat carries one
    heard: dt.datetime | None = None
    connector: str = ''    # which writer sent the heartbeat; the study says 'motivewave-journal-study'

    @property
    def label(self) -> str:
        return self.state + (f' · study {self.version}' if self.version and self.state == RECORDING else '')


def recent(heard, now=None) -> bool:
    """Whether the study reported in within the last STALE_AFTER seconds, so it is on a chart in MotiveWave."""
    return heard is not None and ((now or dt.datetime.now(dt.timezone.utc)) - heard).total_seconds() <= STALE_AFTER


def version_parts(text) -> tuple[int, ...] | None:
    """'0.3.2' -> (0, 3, 2); None for anything that is not dotted whole numbers."""
    parts = str(text or '').strip().split('.')
    return tuple(int(p) for p in parts) if all(p.isdigit() for p in parts) else None


def study_outdated(link) -> bool:
    """The study that reported in is ours and older than the one this app bundles. Studies before 0.3.3
    never receive ticks, before 0.3.4 label replay prints simulated, and before 0.3.6 record no stop order,
    so Settings asks for the update. Another writer's version numbers are its own."""
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
    """The connection that reads the study's bridge file (sync.ensure_default_connection), or (None, None)."""
    profile = journal.get('profiles').get(DEFAULT_CONNECTION)
    return (DEFAULT_CONNECTION, profile) if profile is not None else (None, None)


def link_state(profile: dict | None, status: dict | None, *, worker_error: str | None = None, now=None) -> Link:
    """What the stored sync status and the last heartbeat say about the study, in one of three words.

    Recording: the study reported in within the last minute. Not recording: no bridge file or no events yet, or
    a last report older than that (MotiveWave is closed, or the study is off its charts). Needs attention: no
    connection reads the study's file, the reader or the study reported an error, or events arrive with no
    report from the study at all (an older study or another program). The watcher only rewrites the status when
    new lines arrive, so freshness comes from the heartbeat's own timestamp.
    """
    status = status or {}
    heartbeat = status.get('heartbeat') or {}
    try:
        heard = instant(heartbeat['ts']) if heartbeat.get('ts') else None
    except (ValueError, TypeError, AttributeError):
        heard = None
    now = now or dt.datetime.now(dt.timezone.utc)
    known = {'version': str(heartbeat.get('connector_version') or ''), 'heard': heard,
             'connector': str(heartbeat.get('connector') or '')}
    if profile is None:
        return Link(ATTENTION, 'critical',
                    'The app is not reading the study\'s file yet. Quit the app and open it again.')
    if worker_error:
        return Link(ATTENTION, 'critical', f'The background reader failed ({worker_error}) and is retrying.', **known)
    if status.get('error'):
        return Link(ATTENTION, 'critical', str(status['error']), **known)
    try:
        exists = Path(profile.get('path') or '').expanduser().is_file()
    except OSError:
        exists = False
    if status.get('status') == 'waiting for study' or not exists or not status:
        return Link(NOT_RECORDING, 'neutral', 'The study has not written anything yet. Add it to a chart in '
                    'MotiveWave.', **known)
    if heard is None:
        return Link(ATTENTION, 'critical', 'Events are being read, but the study has not reported in: an older study '
                    'or another program writes this file. Install the study again under Study below.', **known)
    age = (now - heard).total_seconds()
    if not recent(heard, now):
        return Link(NOT_RECORDING, 'neutral', f'The study was last heard from {ago(age)}. Open MotiveWave with the '
                    'study on your chart.', **known)
    return Link(RECORDING, 'good', f'Heard from the study {ago(age)}.', **known)


def study_link(journal, *, now=None) -> tuple[str | None, dict | None, dict, Link]:
    """(connection name, its profile, its stored status, the study's state) for this environment."""
    name, profile = study_connection(journal)
    statuses = journal.get('sync_status')
    status = statuses.get(name, {}) if name else {}
    worker = statuses.get('worker', {}).get('error')
    return name, profile, status, link_state(profile, status, worker_error=worker, now=now)
