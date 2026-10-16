"""Trades: the list, one trade on its one-minute chart, the review form and its excursions."""

from __future__ import annotations

import hashlib
import json

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

import common
import theme
import tvchart
import runner_controls
import tag_controls
from views import checklists
from common import PRECISION, amount, money
from mwjournal.chart import chart_window, trade_chart
from mwjournal.model import finite, instant
from mwjournal.metrics import excursions
from mwjournal import units
from mwjournal import merging

ADHERENCE = ['Unreviewed', 'Yes', 'No']
COLUMNS = {'opened': 'Opened (UTC)', 'symbol': 'Contract', 'direction': 'Side', 'status': 'Status',
           'net': 'Net P&L', 'mae': 'MAE', 'mfe': 'MFE', 'setup': 'Setup', 'tags': 'Tags'}


def render(journal, rows) -> None:
    if not rows:
        empty()
        return
    detail_key = common.key(journal, 'trade-detail-open')
    heading, merge_col, filters = st.columns([4, 1, 1])
    with heading:
        theme.section('Trades', 'captured automatically · click tags to save')
    with filters.popover('Trade filters', width='stretch'):
        shown = common.filter_rows(journal, rows)
    with merge_col.popover('More', width='stretch'):
        originals = [r for r in rows if r['status'] == 'closed' and r.get('complete', True) and not r.get('merge_leg_ids')]
        by_id = {r['id']: r for r in originals}
        with st.form(common.key(journal, 'merge-review-form')):
            ids = st.multiselect('Original trades to merge', list(by_id),
                                  format_func=lambda id: f'{by_id[id]["opened"][:19]} · {by_id[id]["symbol"]} · {money(by_id[id]["net"])}')
            st.caption('Same provider/account/exact contract/currency/direction, within 24 hours. '
                       'A review grouping only: raw fills, position IDs and leg notes are preserved. Initial risk is not guessed.')
            if st.form_submit_button('Merge selected reviews'):
                try:
                    trade = merging.create(getattr(journal, 'original', journal), ids)
                    st.session_state[common.key(journal, 'selected-trade')] = trade.id
                    st.rerun()
                except ValueError as exc:
                    st.error(str(exc))
        groups = {id: d for id, d in merging.definitions(journal).items() if d['active']}
        if groups:
            selected_group = st.selectbox('Merged review to split', list(groups),
                                           format_func=lambda id: f'{len(groups[id]["leg_ids"])} original legs · {groups[id]["at"][:19]}')
            if st.button('Split into original reviews', key=common.key(journal, 'split-review')):
                merging.split(journal, selected_group)
                st.rerun()
    selected = common.key(journal, 'selected-trade')
    filter_state = {k: st.session_state[k] for k in st.session_state
                    if k.startswith('filter-') and k.endswith('-' + getattr(journal, 'ui_scope', journal.dataset))}
    signature = json.dumps(filter_state, sort_keys=True, default=str)
    signature_key = common.key(journal, 'review-filter-signature')
    unchanged = st.session_state.get(signature_key) == signature
    st.session_state[signature_key] = signature
    pinned = next((r for r in rows if unchanged and r['id'] == st.session_state.get(selected) and r not in shown), None)
    if not shown:
        st.info('No trades match these filters.')
        if not pinned:
            return
    shown = sorted(shown, key=lambda r: r['opened'], reverse=True)
    shown = common.display_excursion_rows(journal, shown)
    unit = common.excursion_unit(journal)
    review_rows = [*shown, *([pinned] if pinned else [])]
    ids = [r['id'] for r in review_rows]
    table_key = common.key(journal, 'trades-' + hashlib.sha256(','.join(ids).encode()).hexdigest()[:12])

    def select_row():
        picked = st.session_state.get(table_key, {}).get('selection', {}).get('rows', [])
        if picked and picked[0] < len(ids):
            st.session_state[selected] = ids[picked[0]]
            open_trade()

    def open_trade():
        st.session_state[detail_key] = True
        st.session_state[common.key(journal, 'detail-trade-ids')] = ids

    list_col, review_col = st.columns([1.1, 3.9], gap='medium')
    compact_columns = {'opened': 'Opened (UTC)', 'symbol': 'Contract', 'direction': 'Side', 'net': 'Net P&L', 'status': 'Status'}
    table = pd.DataFrame([{label: (str(r[k])[:19].replace('T', ' ') if k in ('opened', 'closed') and r.get(k)
                                   else r.get('display_' + k) if k in ('mae', 'mfe') else r.get(k))
                           for k, label in compact_columns.items()} for r in shown], columns=list(compact_columns.values()))
    if st.session_state.get(selected) not in ids:
        st.session_state[selected] = ids[0]
    labels = {r['id']: f"{r['symbol']} {r['direction']} · {money(r['net'])} · {r['opened'][5:16].replace('T', ' ')}" for r in review_rows}
    with list_col:
        st.selectbox('Review a trade', ids, key=selected, format_func=lambda id: labels.get(id, id))
        st.dataframe(table.style.apply(theme.outcome_style, axis=1), hide_index=True, width='stretch', key=table_key,
                     on_select=select_row, selection_mode='single-row', height=450,
                     column_order=['Contract', 'Net P&L', 'Opened (UTC)', 'Side', 'Status'],
                     column_config={'Net P&L': st.column_config.NumberColumn(format='%.2f')})
        st.caption(f'{len(shown)} trades · click a row · account-currency P&L')
        if st.button('Refresh trades', key=common.key(journal, 'trades-refresh'), width='stretch'):
            st.rerun()
    st.session_state[common.key(journal, 'detail-trade-ids')] = ids
    with review_col.container(key='trade-review-workspace'):
        if pinned:
            st.caption('This trade no longer matches your filters. Kept open so editing tags does not interrupt review.')
        detail(journal, review_rows, embedded=True)


def detail(journal, rows, *, embedded=False):
    """A focused workspace: no trade table or list filters above the chart and tagging panel."""
    if not embedded:
        for name in list(st.session_state):
            if name.startswith('filter-') and name.endswith('-' + getattr(journal, 'ui_scope', journal.dataset)):
                st.session_state[name] = st.session_state[name]  # retain filters only when their widgets are absent
    by_id = {r['id']: r for r in rows}
    ids = [id for id in st.session_state.get(common.key(journal, 'detail-trade-ids'), []) if id in by_id]
    if not ids:
        st.session_state[common.key(journal, 'trade-detail-open')] = False
        st.rerun()
    selected = common.key(journal, 'selected-trade')
    if st.session_state.get(selected) not in ids:
        st.session_state[selected] = ids[0]

    def back():
        st.session_state[common.key(journal, 'trade-detail-open')] = False

    def move(offset):
        position = ids.index(st.session_state[selected])
        st.session_state[selected] = ids[position + offset]

    back_col, choice_col, prev_col, next_col = st.columns([1.4, 5, 1, 1], vertical_alignment='bottom')
    if not embedded:
        back_col.button('← Trades', on_click=back, width='stretch', key=common.key(journal, 'back-to-trades'))
    else:
        back_col.caption(f'{ids.index(st.session_state[selected]) + 1} / {len(ids)}')
    chosen = st.session_state[selected] if embedded else choice_col.selectbox('Review a trade', ids, key=selected,
                                  format_func=lambda id: f'{by_id[id]["symbol"]} · {by_id[id]["direction"].title()} · '
                                                        f'{by_id[id]["opened"][:19].replace("T", " ")} UTC',
                                  label_visibility='collapsed')
    if embedded:
        choice_col.caption('Click tags to autosave')
    position = ids.index(chosen)
    prev_col.button('← Prev', on_click=move, args=(-1,), disabled=position == 0, width='stretch',
                     key=common.key(journal, 'previous-trade'))
    next_col.button('Next →', on_click=move, args=(1,), disabled=position == len(ids) - 1, width='stretch',
                     key=common.key(journal, 'next-trade'))
    trade = next((t for t in journal.trades() if t.id == chosen), None)
    if trade is None:                      # the watcher regrouped fills between two reads of the journal
        st.info('The trade list changed. Return to Trades and open it again.')
        return
    annotation = journal.annotations().get(chosen, {})
    if getattr(trade, 'merge_leg_ids', None):
        st.caption(f'Merged review · {len(trade.merge_leg_ids)} original legs · raw fills and leg notes remain intact')
        if getattr(trade, 'merge_overlapping', False):
            st.caption('Overlapping positions: combined currency/R equity is available when prices qualify. '
                       'Points/ticks remain unavailable because the original legs have different held-entry bases.')
    cols = st.columns(4)
    outcome = 'positive' if trade.net is not None and trade.net > 0 else 'negative' if trade.net is not None and trade.net < 0 else 'neutral'
    with cols[0].container(key='trade-result-' + outcome):
        st.metric('Recorded P&L' if trade.closed else 'Realised P&L · still open', money(trade.net))
    unit = common.excursion_unit(journal)
    metric = common.best_display_metric(journal, trade)
    mae, mfe = common.excursion_values(journal, trade, metric, annotation.get('initial_risk'))
    cols[1].metric('MAE', common.excursion_amount(mae, unit, trade.currency), help=units.description(unit))
    cols[2].metric('MFE', common.excursion_amount(mfe, unit, trade.currency), help=units.description(unit))
    cols[3].metric('Size · peak', f'{trade.max_qty:g}')
    precision = metric.get('point_precision') if unit in ('points', 'ticks') else metric.get('precision')
    st.caption(f"{trade.symbol} · {trade.direction.title()} · {trade.opened[:19]} → "
               f"{trade.closed[:19] if trade.closed else 'Still open'} UTC · "
               f"{PRECISION.get(precision, 'Waiting for prices')} · MAE / MFE in {units.suffix(unit, trade.currency)}")
    if not metric and journal.display_settings()['excursion_policy'] == 'ticks-only':
        st.caption('Recorded ticks only · no qualifying path yet. Candle estimates are excluded, not displayed as zero.')
    if trade.point_value is None:
        st.warning(f'{trade.symbol} has no dollars-per-point multiplier yet, so its P&L cannot be calculated. Set it here.')
        common.multiplier_form(journal, 'trade-multiplier', trade.symbol)
    overview, notes, more = st.tabs(['Chart & tags', 'Notes', 'More'], key=common.key(journal, 'trade-tabs'))
    with overview:
        chart_col, tag_col = st.columns([1.7, 1], gap='medium')
        with chart_col:
            chart_panel(journal, trade, annotation, compact=True)
        with tag_col:
            tag_controls.render(journal, trade)
    with notes:
        review_form(journal, trade, annotation)
    with more:
        metrics, executions, runner, checks = st.tabs(
            ['MAE / MFE', 'Executions', 'Runner', 'Checklists'],
            key=common.key(journal, 'trade-more-tabs'))
        with executions:
            if trade.flags:
                st.warning('Data quality: ' + ' · '.join(trade.flags))
            st.dataframe(pd.DataFrame(trade.fills)[['ts', 'role', 'side', 'qty', 'price', 'commission', 'order_id',
                                                    'execution_id']], hide_index=True, width='stretch')
            if getattr(trade, 'merge_leg_ids', None):
                original = getattr(journal, 'original', journal)
                leg_reviews = original.annotations()
                legs = {t.id: t for t in original.trades()}
                st.dataframe([{'Leg': index + 1, 'Opened': legs[id].opened, 'Closed': legs[id].closed,
                               'Net': legs[id].net, 'Tags': leg_reviews.get(id, {}).get('tags', []),
                               'Notes': leg_reviews.get(id, {}).get('notes', '')} for index, id in enumerate(trade.merge_leg_ids)], hide_index=True)
        with metrics:
            excursions_panel(journal, trade, annotation)
        with runner:
            if getattr(trade, 'merge_leg_ids', None):
                st.info('Runner analysis stays per original position: a merged review can have different leg stops. '
                        'Split the review to analyze its original runners without inventing a shared stop.')
            elif trade.closed:
                runner_controls.trade_panel(journal, trade, annotation)
            else:
                st.info('Post-exit runner analysis becomes available after the trade closes.')
        with checks:
            checklists.run_panel(journal, trade, prefix='trade-' + trade.id)


def empty() -> None:
    st.info('No trades yet. Leave this app open, add MotiveWave Journal to the MotiveWave chart you trade, then trade normally.')
    theme.section('Automatic capture', 'MotiveWave → journal, with no CSV step')
    st.write('One journal trade starts with the first fill and ends when that same account and instrument position '
             'returns to flat. Scale-ins and partial exits stay together. While the position is open, the study '
             'records the observed price path for tick-based MAE and MFE. Live and Replay are kept separate.')
    st.button('Open setup', on_click=common.go('Connection'), type='primary')
    with st.expander('Manual import · optional'):
        st.caption('Only use this for old executions. Normal MotiveWave capture needs no CSV.')
        st.button('Import my executions', on_click=common.go('Import'))
        st.button('Connect the study', on_click=common.go('Connection'))


def review_form(journal, trade, a: dict) -> None:
    st.markdown('### Journal notes')
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
                  args=(journal, trade.id, 'setup', setup_key))
    notes_key = common.key(journal, 'notes-' + trade.id)
    st.text_area('Review notes', a.get('notes', ''), placeholder='What worked? What will I change next time?',
                 key=notes_key, height=160, on_change=tag_controls.save_field,
                 args=(journal, trade.id, 'notes', notes_key))
    st.caption('Setup and notes save automatically when you leave the field. Reuse a saved setup without retyping it.')
    theme.section('Trade plan', 'optional · saved plan is distinct from confirmed orders')
    with st.form(common.key(journal, 'review-' + trade.id)):
        c1, c2 = st.columns(2)
        risk = c1.number_input('Initial planned risk · total currency', min_value=0.,
                               value=float(a.get('initial_risk') or 0.),
                               help='The risk you planned before entry. Zero leaves R unavailable.')
        adherence = c2.selectbox('Followed my plan', ADHERENCE,
                                 index=ADHERENCE.index(a.get('adherence', 'Unreviewed')))
        c1, c2 = st.columns(2)
        planned_stop = c1.text_input('Planned stop-loss price · optional',
                                     '' if a.get('planned_stop') is None else str(a['planned_stop']),
                                     help='Your saved plan, not a confirmed order. Blank removes the line.')
        planned_target = c2.text_input('Planned take-profit price · optional',
                                       '' if a.get('planned_target') is None else str(a['planned_target']),
                                       help='Your saved plan. Orders the study captured are drawn separately.')
        if not st.form_submit_button('Save plan', type='primary'):
            return
        try:
            stop = finite(planned_stop, 'Planned stop') if planned_stop.strip() else None
            target = finite(planned_target, 'Planned target') if planned_target.strip() else None
        except ValueError as exc:
            st.error(str(exc))
            return
        journal.update_annotation(trade.id, {'initial_risk': risk or None, 'planned_stop': stop,
                                             'planned_target': target, 'adherence': adherence})
        st.rerun()


def excursions_panel(journal, trade, a: dict) -> None:
    theme.section('MAE / MFE', 'from the prices the study recorded or you imported')
    unit = common.excursion_unit(journal)
    tick = units.tick_size(trade.symbol, journal.display_settings()['tick_sizes'])
    options = [common.display_metric(journal, trade, m) for m in common.metric_options(journal, trade)]
    if not options:
        st.info('Unavailable under the selected accuracy policy. Recorded ticks are required in tick-only mode; '
                'OHLC, synthetic replay and recorded capture gaps do not qualify. '
                'The one-minute chart can still be displayed independently.')
        if journal.metric_options(trade):
            st.caption('Other measurements are retained, not deleted. Opt into labelled estimates in the sidebar to inspect them.')
    else:
        best = common.best_display_metric(journal, trade)
        index = next((i for i, m in enumerate(options) if m['source'] == best.get('source')
                      and m.get('basis', 'last') == best.get('basis', 'last')), 0)
        # The key carries the option list's shape, so a better measurement that arrives later is shown.
        m = options[st.selectbox('Measurement', range(len(options)), index=index,
                                 key=common.key(journal, f'measurement-{trade.id}-{len(options)}-{index}-{unit}'),
                                 format_func=lambda i: f"{PRECISION.get(options[i].get('point_precision') if unit in ('points', 'ticks') else options[i]['precision'], 'Unavailable')} · {options[i]['source']} · "
                                                      f"{options[i].get('basis', 'last')}")]
        m = common.display_metric(journal, trade, m)
        common.excursion_help(journal, trade, m, a.get('initial_risk'))
        mae, mfe = common.excursion_values(journal, trade, m, a.get('initial_risk'))
        c1, c2 = st.columns(2)
        c1.metric('Maximum adverse excursion', common.excursion_amount(mae, unit, trade.currency))
        c2.metric('Maximum favorable excursion', common.excursion_amount(mfe, unit, trade.currency))
        if a.get('initial_risk') and unit != 'r':
            c1, c2 = st.columns(2)
            c1.metric('MAE / planned risk', f"{m['mae'] / a['initial_risk']:.2f}R" if m.get('mae') is not None else '—')
            c2.metric('MFE / planned risk', f"{m['mfe'] / a['initial_risk']:.2f}R" if m.get('mfe') is not None else '—')
        for note in dict.fromkeys([*m.get('notes', []), *(m.get('point_notes', []) if unit in ('points', 'ticks') else [])]):
            st.caption(note)
        if m.get('path'):
            full = st.checkbox('Show/export full recorded trade path', key=common.key(journal, 'full-path-' + trade.id))
            if full:
                samples = journal.prices(trade.symbol, m['source'], start=trade.opened, end=trade.closed)
                clock = journal.trade_clock(trade)
                m = excursions(trade, samples, m['source'], basis=m.get('basis', 'last'), now=clock,
                               max_path_points=None)
                m = merging.mask_overlap_points(trade, m)
                st.caption(f"{len(m['path']):,} recorded path observations. These are the available observations, "
                           'not a guarantee that the feed delivered every tick. OHLC paths remain bounds, not exact ticks.')
                st.download_button('Download full recorded P&L path CSV', pd.DataFrame(m['path']).to_csv(index=False),
                                   file_name=trade.id + '-recorded-path.csv', mime='text/csv',
                                   key=common.key(journal, 'path-export-' + trade.id))
            path = pd.DataFrame(m['path'])
            if path.empty:
                st.info('No matching recorded path observations remain for this source.')
                return
            if full and len(path) > 20000:
                st.caption('The CSV includes every observation; the plot uses compacted points to keep review responsive.')
                compact = next(
                    (v for v in options if v['source'] == m['source'] and v.get('basis', 'last') == m.get('basis', 'last')), None)
                if compact:
                    path = pd.DataFrame(compact['path'])
            fig = go.Figure()
            high = [units.path_value(p, 'high', unit, risk=a.get('initial_risk'), tick=tick) for p in path.to_dict('records')]
            low = [units.path_value(p, 'low', unit, risk=a.get('initial_risk'), tick=tick) for p in path.to_dict('records')]
            if all(v is None for v in high + low):
                st.info('No path values are available in these units yet.')
            else:
                fig.add_scatter(x=path.ts, y=high, name='Upper bound', line_color=theme.UP)
                fig.add_scatter(x=path.ts, y=low, name='Lower bound', line_color=theme.DOWN)
                fig.add_hline(y=0, line_color=theme.MUTED)
                for fill in trade.fills:
                    fig.add_vline(x=instant(fill['ts']).timestamp() * 1000, line_width=1,
                                  line_dash='dot', line_color=theme.UP if fill['role'] == 'entry' else theme.DOWN)
                definition = 'Price move from held average entry' if unit in ('points', 'ticks') else 'Gross position P&L'
                fig.update_layout(height=290, yaxis_title=f'{definition} ({units.suffix(unit, trade.currency)})')
                st.plotly_chart(fig, width='stretch', config=theme.PLOTLY_CONFIG,
                                key=common.key(journal, 'path-' + trade.id))
    if st.button('Recalculate from stored prices', key=common.key(journal, 'recalculate')):
        common.recalculate(journal)
        st.rerun()


def chart_panel(journal, trade, annotation: dict, *, compact=False) -> None:
    if not compact:
        theme.section('Trade chart', '1-minute · entry → exit')
    sources = journal.price_sources(trade.symbol, trade)
    if not sources:
        st.info('No candles yet for this contract. Add the study to its chart in MotiveWave, or import '
                '1-minute bars or ticks in Import.')
        return
    preferred = next((s for s in sources if s == 'motivewave-bars:1m'), sources[0])
    with st.popover('Chart settings'):
        source = st.selectbox('Chart price source', sources, index=sources.index(preferred), key='chart-source-' + trade.id)
        timezone = st.selectbox('Chart timezone', common.TIMEZONES, key='chart-tz-' + trade.id)
        context = st.selectbox('Context each side', [15, 30, 60, 120, 0], format_func=lambda n: f'{n} minutes',
                           key='chart-context-' + trade.id)
        levels = st.checkbox('Show SL / TP levels', value=True, key='chart-levels-' + trade.id)
    clock = journal.trade_clock(trade)
    start, end = chart_window(trade, context, now=clock)
    samples = journal.prices(trade.symbol, source, start=start.isoformat(), end=end.isoformat())
    events = journal.protection_events(trade)
    payload = trade_chart(trade, samples, source, annotation, events, now=clock)
    candles = payload['series']['data']
    if candles:
        overlays = payload['overlays'] if levels else {**payload['overlays'], 'levels': []}
        tvchart.show([payload['series']], key='journal-candles-' + trade.id, height=320 if compact else 520,
                     title=f'{trade.symbol} · {trade.direction.upper()} · 1 MINUTE · {timezone}', intraday=True,
                     precision=4 if trade.symbol.startswith(('6', 'Z')) else 2,
                     watermark=trade.symbol, timezone=timezone, trade_overlays=overlays,
                     visible_range={'from': candles[0]['time'], 'to': max(candles[-1]['time'], candles[0]['time'] + 60)})
        st.caption('Blue = entry · amber = exit · red = SL · green = TP. Hover arrows for exact fills. '
                   'Scroll to zoom · drag to pan. Review only; no order entry. ' + common.CREDIT)
    else:
        st.info('This source has no usable 1-minute candles in the trade window. Full OHLC or observed ticks are '
                'needed; high/low-only and 5-minute history cannot be turned into real 1-minute candles.')
    if trade.closed and instant(trade.closed) > end:
        st.warning('The chart is capped at 10,000 minutes from entry. This position was held beyond that window.')
    if journal.capture_gaps(source, start=start.isoformat(), end=end.isoformat()):
        st.warning('This chart window has a recorded capture gap. Missing prices can understate excursions.')
    with st.expander('Chart data & order details'):
        for note in payload['notes']:
            st.caption(note)
        if events:
            st.dataframe(pd.DataFrame([e.__dict__ for e in events]), hide_index=True, width='stretch')
        else:
            st.caption('No linked SL/TP orders were captured. Add planned prices in Notes & plan; orders are never guessed.')
        st.dataframe(pd.DataFrame([{'time': pd.Timestamp(f['ts']).tz_convert(timezone).isoformat(),
                                   'role': f['role'], 'side': f['side'], 'qty': f['qty'], 'price': f['price'],
                                   'execution_id': f['execution_id']} for f in trade.fills]),
                     hide_index=True, width='stretch')
