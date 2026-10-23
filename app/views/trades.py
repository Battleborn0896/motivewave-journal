"""Trades: a day-by-day list on the left; the selected trade's chart, tags, review and checklist on the right.

One way to pick a trade: click its row, or step with Prev / Next. Click a day's header instead and its Daily
Report Card takes the trade's place, until a trade is clicked or Back to Trades. Above both, the session
tools (a check-in timer, check-ins, pre-trade drafts, a session checklist) share a row with Filters and
Options. The list follows the background watcher by itself, without moving the selection, the open tab or a
note being typed.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import html
import itertools
import json
import time

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

import common
import theme
import tvchart
import runner_controls
import tag_controls
from views import checklists, daily, preferences, session
from common import PRECISION, money
from mwjournal.chart import STOP, chart_window, trade_chart
from mwjournal.model import WHY_INCOMPLETE, finite, instant
from mwjournal.metrics import excursions
from mwjournal.scope import LIVE_TICKS, PROVENANCE, provenance
from mwjournal import humanize, risk, units
from mwjournal import journals, merging

ADHERENCE = ['Unreviewed', 'Yes', 'No']
LIST_LIMIT = 150           # rows drawn at once; Show Earlier Trades draws the next ones
REPORT = 'report-card-open'  # per environment: the Daily Report Card is shown in place of the trade
# How often the page looks for trades the watcher has added or changed, and an open trade's numbers and chart
# are drawn again: one clock, so a fill and the prices after it reach the page together.
LIVE = '1s'
KEPT = 'live-chart-kept'   # per session: the prices an open trade's chart has drawn before the current minute
CONTEXT = [15, 30, 60, 120, 0]
LONG, SHORT = theme.SERIES[0], theme.SERIES[1]

CSS = f"""
<style>
.st-key-trade-list {{ gap:2px !important; max-height:calc(100dvh - 150px); min-height:220px; overflow-y:auto;
    padding-right:4px; }}
[class*="st-key-trade-row-"], [class*="st-key-trade-day-"] {{ position:relative; gap:0 !important; }}
.st-key-trade-list [data-testid="stMarkdownContainer"] {{ margin-bottom:0 !important; }}
[class*="st-key-trade-row-"] [data-testid="stElementContainer"]:has(.stButton),
[class*="st-key-trade-day-"] [data-testid="stElementContainer"]:has(.stButton) {{ position:absolute !important;
    inset:0; z-index:2; margin:0 !important; }}
[class*="st-key-trade-row-"] .stButton, [class*="st-key-trade-row-"] .stButton button,
[class*="st-key-trade-day-"] .stButton, [class*="st-key-trade-day-"] .stButton button {{ width:100%; height:100%;
    min-height:0 !important; padding:0; opacity:0; cursor:pointer; }}
[class*="st-key-trade-row-"]:hover .mwj-trade {{ background:{theme.SURFACE_2}; }}
[class*="st-key-trade-row-"]:has(button:focus-visible) .mwj-trade {{ outline:2px solid {theme.ACCENT}; }}
[class*="st-key-trade-day-"]:hover .mwj-day {{ background:{theme.SURFACE_2}; }}
[class*="st-key-trade-day-"]:hover .mwj-day .go {{ color:{theme.ACCENT}; }}
[class*="st-key-trade-day-"]:has(button:focus-visible) .mwj-day {{ outline:2px solid {theme.ACCENT}; }}
.st-key-trade-list > div:first-child .mwj-day {{ margin-top:0; }}
.mwj-trade {{ display:flex; flex-wrap:wrap; align-items:baseline; column-gap:6px; row-gap:1px; padding:5px 7px;
    border-radius:4px; border:1px solid transparent; border-left:3px solid transparent; font-family:{theme.FONT};
    font-size:12.5px; line-height:1.35; color:{theme.INK}; font-variant-numeric:tabular-nums; }}
.mwj-trade.win {{ border-left-color:{theme.UP}55; }}
.mwj-trade.loss {{ border-left-color:{theme.DOWN}55; }}
.mwj-trade.sel {{ background:{theme.SURFACE_2}; border-color:{theme.ACCENT}; border-left-color:{theme.ACCENT}; }}
.mwj-trade .t {{ min-width:4.1em; color:{theme.INK_2}; white-space:nowrap; }}
.mwj-trade .c {{ font-weight:600; white-space:nowrap; }}
.mwj-trade .s {{ white-space:nowrap; color:{theme.INK_2}; }}
.mwj-trade .s b {{ font-weight:600; }}
.mwj-trade .s .long {{ color:{LONG}; }}
.mwj-trade .s .short {{ color:{SHORT}; }}
.mwj-trade .p {{ margin-left:auto; white-space:nowrap; font-weight:600; }}
.mwj-trade .x {{ min-width:3.9em; text-align:right; white-space:nowrap; color:{theme.MUTED}; }}
.mwj-win {{ color:{theme.UP}; }}
.mwj-loss {{ color:{theme.DOWN}; }}
.mwj-flat {{ color:{theme.INK_2}; }}
.mwj-unknown {{ color:{theme.MUTED}; font-weight:400 !important; font-style:italic; }}
.mwj-badge {{ display:inline-block; font-size:10.5px; font-weight:650; letter-spacing:.03em; padding:0 6px;
    border-radius:3px; white-space:nowrap; line-height:16px; }}
.mwj-badge.open {{ color:{theme.ACCENT}; background:{theme.ACCENT}22; border:1px solid {theme.ACCENT}55; }}
.mwj-badge.bad {{ color:{theme.DOWN}; background:{theme.DOWN}22; border:1px solid {theme.DOWN}55; }}
.mwj-badge.info {{ color:{theme.INK_2}; background:{theme.SURFACE_2}; border:1px solid {theme.BORDER}; }}
.mwj-day {{ display:flex; flex-wrap:wrap; align-items:baseline; column-gap:8px; margin:10px 0 1px; padding:3px 8px 3px;
    border-bottom:1px solid {theme.BORDER}; border-radius:4px 4px 0 0; font-family:{theme.FONT}; font-size:12px;
    font-variant-numeric:tabular-nums; }}
.mwj-day:first-child {{ margin-top:0; }}
.mwj-day.sel {{ background:{theme.SURFACE_2}; border-bottom-color:{theme.ACCENT}; }}
.mwj-day .d {{ color:{theme.INK}; font-weight:650; white-space:nowrap; }}
.mwj-day .go {{ color:{theme.MUTED}; font-size:11px; white-space:nowrap; }}
.mwj-day.sel .go {{ color:{theme.ACCENT}; }}
.mwj-day .n {{ margin-left:auto; font-weight:650; white-space:nowrap; }}
.mwj-day .k {{ color:{theme.MUTED}; white-space:nowrap; }}
.mwj-title {{ font-family:{theme.FONT}; font-size:15px; color:{theme.INK}; line-height:1.4; }}
.mwj-title b {{ font-weight:650; }}
.mwj-title .when {{ color:{theme.INK_2}; }}
.mwj-legend span {{ font-size:13px; }}
.mwj-legend .mwj-dot {{ display:inline-block; width:8px; height:8px; border-radius:50%; margin:0 5px 1px 0;
    vertical-align:middle; }}
.st-key-trade-review-workspace [data-testid="stMetricValue"] {{ font-size:21px; }}
/* The session bar: a large count, small square buttons, a narrow duration box. */
.st-key-session-timer {{ gap:6px !important; }}
.st-key-session-timer .stButton button {{ min-height:32px; height:32px; width:34px; padding:0; }}
.st-key-session-timer [data-testid="stNumberInputContainer"] {{ height:32px; }}
.st-key-session-bar [data-testid="stPopover"] button {{ padding-left:8px; padding-right:8px; }}
.mwj-timer {{ font-family:{theme.FONT}; font-size:26px; font-weight:600; line-height:32px; color:{theme.INK};
    font-variant-numeric:tabular-nums; letter-spacing:.01em; min-width:3.1em; }}
.mwj-timer.off, .mwj-timer.paused {{ color:{theme.MUTED}; }}
.mwj-timer.due {{ color:{theme.ACCENT}; }}
[class*="st-key-session-check-in-"] [data-testid="stWidgetLabel"] p {{ color:{theme.ACCENT} !important;
    font-weight:650; }}
</style>
"""


# ---------------------------------------------------------------- what a row says

def side_html(direction, size=None) -> str:
    word = 'Long' if direction == 'long' else 'Short'
    shown = f"<b class='{direction}'>{word}</b>"
    return shown + (f' {size:g}' if size is not None else '')


def result_html(row) -> tuple[str, str]:
    """(P&L cell, the badge or duration cell) of one trade: unknown results say so."""
    if row['status'] == 'incomplete':
        return ("<span class='p mwj-unknown'>Unknown</span>",
                "<span class='x'><span class='mwj-badge bad' title='Some fills are missing from the platform history'>"
                "Incomplete</span></span>")
    if row.get('net') is None:
        return ("<span class='p mwj-unknown'>Unknown</span>",
                "<span class='x'><span class='mwj-badge bad' title='Set this contract’s dollars per point to see its P&amp;L'>"
                'Unpriced</span></span>')
    if row['status'] == 'open':
        # The header's Open P&L (open_row puts it in the row), '—' until a price has been measured.
        shown = _money(row['open_net'], row['currency']) if row.get('open_net') is not None else '—'
        return (f"<span class='p mwj-flat' title='Open P&amp;L'>{shown}</span>",
                "<span class='x'><span class='mwj-badge open'>Open</span></span>")
    tone = 'mwj-win' if row['net'] > 0 else 'mwj-loss' if row['net'] < 0 else 'mwj-flat'
    return (f"<span class='p {tone}'>{_money(row['net'], row['currency'])}</span>",
            f"<span class='x'>{humanize.duration(humanize.held(row['opened'], row['closed']))}</span>")


def _money(value, currency) -> str:
    return html.escape(money(value, currency)).replace('$', '&#36;')


def row_html(row, zone, selected) -> str:
    pnl, tail = result_html(row)
    outcome = ('win' if row['net'] > 0 else 'loss' if row['net'] < 0 else '') \
        if row['status'] == 'closed' and row.get('net') is not None else ''
    merged = " <span class='mwj-badge info'>Merged</span>" if row.get('merge_leg_ids') else ''
    return (f"<div class='mwj-trade {outcome}{' sel' if selected else ''}'>"
            f"<span class='t'>{common.clock(row['opened'], zone)}</span>"
            f"<span class='c'>{html.escape(row['symbol'])}{merged}</span>"
            f"<span class='s'>{side_html(row['direction'], row['max_qty'])}</span>{pnl}{tail}</div>")


def row_label(row, zone) -> str:
    """What a screen reader says for the row's button."""
    result = ('P&L unknown' if row.get('net') is None or row['status'] == 'incomplete' else
              '' if row['status'] == 'open' else money(row['net'], row['currency']))
    state = {'open': 'Open', 'incomplete': 'Incomplete'}.get(row['status']) or (
        'Unpriced' if row.get('net') is None else humanize.duration(humanize.held(row['opened'], row['closed'])))
    return common.md(' '.join(part for part in (common.clock(row['opened'], zone), row['symbol'], row['direction'].title(),
                                                f"{row['max_qty']:g}", result, state) if part))


def day_html(day, rows, zone, selected=False) -> str:
    """A day's header: its date, a hint that it opens the Daily Report Card, its net result and trade count."""
    known = [r['net'] for r in rows if r['status'] == 'closed' and r.get('net') is not None]
    net = sum(known)
    tone = 'mwj-win' if net > 0 else 'mwj-loss' if net < 0 else 'mwj-flat'
    total = _money(net, rows[0]['currency']) if known else '—'
    return (f"<div class='mwj-day{' sel' if selected else ''}'><span class='d'>{common.day_label(day, zone, weekday=True)}</span>"
            "<span class='go'>Report Card ›</span>"
            f"<span class='n {tone}'>{total}</span>"
            f"<span class='k'>{len(rows)} trade{'' if len(rows) == 1 else 's'}</span></div>")


# ---------------------------------------------------------------- the page

def render(journal, rows, *, every=None) -> None:
    """`rows` have passed the saved filter; `every` has not, so the session tools see every trade."""
    every = rows if every is None else every
    st.markdown(CSS, unsafe_allow_html=True)
    report_key = common.key(journal, REPORT)
    if st.session_state.pop(common.REPORT_KEY, False):         # asked for from elsewhere, such as a calendar day
        st.session_state[report_key] = True
    open_rows = [r for r in every if r['status'] == 'open']
    if not every:
        top_row(journal, every, open_rows)
        if st.session_state.get(report_key):
            report_card(journal, rows)
        else:
            empty(journal)
        return
    zone = common.display_zone(journal)
    capture = getattr(journal, 'capture_journal', journal)
    seen = common.changes(capture)
    selected = common.key(journal, 'selected-trade')
    shown, options_slot = top_row(journal, every, open_rows, rows)
    filter_state = {k: st.session_state[k] for k in common.filter_keys(journal)}
    signature = json.dumps(filter_state, sort_keys=True, default=str)
    signature_key = common.key(journal, 'review-filter-signature')
    unchanged = st.session_state.get(signature_key) == signature
    st.session_state[signature_key] = signature
    # A tag edit that drops the open trade out of the filters keeps it open until the filters change.
    pinned = next((r for r in rows if unchanged and r['id'] == st.session_state.get(selected) and r not in shown), None)
    count = f'{len(shown)} of {len(rows)}' if len(shown) != len(rows) else ''
    shown = sorted(shown, key=lambda r: r['opened'], reverse=True)
    review_rows = [*shown, *([pinned] if pinned else [])]
    ids = [r['id'] for r in review_rows]
    if ids and st.session_state.get(selected) not in ids:
        st.session_state[selected] = ids[0]
    trade = next((t for t in journal.trades() if t.id == st.session_state.get(selected)), None) if ids else None
    with options_slot:
        options_menu(journal, rows, trade)
    list_col, review_col = st.columns([1.75, 3.25], gap='medium')
    with list_col:
        theme.section('Trades', count)
        if shown:
            trade_list(journal, shown, zone)
        else:
            st.info('No trades match these filters.')
    with review_col.container(key='trade-review-workspace'):
        if st.session_state.get(report_key):
            report_card(journal, rows)
        elif trade is None:
            if ids:                            # the watcher regrouped fills between two reads
                st.info('This trade was just regrouped by new fills. Pick it again from the list.')
        else:
            if pinned:
                st.caption('Kept open: this trade no longer matches your filters.')
            detail(journal, trade, ids, zone)
    follow(capture, seen)


def top_row(journal, every, open_rows, rows=None):
    """The session tools, then Filters and Options: one row above the list. Returns the trades the filters
    keep and the Options menu, which is filled in once the selected trade is known."""
    # The timer takes the width it needs and the menus the rest; on a narrow window the menus move below it
    # together rather than one by one.
    bar = st.container(horizontal=True, vertical_alignment='top', gap='small', key='session-bar')
    with bar.container(width='content'):
        session.timer(journal, open_rows)
    with bar.container(horizontal=True, horizontal_alignment='right', vertical_alignment='center', gap='small'):
        session.menus(journal, every)
        if rows is None:
            return [], None
        with st.popover('Filters', help='Narrow the list by market, date, account, tag, playbook or rating. The '
                                        'saved filter here applies to every page.'):
            shown = preferences.filter_menu(journal, rows)
        options = st.popover('Options', help='Chart settings, and merging or splitting trades.')
    return shown, options


def report_card(journal, rows) -> None:
    """The Daily Report Card in place of the trade, with the way back to it."""
    def back():
        st.session_state[common.key(journal, REPORT)] = False

    st.button('← Back to Trades', key=common.key(journal, 'back-to-trades'), type='tertiary', on_click=back,
              help='Shows the selected trade again. Clicking any trade in the list does too.')
    daily.render(journal, rows)


@st.fragment(run_every=LIVE)
def follow(capture, seen) -> None:
    """Redraws the page when the background watcher has added or changed trades. Widgets keep their
    keys, so the selection, the open tab and text being typed stay as they are."""
    if common.changes(capture) != seen:
        st.rerun()


def trade_list(journal, shown, zone) -> None:
    selected = common.key(journal, 'selected-trade')
    limit_key = common.key(journal, 'trade-list-limit')
    position = next((i for i, r in enumerate(shown) if r['id'] == st.session_state.get(selected)), 0)
    limit = max(st.session_state.get(limit_key, LIST_LIMIT), position + 1)

    report_key = common.key(journal, REPORT)
    reporting = bool(st.session_state.get(report_key))
    day_key, zone_key = common.key(journal, 'review-day'), common.key(journal, 'review-tz')

    def pick(id):
        st.session_state[selected] = id
        st.session_state[report_key] = False

    def open_day(day):
        # The report card's own boxes take this day, in the zone the list groups days by.
        st.session_state.update({report_key: True, day_key: day, zone_key: zone})

    drawn = 0
    with st.container(key='trade-list'):
        for day, day_rows in itertools.groupby(shown, key=lambda r: common.local_day(r['opened'], zone)):
            if drawn >= limit:
                break
            day_rows = list(day_rows)
            chosen_day = reporting and st.session_state.get(day_key) == day and st.session_state.get(zone_key) == zone
            with st.container(key='trade-day-' + day.isoformat()):
                st.markdown(day_html(day, day_rows, zone, chosen_day), unsafe_allow_html=True)
                st.button(common.md(f'Daily Report Card for {common.day_label(day, zone, weekday=True)}'),
                          key=common.key(journal, 'open-day-' + day.isoformat()), on_click=open_day, args=(day,),
                          width='stretch')
            for row in day_rows:
                with st.container(key='trade-row-' + hashlib.sha256(row['id'].encode()).hexdigest()[:16]):
                    chosen = not reporting and row['id'] == st.session_state.get(selected)
                    if row['status'] == 'open':
                        live_row(journal, row, zone, chosen)
                    else:
                        st.markdown(row_html(row, zone, chosen), unsafe_allow_html=True)
                    st.button(row_label(row, zone), key=common.key(journal, 'pick-' + row['id']),
                              on_click=pick, args=(row['id'],), width='stretch')
                drawn += 1
        if drawn < len(shown):
            st.button('Show Earlier Trades', key=common.key(journal, 'trade-list-more'), width='stretch',
                      on_click=lambda: st.session_state.update({limit_key: limit + LIST_LIMIT}))


def open_row(journal, row, zone, selected) -> None:
    """An open trade's row, showing the same Open P&L as the header: at the newest price measured, drawn again
    every LIVE as the header is (a named journal's copy of the trade brought up to date first, as there)."""
    trade = common.row_trade(row)
    journals.follow(journal, trade)
    open_net = common.open_pnl(trade, common.best_display_metric(journal, trade))
    st.markdown(row_html({**row, 'open_net': open_net}, zone, selected), unsafe_allow_html=True)


def options_menu(journal, rows, trade) -> None:
    """The rarely used actions, together: how the chart is drawn, and merging or splitting reviews."""
    st.markdown('**Chart**')
    sources = journal.price_sources(trade.symbol, trade) if trade else []
    if sources:
        preferred = default_source(sources)
        names = [PROVENANCE[provenance(s)] for s in sources]
        st.selectbox('Chart price source', sources, index=sources.index(preferred), key='chart-source-' + trade.id,
                     format_func=lambda s: PROVENANCE[provenance(s)] + (
                         '' if names.count(PROVENANCE[provenance(s)]) == 1 and provenance(s) != 'imported' else f' · {s}'))
    st.selectbox('Context each side', CONTEXT, format_func=lambda n: f'{n} minutes',
                 key=common.key(journal, 'chart-context'), help='Minutes of chart before entry and after exit.')
    st.checkbox('Show Stop', value=True, key=common.key(journal, 'chart-levels'))
    st.markdown('**Merge Trades**')
    originals = [r for r in rows if r['status'] == 'closed' and r.get('complete', True) and not r.get('merge_leg_ids')]
    by_id = {r['id']: r for r in originals}
    zone = common.display_zone(journal)
    with st.form(common.key(journal, 'merge-review-form'), border=False):
        ids = st.multiselect('Original trades to merge', list(by_id),
                             format_func=lambda id: f'{common.moment(by_id[id]["opened"], zone, seconds=False)} · '
                                                    f'{by_id[id]["symbol"]} · {money(by_id[id]["net"], by_id[id]["currency"])}',
                             help='Reviews them as one trade. Same account, contract, currency and direction, within '
                                  '24 hours. The original fills, positions and notes are kept; Split undoes it.')
        if st.form_submit_button('Merge Selected'):
            try:
                merged = merging.create(getattr(journal, 'original', journal), ids)
                st.session_state[common.key(journal, 'selected-trade')] = merged.id
                st.rerun()
            except ValueError as exc:
                st.error(str(exc))
    groups = {id: d for id, d in merging.definitions(journal).items() if d['active']}
    if groups:
        group = st.selectbox('Merged trade to split', list(groups),
                             format_func=lambda id: f'{len(groups[id]["leg_ids"])} trades · merged '
                                                    f'{common.moment(groups[id]["at"], zone, seconds=False)}')
        if st.button('Split Into Original Trades', key=common.key(journal, 'split-review')):
            merging.split(journal, group)
            st.rerun()


def detail(journal, trade, ids, zone) -> None:
    """The selected trade: its header, result, chart and tags, notes and the less used details."""
    selected = common.key(journal, 'selected-trade')

    def move(offset):
        st.session_state[selected] = ids[ids.index(st.session_state[selected]) + offset]

    position = ids.index(trade.id)
    with st.container(horizontal=True, vertical_alignment='center', gap='small'):
        st.button('← Prev', on_click=move, args=(-1,), disabled=position == 0,
                  key=common.key(journal, 'previous-trade'), help='The newer trade above in the list')
        st.button('Next →', on_click=move, args=(1,), disabled=position == len(ids) - 1,
                  key=common.key(journal, 'next-trade'), help='The older trade below in the list')
        st.markdown(f"<div class='mwj-title'><b>{html.escape(trade.symbol)}</b> · "
                    f"{side_html(trade.direction, trade.max_qty)} · "
                    f"<span class='when'>{common.moment(trade.opened, zone)}</span></div>", unsafe_allow_html=True)
        st.caption(f'{position + 1} of {len(ids)}', width='content')
    annotation = journal.annotations().get(trade.id, {})
    live = trade.status == 'open'          # an open trade follows the market: see live_numbers at the end
    (live_numbers if live else numbers)(journal, trade, annotation, zone)
    if trade.point_value is None:
        st.warning(f'{trade.symbol} has no dollars-per-point multiplier yet, so its P&L cannot be calculated. Set it here.')
        common.multiplier_form(journal, 'trade-multiplier', trade.symbol)
    overview, notes, checks, more = st.tabs(['Chart & Tags', 'Notes', 'Checklist', 'More'],
                                            key=common.key(journal, 'trade-tabs'))
    with overview:
        chart_col, tag_col = st.columns([1.35, 1], gap='small')
        with chart_col:
            (live_chart if live else chart_panel)(journal, trade, annotation, zone)
        with tag_col:
            tag_controls.render(journal, trade)
    with notes:
        review_form(journal, trade, annotation)
    with checks:
        checklists.run_panel(journal, trade, prefix='trade-' + trade.id)
    with more:
        metrics, executions, runner = st.tabs(['MAE / MFE', 'Executions', 'Runner'],
                                              key=common.key(journal, 'trade-more-tabs'))
        with executions:
            executions_panel(journal, trade, zone)
        with metrics:
            (live_excursions if live else excursions_panel)(journal, trade, annotation, zone)
        with runner:
            if getattr(trade, 'merge_leg_ids', None):
                st.info('Runner analysis stays per original trade: a merged trade can have different stops. '
                        'Split it to analyse each original runner.')
            elif trade.closed:
                runner_controls.trade_panel(journal, trade)
            else:
                st.info('Post-exit runner analysis becomes available after the trade closes.')


def numbers(journal, trade, annotation: dict, zone) -> None:
    """The trade's result, MAE, MFE and size, and what they were measured from. An open trade's result is
    what it would be if closed at the newest price its MAE and MFE were measured with."""
    unit = common.excursion_unit(journal)
    metric = common.best_display_metric(journal, trade)
    precision = metric.get('point_precision') if unit in ('points', 'ticks') else metric.get('precision')
    measured = PRECISION.get(precision, 'Waiting for prices') if common.measured(metric) else ''
    if measured and metric.get('provenance') in ('history-backfill', 'replay-history'):
        measured += ' · ' + PROVENANCE[metric['provenance']]      # real prints, loaded after the trade
    st.caption(' · '.join(part for part in (common.span(trade.opened, trade.closed, zone), measured) if part))
    if getattr(trade, 'merge_leg_ids', None):
        st.caption(f'Merged from {len(trade.merge_leg_ids)} trades · their own fills and notes are kept',
                   help='Overlapping positions: points and ticks are unavailable because the original trades had '
                        'different average entries. Currency and R remain.' if getattr(trade, 'merge_overlapping', False) else None)
    cols = st.columns([1.25, 1, 1, .75])
    result, label, shown = trade.net, 'Net P&L', 'Unknown'
    why = (' '.join(WHY_INCOMPLETE[reason] for reason in trade.incomplete) if trade.incomplete
           else 'This contract has no dollars per point yet.' if trade.net is None else 'After recorded fees.')
    if trade.status == 'open':
        # At the newest price the MAE and MFE beside it were measured with, so the three always agree.
        result, label = common.open_pnl(trade, metric), 'Open P&L'
        if result is not None:
            why = (f'If the {trade.open_qty:g} still held were closed at {humanize.price(metric["last_price"])}, the '
                   f'last recorded price ({common.moment(metric["last_ts"], zone)}). Realised so far: '
                   f"{money(trade.net, trade.currency)}. The exit's fees are not included.")
        elif trade.net is not None:
            # No measured price: the line under these numbers says why (common.no_excursions).
            shown, why = '—', f'Realised so far: {money(trade.net, trade.currency)}.'
    outcome = 'positive' if result is not None and result > 0 else 'negative' if result is not None and result < 0 else 'neutral'
    with cols[0].container(key='trade-result-' + outcome):
        st.metric(label, money(result, trade.currency) if result is not None else shown, help=why)
    plan = risk.plan_for(journal, trade)
    mae, mfe = common.excursion_values(journal, trade, metric, plan.risk)
    explain = units.description(unit) + (f' Measured from: {measured}.' if measured else '')
    cols[1].metric('MAE', common.excursion_amount(mae, unit, trade.currency), help=explain)
    cols[2].metric('MFE', common.excursion_amount(mfe, unit, trade.currency), help=explain)
    cols[3].metric('Peak size', f'{trade.max_qty:g}', help='The most contracts held at once.')
    if not common.measured(metric) or common.replay_ticks_pending(journal, trade):
        st.caption(common.no_excursions(journal, trade))
    elif unit == 'r' and plan.risk is None and plan.why:
        st.caption(plan.why)                       # in R, why there is none: no stop reported, typed or found


def executions_panel(journal, trade, zone) -> None:
    if trade.flags:
        st.warning('Data quality: ' + ' · '.join(trade.flags))
    st.dataframe(pd.DataFrame([{'Time': common.moment(f['ts'], zone), 'Role': f['role'].title(), 'Side': f['side'].title(),
                                'Qty': f['qty'], 'Price': f['price'], 'Fee': f['commission']} for f in trade.fills]),
                 hide_index=True, width='stretch', column_config={'Fee': common.money_column('Fee', trade.currency)})
    if getattr(trade, 'merge_leg_ids', None):
        original = getattr(journal, 'original', journal)
        leg_reviews = original.annotations()
        legs = {t.id: t for t in original.trades()}
        st.dataframe(common.money_table([{'Trade': index + 1, 'Opened': common.moment(legs[id].opened, zone),
                                          'Closed': common.moment(legs[id].closed, zone), 'Net P&L': legs[id].net,
                                          'Tags': leg_reviews.get(id, {}).get('tags', []),
                                          'Notes': leg_reviews.get(id, {}).get('notes', '')}
                                         for index, id in enumerate(trade.merge_leg_ids)], trade.currency,
                                        results=['Net P&L']), hide_index=True)


def empty(journal) -> None:
    if journal.dataset == 'replay':
        st.info(common.NO_REPLAY_TRADES + '. Start a replay in MotiveWave with the study on its chart.')
    else:
        st.info('No trades yet. Leave this app open, add MotiveWave Journal to the MotiveWave chart you trade, then '
                'trade normally.')
    theme.section('Automatic Recording')
    st.write('A trade starts with its first fill and ends when that account and contract are flat again. Scale-ins '
             'and partial exits stay in one trade. Live and Replay are kept apart.')
    with st.container(horizontal=True, gap='small'):
        st.button('Open Settings', on_click=common.go('Settings'), type='primary',
                  help='Whether the study is recording, the one step to take if it is not, and every setting.')
        st.button('Import Old Trades', on_click=common.go('Import'),
                  help='Trades from before the study was installed: a fills CSV from your platform or broker.')


def review_form(journal, trade, a: dict) -> None:
    setup_key = common.key(journal, 'setup-' + trade.id)
    setup_col, reuse_col = st.columns([1.5, 1])
    setups = sorted({str(saved.get('setup') or '').strip() for saved in journal.annotations().values()
                     if str(saved.get('setup') or '').strip()})
    reuse_key = common.key(journal, 'reuse-setup-' + trade.id)
    def reuse_setup():
        chosen = st.session_state.get(reuse_key)
        if chosen:
            journal.update_annotation(trade.id, {'setup': chosen})
            st.session_state[setup_key] = chosen
            st.session_state[reuse_key] = None
    if setups:
        reuse_col.selectbox('Use saved setup', setups, index=None, placeholder='Choose an existing setup',
                             key=reuse_key, on_change=reuse_setup)
    setup_col.text_input('Setup / playbook', a.get('setup', ''), key=setup_key,
                  placeholder='Name your setup', on_change=tag_controls.save_field,
                  args=(journal, trade.id, 'setup', setup_key), help='Saves when you leave the box.')
    notes_key = common.key(journal, 'notes-' + trade.id)
    st.text_area('Review notes', a.get('notes', ''), placeholder='What worked? What will I change next time?',
                 key=notes_key, height=160, on_change=tag_controls.save_field,
                 args=(journal, trade.id, 'notes', notes_key), help='Saves when you leave the box.')
    theme.section('Trade Plan')
    plan = risk.plan_for(journal, trade)
    c1, c2 = st.columns(2)
    if plan.source != 'merged':
        shown = stop_text(plan.stop)
        # The key carries what the box was filled with, so a stop saved here, or a stop order that arrives
        # after the box was drawn, fills it again.
        stop_key = common.key(journal, f'stop-{trade.id}-{shown}')
        typed = c1.text_input('Stop', shown, placeholder='Your stop price', key=stop_key, on_change=save_stop,
                              args=(journal, trade.id, stop_key, shown),
                              help=f'The price R is measured from. Blank uses your stop order as it stood '
                                   f'{risk.STOP_SETTLES} seconds after entry; moving it later is managing the trade '
                                   'and leaves R alone. Saves when you leave the box.')
        if error := stop_error(typed, shown):
            c1.error(error)
    c1.caption(common.md(stop_caption(plan, trade.currency)))
    adherence_key = common.key(journal, 'adherence-' + trade.id)
    c2.selectbox('Followed my plan', ADHERENCE, index=ADHERENCE.index(a.get('adherence', 'Unreviewed')),
                 key=adherence_key, on_change=tag_controls.save_field,
                 args=(journal, trade.id, 'adherence', adherence_key))


def typed_stop(typed):
    """A stop as typed, read the way prices are shown: '4,996.25' and '4996.25' are the same price."""
    return finite(typed.replace(',', '').strip(), 'Stop')


def stop_error(typed, shown) -> str:
    """Why a typed stop cannot be saved, or '' when it can."""
    try:
        if typed.strip() and typed.strip() != shown:
            typed_stop(typed)
    except ValueError as exc:
        return str(exc)
    return ''


def save_stop(journal, trade_id, widget_key, shown) -> None:
    """Saves the Stop box when it changes, as the notes box saves. Only a stop that was edited becomes the
    trader's own: the box may hold the stop order's price, which stays the order's. Blank returns to the order."""
    typed = str(st.session_state.get(widget_key) or '').strip()
    if typed == shown or stop_error(typed, shown):
        return
    journal.update_annotation(trade_id, {'planned_stop': typed_stop(typed) if typed else None})


def stop_text(price) -> str:
    """A stop price as typed, with no thousands separators, so the box reads back exactly what it shows."""
    return '' if price is None else f'{price:.6f}'.rstrip('0').rstrip('.')


def stop_caption(plan, currency) -> str:
    """Where the trade's stop came from and the risk it gives: 'From your stop order, 10 seconds after entry · risk $250'."""
    where = {'saved': 'Your stop', 'order': f'From your stop order, {risk.STOP_SETTLES} seconds after entry',
             'merged': 'The trades merged here, together'}.get(plan.source)
    result = plan.why if plan.risk is None else 'risk ' + money(plan.risk, currency, signed=False)
    text = ' · '.join(part for part in (where, result) if part)
    if plan.source == 'saved' and plan.order is not None and plan.order != plan.stop:
        text += f'. Clear it to use your stop order, {humanize.price(plan.order)}.'
    return text


def excursions_panel(journal, trade, a: dict, zone) -> None:
    theme.section('MAE / MFE')
    unit = common.excursion_unit(journal)
    at_risk = risk.plan_for(journal, trade).risk
    tick = units.tick_size(trade.symbol, journal.display_settings()['tick_sizes'])
    # The one measurement the trade is shown with everywhere (common.best_display_metric): nothing to choose.
    m = common.best_display_metric(journal, trade)
    if not common.measured(m):
        st.info(common.no_excursions(journal, trade))
    else:
        m = shown = common.display_metric(journal, trade, m)
        common.excursion_help(journal, trade, m, at_risk)
        mae, mfe = common.excursion_values(journal, trade, m, at_risk)
        c1, c2 = st.columns(2)
        c1.metric('Maximum adverse excursion', common.excursion_amount(mae, unit, trade.currency), help=units.description(unit))
        c2.metric('Maximum favorable excursion', common.excursion_amount(mfe, unit, trade.currency), help=units.description(unit))
        if at_risk and unit != 'r':
            c1, c2 = st.columns(2)
            c1.metric('MAE / risk', f"{m['mae'] / at_risk:.2f}R" if m.get('mae') is not None else '—',
                      help='MAE divided by the risk to your stop.')
            c2.metric('MFE / risk', f"{m['mfe'] / at_risk:.2f}R" if m.get('mfe') is not None else '—',
                      help='MFE divided by the risk to your stop.')
        for note in dict.fromkeys([*m.get('notes', []), *(m.get('point_notes', []) if unit in ('points', 'ticks') else [])]):
            st.caption(note)
        if m.get('path'):
            # Every recorded price is read for it: once the trade is closed, not again every second.
            full = trade.closed is not None and st.checkbox('Show/export full recorded trade path',
                                                            key=common.key(journal, 'full-path-' + trade.id))
            if trade.closed is None:
                st.caption('The full recorded path can be shown and exported once the trade is closed.')
            if full:
                samples = journal.prices(trade.symbol, m['source'], start=trade.opened, end=trade.closed)
                clock = journal.trade_clock(trade)
                m = excursions(trade, samples, m['source'], now=clock, max_path_points=None)
                m = merging.mask_overlap_points(trade, m)
                st.caption(f"{len(m['path']):,} recorded prices",
                           help='Every observation that was recorded, not proof that the feed delivered every tick. '
                                'OHLC paths remain bounds, not exact ticks. The CSV times are UTC.')
                st.download_button('Download Full Path CSV', pd.DataFrame(m['path']).to_csv(index=False),
                                   file_name=trade.id + '-recorded-path.csv', mime='text/csv',
                                   key=common.key(journal, 'path-export-' + trade.id))
            path = pd.DataFrame(m['path'])
            if path.empty:
                st.info('No matching recorded path observations remain for this source.')
                return
            if full and len(path) > 20000:
                st.caption('The CSV has every price; the plot uses fewer so it stays responsive.')
                path = pd.DataFrame(shown['path'])
            fig = go.Figure()
            high = [units.path_value(p, 'high', unit, risk=at_risk, tick=tick) for p in path.to_dict('records')]
            low = [units.path_value(p, 'low', unit, risk=at_risk, tick=tick) for p in path.to_dict('records')]
            if all(v is None for v in high + low):
                st.info('No path values are available in these units yet.')
            else:
                times = common.wall_clock(path.ts, zone)
                fig.add_scatter(x=times, y=high, name='Upper bound', line_color=theme.UP)
                fig.add_scatter(x=times, y=low, name='Lower bound', line_color=theme.DOWN)
                fig.add_hline(y=0, line_color=theme.MUTED)
                for fill in trade.fills:
                    fig.add_vline(x=common.wall_clock([fill['ts']], zone)[0], line_width=1,
                                  line_dash='dot', line_color=theme.UP if fill['role'] == 'entry' else theme.DOWN)
                definition = 'Price move from held average entry' if unit in ('points', 'ticks') else 'Move of the position held'
                # uirevision: a zoom survives the redraws of an open trade's path.
                fig.update_layout(height=290, yaxis_title=f'{definition} ({units.suffix(unit, trade.currency)})',
                                  xaxis_title=f'Time ({humanize.zone_name(zone, trade.opened)})', uirevision=trade.id)
                if unit == 'currency':
                    theme.money_axis(fig, trade.currency, [*high, *low])
                st.plotly_chart(fig, width='stretch', config=theme.PLOTLY_CONFIG,
                                key=common.key(journal, 'path-' + trade.id))
    if st.button('Recalculate From Stored Prices', key=common.key(journal, 'recalculate'),
                 help='Measures MAE / MFE again from the prices already stored.'):
        common.recalculate(journal)
        st.rerun()


def window_prices(journal, trade, source, start, end) -> list:
    """The prices of one source in the chart window. An open trade's chart is drawn every second, and the
    minutes it has drawn before the newest one do not change: they are kept, and each second reads only the
    prices from the newest minute on. The window is read whole again once a wall-clock minute, for prices
    stored late (a platform candle written after its minute)."""
    if trade.status != 'open':
        st.session_state.pop(KEPT, None)
        return journal.prices(trade.symbol, source, start=start.isoformat(), end=end.isoformat())
    key = (str(journal.path), trade.id, source, start.isoformat(), int(time.time() // 60))
    kept = st.session_state.get(KEPT)
    if kept and kept[0] == key:
        settled, boundary = kept[1], kept[2]
        fresh = journal.prices(trade.symbol, source, start=boundary, end=end.isoformat())
        # The contract's own prices first (model.trade_prices): the kept part cannot keep the continuous
        # chart's once the contract's appear.
        exact = trade.symbol.strip().upper()
        if settled and any(s.symbol == exact for s in fresh) and not any(s.symbol == exact for s in settled):
            fresh, settled = journal.prices(trade.symbol, source, start=start.isoformat(), end=end.isoformat()), []
    else:
        settled, fresh = [], journal.prices(trade.symbol, source, start=start.isoformat(), end=end.isoformat())
    samples = settled + fresh
    # Minutes before the newest one join the kept part, however fast a replay plays.
    newest = max((s.ts for s in fresh), default=None)
    if newest is not None:
        boundary = instant(newest).replace(second=0, microsecond=0).isoformat()
        # Stored times are UTC ISO text, so they compare as text in time order.
        settled = settled + [s for s in fresh if s.ts < boundary]
    elif not settled:
        boundary = start.isoformat()
    st.session_state[KEPT] = (key, settled, boundary)
    return samples


def default_source(sources) -> str:
    """What a trade's chart is drawn from unless another source is chosen under Options: MotiveWave's 1-minute
    bars (a replay trade's are its own run's), else the first source."""
    return next((s for s in sources if provenance(s) == 'platform-bars'), sources[0])


def chart_panel(journal, trade, annotation: dict, zone) -> None:
    sources = journal.price_sources(trade.symbol, trade)
    if not sources:
        st.info('No candles yet for this contract. Add the study to its chart in MotiveWave, or import '
                '1-minute bars or ticks in Import.')
        return
    source = st.session_state.get('chart-source-' + trade.id)
    source = source if source in sources else default_source(sources)
    context = st.session_state.get(common.key(journal, 'chart-context'), CONTEXT[0])
    levels = st.session_state.get(common.key(journal, 'chart-levels'), True)
    clock = journal.trade_clock(trade)
    start, end = chart_window(trade, context, now=clock)
    samples = window_prices(journal, trade, source, start, end)
    prints = []
    # The trade's own recorded prints: live, or its replay run's.
    recorded = next((s for s in sources if provenance(s) in ('recorded-live', 'replay-ticks', 'replay-simulated')), None)
    if provenance(source) == 'platform-bars' and recorded:
        # Minutes with no platform candle are drawn from the prints (trade_chart): the one in progress, a replay's
        # last one, or ones MotiveWave's history had not filled yet. Read from the first such minute on.
        have = {int(instant(s.ts).timestamp()) // 60 for s in samples if s.resolution_seconds}
        first = next((m for m in range(int(start.timestamp()) // 60, int(end.timestamp()) // 60 + 1) if m not in have),
                     None)
        if first is not None:
            after = dt.datetime.fromtimestamp(first * 60, dt.timezone.utc)
            prints = journal.prices(trade.symbol, recorded, start=after.isoformat(), end=end.isoformat())
    events = [e for e in journal.protection_events(trade) if e.role == 'stop']      # take profit plays no part
    payload = trade_chart(trade, samples, source, risk.plan_for(journal, trade).stop, events, now=clock,
                          prints=prints, prints_source=recorded or LIVE_TICKS)
    candles = payload['series']['data']
    if candles:
        overlays = payload['overlays'] if levels else {**payload['overlays'], 'levels': []}
        tvchart.show([payload['series']], key='journal-candles-' + trade.id, height=320,
                     title=f'{trade.symbol} · 1m · {humanize.zone_name(zone, trade.opened)}', intraday=True,
                     precision=4 if trade.symbol.startswith(('6', 'Z')) else 2,
                     watermark=trade.symbol, timezone=zone, trade_overlays=overlays,
                     visible_range={'from': candles[0]['time'], 'to': max(candles[-1]['time'], candles[0]['time'] + 60)})
        # Drawn dots rather than '●', which IBM Plex Sans does not have.
        legend = ' '.join(f"<span class='mwj-dot' style='background:{colour}'></span>{name}" for name, colour in
                          (('Buy', tvchart.CANDLE_UP), ('Sell', tvchart.CANDLE_DOWN), ('Stop', STOP)))
        st.caption(f"<span class='mwj-legend'>{legend}</span> · {common.CREDIT}", unsafe_allow_html=True,
                   help='Each fill is an arrow pointing at its exact price and time, with its size and price beside '
                        'it: +1 bought, −1 sold. Hover one for its details. A dashed line joins each entry to the exit '
                        'that closed it, first in first out: green where that part made money, red where it lost. '
                        'The solid stop is the one R uses; dashed, where your stop order was moved after that. '
                        'Scroll to zoom, drag to pan, double-click to reset. Review '
                        'only: nothing here can place an order.')
    else:
        st.info('This source has no usable 1-minute candles in the trade window. Full OHLC or observed ticks are '
                'needed; high/low-only and 5-minute history cannot be turned into real 1-minute candles.')
    if trade.closed and instant(trade.closed) > end:
        st.warning('The chart is capped at 10,000 minutes from entry. This position was held beyond that window.')
    if journal.capture_gaps(source, start=start.isoformat(), end=end.isoformat(), symbol=trade.symbol):
        st.warning('This chart window has a recorded capture gap. Missing prices can understate excursions.')
    with st.expander('Chart Data & Orders'):
        for note in payload['notes']:
            st.caption(note)
        if events:
            # Each stop order by its number, 1 for the first placed, so its moves read as one order's.
            number = {id: n for n, id in enumerate(dict.fromkeys(e.order_id for e in events), 1)}
            st.dataframe(pd.DataFrame([{'Time': common.moment(e.ts, zone), 'Stop': number[e.order_id],
                                        'Status': e.state.title(), 'Price': e.price} for e in events]),
                         hide_index=True, width='stretch')
        else:
            st.caption('No stop order was recorded for this trade. Type your stop under Notes.')
        st.dataframe(pd.DataFrame([{'Time': common.moment(f['ts'], zone), 'Role': f['role'].title(),
                                    'Side': f['side'].title(), 'Qty': f['qty'], 'Price': f['price']}
                                   for f in trade.fills]),
                     hide_index=True, width='stretch')


def following(journal, trade, annotation: dict, zone) -> None:
    """An open trade's numbers, after bringing a named journal's copy of the trade up to date
    (journals.follow); the watcher keeps the capture journal so."""
    journals.follow(journal, trade)
    numbers(journal, trade, annotation, zone)


# An open trade's numbers, chart and MAE / MFE are drawn again every LIVE from what has been stored by then, the
# rest of the page staying as it is. A closed trade's are drawn once.
live_numbers = st.fragment(run_every=LIVE)(following)
live_chart = st.fragment(run_every=LIVE)(chart_panel)
live_excursions = st.fragment(run_every=LIVE)(excursions_panel)
live_row = st.fragment(run_every=LIVE)(open_row)
