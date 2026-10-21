"""Stats: the headline numbers first, then charts of what the closed trades say. Descriptive, never a forecast.

One row holds the page title, Filters (with the saved filter every page uses) and Customize; the numbers
come straight after it, then a tab each for Overview, Compare, MAE / MFE and Runners, and Dashboards, your
own pages of widgets, always last. The equity curve counts trades along the bottom (1, 2, 3, ...) so nights
and weekends take no room; a toggle puts it on the clock instead, in the display time zone.
"""

from __future__ import annotations

import itertools

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

import common
import theme
import runner_controls
from views import preferences
from common import money
from mwjournal.analytics import cost_summary, grouped, summary
from mwjournal.model import instrument_market
from mwjournal import units

SEGMENTS = ['Market', 'Exact contract', 'Entry hour', 'Weekday', 'Direction', 'Followed my plan', 'Process rating']
SECTIONS = ['Overview', 'Compare', 'MAE / MFE', 'Runners']
DASHBOARDS = 'Dashboards'          # always the last tab, so Customize can never hide it
AXES = {'trade': 'By Trade', 'time': 'By Date'}
LABELS = {'closed': 'Exit', 'net': 'Net P&L', 'mae': 'MAE', 'mfe': 'MFE', 'max_qty': 'Size',
          'precision': 'Measured from', 'symbol': 'Instrument', 'setup': 'Setup', 'giveback': 'Giveback'}
COMPARE_HELP = ('Start with Trades, the sample size, then Average Trade, the net result per trade after recorded '
                'fees. A trade with several tags counts in each, so tag rows overlap. Average R uses only trades '
                'with a saved planned risk. These describe your recorded trades, not proof of an edge: small '
                'samples mislead.')


def render(journal, rows) -> None:
    """`rows` have passed the saved filter."""
    all_closed = [r for r in rows if r['status'] == 'closed' and r['net'] is not None]
    heading, filter_col, custom_col = st.columns([5, 1, 1], vertical_alignment='center')
    with filter_col.popover('Filters', width='stretch',
                            help='Narrow the stats by market, date, account, tag, playbook or rating. The saved '
                                 'filter here applies to every page.'):
        rows = preferences.filter_menu(journal, rows)
    with custom_col.popover('Customize', width='stretch', help='Choose which tabs Stats shows, and their order.'):
        customize(journal)
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
    if panels[DASHBOARDS].open:                # drawn only when open: every widget is a chart of its own
        from views import dashboard
        with panels[DASHBOARDS]:
            dashboard.render(journal, rows)
    if not closed:
        return
    frame = pd.DataFrame(closed).sort_values('closed')
    if 'Overview' in panels:
        with panels['Overview']:
            overview(journal, closed, stat, frame)
    if 'Compare' in panels:
        with panels['Compare']:
            comparisons(journal, closed)
    if 'MAE / MFE' in panels:
        with panels['MAE / MFE']:
            excursion(journal, closed)
    if 'Runners' in panels:
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
        r_help = (f"Across {stat['risk_recorded']} trades with a saved planned risk." if stat['expectancy_r'] is not None
                  else 'Needs a planned risk saved on the trade, under Notes.')
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
    st.markdown('### How Far Did Trades Move?', help=(
        units.description(unit) + ' Left is less movement against you; up is more movement in your favour. '
        'Colour shows how the move was measured, not a win or loss. Giveback = peak gross position P&L minus '
        'final gross P&L, always in account currency; it is not a guaranteed alternative exit.'
        + (' Points and ticks are not worth the same in every market: filter to one market at a time.'
           if unit in ('points', 'ticks') else '')))
    shown = pd.DataFrame(common.display_excursion_rows(journal, closed))
    measured = shown[shown.display_mfe.notna() & shown.display_mae.notna()].copy()
    st.caption(f'{len(measured)} of {len(closed)} closed trades measured',
               help='Only trades with a price path that meets your MAE / MFE Units and Accuracy (Setup and Setup → '
                    'Advanced). Unavailable values are left out, never counted as zero.')
    if measured.empty:
        st.info('No MAE / MFE values in these units yet. They need recorded prices; R also needs a planned risk, '
                'and ticks need a known tick size.')
        return
    measured['giveback'] = (measured.mfe - measured.gross).clip(lower=0)
    measured['Net P&L'] = [money(v, currency) for v in measured.net]
    measured['Giveback'] = [money(v, currency, signed=False) for v in measured.giveback]
    label = units.suffix(unit, currency)
    labels = {**LABELS, 'display_mae': f'Against me · MAE ({label})',
              'display_mfe': f'In my favour · MFE ({label})', 'display_precision': 'Measured from'}
    figure = px.scatter(measured, x='display_mae', y='display_mfe', color='display_precision', size='max_qty',
                        labels=labels, hover_data={'symbol': True, 'Net P&L': True, 'setup': True, 'Giveback': True,
                                                   'max_qty': True},
                        title='One dot per measured trade')
    if unit == 'currency':
        values = [*measured.display_mae, *measured.display_mfe]
        theme.money_axis(figure, currency, values, axis='xaxis')
        theme.money_axis(figure, currency, values)
    chart(journal, figure, 'excursions')


def comparisons(journal, closed):
    st.markdown('### What Worked Best?', help=COMPARE_HELP)
    options = [*SEGMENTS, 'Tags', 'Setups', 'Playbooks']
    field = st.columns([1, 2])[0].selectbox('Compare by', options, index=options.index('Tags'),
                                            key=common.key(journal, 'edge-segment'),
                                            help='Times use your display time zone.')
    if field in ('Tags', 'Setups', 'Playbooks'):
        grouped_rows = grouped(closed, {'Tags': 'tags', 'Setups': 'setup', 'Playbooks': 'playbooks'}[field])
    else:
        timezone = common.display_zone(journal)
        segmented = []
        for row in closed:
            opened = pd.Timestamp(row['opened']).tz_convert(timezone)
            value = {'Entry hour': f'{opened.hour:02d}:00', 'Weekday': opened.day_name(),
                     'Market': instrument_market(row['symbol']), 'Exact contract': row['symbol'], 'Direction': row['direction'],
                     'Followed my plan': row.get('adherence', 'Unreviewed'),
                     'Process rating': str(row.get('rating', 'Unreviewed'))}[field]
            segmented.append({**row, 'segment': value})
        grouped_rows = grouped(segmented, 'segment')
    table = compare_table(grouped_rows, field, closed[0]['currency'])
    st.dataframe(table, hide_index=True, width='stretch', height=min(430, 36 + 35 * len(table.data)))


def compare_table(grouped_rows, field, currency):
    """The comparison as a table: money reads '+$810.02', Average R '+0.50R' or '—' without a planned
    risk, and every number still sorts as a number."""
    columns = {'group': field.rstrip('s') if field in ('Tags', 'Setups', 'Playbooks') else field, 'count': 'Trades',
               'net': 'Net P&L', 'win_rate': 'Win %', 'expectancy': 'Average Trade',
               'avg_win': 'Average Winner', 'avg_loss': 'Average Loser', 'expectancy_r': 'Average R',
               'risk_recorded': 'Trades With Risk'}
    table = pd.DataFrame(grouped_rows, columns=list(columns)).rename(columns=columns)
    table['Average R'] = pd.to_numeric(table['Average R'], errors='coerce')
    styled = common.money_table(table, currency, results=('Net P&L', 'Average Trade', 'Average Winner', 'Average Loser'))
    return styled.format(lambda v: '—' if v is None or v != v else f'{v:+.2f}R', subset=['Average R'])


def chart(journal, figure, name: str, *, height=320, keep_layout=False) -> None:
    if not keep_layout:
        # Room above the plot for both the title and the legend row, which otherwise print over each other.
        figure.update_layout(height=height, legend_title_text='', margin_t=64 if figure.layout.title.text else 24)
    st.plotly_chart(figure, width='stretch', config=theme.PLOTLY_CONFIG, key=common.key(journal, 'edge-' + name))
