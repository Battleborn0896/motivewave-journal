"""Trade and tag views of fixed-horizon, explicitly hypothetical post-exit continuation."""
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

import common
import theme
from mwjournal.humanize import price
from mwjournal.post_exit import best_post_exit, runner_groups
from mwjournal.risk import plan_for
from mwjournal import tagging, units

HORIZONS = [5, 15, 30, 60, 120]
STOPS = {'original': 'Your stop · the one R uses', 'at-exit': 'Your stop order when you exited',
         'breakeven': 'Breakeven · your average entry'}
# What post_exit reports, in a trader's words: where the prices came from, whether they cover the whole time
# after the exit, and how that time ended.
SOURCES = {'observed-ticks': 'From ticks', 'estimated-bars': 'From candles, an estimate',
           'proxy-estimate': "From another contract's prices, an estimate",
           'replay-simulated': 'From simulated replay prices', 'pending': 'No prices yet'}
COVERAGE = {'window-observed': 'price after exit complete', 'stop-observed': 'price after exit complete up to the stop',
            'partial-window': 'price after exit not complete yet',
            'ambiguous-stop-bar': 'stop and best price in the same candle', 'unavailable': 'no price after exit'}
ENDINGS = {'stop-touch': 'stop reached', 'still-running-at-cutoff': 'still running at the end',
           'awaiting-prices': 'waiting for prices', 'stop-already-touched-at-exit': 'stop already reached at your exit',
           'unavailable': 'no prices'}


def contracts(quantity) -> str:
    return f'{quantity:g} contract' + ('' if quantity == 1 else 's')


def trade_panel(journal, trade):
    if not trade.closed:
        return
    risk = plan_for(journal, trade).risk
    with st.expander('Could I Have Let This Trade Run?'):
        minutes = st.selectbox('Look after exit', HORIZONS, index=1, format_func=lambda n: f'{n} minutes',
                               key=common.key(journal, 'runner-horizon-' + trade.id),
                               help='Holds only the final exit quantity longer; earlier partial exits are unchanged. '
                                    'A later peak is hindsight, not an exit you could have made or a tested stop rule.')
        stop_mode = st.selectbox('Runner stop rule', list(STOPS), format_func=STOPS.get,
                                  key=common.key(journal, 'runner-stop-' + trade.id))
        result = best_post_exit(journal, trade, minutes, risk, stop_mode=stop_mode)
        if result['extra_mfe'] is None:
            st.info(result['notes'][0])
            return
        c1, c2, c3 = st.columns(3)
        unit = common.excursion_unit(journal)
        def convert(value):
            return units.runner_value(value, unit, risk=risk,
                                      tick=units.tick_size(trade.symbol, journal.display_settings()['tick_sizes']),
                                      multiplier=trade.point_value, quantity=result.get('exit_qty'))
        peak_label = common.excursion_amount(convert(result['extra_mfe']), unit, trade.currency)
        if result['extra_mfe_upper'] != result['extra_mfe']:
            peak_label += ' to ' + common.excursion_amount(convert(result['extra_mfe_upper']), unit, trade.currency)
        c1.metric('Best extra before your stop', common.md(peak_label))
        c2.metric('Worst move against you', common.excursion_amount(convert(result['extra_mae']), unit, trade.currency))
        horizon = convert(result['horizon_extra'])
        c3.metric(f'Extra after {minutes} minutes', common.money(horizon, trade.currency) if unit == 'currency'
                  else common.excursion_amount(horizon, unit))
        if result['extra_mfe_r'] is not None:
            st.caption(f"Best extra {result['extra_mfe_r']:.2f}R · against you {result['extra_mae_r']:.2f}R",
                       help='Divided by the risk to your stop.')
        if result['minutes_to_peak'] is not None:
            st.caption(f"Best price about {result['minutes_to_peak']:.1f} minutes after exit",
                       help='With candles this finds the best candle, not the exact moment.')
        st.caption(f"{SOURCES.get(result['precision'], result['precision'])} · "
                   f"{COVERAGE.get(result['coverage'], result['coverage'])} · {contracts(result['exit_qty'])}")
        zone = common.display_zone(journal)
        stop = price(result['stop_price']) if result['stop_price'] is not None else 'none'
        st.caption(f"Stop {stop} · {result['stop_source']} · "
                   f"{ENDINGS.get(result['termination'], result['termination'])}"
                   + (f" at {common.moment(result['stop_ts'], zone)}" if result['stop_ts'] else ''))
        for note in result['notes']:
            st.caption(note)
        path = pd.DataFrame(result['path'])
        if path.empty:
            return
        figure = go.Figure()
        times = common.wall_clock(path.ts, zone)
        figure.add_scatter(x=times, y=[convert(v) for v in path.high], name='Best', line_color=theme.UP)
        if not path.low.equals(path.high):         # ticks have one price each: one line says it
            figure.add_scatter(x=times, y=[convert(v) for v in path.low], name='Worst', line_color=theme.DOWN)
        figure.add_hline(y=0)
        figure.update_layout(height=260, yaxis_title=f'Extra if held ({units.suffix(unit, trade.currency)})')
        st.plotly_chart(figure, width='stretch', config=theme.PLOTLY_CONFIG,
                       key=common.key(journal, 'runner-path-' + trade.id))


def edge_panel(journal, rows):
    """Which tags leave room for a runner. It reads the prices after every exit, so it runs only when asked: the
    choices sit in a form, and the table is built when Compare Runners is pressed."""
    labels = {'tags': 'Individual tags', 'tag_categories': 'Tag categories', 'setup': 'Setup / playbook'}
    # The choices last compared stay chosen while another Stats tab is open.
    remember = common.keep({common.key(journal, name): default for name, default in (
        ('runner-edge-horizon', 15), ('runner-edge-stop', 'original'), ('runner-winners', True),
        ('runner-one-r', False), ('runner-group', 'tags'))})
    with st.form(common.key(journal, 'runner-edge'), border=False):
        minutes = st.selectbox('Runner comparison horizon', HORIZONS, format_func=lambda n: f'{n} minutes',
                               key=common.key(journal, 'runner-edge-horizon'),
                               help='The same time after exit for every trade. Ticks, candle estimates and simulated '
                                    'replay are grouped separately; incomplete windows are left out, not counted as zero.')
        stop_mode = st.selectbox('Runner comparison stop', list(STOPS), format_func=STOPS.get,
                                 key=common.key(journal, 'runner-edge-stop'))
        winners = st.checkbox('Only winning trades', key=common.key(journal, 'runner-winners'))
        one_r = st.checkbox('Only exits near 1R · 0.8–1.2R', key=common.key(journal, 'runner-one-r'),
                            help='Needs the trade\'s stop. Uses the actual net result divided by the risk to that stop.')
        group = st.selectbox('Compare runners by', list(labels), format_func=labels.get,
                             key=common.key(journal, 'runner-group'))
        asked = st.form_submit_button('Compare Runners', on_click=remember,
                                      help='Reads the prices after every exit you selected.')
    if not asked:
        return
    selected = [r for r in rows if r['status'] == 'closed' and r.get('net') is not None and not r.get('merge_leg_ids')
                and (not winners or r['net'] > 0) and (not one_r or r.get('r') is not None and .8 <= r['r'] <= 1.2)]
    trades = {t.id: t for t in journal.trades()}
    if any(r.get('merge_leg_ids') for r in rows):
        st.caption('Merged reviews are excluded from runner comparisons because their original positions can have different stops.')
    results = [{**row, **best_post_exit(journal, trades[row['id']], minutes, row.get('initial_risk'), stop_mode=stop_mode)}
               for row in selected if row['id'] in trades]
    library = journal.tag_library()
    for row in results:
        ids = set(row.get('tag_ids') or []) or tagging.ids(library, row.get('tags', []))
        row['tag_categories'] = sorted({library['tags'][id]['group'] for id in ids if id in library['tags']})
    unit = common.excursion_unit(journal)
    sizes = journal.display_settings()['tick_sizes']
    for row in results:
        for name in ('extra_mfe', 'extra_mae', 'extra_mfe_upper', 'horizon_extra'):
            row[name] = units.runner_value(row.get(name), unit, risk=row.get('initial_risk'),
                                           tick=units.tick_size(row['symbol'], sizes),
                                           multiplier=row.get('point_value'), quantity=row.get('exit_qty'))
    groups = runner_groups(results, group)
    excluded = sum(r['coverage'] not in ('window-observed', 'stop-observed') or r.get('extra_mfe') is None for r in results)
    st.caption(f'{len(results)} selected trades · {excluded} left out',
               help='Left out: missing or partial price coverage, a stop and peak in the same candle, or a unit '
                    'that cannot be measured. A trade with several tags counts in each, so groups overlap.')
    if groups:
        label = units.suffix(unit, results[0]['currency'])
        table = pd.DataFrame(groups)
        table['quality'] = [SOURCES.get(q, q) for q in table['quality']]
        table = table.rename(columns={
            'group': labels[group], 'quality': 'Measured', 'trades': 'Trades',
            'avg_extra_peak': f'Average extra peak ({label})', 'avg_adverse_after_exit': f'Average move against ({label})',
            'avg_extra_peak_R': 'Extra peak in R', 'additional_1R_reached_pct': '1R more reached (%)',
            'trades_with_r': 'Trades with a stop'})
        money = {name: common.money_column(name, results[0]['currency']) for name in table.columns
                 if unit == 'currency' and name.startswith('Average')}
        st.dataframe(table, hide_index=True, width='stretch', column_config=money)
    else:
        st.info('No comparable completed windows yet: no selected trade has prices for the whole time after its exit. '
                'Keep the study running after you exit. R also needs a stop; ticks need a known tick size.')
    st.caption('Hindsight to test, not profit you missed',
               help='Only prices before the stop is touched or the cutoff count. A stop touch is not a guaranteed '
                    'fill, and trailing stops are not modelled. Do not add these peaks to your expectancy: pick '
                    'a fixed exit rule from them, then check it on trades you have not looked at yet.')
