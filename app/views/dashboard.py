"""Configurable local dashboard pages, independently filtered widgets and a clickable calendar."""
from __future__ import annotations

import calendar
import datetime as dt
import html
from zoneinfo import ZoneInfo

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

import common
import theme
import script_widget
from views import edge
from mwjournal import tagging
from mwjournal.analytics import summary
from mwjournal.model import instant
from mwjournal.workspace import WIDGETS, METRICS, GROUPS, filter_sample

DEFAULT = {'name': 'Overview', 'columns': 2, 'filters': {}, 'widgets': [
    {'id': 'summary', 'kind': 'Summary', 'title': 'Results at a Glance', 'metric': 'Net P&L'},
    {'id': 'calendar', 'kind': 'Calendar', 'title': 'Trading Calendar', 'metric': 'Net P&L'},
    {'id': 'equity', 'kind': 'Equity', 'title': 'Equity Curve', 'metric': 'Net P&L'}]}
LINES = ('Equity', 'Tag score', 'Rating', 'Drawdown')
WIDGET_HELP = {'Radar': 'The share of this sample tagged in each category. Not an edge or a performance score.',
               'Calendar': 'Each day\'s net result and closed trades in your display time zone, a deeper green or red '
                           'for a bigger day of the month; each week\'s total ends its row. Click a day for its Daily '
                           'Report Card.',
               'Drawdown': 'How far the running total sits below its highest point so far.'}       # drawn along the trades, one point each
AXES = {'trade': 'Trade number', 'time': 'Date and time'}


def filter_editor(journal, rows, prefix, old=None):
    """A dashboard page's or widget's filter rule, the kind of rule a saved filter is."""
    old = old or {}
    library = journal.tag_library()
    key = lambda name: common.key(journal, prefix + '-' + name)
    result = {}
    names = {r['account']: r['account_label'] for r in rows}         # accounts by the name given to them
    for field, title, column in [('markets', 'Markets', 'market'), ('accounts', 'Accounts', 'account'),
                                 ('symbols', 'Exact contracts', 'symbol')]:
        options = sorted({r[column] for r in rows} | set(old.get(field, [])))
        result[field] = st.multiselect(title, options, default=old.get(field, []), key=key(field),
                                       format_func=(lambda a: names.get(a, a)) if field == 'accounts' else str)
    result['directions'] = st.multiselect('Directions', ['long', 'short'], default=old.get('directions', []), key=key('directions'))
    options = sorted(library['tags'], key=lambda id: library['tags'][id]['name'])
    for field, title in [('tags_all', 'Require every tag'), ('tags_any', 'Require any tag'), ('tags_none', 'Exclude tags')]:
        result[field] = st.multiselect(title, options, default=old.get(field, []),
                                       format_func=lambda id: library['tags'][id]['name'], key=key(field))
    result['status'] = st.multiselect('Trade status', ['closed', 'open', 'incomplete'], default=old.get('status', []), key=key('status'))
    with st.expander('Dates and Number Ranges'):
        date_filter = st.checkbox('Limit entry dates', value=bool(old.get('start') or old.get('end')), key=key('dates-on'),
                                  help='Calendar days in your display time zone.')
        if date_filter:
            days = [dt.date.fromisoformat(r['entry_day']) if r.get('entry_day') else instant(r['opened']).date()
                    for r in rows] or [dt.datetime.now(ZoneInfo(common.display_zone(journal))).date()]
            period = st.date_input('Entry date range', (dt.date.fromisoformat(old['start']) if old.get('start') else min(days),
                                                        dt.date.fromisoformat(old['end']) if old.get('end') else max(days)), key=key('dates'))
            if isinstance(period, (tuple, list)) and len(period) == 2:
                result.update(start=period[0].isoformat(), end=period[1].isoformat())
        choices = {'score': 'Signed tag score', 'net': 'Net P&L', 'r': 'Result in R', 'mae': 'MAE · currency',
                   'mfe': 'MFE · currency', 'quantity': 'Peak quantity', 'duration': 'Duration · minutes'}
        ranges = st.multiselect('Numbers to limit', list(choices),
                                default=[name for name in choices if old.get(name + '_min') is not None or old.get(name + '_max') is not None],
                                format_func=choices.get, key=key('numeric-fields'),
                                help='MAE / MFE limits use account currency, not the display unit. A trade without '
                                     'a value never passes a limit. R needs a stop; '
                                     'duration is in minutes.')
        for name in ranges:
            cols = st.columns(2)
            for col, suffix, word in [(cols[0], '_min', 'Minimum'), (cols[1], '_max', 'Maximum')]:
                enabled = col.checkbox(word + ' ' + choices[name], value=old.get(name + suffix) is not None, key=key(name + suffix + '-on'))
                if enabled:
                    result[name + suffix] = col.number_input(word + ' value', value=float(old.get(name + suffix) or 0), key=key(name + suffix))
    return result


def render(journal, rows):
    """One dashboard page, on the Dashboards tab of Stats. `rows` are what the Stats filters keep."""
    state = journal.workspace()
    pages = state['dashboards']
    selector, top, filter_col, actions = st.columns([2, 2.4, 1, 1], vertical_alignment='center')
    choices = ['default', *sorted(pages, key=lambda id: pages[id]['order'])]
    page_id = selector.selectbox('Dashboard page', choices, format_func=lambda id: pages[id]['name'] if id in pages else 'Overview',
                            key=common.key(journal, 'dashboard-page'), label_visibility='collapsed')
    page = pages.get(page_id, DEFAULT)
    with filter_col.popover('Page Filters', width='stretch',
                            help='Filters for this dashboard page, on top of the Stats filters. Each widget can add its own.'):
        with st.form(common.key(journal, 'page-filters-' + page_id), border=False):
            rule = filter_editor(journal, rows, 'page-filter-' + page_id, page.get('filters'))
            if st.form_submit_button('Save Page Filters', type='primary'):
                saved = journal.save_dashboard(page['name'], page['widgets'], id=page_id if page_id in pages else None,
                                                columns=page['columns'], filters=rule)
                st.session_state[common.key(journal, 'dashboard-next')] = saved
                st.rerun()
    with actions.popover('Customize Page', width='stretch', help='Name, columns and widgets of this page; clone it.'):
        customize(journal, rows, page_id, page)
    rows = filter_sample(rows, page.get('filters', {}))
    closed = [r for r in rows if r['status'] == 'closed' and r['net'] is not None]
    top.caption(f'{len(closed)} closed trade{"" if len(closed) == 1 else "s"} on this page')
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
    with st.form(common.key(journal, 'widget-form-' + page_id + '-' + str(selected))):
        kind = st.selectbox('Widget type', WIDGETS, index=WIDGETS.index(old.get('kind', 'Equity')))
        title = st.text_input('Widget title', old.get('title', ''), max_chars=80)
        metric = st.selectbox('Series metric', METRICS, index=METRICS.index(old.get('metric', 'Net P&L')))
        group = st.selectbox('Compare by', GROUPS, index=GROUPS.index(old.get('group', 'Market')))
        axis = st.selectbox('Along the bottom', list(AXES), format_func=AXES.get,
                            index=list(AXES).index(old.get('axis', 'trade')) if old.get('axis', 'trade') in AXES else 0,
                            help='For line widgets. Trade number puts trades side by side, so nights and weekends '
                                 'take no room; date and time uses your display time zone.')
        ratings = journal.workspace()['ratings']
        rating_id = st.selectbox('Rating definition', [None, *ratings],
                                 format_func=lambda id: ratings[id]['name'] if id else 'No rating model',
                                 index=([None, *ratings].index(old.get('rating_id')) if old.get('rating_id') in ratings else 0))
        included = st.multiselect('Score only these tags · empty means all', list(journal.tag_library()['tags']),
                                  default=old.get('score_tags', []),
                                  format_func=lambda id: journal.tag_library()['tags'][id]['name'])
        code = st.text_area('Local script · Script widgets only', value=old.get('code', script_widget.STARTER),
                            height=110, max_chars=30000)
        filters = filter_editor(journal, rows, f'widget-{page_id}-{selected}', old.get('filters'))
        if st.form_submit_button('Save Widget', type='primary'):
            updated = {'kind': kind, 'title': title or kind, 'metric': metric, 'group': group, 'axis': axis,
                       'rating_id': rating_id, 'score_tags': included, 'filters': filters, 'code': code}
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
        if hide.button('Remove Widget', disabled=len(widgets) < 2, key=common.key(journal, 'remove-widget-' + page_id)):
            save([w for i, w in enumerate(widgets) if i != selected])


def values(journal, rows, widget):
    from mwjournal.workspace import tag_score
    field = {'Net P&L': 'net', 'Gross P&L': 'gross', 'R': 'r', 'Tag score': 'tag_score', 'Rating': 'rating'}[widget.get('metric', 'Net P&L')]
    library = journal.tag_library()
    result = []
    for row in rows:
        val = (row.get('ratings', {}).get(widget.get('rating_id')) if field == 'rating' else
               tag_score(library, row['tag_ids'], widget['score_tags']) if field == 'tag_score' and widget.get('score_tags') else row.get(field))
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
        trading_calendar(journal, rows, key)
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
        measured = [r for r in common.display_excursion_rows(journal, closed) if r['display_mae'] is not None and r['display_mfe'] is not None]
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
        # Score/rating widgets select their own metric, not the default P&L.
        widget = {**widget, 'metric': 'Tag score' if kind == 'Tag score' else 'Rating' if kind == 'Rating' else widget.get('metric', 'Net P&L')}
        data = values(journal, closed, widget)
        if not data:
            st.info('No closed values to show. A rating needs a rating system chosen in the widget; R needs a stop.')
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
            group = widget.get('group', 'Market')
            pairs = []
            for r in data:
                time = pd.Timestamp(r['opened']).tz_convert(zone)
                labels = (r['tags'] if group == 'Tags' else r['playbooks'] if group == 'Playbooks' else
                          [r['market'] if group == 'Market' else r['symbol'] if group == 'Exact contract' else
                           r['direction'] if group == 'Direction' else f'{time.hour:02d}:00' if group == 'Entry hour' else time.day_name()])
                pairs.extend({'group': label, 'value': r['value']} for label in labels)
            if not pairs:
                st.info('No values in the chosen groups.')
                return
            frame = pd.DataFrame(pairs).groupby('group', as_index=False).agg(value=('value', 'mean'), count=('value', 'count'))
            fig = px.bar(frame, x='group', y='value', hover_data=['count'],
                         labels={'value': 'Average ' + widget['metric'], 'group': group, 'count': 'Trades'})
            if money:
                theme.money_axis(fig, currency, frame.value)
    fig.update_layout(height=270, margin=dict(l=20, r=20, t=20, b=25), showlegend=False)
    st.plotly_chart(fig, width='stretch', config=theme.PLOTLY_CONFIG, key=key)


def formatter(metric, currency):
    """How one value of a widget's measure is written when hovered."""
    if metric in ('Net P&L', 'Gross P&L'):
        return lambda v: common.money(v, currency)
    if metric == 'R':
        return lambda v: '—' if v is None else f'{v:+.2f}R'
    return lambda v: '—' if v is None else f'{v:+,.2f}'.rstrip('0').rstrip('.')


def trade_count(n) -> str:
    return f'{n} trade{"" if n == 1 else "s"}'


def calendar_cell(label, net, trades, biggest, currency, *, kind='mwj-cal') -> str:
    """A calendar cell (a day, a week, or the month's total, kind 'mwj-cal-month'): its label, then its net result
    and number of closed trades when it has any, tinted by the result against `biggest`, the month's largest
    day (or week). The tint follows the whole amount the cell shows, so a cell that reads $0 stays neutral: a
    break-even day of prices such as 71.15 adds up to a crumb like 0.00000000001, not 0. '$' is written as an
    entity: two in one block of Markdown would start a formula."""
    amount = html.escape(common.money(net, currency, decimals=0)).replace('$', '&#36;')
    lines, title = f"<span class='d'>{label}</span>" if label else '', ''
    if trades:
        lines += f"<span class='n'>{amount}</span><span class='k'>{trade_count(trades)}</span>"
        title = f" title='{amount}, {trade_count(trades)}'"       # all of it, for a cell too narrow to show it
    return f"<div class='{kind} {theme.tint(round(net), biggest)}'{title}>{lines}</div>"


def trading_calendar(journal, rows, key):
    """The month's days, each tinted by its closed trades' net result, deeper for a bigger day of that month, with
    each week's total at the end of its row and the month's beside the month. A day opens its Daily Report Card."""
    zone = common.display_zone(journal)
    currency = rows[0]['currency'] if rows else 'USD'
    days = [instant(r['opened']).astimezone(ZoneInfo(zone)).date() for r in rows]
    latest = max(days) if days else dt.datetime.now(ZoneInfo(zone)).date()
    months = sorted({d.replace(day=1) for d in days} | {latest.replace(day=1)}, reverse=True)

    def open_day(date):
        # The Daily Report Card is on Trades, in place of a trade, for this date.
        st.session_state.update(common.navigation('Daily review'))
        st.session_state[common.key(journal, 'review-day')] = date

    with st.container(key='journal-calendar-' + key):
        choice, total = st.columns(2, vertical_alignment='center')
        month = choice.selectbox('Month', months, format_func=lambda d: d.strftime('%B %Y'), key=key + '-month',
                                 label_visibility='collapsed')
        results = {}                                   # the month's days with closed trades: [net, trades]
        for r, day in zip(rows, days):
            if day.replace(day=1) == month and r['status'] == 'closed' and r['net'] is not None:
                result = results.setdefault(day, [0, 0])
                result[0] += r['net']
                result[1] += 1
        add = lambda dates: [sum(results[d][i] for d in dates if d in results) for i in (0, 1)]
        weeks = [[dt.date(month.year, month.month, day) if day else None for day in week]
                 for week in calendar.monthcalendar(month.year, month.month)]
        # A week's total counts all its days in this month, a hidden weekend's too, as the month's total does.
        totals = [add(week) for week in weeks]
        month_net, month_trades = add(results)
        total.markdown(calendar_cell('', month_net, month_trades, abs(month_net), currency, kind='mwj-cal-month'),
                       unsafe_allow_html=True)
        biggest_day = max((abs(net) for net, _ in results.values()), default=0)
        biggest_week = max((abs(net) for net, _ in totals), default=0)
        shown = 5 if journal.workspace()['preferences'].get('hide_weekends', False) else 7
        names = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'][:shown] + ['Week']
        for col, name in zip(st.columns(len(names)), names):
            col.caption(name)
        number = 0
        for week, (week_net, week_trades) in zip(weeks, totals):
            if not any(week[:shown]) and not week_trades:
                continue                               # a hidden weekend that starts the month, nothing traded
            number += 1
            cols = st.columns(len(names))
            for col, date in zip(cols, week[:shown]):
                if date is None:
                    col.caption(' ')
                    continue
                net, trades = results.get(date, (0, 0))
                with col.container(key=f'calendar-day-{key}-{date}'):
                    st.markdown(calendar_cell(date.day, net, trades, biggest_day, currency), unsafe_allow_html=True)
                    # Invisible, over the cell: its label is what a screen reader says.
                    st.button(f'{date.day}  \n' + common.md(common.money(net, currency, decimals=0)) +
                              f'  \n{trade_count(trades)}' if trades else str(date.day),
                              key=key + '-day-' + str(date), on_click=open_day, args=(date,), width='stretch',
                              help=common.md(f'{common.day_label(date, zone, weekday=True)}: {trade_count(trades)} '
                                             f'closed, {common.money(net, currency)} · click for its Daily Report Card '
                                             'on Trades'))
            cols[-1].markdown(calendar_cell(f'Week {number}', week_net, week_trades, biggest_week, currency),
                              unsafe_allow_html=True)
