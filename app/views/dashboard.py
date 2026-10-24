"""Your own dashboard pages on Stats: widgets you add, each with its own filters. A page starts empty: Overview
already draws the equity curve, the calendar and the results, so nothing here repeats them until you add it."""
from __future__ import annotations

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

import common
import tag_controls
import theme
import script_widget
from views import edge
from mwjournal import tagging
from mwjournal.analytics import summary
from mwjournal.workspace import WIDGETS, METRICS, GROUPS, filter_sample, group_labels

# The page shown while you have none: empty, and stored only once you save it.
DEFAULT = {'name': 'My Dashboard', 'columns': 2, 'filters': {}, 'widgets': []}
LINES = ('Equity', 'Tag score', 'Rating', 'Drawdown')
WIDGET_HELP = {'Radar': 'The share of these trades with a tag in each category.',
               'Calendar': edge.CALENDAR_HELP,
               'Drawdown': 'How far the running total sits below its highest point so far.'}       # drawn along the trades, one point each
AXES = {'trade': 'Trade number', 'time': 'Date and time'}


def render(journal, rows):
    """One dashboard page, on the Dashboards tab of Stats. `rows` are what the Stats filters keep."""
    state = journal.workspace()
    pages = state['dashboards']
    selector, top, filter_col, actions = st.columns([2, 2.4, 1, 1], vertical_alignment='center')
    choices = sorted(pages, key=lambda id: pages[id]['order']) or ['default']
    page_id = selector.selectbox('Dashboard page', choices,
                                 format_func=lambda id: pages[id]['name'] if id in pages else DEFAULT['name'],
                                 key=common.key(journal, 'dashboard-page'), label_visibility='collapsed')
    page = pages.get(page_id, DEFAULT)
    count = common.active_filters(page.get('filters', {}))
    with filter_col.popover(f'Page Filters · {count}' if count else 'Page Filters', width='stretch',
                            key=common.key(journal, 'page-filters-menu-' + page_id),
                            help='Filters for this dashboard page, on top of the Stats filters; each widget can '
                                 'add its own.'):
        with st.form(common.key(journal, 'page-filters-' + page_id), border=False):
            rule = common.filter_rule(journal, rows, 'page-filter-' + page_id, page.get('filters'))
            if st.form_submit_button('Save Page Filters', type='primary'):
                saved = journal.save_dashboard(page['name'], page['widgets'], id=page_id if page_id in pages else None,
                                                columns=page['columns'], filters=rule)
                st.session_state[common.key(journal, 'dashboard-next')] = saved
                st.rerun()
    with actions.popover('Customize Page', width='stretch',
                         help="Changes this page's name, columns and widgets, or clones it."):
        customize(journal, rows, page_id, page)
    rows = filter_sample(rows, page.get('filters', {}))
    closed = [r for r in rows if r['status'] == 'closed' and r['net'] is not None]
    top.caption(f'{len(closed)} closed trade{"" if len(closed) == 1 else "s"} on this page')
    if not page['widgets']:
        st.info('Add a widget under Customize Page.')
        return
    columns = page['columns']
    cols = st.columns(columns, gap='small')
    for index, widget in enumerate(page['widgets']):
        with cols[index % columns].container(border=True):
            st.markdown('### ' + widget['title'], help=WIDGET_HELP.get(widget['kind']))
            sample = filter_sample(rows, widget.get('filters', {}))
            draw(journal, sample, widget, page_id)


def customize(journal, rows, page_id, page):
    pages = journal.workspace()['dashboards']
    with st.form(common.key(journal, 'dashboard-layout-' + page_id)):
        name = st.text_input('Page name', page['name'], max_chars=80)
        columns = st.radio('Columns', [1, 2, 3], index=page['columns'] - 1, horizontal=True)
        if st.form_submit_button('Save Page Layout'):
            saved = journal.save_dashboard(name, page['widgets'], id=page_id if page_id in pages else None,
                                            filters=page.get('filters', {}), columns=columns)
            st.session_state[common.key(journal, 'dashboard-next')] = saved
            st.rerun()
    if st.button('Clone Page', key=common.key(journal, 'clone-page-' + page_id)):
        widgets = [{k: v for k, v in w.items() if k != 'id'} for w in page['widgets']]
        saved = journal.save_dashboard(page['name'] + ' copy', widgets, columns=page['columns'], filters=page.get('filters'))
        st.session_state[common.key(journal, 'dashboard-next')] = saved
        st.rerun()
    widgets = page['widgets']
    selected = st.selectbox('Edit widget', ['new', *range(len(widgets))],
                             format_func=lambda i: '+ Add a widget' if i == 'new' else widgets[i]['title'],
                             key=common.key(journal, 'widget-edit-' + page_id))
    old = widgets[selected] if selected != 'new' else {}
    # Tag score is offered once a tag has a score (tagging.scored); a widget saved with it keeps it.
    scored = tagging.scored(journal.tag_library())
    kinds = [k for k in WIDGETS if k != 'Tag score' or scored or old.get('kind') == k]
    metrics = [m for m in METRICS if m != 'Tag score' or scored or old.get('metric') == m]
    with st.form(common.key(journal, 'widget-form-' + page_id + '-' + str(selected))):
        kind = st.selectbox('Widget type', kinds, index=kinds.index(old.get('kind', 'Equity')))
        title = st.text_input('Widget title', old.get('title', ''), max_chars=80)
        metric = st.selectbox('Series metric', metrics, index=metrics.index(old.get('metric', 'Net P&L')))
        group = st.selectbox('Compare by', GROUPS, index=GROUPS.index(old.get('group', 'Market')))
        axis = st.selectbox('Along the bottom', list(AXES), format_func=AXES.get,
                            index=list(AXES).index(old.get('axis', 'trade')) if old.get('axis', 'trade') in AXES else 0,
                            help='For line widgets: Trade number puts trades side by side, so nights and weekends '
                                 'take no room.')
        included = old.get('score_tags', [])
        if scored:
            included = st.multiselect('Score only these tags · empty means all', list(journal.tag_library()['tags']),
                                      default=included, format_func=lambda id: journal.tag_library()['tags'][id]['name'])
        code = st.text_area('Local script · Script widgets only', value=old.get('code', script_widget.STARTER),
                            height=110, max_chars=30000)
        # Keyed by the widget, not its place: a widget moved up or down opens on its own filters.
        filters = common.filter_rule(journal, rows, f"widget-{page_id}-{old.get('id', 'new')}", old.get('filters'))
        if st.form_submit_button('Save Widget', type='primary'):
            updated = {'kind': kind, 'title': title or kind, 'metric': metric, 'group': group, 'axis': axis,
                       'score_tags': included, 'filters': filters, 'code': code}
            new = [dict(w) for w in widgets]
            if selected == 'new':
                new.append(updated)
            else:
                new[selected] = {**updated, 'id': old['id']}
            saved = journal.save_dashboard(page['name'], new, id=page_id if page_id in journal.workspace()['dashboards'] else None,
                                            filters=page.get('filters'), columns=page['columns'])
            st.session_state[common.key(journal, 'dashboard-next')] = saved
            st.rerun()
    if selected != 'new':
        early, late, hide = st.columns(3)
        def save(new):
            saved = journal.save_dashboard(page['name'], new, id=page_id if page_id in pages else None,
                                            filters=page.get('filters'), columns=page['columns'])
            st.session_state[common.key(journal, 'dashboard-next')] = saved
            st.rerun()
        for column, direction, text in [(early, -1, '↑ Earlier'), (late, 1, '↓ Later')]:
            other = selected + direction
            if column.button(text, disabled=not 0 <= other < len(widgets), key=common.key(journal, text + page_id)):
                new = [dict(w) for w in widgets]
                new[selected], new[other] = new[other], new[selected]
                save(new)
        if hide.button('Remove Widget', key=common.key(journal, 'remove-widget-' + page_id)):
            save([w for i, w in enumerate(widgets) if i != selected])


def values(journal, rows, widget):
    from mwjournal.workspace import tag_score
    field = {'Net P&L': 'net', 'Gross P&L': 'gross', 'R': 'r', 'Tag score': 'tag_score', 'Rating': 'rating'}[widget.get('metric', 'Net P&L')]
    library = journal.tag_library()
    result = []
    for row in rows:
        val = (tag_score(library, row['tag_ids'], widget['score_tags']) if field == 'tag_score' and widget.get('score_tags')
               else row.get(field))
        if row['status'] == 'closed' and val is not None:
            result.append({**row, 'value': val})
    return sorted(result, key=lambda r: r['closed'])


def draw(journal, rows, widget, page_id):
    kind = widget['kind']
    key = common.key(journal, f'dashboard-{page_id}-{widget["id"]}')
    closed = [r for r in rows if r['status'] == 'closed' and r['net'] is not None]
    if kind == 'Script':
        script_widget.render(journal, rows, widget, key)
        return
    currency = rows[0]['currency'] if rows else 'USD'
    zone = common.display_zone(journal)
    if kind == 'Summary':
        stats = summary(closed)
        cols = st.columns(2)
        cols[0].metric('Net P&L', common.money(stats['net'], currency), help='After recorded fees.')
        cols[1].metric('Win Rate', f'{stats["win_rate"]:.1f}%' if stats['win_rate'] is not None else '—')
        cols[0].metric('Average Trade', common.money(stats['expectancy'], currency))
        cols[1].metric('Trades', str(stats['count']), help='Closed trades in this widget\'s sample.')
        return
    if kind == 'Calendar':
        edge.trading_calendar(journal, rows, key)
        return
    if kind == 'Trades':
        if rows:
            # Net P&L is a closed trade's: an open one's would be its fees so far, so it reads '—'.
            st.dataframe(common.money_table([{'Entry': common.moment(r['opened'], zone, seconds=False), 'Contract': r['symbol'],
                                              'Side': r['direction'].title(),
                                              'Net P&L': r['net'] if r['status'] == 'closed' else None, 'Tags': r['tags']}
                                             for r in rows], currency, results=['Net P&L']), hide_index=True, height=280)
        else:
            st.info('No trades in this sample.')
        return
    if kind == 'MAE / MFE':
        measured = [r for r in closed if r['display_mae'] is not None and r['display_mfe'] is not None]
        if not measured:
            st.info('No trade has MAE / MFE in your MAE / MFE Units yet.')
            return
        frame = pd.DataFrame(measured)
        frame['Net P&L'] = [common.money(v, currency) for v in frame.net]
        unit = common.excursion_unit(journal)
        fig = px.scatter(frame, x='display_mae', y='display_mfe', color='direction',
                         hover_data={'symbol': True, 'Net P&L': True, 'direction': False},
                         labels={'display_mae': 'MAE', 'display_mfe': 'MFE', 'symbol': 'Contract'})
        if unit == 'currency':
            theme.money_axis(fig, currency, [*frame.display_mae, *frame.display_mfe], axis='xaxis')
            theme.money_axis(fig, currency, [*frame.display_mae, *frame.display_mfe])
    elif kind == 'Radar':
        library = journal.tag_library()
        groups = tagging.ordered_groups(library)
        axes = list(groups)
        vals = [100 * sum(any(library['tags'][id]['group'] == group for id in r['tag_ids']) for r in closed) / len(closed)
                if closed else 0 for group in axes]
        fig = go.Figure(go.Scatterpolar(r=vals + vals[:1], theta=axes + axes[:1], fill='toself', name='Tagged trades %'))
        fig.update_layout(polar=dict(radialaxis=dict(range=[0, 100])))
    else:
        # Tag score and Rating widgets chart their own measure, not the default P&L: a Rating widget the trade's stars.
        widget = {**widget, 'metric': 'Tag score' if kind == 'Tag score' else 'Rating' if kind == 'Rating' else widget.get('metric', 'Net P&L')}
        data = values(journal, closed, widget)
        if not data:
            st.info('No closed values to show. A Rating needs stars clicked on the trade; R needs a stop.')
            return
        frame = pd.DataFrame(data)
        money = widget['metric'] in ('Net P&L', 'Gross P&L')
        if kind in LINES:
            cumulative = frame.value.cumsum()
            series = cumulative - cumulative.cummax().clip(lower=0) if kind == 'Drawdown' else frame.value if kind == 'Rating' else cumulative
            name = {'Drawdown': 'Drawdown', 'Rating': 'Rating'}.get(kind, 'Total')
            fig = edge.progress_chart(data, [(name, list(series), theme.SERIES[0])], zone, axis=widget.get('axis', 'trade'),
                                      currency=currency if money else None, value=formatter(widget['metric'], currency),
                                      height=270)
            st.plotly_chart(fig, width='stretch', config=theme.PLOTLY_CONFIG, key=key)
            return
        elif kind == 'Distribution':
            fig = px.histogram(frame, x='value', labels={'value': widget['metric']})
            fig.update_layout(yaxis_title='Trades')
            if money:
                theme.money_axis(fig, currency, frame.value, axis='xaxis')
        else:
            fig = compare_bars(journal, data, widget.get('group', 'Market'), widget['metric'], zone)
            if fig is None:
                st.info('No values in the chosen groups.')
                return
            if money:
                theme.money_axis(fig, currency, [v for trace in fig.data for v in trace.y])
    # Tag bars are coloured by category, one trace each: only then does a legend say which colour is which.
    fig.update_layout(height=270, margin=dict(l=20, r=20, t=20, b=25), legend_title_text='',
                      showlegend=kind == 'Compare' and len(fig.data) > 1)
    st.plotly_chart(fig, width='stretch', config=theme.PLOTLY_CONFIG, key=key)


def compare_bars(journal, data, group, metric, zone):
    """A Compare widget's bars: the average of `metric` for each name of `group` (GROUPS), lowest first, or None when
    no trade has one. Tags stand together by category, each category in the library's order and in its colour."""
    library = journal.tag_library()
    tags = library['tags']
    pairs = [{'group': tags[id]['name'], 'category': tags[id]['group'], 'value': r['value']}
             for r in data for id in r['tag_ids'] if id in tags] if group == 'Tags' else [
        {'group': name, 'category': group, 'value': r['value']} for r in data for name in group_labels(r, group, zone)]
    if not pairs:
        return None
    frame = (pd.DataFrame(pairs).groupby(['category', 'group'], as_index=False)
             .agg(value=('value', 'mean'), count=('value', 'count')))
    if group == 'Tags':
        frame['place'] = frame.category.map(lambda name: library['groups'][name]['order'])
        colours = {name: tag_controls.group_colour(name, [t for t in tags.values() if t['group'] == name])
                   for name in frame.category.unique()}
    else:
        frame['place'], colours = 0, {group: theme.SERIES[0]}
    frame = frame.sort_values(['place', 'value', 'group'])
    return px.bar(frame, x='group', y='value', color='category', color_discrete_map=colours,
                  category_orders={'group': list(frame.group), 'category': list(dict.fromkeys(frame.category))},
                  # Only a tag has a category: other bars would read 'Category=Entry hour'.
                  hover_data={'count': True, 'category': group == 'Tags'},
                  labels={'value': 'Average ' + metric, 'group': group, 'count': 'Trades', 'category': 'Category'})


def formatter(metric, currency):
    """How one value of a widget's measure is written when hovered."""
    if metric in ('Net P&L', 'Gross P&L'):
        return lambda v: common.money(v, currency)
    if metric == 'R':
        return lambda v: '—' if v is None else f'{v:+.2f}R'
    if metric == 'Rating':
        return lambda v: '—' if v is None else f'{v:g} star' + ('' if v == 1 else 's')
    return lambda v: '—' if v is None else f'{v:+,.2f}'.rstrip('0').rstrip('.')
