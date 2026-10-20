"""The Daily Report Card: one saved grade and note per trading day and time zone, beside that day's result.

It opens on Trades, in place of a trade, from a day's header in the trade list or a day in a dashboard
calendar. Its own date box picks any other day."""

from __future__ import annotations

import datetime as dt

import pandas as pd
import streamlit as st

import common
import tag_controls
import theme
from common import money
from mwjournal import tagging
from mwjournal.analytics import summary


def render(journal, rows) -> None:
    theme.section('Daily Report Card')
    c1, c2 = st.columns(2)
    timezone = c2.selectbox('Session time zone', common.timezone_choices(common.display_zone(journal),
                                                                          journal.tag_library()['session_timezone']),
                            key=common.key(journal, 'review-tz'),
                            help='Which calendar day a trade belongs to. Each day and time zone keeps its own '
                                 'saved review.')
    days = [pd.Timestamp(r['opened']).tz_convert(timezone).date() for r in rows]
    # Opens on the day that was clicked, else the latest day that has trades: in Replay that is rarely today.
    # The day lives in session state rather than in the box's default, so a clicked day is never overridden.
    day_key = common.key(journal, 'review-day')
    latest = max(days) if days else dt.datetime.now().date()
    st.session_state.setdefault(day_key, latest)
    date = c1.date_input('Trading day', value=None, key=day_key) or latest
    day_rows = [r for r, day in zip(rows, days) if day == date]
    s = summary(day_rows)
    currency = rows[0]['currency'] if rows else 'USD'
    still_open = sum(r['status'] == 'open' for r in day_rows)
    with st.container(key='day-tiles'):
        metrics = st.columns(4)
        tone = 'positive' if s['net'] > 0 else 'negative' if s['net'] < 0 else 'flat'
        with metrics[0].container(key='tile-day-net-' + tone):
            st.metric('Net P&L', money(s['net'], currency), border=True, help='After recorded fees.')
        metrics[1].metric('Trades', str(s['count']), border=True,
                          help='Closed trades on this day.' + (f' {still_open} more still open.' if still_open else ''))
        metrics[2].metric('Win Rate', f'{s["win_rate"]:.1f}%' if s['win_rate'] is not None else '—', border=True)
        metrics[3].metric('Average Trade', money(s['expectancy'], currency), border=True)
    saved = f'{date.isoformat()}:{timezone}'
    old = journal.get('sessions').get(saved, {})
    # Day tags belong to calendar days in the day-tag time zone. The card's own zone (the trade list's, when a
    # day header opened it) can be another one; the tags are still this date's, and their ? names the zone.
    library = journal.tag_library()
    tag_zone = library['session_timezone']
    tag_controls.day_panel(journal, date)
    if timezone != tag_zone and tagging.ordered_groups(library, level='day'):
        st.caption(f'Day tags follow calendar days in {tag_zone}, the day-tag time zone.',
                   help='Set under Setup → Tags, Playbooks & Checklists → Categories. A trade opened late in the '
                        'evening can fall on another date there than in the session time zone above.')
    with st.form(common.key(journal, 'day-report-' + saved)):
        planned = st.checkbox('I had a plan before entry', value=old.get('planned', False))
        risk = st.checkbox('I respected my risk limits', value=old.get('risk_limits', False))
        stops = st.checkbox('I respected my invalidation/stop', value=old.get('stops', False))
        rating = st.slider('Session process score', 1, 5, old.get('rating', 3))
        notes = st.text_area('What I learned / next session adjustment', old.get('notes', ''))
        if st.form_submit_button('Save Daily Review', type='primary'):
            journal.update_session(saved, {'planned': planned, 'risk_limits': risk, 'stops': stops,
                                           'rating': rating, 'notes': notes, 'timezone': timezone,
                                           'date': date.isoformat()})
            st.success('Saved daily review.')
