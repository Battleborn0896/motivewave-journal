"""Trade and tag views of fixed-horizon, explicitly hypothetical post-exit continuation."""
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

import common
import theme
from mwjournal.post_exit import best_post_exit, runner_groups
from mwjournal.risk import plan_for
from mwjournal import tagging, units

HORIZONS = [5, 15, 30, 60, 120]
STOPS = {'original': 'Your stop · the one R uses', 'at-exit': 'Recorded stop at exit',
         'breakeven': 'Entry average · gross breakeven hypothesis'}


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
        c1.metric('Extra peak before stop/cutoff', common.md(peak_label))
        c2.metric('Adverse move after exit', common.excursion_amount(convert(result['extra_mae']), unit, trade.currency))
        horizon = convert(result['horizon_extra'])
        c3.metric('Extra at horizon', common.money(horizon, trade.currency) if unit == 'currency'
                  else common.excursion_amount(horizon, unit))
        if result['extra_mfe_r'] is not None:
            st.caption(f"Extra peak {result['extra_mfe_r']:.2f}R · adverse {result['extra_mae_r']:.2f}R",
                       help='Divided by the risk to your stop.')
        if result['minutes_to_peak'] is not None:
            st.caption(f"Peak about {result['minutes_to_peak']:.1f} minutes after exit",
                       help='With candles this finds the peak candle, not the exact moment.')
        st.caption(f"{result['precision']} · {result['coverage']} · final exit quantity {result['exit_qty']:g}")
        zone = common.display_zone(journal)
        st.caption(f"Stop {result['stop_price']} · {result['stop_source']} · {result['termination']}"
                   + (f" at {common.moment(result['stop_ts'], zone)}" if result['stop_ts'] else ''))
        for note in result['notes']:
            st.caption(note)
        path = pd.DataFrame(result['path'])
        if path.empty:
            return
        figure = go.Figure()
        times = common.wall_clock(path.ts, zone)
        figure.add_scatter(x=times, y=[convert(v) for v in path.high], name='Additional gross upper bound', line_color=theme.UP)
        figure.add_scatter(x=times, y=[convert(v) for v in path.low], name='Additional gross lower bound', line_color=theme.DOWN)
        figure.add_hline(y=0)
        figure.update_layout(height=260, yaxis_title=f'Extra hypothetical excursion ({units.suffix(unit, trade.currency)})')
        st.plotly_chart(figure, width='stretch', config=theme.PLOTLY_CONFIG,
                       key=common.key(journal, 'runner-path-' + trade.id))


def edge_panel(journal, rows):
    with st.expander('Which Tags Have Room for Runners?'):
        minutes = st.selectbox('Runner comparison horizon', HORIZONS, index=1, format_func=lambda n: f'{n} minutes',
                               key=common.key(journal, 'runner-edge-horizon'),
                               help='The same time after exit for every trade. Ticks, candle estimates and simulated '
                                    'replay are grouped separately; incomplete windows are left out, not counted as zero.')
        stop_mode = st.selectbox('Runner comparison stop', list(STOPS), format_func=STOPS.get,
                                 key=common.key(journal, 'runner-edge-stop'))
        winners = st.checkbox('Only winning trades', value=True, key=common.key(journal, 'runner-winners'))
        one_r = st.checkbox('Only exits near 1R · 0.8–1.2R', key=common.key(journal, 'runner-one-r'),
                            help='Needs the trade\'s stop. Uses the actual net result divided by the risk to that stop.')
        labels = {'tags': 'Individual tags', 'tag_categories': 'Tag categories', 'setup': 'Setup / playbook'}
        group = st.selectbox('Compare runners by', list(labels), format_func=labels.get,
                             key=common.key(journal, 'runner-group'))
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
        for row in results:
            for name in ('extra_mfe', 'extra_mae', 'extra_mfe_upper', 'horizon_extra'):
                row[name] = units.runner_value(row.get(name), unit, risk=row.get('initial_risk'),
                                               tick=units.tick_size(row['symbol'], journal.display_settings()['tick_sizes']),
                                               multiplier=row.get('point_value'), quantity=row.get('exit_qty'))
        groups = runner_groups(results, group)
        excluded = sum(r['coverage'] not in ('window-observed', 'stop-observed') or r.get('extra_mfe') is None for r in results)
        st.caption(f'{len(results)} selected trades · {excluded} left out',
                   help='Left out: missing or partial price coverage, a stop and peak in the same candle, or a unit '
                        'that cannot be measured. A trade with several tags counts in each, so groups overlap.')
        if groups:
            label = units.suffix(unit, results[0]['currency'])
            table = pd.DataFrame(groups).rename(columns={
                'group': labels[group], 'avg_extra_peak': f'Average extra peak ({label})',
                'avg_adverse_after_exit': f'Average adverse move ({label})', 'count': 'Trades'})
            money = {name: common.money_column(name, results[0]['currency']) for name in table.columns
                     if unit == 'currency' and name.startswith('Average')}
            st.dataframe(table, hide_index=True, width='stretch', column_config=money)
        else:
            st.info('No comparable completed windows yet. Keep price capture running after exits. '
                    'The study now defaults to continuous chart ticks; older position-gated settings need enough After Exit time. '
                    'R also needs a stop; ticks need a known tick size.')
        st.caption('Hindsight to test, not profit you missed',
                   help='Only prices before the stop is touched or the cutoff count. A stop touch is not a guaranteed '
                        'fill, and trailing stops are not modelled. Do not add these peaks to your expectancy: pick '
                        'a fixed exit rule from them, then check it on trades you have not looked at yet.')
