"""Edge: what the closed trades say about setups, tags, timing and exits. Descriptive, never a forecast."""

from __future__ import annotations

import pandas as pd
import plotly.express as px
import streamlit as st

import common
import theme
import runner_controls
from common import money
from mwjournal.analytics import cost_summary, grouped, summary
from mwjournal.model import instrument_market
from mwjournal import units

SEGMENTS = ['Market', 'Exact contract', 'Entry hour', 'Weekday', 'Direction', 'Followed my plan', 'Process rating']
LABELS = {'closed': 'Closed (UTC)', 'net': 'Net P&L', 'mae': 'MAE', 'mfe': 'MFE', 'max_qty': 'Size',
          'precision': 'Measured from', 'symbol': 'Instrument', 'setup': 'Setup', 'giveback': 'Giveback'}


def render(journal, rows) -> None:
    theme.section('Analytics', 'your results, then the choices behind them')
    with st.popover('Analytics filters'):
        rows = common.filter_rows(journal, rows)
    with st.popover('Customize dashboard'):
        with st.form(common.key(journal, 'dashboard-preferences')):
            sections = st.multiselect('Dashboard sections · ordered', ['Overview', 'Compare', 'MAE / MFE', 'Runners'],
                                       default=journal.display_settings()['dashboard_sections'])
            st.caption('Remove a section to hide it. Re-add it to move it to the end. Saved separately for Live and Replay.')
            if st.form_submit_button('Save dashboard'):
                try:
                    journal.set_dashboard_sections(sections)
                    st.rerun()
                except ValueError as exc:
                    st.error(str(exc))
    if st.button('Refresh trades', key=common.key(journal, 'edge-refresh')):
        st.rerun()
    closed = [r for r in rows if r['status'] == 'closed' and r['net'] is not None]
    if not closed:
        st.info('Statistics need at least one closed round trip with a known multiplier.')
        return
    stat = summary(closed)
    cols = st.columns(4)
    cols[0].metric('Net after recorded fees', money(stat['net']), help='Total result of the closed trades in your filters.')
    cols[1].metric('Average profit / trade', money(stat['expectancy']),
                   help='Total net result divided by the number of closed trades. Often called expectancy.')
    cols[2].metric('Win rate', f"{stat['win_rate']:.1f}%", help='Percentage of closed trades with positive net P&L.')
    cols[3].metric('Largest drawdown', f"{stat['drawdown']:,.2f}",
                   help='Largest drop from a peak in cumulative CLOSED-trade P&L; not open-position/account drawdown.')
    st.caption(f"{stat['count']} closed trades · results in {closed[0]['currency']} · "
               'the unit selector changes excursions, not actual profit/loss.')
    sections = journal.display_settings()['dashboard_sections']
    panels = dict(zip(sections, st.tabs(sections, key=common.key(journal, 'analytics-tabs'))))
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
            st.markdown('### Was there room to hold my final exit longer?')
            st.caption('A separate hindsight hypothesis after exit—not the MAE/MFE of the trade you actually took.')
            runner_controls.edge_panel(journal, closed)


def overview(journal, closed, stat, frame):
        st.markdown('### How did I do?')
        st.caption('Your cumulative results before and after recorded fees. A rising line means profits are accumulating.')
        frame['Gross equity'] = frame.gross.cumsum()
        frame['Net equity'] = frame.net.cumsum()
        chart(journal, px.line(frame, x='closed', y=['Gross equity', 'Net equity'], markers=True,
                               labels={**LABELS, 'value': f'Closed-trade P&L ({closed[0]["currency"]})', 'variable': 'Basis'},
                               title='Cumulative closed-trade results'), 'equity')
        show_costs(journal, closed, stat)
        with st.expander('Profit and loss distribution'):
            chart(journal, px.histogram(frame, x='net', nbins=min(30, max(5, len(frame))), labels=LABELS,
                                        title='How often each trade outcome occurred'), 'outcomes')


def excursion(journal, closed):
        st.markdown('### How far did trades move against me or in my favour?')
        unit = common.excursion_unit(journal)
        st.caption(units.description(unit))
        shown = pd.DataFrame(common.display_excursion_rows(journal, closed))
        measured = shown[shown.display_mfe.notna() & shown.display_mae.notna()].copy()
        st.caption(f'{len(measured)} of {len(closed)} closed trades qualify under your accuracy and unit settings. '
                   'Unavailable observations are excluded, never counted as zero.')
        if measured.empty:
            st.info('No MAE/MFE values are available in the selected units. Prices are required; '
                    'R also needs planned risk, and ticks need a known tick size.')
        else:
            measured['giveback'] = (measured.mfe - measured.gross).clip(lower=0)
            label = units.suffix(unit, closed[0]['currency'])
            labels = {**LABELS, 'display_mae': f'Against me · MAE ({label})',
                      'display_mfe': f'In my favour · MFE ({label})', 'display_precision': 'Measurement quality',
                      'giveback': f'Giveback ({closed[0]["currency"]})'}
            chart(journal, px.scatter(measured, x='display_mae', y='display_mfe', color='display_precision', size='max_qty',
                                      labels=labels, hover_data=['symbol', 'net', 'setup', 'giveback'],
                                      title='One dot per measured trade'), 'excursions')
            st.caption('Left is less adverse movement; up is more favourable movement. Colour indicates data quality, not a win/loss.')
            st.caption('Giveback = peak gross position P&L minus final gross P&L, always in account currency. Estimates '
                       'and platform measurements stay visibly separate; this is not a guaranteed alternative exit.')
            if unit in ('points', 'ticks'):
                st.caption('Use Markets above to compare one instrument at a time; points/ticks are not equally valuable across markets.')


def show_costs(journal, closed, stat):
    with st.expander('Fees, profit factor and risk details'):
        costs = cost_summary(closed)
        cols = st.columns(3)
        cols[0].metric('Gross before recorded fees', money(costs['gross']))
        cols[1].metric('Recorded fee drag', f"{costs['fees']:,.2f}")
        cols[2].metric('Fees / closed trade', f"{costs['fee_per_trade']:,.2f}" if costs['fee_per_trade'] is not None else '—')
        if stat['profit_factor'] is not None:
            st.caption(f"Profit factor {stat['profit_factor']:.2f} · total winning P&L ÷ absolute total losing P&L.")
        if stat['expectancy_r'] is not None:
            st.caption(f"Average result {stat['expectancy_r']:+.2f}R across {stat['risk_recorded']} trades with saved planned risk.")
        if costs['fee_per_r'] is not None:
            st.caption(f"Recorded fees average {costs['fee_per_r']:.3f}R across {costs['risk_recorded']} trades with planned initial risk.")
        st.caption('Gross minus net is recorded commissions/fees, not an estimate of slippage. Missing fees '
                   'are not proven zero; native fees may be platform estimates. These are closed-trade results, not marked account equity.')
        if costs['unverified_fees']:
            st.warning(f"{costs['unverified_fees']} trades have missing or unverified fees; net results and fee drag may be incomplete.")


def comparisons(journal, closed):
    st.markdown('### What worked best in my recorded trades?')
    options = [*SEGMENTS, 'Tags', 'Setups', 'Playbooks']
    field = st.selectbox('Compare edge by', options, index=options.index('Tags'), key=common.key(journal, 'edge-segment'))
    if field in ('Tags', 'Setups', 'Playbooks'):
        grouped_rows = grouped(closed, {'Tags': 'tags', 'Setups': 'setup', 'Playbooks': 'playbooks'}[field])
    else:
        timezone = st.selectbox('Analysis timezone', common.TIMEZONES, key=common.key(journal, 'edge-tz'))
        segmented = []
        for row in closed:
            opened = pd.Timestamp(row['opened']).tz_convert(timezone)
            value = {'Entry hour': f'{opened.hour:02d}:00', 'Weekday': opened.day_name(),
                     'Market': instrument_market(row['symbol']), 'Exact contract': row['symbol'], 'Direction': row['direction'],
                     'Followed my plan': row.get('adherence', 'Unreviewed'),
                     'Process rating': str(row.get('rating', 'Unreviewed'))}[field]
            segmented.append({**row, 'segment': value})
        grouped_rows = grouped(segmented, 'segment')
    columns = {'group': field.rstrip('s') if field in ('Tags', 'Setups', 'Playbooks') else field, 'count': 'Trades',
               'net': 'Net P&L', 'win_rate': 'Win %', 'expectancy': 'Average / trade',
               'avg_win': 'Average winner', 'avg_loss': 'Average loser', 'expectancy_r': 'Average R',
               'risk_recorded': 'Trades with risk'}
    table = pd.DataFrame(grouped_rows)[list(columns)].rename(columns=columns)
    st.dataframe(table, hide_index=True, width='stretch', height=min(430, 36 + 35 * len(table)))
    st.caption('Start with Trades (sample size), then Average / trade (net after recorded fees). '
               'A multi-tag trade appears in each tag, so tag counts overlap. Average R uses only trades with saved risk.')
    st.caption('These describe your recorded sample—not proof of a repeatable edge. Small samples can be misleading.')


def chart(journal, figure, name: str) -> None:
    # Room above the plot for both the title and the legend row, which otherwise print over each other.
    figure.update_layout(height=320, legend_title_text='', margin_t=64)
    st.plotly_chart(figure, width='stretch', config=theme.PLOTLY_CONFIG, key=common.key(journal, 'edge-' + name))
