"""What more than one view needs: trade rows, the recalculation pass, the study-connection status.

Views take (journal, rows) and draw; anything two of them share lives here so they can change
independently. Widget keys are scoped to the trading environment with key(), so Live and Replay
never show each other's selections.
"""

from __future__ import annotations

import datetime as dt
import sqlite3
from dataclasses import dataclass, fields
from pathlib import Path

import streamlit as st

from mwjournal import Journal, paths
from mwjournal.metrics import measurements
from mwjournal.model import Trade, instant, instrument_market, point_value
from mwjournal.scope import replay_run
from mwjournal import humanize, units, tagging
from mwjournal.sync import DEFAULT_CONNECTION

VIEWS = ('Trades', 'Edge', 'Import', 'Connection', 'Daily review', 'Export')
PRIMARY_VIEWS = ('Trades', 'Analytics', 'Settings')
NAV_LABELS = {'Trades': 'Trades', 'Analytics': 'Stats', 'Settings': 'Setup',
              'Daily review': 'Daily Report Card', 'Live session': 'Session tools'}
TOOLS = ('Connection', 'Tags', 'Ratings', 'Journals', 'General', 'Import', 'Export',
         'Dashboard', 'Daily review', 'Live session', 'Checklists')
VIEW_KEY = 'journal-view'
TOOL_KEY = 'journal-settings-page'
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
    if view in TOOLS:
        return {VIEW_KEY: 'Settings', TOOL_KEY: view}
    if view == 'Setup':                     # the first Setup page: status, the three steps, display settings
        return {VIEW_KEY: 'Settings', TOOL_KEY: 'Setup'}
    return {VIEW_KEY: 'Analytics' if view == 'Edge' else view}


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
    the platform described again, a linked order, a multiplier or a trade's first measurement."""
    try:
        with journal.connect() as db:
            return tuple(db.execute('SELECT (SELECT count(*) FROM executions), '
                                    '(SELECT total(length(payload)) FROM executions), (SELECT count(*) FROM metrics), '
                                    '(SELECT count(*) FROM protection), (SELECT count(*) FROM instruments)').fetchone())
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
    """The one time zone every time is shown in: the choice saved in Setup → Display,
    else this computer's own zone. Stored times stay UTC."""
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


def unit_selector(journal):
    widget_key = key(journal, 'excursion-unit')
    st.session_state.setdefault(widget_key, journal.display_settings()['excursion_unit'])
    def save():
        journal.set_excursion_unit(st.session_state[widget_key])
    st.selectbox('MAE / MFE units', units.UNITS, format_func=units.LABELS.get, key=widget_key, on_change=save,
                 help='Display preference only: fills and the raw currency measurements stay unchanged. '
                      'Points/ticks measure price movement, not quantity-weighted position profit.')
    policy_key = key(journal, 'excursion-policy')
    st.session_state.setdefault(policy_key, journal.display_settings()['excursion_policy'])
    def save_policy():
        journal.set_excursion_policy(st.session_state[policy_key])
    st.selectbox('MAE / MFE accuracy', ['ticks-only', 'best-available'],
                 format_func=lambda p: 'Recorded ticks only' if p == 'ticks-only' else 'Allow labelled estimates',
                 key=policy_key, on_change=save_policy,
                 help='Tick-only excludes OHLC, proxies, platform-only reports, simulated replay and recorded capture gaps. '
                      'Missing tick history means unavailable, not zero. The candle chart is independent.')


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


def display_metric(journal, trade, metric):
    """Old stored reports need matching prices for points; never divide size-weighted currency blindly."""
    if getattr(trade, 'merge_overlapping', False) or excursion_unit(journal) not in ('points', 'ticks') or not metric or 'mae_points' in metric:
        return metric
    from mwjournal.metrics import excursions
    samples = journal.prices(trade.symbol, metric['source'], start=trade.opened, end=trade.closed)
    calculated = excursions(trade, samples, metric['source'], basis=metric.get('basis', 'last'),
                            now=journal.trade_clock(trade))
    return {**metric, **{k: calculated.get(k) for k in ('mae_points', 'mfe_points', 'point_definition', 'point_precision')},
            'path': calculated.get('path', []), 'point_notes': calculated.get('notes', [])}


def best_display_metric(journal, trade):
    options = metric_options(journal, trade)
    rank = {'platform-reported': 4, 'observed-ticks': 3, 'estimated-bars': 2,
            'replay-simulated': 1, 'proxy-estimate': 1, 'pending': 0}
    best = max(options, key=lambda m: (rank.get(m.get('precision'), 0), m.get('basis', 'last') == 'last',
                                       m.get('observations', 0))) if options else {}
    if excursion_unit(journal) not in ('points', 'ticks'):
        return best
    options = [display_metric(journal, trade, m) for m in options]
    available = [m for m in options if m.get('mae_points') is not None and m.get('mfe_points') is not None]
    rank = {'platform-reported': 4, 'observed-ticks': 3, 'estimated-bars': 2,
            'replay-simulated': 1, 'proxy-estimate': 1, 'pending': 0}
    return max(available, key=lambda m: (rank.get(m.get('point_precision'), 0),
                                        m.get('basis', 'last') == 'last', m.get('observations', 0))) if available else display_metric(journal, trade, best)


def replay_ticks_pending(journal, trade) -> bool:
    """A replay trade without a measurement from real ticks yet, under any accuracy policy. The study
    backfills one from MotiveWave's history once the trade is flat and the study is on the replay chart."""
    if getattr(journal, 'dataset', None) != 'replay' or not replay_run(getattr(trade, 'provider', '') or ''):
        return False
    if not getattr(trade, 'complete', True):
        return False                       # fills are missing: its excursions stay unknown, ticks or not
    return not any(m.get('precision') == 'observed-ticks' for m in journal.metric_options(trade))


def metric_options(journal, trade):
    options = journal.metric_options(trade)
    if journal.display_settings()['excursion_policy'] != 'ticks-only':
        return options
    return [m for m in options if m.get('precision') == 'observed-ticks'
            and not journal.capture_gaps(m['source'], start=trade.opened, end=trade.closed)]


def excursion_values(journal, trade, metric, risk=None):
    metric = display_metric(journal, trade, metric)
    unit = excursion_unit(journal)
    tick = units.tick_size(trade.symbol, journal.display_settings()['tick_sizes'])
    return tuple(units.value(metric, name, unit, risk=risk, tick=tick) for name in ('mae', 'mfe'))


def display_excursion_rows(journal, rows):
    result = []
    keys = [f.name for f in fields(Trade)]
    unit = excursion_unit(journal)
    sizes = journal.display_settings()['tick_sizes']
    for row in rows:
        trade = Trade(**{k: row[k] for k in keys})
        if row.get('merge_leg_ids'):
            trade.merge_leg_ids = row['merge_leg_ids']
            trade.merge_overlapping = row.get('merge_overlapping', False)
        metric = best_display_metric(journal, trade)
        mae, mfe = (units.value(metric, name, unit, risk=row.get('initial_risk'), tick=units.tick_size(trade.symbol, sizes))
                    for name in ('mae', 'mfe'))
        precision = metric.get('point_precision') if unit in ('points', 'ticks') else metric.get('precision')
        result.append({**row, 'display_mae': mae, 'display_mfe': mfe,
                       'display_precision': PRECISION.get(precision, 'Waiting for prices')})
    return result


def excursion_help(journal, trade, metric, risk):
    unit = excursion_unit(journal)
    if unit == 'r' and not units.positive(risk):
        st.info('R needs your initial total risk. Set it once under Notes → Initial planned risk.')
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


def rows_for(journal) -> list[dict]:
    annotations = journal.annotations()
    library = journal.tag_library()
    sessions = journal.get('sessions')
    from mwjournal.workspace import tag_score, rating
    workspace = journal.workspace()
    from mwjournal import journals
    aliases = journals.registry(getattr(journal, 'capture_journal', journal))['account_aliases']
    import json
    zone = display_zone(journal)
    rows = []
    for trade in journal.trades():
        a = annotations.get(trade.id, {})
        m = best_display_metric(journal, trade)
        tag_ids = tagging.effective(library, a, sessions.get(tagging.session_key(library, trade.opened), {}))
        risk = a.get('initial_risk')
        # setup is always present: a captured trade nobody has reviewed yet still has to chart and group.
        rows.append({**trade.row(), 'setup': '', **a, 'market': instrument_market(trade.symbol),
                     'merge_leg_ids': getattr(trade, 'merge_leg_ids', []),
                     'merge_overlapping': getattr(trade, 'merge_overlapping', False),
                     'account_label': aliases.get(json.dumps([trade.provider, trade.account], sort_keys=True), trade.account),
                     'tags': tagging.names(library, tag_ids), 'tag_ids': sorted(tag_ids),
                     'playbooks': tagging.matched_playbooks(library, tag_ids), 'metric': m,
                     'tag_score': tag_score(library, tag_ids),
                     'ratings': {id: rating(library, tag_ids, definition) for id, definition in workspace['ratings'].items()},
                     'mae': m.get('mae'), 'mfe': m.get('mfe'),
                     'precision': PRECISION.get(m.get('precision'), 'Waiting for data'),
                     'r': trade.net / risk if trade.net is not None and risk else None,
                     'mae_r': m['mae'] / risk if m.get('mae') is not None and risk else None,
                     'mfe_r': m['mfe'] / risk if m.get('mfe') is not None and risk else None,
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


def filter_rows(journal, rows: list[dict]) -> list[dict]:
    if not rows:
        return []
    c1, c2 = st.columns([1, 1])
    markets = c1.multiselect('Markets', sorted({instrument_market(r['symbol']) for r in rows}),
                             key=key(journal, 'filter-markets'),
                             placeholder='All markets · expiries grouped',
                             help='NQZ6, NQM6 and other NQ expiries share NQ. Micros stay separate. '
                                  'Charts and MAE/MFE still use the exact traded contract.')
    zone = display_zone(journal)
    days = {r['id']: local_day(r['opened'], zone) for r in rows}
    dates = list(days.values())
    # The span is part of the key: a trade on a new day widens the filter instead of hiding behind
    # the range that was right yesterday.
    period = c2.date_input('Entry dates', (min(dates), max(dates)),
                           key=key(journal, f'filter-dates-{min(dates)}-{max(dates)}'),
                           help=f'Calendar days in your display time zone, {zone}.')
    with st.expander('More Filters'):
        c1, c2, c3 = st.columns(3)
        accounts = c1.multiselect('Accounts', sorted({r['account'] for r in rows}), key=key(journal, 'filter-accounts'))
        symbols = c2.multiselect('Exact contracts', sorted({r['symbol'] for r in rows}), key=key(journal, 'filter-symbols'))
        available_tags = {t for r in rows for t in r['tags']}
        # A removed tag can still be an active filter; do not silently broaden the list on return.
        available_tags.update(st.session_state.get(key(journal, 'filter-tags'), []))
        tags = c3.multiselect('Tags', sorted(available_tags), key=key(journal, 'filter-tags'),
                              help='Trades with all of these tags, or any of them: choose under Tag match.')
        match = c3.radio('Tag match', ['All selected', 'Any selected'], horizontal=True, key=key(journal, 'filter-tag-match'))
        excluded = c3.multiselect('Exclude tags', sorted(available_tags), key=key(journal, 'filter-exclude-tags'))
        library = journal.tag_library()
        books = [p for p in library['playbooks'].values() if not p.get('retired')]
        book = c1.selectbox('Playbook combination', [None, *[p['id'] for p in books]],
                             format_func=lambda id: next(p['name'] for p in books if p['id'] == id) if id else 'Any playbook',
                             key=key(journal, 'filter-playbook'))
        direction = c2.multiselect('Directions', ['long', 'short'], key=key(journal, 'filter-directions'))
        rating = c2.multiselect('Process ratings', [1, 2, 3, 4, 5], key=key(journal, 'filter-ratings'))
    required_ids = tagging.ids(library, tags)
    excluded_ids = tagging.ids(library, excluded)
    def tagged(row):
        chosen = set(row.get('tag_ids', tagging.ids(library, row['tags'])))
        # Keep renamed filters attached to stable identities; unresolved required names match nothing.
        includes = not tags or (len(required_ids) == len(set(tags)) and (
            required_ids <= chosen if match == 'All selected' else bool(required_ids & chosen)))
        return includes and not (excluded_ids & chosen) and (not book or tagging.matches(library, chosen, library['playbooks'][book]))
    return [r for r in rows if (not markets or instrument_market(r['symbol']) in markets)
            and (not accounts or r['account'] in accounts) and (not symbols or r['symbol'] in symbols)
            and tagged(r) and (not direction or r['direction'] in direction)
            and (not rating or r.get('rating') in rating) and (not isinstance(period, (tuple, list)) or len(period) < 2
            or period[0] <= days.get(r['id'], local_day(r['opened'], zone)) <= period[1])]


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
    never receive ticks, so Setup asks for the update. Another writer's version numbers are its own."""
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
        return Link('Paused', 'neutral', 'Capture is switched off. Nothing is read until it is switched on again.')
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
