"""Stats: the headline numbers first, then charts of what the closed trades say. Descriptive, never a forecast.

One row holds the page title, Filters (with the saved filter every page uses) and Customize; the numbers
come straight after it, then a tab each for Overview, Compare, MAE / MFE and Runners, and Dashboards, your
own pages of widgets, always last. The equity curve counts trades along the bottom (1, 2, 3, ...) so nights
and weekends take no room; a toggle puts it on the clock instead, in the display time zone.

Compare finds an edge by tag, the way a trader drills down: chips for every tag category and the Rating stars
keep or leave out trades, then Totals and a table for each category side by side, every tag listed even at 0
trades, then the equity curve of the trades left and those trades, each opening on Trades. The tags kept can
be saved as a playbook.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import html
import itertools
from zoneinfo import ZoneInfo

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

import common
import theme
import runner_controls
import tag_controls
from views import preferences
from views.trades import REPORT
from common import money
from mwjournal.analytics import cost_summary, grouped, summary
from mwjournal.model import instrument_market
from mwjournal import units

SEGMENTS = ['Market', 'Exact contract', 'Entry hour', 'Weekday', 'Direction']
SECTIONS = ['Overview', 'Compare', 'MAE / MFE', 'Runners']
DASHBOARDS = 'Dashboards'          # always the last tab, so Customize can never hide it
AXES = {'trade': 'By Trade', 'time': 'By Date'}
LABELS = {'closed': 'Exit', 'net': 'Net P&L', 'mae': 'MAE', 'mfe': 'MFE', 'max_qty': 'Contracts',
          'symbol': 'Instrument'}
COMPARE_HELP = ('Start with Trades, the sample size, then Average Trade, the net result per trade after recorded '
                'fees. A trade with several tags counts in each, so tag rows overlap; "No Setup tag" is the trades '
                'with no tag in that category. Average R uses only trades with a stop. These describe your recorded '
                'trades, not proof of an edge: small samples mislead.')
TABLE_WIDTH = 360          # pixels a tag table needs at least: three to a row on a laptop screen, more on a wider one
SMALL_SAMPLE = 10          # fewer trades in a row than this is too few to judge (and 100 to 150 for the whole journal)
LIST_LIMIT = 50            # trades listed at once under the Compare tables; Show More Trades lists the next ones
RATING = 'Rating'          # a trade's 1 to 5 stars (Trades, the tag panel): one more group of chips and a table
# The period above Stats: the month under review first, as a monthly review reads (docs/research, 'Sample size first').
PERIODS = ('This Month', 'Last Month', 'Last 3 Months', 'All')
RATINGS = (5, 4, 3, 2, 1)  # best first
NEXT = {None: 'with', 'with': 'without', 'without': None}           # a chip's state after a click
# Material icons, like the star on a rating chip: IBM Plex Sans has no cross to go with its check mark.
ICONS = {'with': ':material/check:', 'without': ':material/close:'}
COMPARE_CSS = f"""<style>
[class*="st-key-edge-chip-"] button {{ min-height:30px; padding:2px 10px; gap:4px; background:transparent;
    border:1px solid {theme.BASELINE}; }}
[class*="st-key-edge-chip-"] button p {{ color:inherit !important; font-size:12.5px; }}
.st-key-edge-tag-chips .mwj-card-title {{ font-family:{theme.FONT}; font-size:12.5px; font-weight:650; }}
.st-key-edge-narrowed [data-testid="stCaptionContainer"] p {{ color:{theme.INK_2}; font-size:13px; }}
/* The tag tables share a row, as many as fit at TABLE_WIDTH or wider, all one width: a table alone on the last row
   keeps that width instead of stretching across the page. */
.st-key-edge-tables {{ display:grid !important; gap:14px !important;
    grid-template-columns:repeat(auto-fill, minmax(min({TABLE_WIDTH}px, 100%), 1fr)); }}
.st-key-edge-tables > [data-testid="stElementContainer"] {{ width:auto !important; min-width:0; }}
.mwj-edge-table {{ width:100%; border-collapse:collapse; background:{theme.SURFACE}; border:1px solid {theme.BORDER};
    font-family:{theme.FONT}; font-size:12.5px; line-height:1.3; color:{theme.INK}; {theme.TABULAR}; margin:0; }}
.mwj-edge-table th, .mwj-edge-table td {{ padding:5px 8px; text-align:right; white-space:nowrap;
    border:0; border-bottom:1px solid {theme.HAIRLINE}; }}
.mwj-edge-table th {{ color:{theme.MUTED}; font-size:11.5px; font-weight:500; }}
.mwj-edge-table th.t {{ font-size:12.5px; font-weight:650; }}
.mwj-edge-table .t {{ text-align:left; white-space:normal; }}
.mwj-edge-table tbody tr:last-child td {{ border-bottom:0; }}
.mwj-edge-table .up {{ color:{theme.UP}; }}
.mwj-edge-table .down {{ color:{theme.DOWN}; }}
.mwj-edge-table tr.small td.n, .mwj-edge-table tr.none td.n {{ color:{theme.MUTED}; }}
.mwj-edge-table tr.none td.t {{ opacity:.4; }}
</style>"""
# The trade list under the tables, as on Trades: an invisible button lies over each row and opens the trade.
LIST_CSS = f"""<style>
.st-key-edge-list {{ gap:2px !important; }}
[class*="st-key-edge-row-"] {{ position:relative; gap:0 !important; }}
.st-key-edge-list [data-testid="stMarkdownContainer"] {{ margin-bottom:0 !important; }}
[class*="st-key-edge-row-"] [data-testid="stElementContainer"]:has(.stButton) {{ position:absolute !important;
    inset:0; z-index:2; margin:0 !important; }}
[class*="st-key-edge-row-"] .stButton, [class*="st-key-edge-row-"] .stButton button {{ width:100%; height:100%;
    min-height:0 !important; padding:0; opacity:0; cursor:pointer; }}
[class*="st-key-edge-row-"] .stButton :has(button) {{ height:100%; }}
[class*="st-key-edge-row-"]:hover .mwj-edge-trade {{ background:{theme.SURFACE_2}; }}
[class*="st-key-edge-row-"]:has(button:focus-visible) .mwj-edge-trade {{ outline:2px solid {theme.ACCENT}; }}
.mwj-edge-trade {{ display:flex; flex-wrap:wrap; align-items:center; column-gap:12px; row-gap:3px; padding:5px 8px;
    border-radius:4px; border-bottom:1px solid {theme.HAIRLINE}; font-family:{theme.FONT}; font-size:12.5px;
    line-height:1.35; color:{theme.INK}; {theme.TABULAR}; }}
.mwj-edge-trade .w {{ min-width:9.6em; color:{theme.INK_2}; white-space:nowrap; }}
.mwj-edge-trade .c {{ min-width:4.4em; font-weight:600; white-space:nowrap; }}
.mwj-edge-trade .s {{ min-width:4.4em; color:{theme.INK_2}; white-space:nowrap; }}
.mwj-edge-trade .s b {{ font-weight:600; }}
.mwj-edge-trade .s .long {{ color:{theme.SERIES[0]}; }}
.mwj-edge-trade .s .short {{ color:{theme.SERIES[1]}; }}
.mwj-edge-trade .p {{ min-width:6.6em; text-align:right; font-weight:600; white-space:nowrap; }}
.mwj-edge-trade .r {{ min-width:5.2em; color:{theme.MUTED}; white-space:nowrap; }}
/* The tags take the rest of the row, or all of a second line when that is under 320px: the same for every row. */
.mwj-edge-trade .g {{ flex:1 1 320px; display:flex; flex-wrap:wrap; gap:4px; }}
.mwj-edge-trade .tag {{ padding:0 7px; border-radius:3px; font-size:11.5px; line-height:18px; white-space:nowrap;
    background:{theme.SURFACE_2}; color:{theme.INK_2}; }}
.mwj-edge-trade .mwj-win {{ color:{theme.UP}; }}
.mwj-edge-trade .mwj-loss {{ color:{theme.DOWN}; }}
.mwj-edge-trade .mwj-flat {{ color:{theme.INK_2}; }}
</style>"""


def render(journal, rows) -> None:
    """`rows` have passed the saved filter."""
    all_closed = [r for r in rows if r['status'] == 'closed' and r['net'] is not None]
    heading, filter_col, custom_col = st.columns([5, 1, 1], vertical_alignment='center')
    with filter_col.popover(preferences.filters_label(journal, rows), width='stretch',
                            key=common.key(journal, 'filters-menu'),
                            help='Narrow the stats by market, date, account, tag, playbook or rating; the saved filter '
                                 'here applies to every page.'):
        rows = preferences.filter_menu(journal, rows)
    with custom_col.popover('Customize', width='stretch', help='Choose which tabs Stats shows, and their order.'):
        customize(journal)
    rows = period_rows(journal, rows)
    closed = [r for r in rows if r['status'] == 'closed' and r['net'] is not None]
    with heading:
        count = f'{len(closed)} closed trade{"" if len(closed) == 1 else "s"}'
        theme.section('Stats', f'{len(closed)} of {len(all_closed)} closed trades' if len(closed) != len(all_closed) else count)
    # Without a closed trade there is nothing to count, but the dashboards (and their calendar) still open.
    sections = journal.display_settings()['dashboard_sections'] if closed else []
    if closed:
        stat = summary(closed)
        tiles(stat, closed[0]['currency'])
    else:
        st.info('Statistics need at least one closed round trip with a known multiplier.')
    labels = [*sections, DASHBOARDS]
    panels = dict(zip(labels, common.tabs(labels, common.key(journal, 'analytics-tabs'))))
    # Each tab is drawn only while it is open: each goes over every closed trade, and some read prices too.
    if panels[DASHBOARDS].open:                # every widget is a chart of its own
        from views import dashboard
        with panels[DASHBOARDS]:
            dashboard.render(journal, rows)
    if not closed:
        return
    if 'Overview' in panels and panels['Overview'].open:
        with panels['Overview']:
            overview(journal, closed, stat, pd.DataFrame(closed).sort_values('closed'))
    if 'Compare' in panels and panels['Compare'].open:
        with panels['Compare']:
            comparisons(journal, closed)
    if 'MAE / MFE' in panels and panels['MAE / MFE'].open:
        with panels['MAE / MFE']:
            excursion(journal, closed)
    if 'Runners' in panels and panels['Runners'].open:     # the comparison itself waits for its button
        with panels['Runners']:
            st.markdown('### Was There Room to Hold Longer?',
                        help='A separate hindsight check of the prices after your final exit. It is not the '
                             'MAE / MFE of the trade you actually took.')
            runner_controls.edge_panel(journal, closed)


def customize(journal) -> None:
    with st.form(common.key(journal, 'dashboard-preferences'), border=False):
        sections = st.multiselect('Tabs, in order', SECTIONS, default=journal.display_settings()['dashboard_sections'],
                                  help='Remove a tab to hide it; add it again to put it at the end. Dashboards '
                                       'is always last. Saved separately for Live and Replay.')
        if st.form_submit_button('Save Tabs'):
            try:
                journal.set_dashboard_sections(sections)
                st.rerun()
            except ValueError as exc:
                st.error(str(exc))


def tone(value) -> str:
    return 'positive' if value is not None and value > 0 else 'negative' if value is not None and value < 0 else 'flat'


def tiles(stat, currency) -> None:
    """The five numbers a trader looks for first, each explained on hover."""
    with st.container(key='stat-tiles'):
        cols = st.columns(5, gap='small')
        with cols[0].container(key='tile-net-' + tone(stat['net'])):
            st.metric('Net P&L', money(stat['net'], currency), border=True,
                      help='The total result of the closed trades in your filters, after recorded fees.')
        cols[1].metric('Win Rate', f"{stat['win_rate']:.1f}%", border=True,
                       help='The share of closed trades with a positive net result.')
        with cols[2].container(key='tile-average-' + tone(stat['expectancy'])):
            st.metric('Average Trade', money(stat['expectancy'], currency), border=True,
                      help='Net P&L divided by the number of closed trades, often called expectancy.')
        cols[3].metric('Profit Factor', f"{stat['profit_factor']:.2f}" if stat['profit_factor'] is not None else '—',
                       border=True, help='Everything won divided by everything lost. Shows — until there is a losing trade.')
        cols[4].metric('Largest Drawdown', money(stat['drawdown'], currency, signed=False), border=True,
                       help='The largest drop from a peak in the running total of closed trades. Not the drawdown '
                            'of an open position or of the account.')


# ---------------------------------------------------------------- the equity curve

def progress_chart(rows, lines, zone, *, axis='trade', currency=None, value=None, height=320) -> go.Figure:
    """A running total with one point per closed trade, in exit order.

    axis='trade' numbers the trades 1, 2, 3 along the bottom, so a night or a weekend takes no room;
    axis='time' places each one at its exit time in the display time zone. `lines` are (name, values,
    colour); the first carries the trade's date, time and result when hovered. `value` writes one
    trade's result; it defaults to money in `currency`.
    """
    value = value or (lambda v: money(v, currency))
    numbers = list(range(1, len(rows) + 1))
    by_trade = axis == 'trade'
    x = numbers if by_trade else common.wall_clock([r['closed'] for r in rows], zone)
    when = [common.moment(r['closed'], zone, seconds=False) for r in rows]
    figure = go.Figure()
    for index, (name, values, colour) in enumerate(lines):
        values = list(values)
        if index == 0:
            first = [f'{w} · {r["symbol"]}' if by_trade else f'Trade {n} · {r["symbol"]}'
                     for n, w, r in zip(numbers, when, rows)]
            data = [[f, value(r.get('value', r.get('net'))), value(v)] for f, r, v in zip(first, rows, values)]
            template = '%{customdata[0]}<br>This trade %{customdata[1]}<br>' + name + ' %{customdata[2]}<extra></extra>'
        else:
            data = [[value(v)] for v in values]
            template = name + ' %{customdata[0]}<extra></extra>'
        figure.add_trace(go.Scatter(x=x, y=values, name=name, customdata=data, hovertemplate=template,
                                    mode='lines+markers' if len(rows) <= 80 else 'lines',
                                    line=dict(color=colour, width=2 if index == 0 else 1.5,
                                              dash='solid' if index == 0 else 'dot'),
                                    marker=dict(size=5)))
    figure.add_hline(y=0, line_color=theme.BASELINE, line_width=1)
    if by_trade:
        # From half a trade before the first to half after the last: no empty 'trade 0' at the start.
        figure.update_xaxes(title_text='Trade number', tickformat=',d', range=[.5, max(len(rows), 1) + .5],
                            **({'dtick': 1} if len(rows) <= 20 else {}))
        try:
            figure.update_xaxes(unifiedhovertitle=dict(text='Trade %{x}'))     # the hover box's first line
        except ValueError:
            pass                           # an older Plotly: that line is the bare trade number
    else:
        figure.update_xaxes(title_text=f'Exit time · {zone}', hoverformat='%a, %b %-d, %-I:%M %p')
    if currency is not None:
        theme.money_axis(figure, currency, [v for _, values, _ in lines for v in values])
    figure.update_layout(height=height, showlegend=len(lines) > 1, legend_title_text='',
                         margin=dict(l=10, r=10, t=30 if len(lines) > 1 else 10, b=10))
    return figure


def axis_toggle(key: str, *, label_visibility='collapsed') -> str:
    """By Trade or By Date. The choice is kept under its own key for the session, so it is still there
    after a visit to another page (Streamlit forgets the value of a widget a page does not draw)."""
    chosen = key + '-chosen'
    st.session_state[key] = st.session_state.get(chosen) if st.session_state.get(chosen) in AXES else 'trade'

    def remember():
        st.session_state[chosen] = st.session_state.get(key) or 'trade'

    choice = st.segmented_control('Equity curve axis', list(AXES), format_func=AXES.get, required=True, key=key,
                                  on_change=remember, label_visibility=label_visibility,
                                  help='By Trade puts trades side by side, so nights and weekends take no room. '
                                       'By Date places each trade at its exit time in your display time zone.')
    return choice or 'trade'


def overview(journal, closed, stat, frame):
    zone = common.display_zone(journal)
    currency = closed[0]['currency']
    title, toggle = st.columns([3, 1.4], vertical_alignment='center')
    title.markdown('### Equity Curve', help='The running total of your closed trades: net after recorded fees, '
                                            'and gross before them. A rising line means profits are adding up.')
    with toggle:
        axis = axis_toggle(common.key(journal, 'equity-axis'))
    ordered = sorted(closed, key=lambda r: r['closed'])
    figure = progress_chart(ordered, [('Net total', list(itertools.accumulate(r['net'] for r in ordered)), theme.SERIES[0]),
                                      ('Gross total', list(itertools.accumulate(r['net'] if r.get('gross') is None else r['gross']
                                                                                for r in ordered)), theme.MUTED)],
                            zone, axis=axis, currency=currency)
    chart(journal, figure, 'equity', keep_layout=True)
    show_costs(closed, stat)
    st.markdown('### Trade Results', help='How often each net result occurred, one bar per range of results.')
    outcomes = px.histogram(frame, x='net', nbins=min(30, max(5, len(frame))), labels=LABELS)
    outcomes.update_traces(hovertemplate='%{x}<br>%{y} trades<extra></extra>', marker_color=theme.SERIES[0])
    outcomes.update_layout(yaxis_title='Trades', bargap=.05)
    theme.money_axis(outcomes, currency, frame.net, axis='xaxis')
    chart(journal, outcomes, 'outcomes', height=240)


def show_costs(closed, stat):
    """Gross, fees and R in one row of small numbers, each explained on hover."""
    costs = cost_summary(closed)
    currency = closed[0]['currency']
    with st.container(key='cost-tiles'):
        cols = st.columns(4)
        cols[0].metric('Gross P&L', money(costs['gross'], currency),
                       help='The result before recorded commissions and fees.')
        cols[1].metric('Fees', money(costs['fees'], currency, signed=False),
                       help='Recorded commissions and fees: gross minus net. This is not an estimate of slippage. '
                            'Missing fees are not proven zero, and platform fees can be estimates.')
        cols[2].metric('Fees Per Trade', money(costs['fee_per_trade'], currency, signed=False),
                       help='Recorded fees divided by the number of closed trades.')
        r_help = (f"Across {stat['risk_recorded']} trades with a stop." if stat['expectancy_r'] is not None
                  else 'Needs a stop: the stop order you place with a trade counts, or type it under Notes.')
        if costs['fee_per_r'] is not None:
            r_help += f" Recorded fees average {costs['fee_per_r']:.3f}R across {costs['risk_recorded']} trades."
        cols[3].metric('Average R', f"{stat['expectancy_r']:+.2f}R" if stat['expectancy_r'] is not None else '—',
                       help=r_help)
    if costs['unverified_fees']:
        count = costs['unverified_fees']
        # A Material warning icon: IBM Plex Sans has no '⚠', so the browser drew it in another font.
        st.caption(f":orange[:material/warning: Fees not confirmed on {count} trade{'' if count == 1 else 's'}]",
                   help='The platform did not confirm the commission on these trades, or reported only an estimate. '
                        'Net P&L and fees may be off by the difference.')


# ---------------------------------------------------------------- the other tabs

def excursion(journal, closed):
    unit = common.excursion_unit(journal)
    currency = closed[0]['currency']
    st.markdown('### How Far Did Trades Move?', help=units.description(unit))
    shown = pd.DataFrame(closed)
    measured = shown[shown.display_mfe.notna() & shown.display_mae.notna()].copy()
    # A point of one market is not worth a point of another: said where it applies, on the page.
    markets = ' · points and ticks differ by market: filter to one' if unit in ('points', 'ticks') and len(
        {instrument_market(s) for s in measured.symbol}) > 1 else ''
    st.caption(f'{len(measured)} of {len(closed)} closed trades measured{markets}',
               help='Only trades measured from recorded ticks with a value in your MAE / MFE Units (Settings); '
                    'unavailable values are left out, never counted as zero.')
    if measured.empty:
        st.info('No MAE / MFE values in these units yet. They need recorded prices; R also needs a stop, '
                'and ticks need a known tick size.')
        return
    measured['Net P&L'] = [money(v, currency) for v in measured.net]
    label = units.suffix(unit, currency)
    labels = {**LABELS, 'display_mae': f'Against me · MAE ({label})', 'display_mfe': f'In my favour · MFE ({label})'}
    figure = px.scatter(measured, x='display_mae', y='display_mfe', size='max_qty', labels=labels,
                        hover_data={'symbol': True, 'Net P&L': True, 'max_qty': True},
                        title='One dot per measured trade')
    if unit == 'currency':
        values = [*measured.display_mae, *measured.display_mfe]
        theme.money_axis(figure, currency, values, axis='xaxis')
        theme.money_axis(figure, currency, values)
    chart(journal, figure, 'excursions')


def comparisons(journal, closed):
    st.markdown(COMPARE_CSS, unsafe_allow_html=True)
    st.markdown('### What Worked Best?', help=COMPARE_HELP)
    currency = closed[0]['currency']
    library = journal.tag_library()
    groups = chip_groups(library, closed)
    narrowed = narrow(journal, closed, groups)
    options = [*SEGMENTS, 'Tags', 'Playbooks']
    segment = common.key(journal, 'edge-segment')
    remember = common.keep({segment: 'Tags'})          # still chosen after another tab has been open
    field = st.columns([1, 2])[0].selectbox('Compare by', options, key=segment, on_change=remember,
                                            help='Tags shows a table for each tag category and for your Rating; '
                                                 'times use your display time zone.')
    if not narrowed:
        st.info('No closed trade matches these chips. Click one again to change it, or Clear All.')
        return
    if field == 'Tags':
        tag_tables(narrowed, groups, currency)
    else:
        if field == 'Playbooks':
            grouped_rows = grouped(narrowed, 'playbooks')
        else:
            timezone = common.display_zone(journal)
            segmented = []
            for row in narrowed:
                opened = pd.Timestamp(row['opened']).tz_convert(timezone)
                value = {'Entry hour': f'{opened.hour:02d}:00', 'Weekday': opened.day_name(),
                         'Market': instrument_market(row['symbol']), 'Exact contract': row['symbol'],
                         'Direction': row['direction']}[field]
                segmented.append({**row, 'segment': value})
            grouped_rows = grouped(segmented, 'segment')
        # A playbook's name in its own colour, as a tag's is.
        colours = {book['name']: book.get('color') for book in library['playbooks'].values()} if field == 'Playbooks' else {}
        grouped_rows = [{**row, 'color': colours.get(row['group'])} for row in grouped_rows]
        # placeholder: the grid writes an empty number itself, as 'None', whatever the table's own '—' says.
        st.dataframe(painted(grouped_rows, field, currency), hide_index=True, width='stretch',
                     height=min(430, 36 + 35 * len(grouped_rows)), placeholder='—')
        small_sample_note()
    these_trades(journal, narrowed, groups)


def marks(row) -> set:
    """What a trade's chips can match: its tags' ids, and its Rating as 'rating-5' to 'rating-1'."""
    rating = row.get('rating')
    return {*row['tag_ids'], *([f'rating-{rating}'] if rating in RATINGS else [])}


def chip_groups(library, closed) -> dict:
    """What Compare narrows by and lists, in order: each tag category in the library's order, then Rating, as
    {group: [chip]}, a chip being {'id', 'name', 'label', 'color', 'retired'} (a rating's id is
    'rating-5' and so on, as marks() gives it). A category holds every active tag, and any retired one these
    trades carry, in its order; it is left out when none of these trades has one of its tags, as Rating is when
    none of them is rated."""
    carried = set().union(*(marks(row) for row in closed))
    groups = {}
    for group in sorted(library['groups'], key=lambda g: library['groups'][g]['order']):
        tags = sorted((t for t in library['tags'].values() if t['group'] == group
                       and (not t['retired'] or t['id'] in carried)), key=lambda t: t['order'])
        if any(t['id'] in carried for t in tags):
            groups[group] = [{'id': t['id'], 'name': t['name'], 'label': t['name'], 'color': t['color'],
                              'retired': t['retired']} for t in tags]
    if any(f'rating-{n}' in carried for n in RATINGS):
        # A Material star icon on the chip: IBM Plex Sans has no '★', so the browser drew it in another font.
        groups[RATING] = [{'id': f'rating-{n}', 'name': stars(n), 'label': f'{n} :material/star:',
                           'color': theme.RATING_COLOURS[n], 'retired': False} for n in RATINGS]
    return groups


def stars(n) -> str:
    return f'{n} star' + ('' if n == 1 else 's')


def listing(names) -> str:
    """'A', 'A and B', 'A, B and C'."""
    return ' and '.join(names) if len(names) < 3 else ', '.join(names[:-1]) + ' and ' + names[-1]


def narrow(journal, closed, groups) -> list[dict]:
    """'Narrow the trades': a row of chips for each group, the active tags and Ratings these trades carry. A
    click keeps only the trades with that chip (✓), the next leaves those trades out (×), the third stops using
    it. Returns the trades with every kept chip and no left-out one, for everything below on this tab; Filters
    have already narrowed the page. A sentence under the chips says what is applied."""
    marked = [(row, marks(row)) for row in closed]
    carried = set().union(*(mine for _, mine in marked))
    offered = {group: [c for c in chips if c['id'] in carried and not c['retired']] for group, chips in groups.items()}
    offered = {group: chips for group, chips in offered.items() if chips}
    if not offered:
        return closed
    state = common.key(journal, 'edge-chips')                  # {chip id: 'with' or 'without'}
    known = {c['id'] for chips in offered.values() for c in chips}
    # A chip the page no longer offers (the Filters changed) stops counting: nothing stays applied unseen.
    chosen = st.session_state[state] = {id: s for id, s in st.session_state.get(state, {}).items() if id in known}
    keys = {id: common.key(journal, 'edge-chip-' + hashlib.sha256(id.encode()).hexdigest()[:12]) for id in known}

    def click(id):
        before = st.session_state.get(state, {})
        after = {**before, id: NEXT[before.get(id)]}
        st.session_state[state] = {k: v for k, v in after.items() if v}

    def clear():
        st.session_state[state] = {}

    colours = {group: theme.INK_2 if group == RATING else tag_controls.group_colour(group, chips)
               for group, chips in offered.items()}
    css = [tag_controls.headers_css(colours)]
    for chips in offered.values():
        for chip in chips:
            ink, colour, selector = theme.ink_on(chip['color']), chip['color'], f'.st-key-{keys[chip["id"]]} button'
            if ink is None:
                continue
            css.append({'with': f'<style>{selector} {{background:{colour} !important; border-color:{colour} !important; '
                                f'color:{ink} !important; font-weight:600;}}</style>',
                        'without': f'<style>{selector} {{border:1px dashed {colour} !important; color:{theme.INK_2} '
                                   f'!important;}} {selector} p {{text-decoration:line-through;}}</style>',
                        None: f'<style>{selector}:hover {{border-color:{colour} !important;}}</style>'}[chosen.get(chip['id'])])
    # One stylesheet: only an element holding a lone <style> takes no room on the page (theme.CSS).
    st.markdown(''.join(css).replace('</style><style>', ''), unsafe_allow_html=True)
    theme.subsection('Narrow the trades', help='Click a chip once to keep only the trades with it (✓), again to '
                                               'leave those trades out (×), and a third time to stop using it. Every '
                                               'table, the equity curve and the trade list below then use the trades '
                                               'left. Leaving out is how to drop, say, your 1-star trades: a trade '
                                               'has one rating. Filters still apply first.')
    with st.container(key='edge-tag-chips', horizontal=True, gap='medium'):
        for group, chips in offered.items():
            with st.container(key=theme.tag_group_key(group), width='content', gap='small'):
                st.markdown(f"<div class='mwj-card-title'>{html.escape(group)}</div>", unsafe_allow_html=True)
                with st.container(horizontal=True, gap='small'):
                    for chip in chips:
                        st.button(chip['label'], key=keys[chip['id']], on_click=click, args=(chip['id'],),
                                  icon=ICONS.get(chosen.get(chip['id'])))
    if not chosen:
        return closed
    kept = {c['id'] for c in chips_in(offered) if chosen.get(c['id']) == 'with'}
    left_out = {c['id'] for c in chips_in(offered) if chosen.get(c['id']) == 'without'}
    shown = [row for row, mine in marked if kept <= mine and not left_out & mine]
    applied = ', '.join(f'{word} {listing([c["name"] for c in chips_in(offered) if c["id"] in ids])}'
                        for word, ids in (('with', kept), ('without', left_out)) if ids)
    with st.container(horizontal=True, vertical_alignment='center', gap='small', key='edge-narrowed'):
        st.caption(common.md(f'{applied[0].upper()}{applied[1:]} · {len(shown)} of {len(closed)} closed trades'),
                   width='content')
        st.button('Clear All', key=common.key(journal, 'edge-chips-clear'), on_click=clear, type='tertiary',
                  help='Stops using every chip: back to all the trades Filters keep.')
        tags = [(group, c) for group, chips in offered.items() if group != RATING for c in chips if c['id'] in kept]
        if tags:
            playbook_saver(journal, tags)
    return shown


def chips_in(groups) -> list[dict]:
    return [chip for chips in groups.values() for chip in chips]


def playbook_saver(journal, tags) -> None:
    """Save as Playbook: the tags kept (✓), as (category, chip), become a playbook with a name and a colour.
    A playbook takes any one of its tags in a category, so it takes one kept tag per category here."""
    with st.popover('Save as Playbook', help='Saves the kept tags (✓) as a playbook for Trades and Compare by '
                                             'Playbooks; left-out tags (×) and Rating chips are not part of a playbook.'):
        rules = {}
        for group, chip in tags:
            rules.setdefault(group, []).append(chip['id'])
        crowded = next((group for group, ids in rules.items() if len(ids) > 1), None)
        if crowded:
            st.info(f'Keep one {crowded} tag to save a playbook: a playbook takes any one of its tags in a '
                    'category, not all of them.')
            return
        with st.form(common.key(journal, 'edge-save-playbook'), border=False):
            name = st.text_input('Playbook name', max_chars=64, placeholder='A+')
            colour = st.color_picker('Playbook color', tags[0][1]['color'])
            if st.form_submit_button('Save Playbook', type='primary'):
                try:
                    journal.save_playbook(name, rules, color=colour)
                    st.success(f'Saved {name.strip()}. Apply it to a trade under its tags, or compare it under '
                               'Compare by Playbooks.')
                except ValueError as exc:
                    st.error(str(exc))


def line(name, colour, trades) -> dict:
    """One row of a Compare table: `name` (in `colour`, if any) and the summary of `trades`, empty at 0."""
    stat = summary(trades)
    return {'group': name, 'color': colour, **stat, 'net': stat['net'] if trades else None}


def tag_tables(narrowed, groups, currency) -> None:
    """Totals (all, wins and losses), then a table for each group: every tag of a category in order, even at 0
    trades, then the trades with none of them; Rating from 5 stars to 1, then Not rated. A trade with
    two tags of a category counts in both. Side by side on a wide window, fewer to a row on a narrow one."""
    if not groups:
        st.info('None of these trades has a tag or a rating yet. Tag and rate trades on Trades, then compare them here.')
        return
    tables = [('Totals', theme.INK, [line('All trades', None, narrowed),
                                     line('Wins', theme.UP, [r for r in narrowed if r['net'] > 0]),
                                     line('Losses', theme.DOWN, [r for r in narrowed if r['net'] < 0])])]
    marked = [(row, marks(row)) for row in narrowed]
    for group, chips in groups.items():
        trades = {c['id']: [] for c in chips}
        none = []
        for row, mine in marked:
            hit = mine.intersection(trades)
            for id in hit:
                trades[id].append(row)
            if not hit:
                none.append(row)
        rows = [line(c['name'], c['color'], trades[c['id']]) for c in chips]
        if none:
            # 'No Mindset tag'; Other tags, where typed tags go, reads 'No Other tag', not 'No Other tags tag'.
            rows.append(line('Not rated' if group == RATING else f'No {group.removesuffix(" tags")} tag', None, none))
        tables.append((group, theme.INK_2 if group == RATING else tag_controls.group_colour(group, chips), rows))
    with st.container(key='edge-tables', horizontal=True):
        for title, colour, rows in tables:
            st.markdown(table_html(title, colour, rows, currency), unsafe_allow_html=True)
    small_sample_note()


def table_html(title, colour, rows, currency) -> str:
    """One compact table, the way a trader reads it down a page: the title over the names, then Trades, Net
    P&L, Win % and Average Trade in whole amounts. Each name in its colour, faded when no trade has it; a
    result green or red; the numbers of a row with fewer than SMALL_SAMPLE trades grey."""
    def amount(value):
        if value is None:
            return "<td class='n'>—</td>"
        tone = ' up' if value > 0 else ' down' if value < 0 else ''
        return f"<td class='n{tone}'>{html.escape(money(value, currency, decimals=0)).replace('$', '&#36;')}</td>"

    body = []
    for row in rows:
        ink = theme.ink_on(row['color'])
        style = f" style='background:{row['color']};color:{ink}'" if ink else ''
        state, tip = (('none', 'None of these trades') if not row['count'] else
                      ('small', f'Fewer than {SMALL_SAMPLE} trades: too few to judge') if row['count'] < SMALL_SAMPLE
                      else ('', ''))
        body.append(f"<tr class='{state}'{f' title=\"{tip}\"' if tip else ''}><td class='t'{style}>"
                    f"{html.escape(row['group'])}</td><td class='n'>{row['count']}</td>{amount(row['net'])}"
                    f"<td class='n'>{'—' if row['win_rate'] is None else format(row['win_rate'], '.1f') + '%'}</td>"
                    f"{amount(row['expectancy'])}</tr>")
    return (f"<table class='mwj-edge-table'><thead><tr><th class='t' style='color:{colour}'>{html.escape(title)}</th>"
            "<th>Trades</th><th>Net P&amp;L</th><th>Win %</th><th>Average Trade</th></tr></thead>"
            f"<tbody>{''.join(body)}</tbody></table>")


def painted(rows, field, currency):
    """compare_table() with a name that has a colour (a playbook's) in it, in a text colour that reads on it,
    and the numbers of a row with fewer than SMALL_SAMPLE trades in grey, as in the tag tables."""
    def paint(frame):
        styles = pd.DataFrame('', index=frame.index, columns=frame.columns)
        for index, row in enumerate(rows):
            ink = theme.ink_on(row.get('color'))
            if ink:
                styles.iloc[index, 0] = f'background-color: {row["color"]}; color: {ink}'
            if row['count'] < SMALL_SAMPLE:
                styles.iloc[index, 1:] = f'color: {theme.MUTED}'
        return styles
    return compare_table(rows, field, currency).apply(paint, axis=None)


def small_sample_note() -> None:
    st.caption(f'Grey numbers: fewer than {SMALL_SAMPLE} trades, too few to judge.',
               help=f'A row needs about {SMALL_SAMPLE} trades before its numbers mean much, and your journal 100 to '
                    '150 trades in all before you judge what works. A faded tag has none of these trades: every tag '
                    'stays listed, so you see what narrowing leaves out.')


def these_trades(journal, narrowed, groups) -> None:
    """Under the tables: the equity curve of the trades the chips leave, then those trades, newest first, each
    opening on Trades."""
    zone = common.display_zone(journal)
    currency = narrowed[0]['currency']
    ordered = sorted(narrowed, key=lambda r: r['closed'])
    st.markdown('### Equity Curve of These Trades',
                help='The running total of the trades the chips leave, after recorded fees, in exit order. As you '
                     'take away what does not work, it should rise more steadily.')
    chart(journal, progress_chart(ordered, [('Net total', list(itertools.accumulate(r['net'] for r in ordered)),
                                             theme.SERIES[0])], zone, currency=currency, height=260),
          'compare-equity', keep_layout=True)
    limit_key = common.key(journal, 'edge-list-limit')
    limit = st.session_state.get(limit_key, LIST_LIMIT)
    newest = ordered[::-1]
    st.markdown('### These Trades', help='Newest first. Click a trade to open it on Trades.')
    chips = {c['id']: c for c in chips_in(groups) if not c['id'].startswith('rating-')}
    st.markdown(LIST_CSS, unsafe_allow_html=True)
    with st.container(key='edge-list', gap=None):
        for row in newest[:limit]:
            with st.container(key='edge-row-' + hashlib.sha256(row['id'].encode()).hexdigest()[:16], gap=None):
                st.markdown(trade_html(row, chips, zone), unsafe_allow_html=True)
                st.button(common.md(f'{common.moment(row["opened"], zone, seconds=False)} {row["symbol"]} '
                                    f'{row["direction"].title()} {row["max_qty"]:g} {money(row["net"], row["currency"])}'),
                          key=common.key(journal, 'edge-open-' + row['id']), on_click=open_trade,
                          args=(journal, row['id']), width='stretch')
    if len(newest) > limit:
        st.button('Show More Trades', key=common.key(journal, 'edge-list-more'),
                  on_click=lambda: st.session_state.update({limit_key: limit + LIST_LIMIT}))


def trade_html(row, chips, zone) -> str:
    """One trade of the list: when, contract, side and size, net P&L, rating, then its tags in their colours."""
    mine = set(row['tag_ids'])
    tags = ''.join(f"<span class='tag' style='background:{c['color']};color:{theme.ink_on(c['color'])}'>"
                   f"{html.escape(c['name'])}</span>" if theme.ink_on(c['color']) else
                   f"<span class='tag'>{html.escape(c['name'])}</span>" for id, c in chips.items() if id in mine)
    rating = row.get('rating')
    rated = (f"<span class='r' style='color:{theme.RATING_COLOURS[rating]}'>{stars(rating)}</span>"
             if rating in RATINGS else "<span class='r'>Not rated</span>")
    tone = 'mwj-win' if row['net'] > 0 else 'mwj-loss' if row['net'] < 0 else 'mwj-flat'
    side = 'Long' if row['direction'] == 'long' else 'Short'
    return (f"<div class='mwj-edge-trade'><span class='w'>{common.moment(row['opened'], zone, seconds=False)}</span>"
            f"<span class='c'>{html.escape(row['symbol'])}</span>"
            f"<span class='s'><b class='{row['direction']}'>{side}</b> {row['max_qty']:g}</span>"
            f"<span class='p {tone}'>{html.escape(money(row['net'], row['currency'])).replace('$', '&#36;')}</span>"
            f"{rated}<span class='g'>{tags}</span></div>")


def open_trade(journal, id) -> None:
    """Shows this trade on Trades, as a click on its row there does: not a Daily Report Card."""
    st.session_state.update(common.navigation('Trades'))
    st.session_state[common.key(journal, 'selected-trade')] = id
    st.session_state[common.key(journal, REPORT)] = False


def period_rows(journal, rows) -> list[dict]:
    """The trades of the period chosen above the stats, by their entry day in the display time zone: this month,
    last month, the last three months (this one and the two before) or all. Every tab of Stats follows it."""
    key = common.key(journal, 'stats-period')
    remember = common.keep({key: 'All'})            # still chosen after Trades or Settings was open
    choice = st.segmented_control('Period', PERIODS, key=key, on_change=remember, required=True,
                                  label_visibility='collapsed',
                                  help='Which trades Stats counts, by the day each was entered. A monthly review '
                                       'starts with Last Month.')
    if choice in (None, 'All'):
        return rows
    first = dt.datetime.now(ZoneInfo(common.display_zone(journal))).date().replace(day=1)
    if choice == 'This Month':
        start, end = first, None
    elif choice == 'Last Month':
        start, end = (first - dt.timedelta(days=1)).replace(day=1), first
    else:                                                       # this month and the two before
        start = first
        for _ in range(2):
            start = (start - dt.timedelta(days=1)).replace(day=1)
        end = None
    return [r for r in rows if r['entry_day'] >= start.isoformat() and (end is None or r['entry_day'] < end.isoformat())]


def compare_table(grouped_rows, field, currency):
    """The comparison as a table: money reads '+$810.02', Average R '+0.50R' or '—' without a stop,
    and every number still sorts as a number."""
    columns = {'group': field.rstrip('s') if field in ('Tags', 'Playbooks') else field, 'count': 'Trades',
               'net': 'Net P&L', 'win_rate': 'Win %', 'expectancy': 'Average Trade',
               'avg_win': 'Average Winner', 'avg_loss': 'Average Loser', 'expectancy_r': 'Average R',
               'risk_recorded': 'Trades With Risk'}
    table = pd.DataFrame(grouped_rows, columns=list(columns)).rename(columns=columns)
    table['Average R'] = pd.to_numeric(table['Average R'], errors='coerce')
    styled = common.money_table(table, currency, results=('Net P&L', 'Average Trade', 'Average Winner', 'Average Loser'))
    return (styled.format(lambda v: '—' if v is None or v != v else f'{v:+.2f}R', subset=['Average R'])
            .format(lambda v: '—' if v is None or v != v else f'{v:.1f}%', subset=['Win %']))   # as the Win Rate card


def chart(journal, figure, name: str, *, height=320, keep_layout=False) -> None:
    if not keep_layout:
        # Room above the plot for both the title and the legend row, which otherwise print over each other.
        figure.update_layout(height=height, legend_title_text='', margin_t=64 if figure.layout.title.text else 24)
    st.plotly_chart(figure, width='stretch', config=theme.PLOTLY_CONFIG, key=common.key(journal, 'edge-' + name))
