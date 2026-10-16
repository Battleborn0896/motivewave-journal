"""Daily review: one saved note per trading day and timezone, beside that day's result."""

from __future__ import annotations

import datetime as dt

import pandas as pd
import streamlit as st

import common
import tag_controls
import theme
from common import money
from mwjournal.analytics import summary


def render(journal, rows) -> None:
    theme.section('Daily Report Card', 'one day · process · results · next-session adjustment')
    c1, c2 = st.columns(2)
    timezone = c2.selectbox('Session timezone', common.TIMEZONES, key=common.key(journal, 'review-tz'))
    days = [pd.Timestamp(r['opened']).tz_convert(timezone).date() for r in rows]
    # Opens on the latest day that has trades: in Replay that day is rarely today.
    date = c1.date_input('Trading day', max(days) if days else dt.datetime.now().date(),
                         key=common.key(journal, 'review-day'))
    day_rows = [r for r, day in zip(rows, days) if day == date]
    s = summary(day_rows)
    metrics = st.columns(4)
    metrics[0].metric('Net after recorded fees', money(s['net']))
    metrics[1].metric('Closed trades', str(s['count']))
    metrics[2].metric('Win rate', f'{s["win_rate"]:.1f}%' if s['win_rate'] is not None else '—')
    metrics[3].metric('Average / trade', money(s['expectancy']))
    still_open = sum(r['status'] == 'open' for r in day_rows)
    st.write(f"{s['count']} closed trades · recorded P&L {money(s['net'])}"
             + (f' · {still_open} still open' if still_open else ''))
    saved = f'{date.isoformat()}:{timezone}'
    old = journal.get('sessions').get(saved, {})
    if timezone == journal.tag_library()['session_timezone']:
        tag_controls.day_panel(journal, date)
    else:
        st.info('Choose the Day-tag timezone configured in Settings → Tags & playbooks to edit inherited day tags.')
    with st.form(common.key(journal, 'day-report-' + saved)):
        planned = st.checkbox('I had a plan before entry', value=old.get('planned', False))
        risk = st.checkbox('I respected my risk limits', value=old.get('risk_limits', False))
        stops = st.checkbox('I respected my invalidation/stop', value=old.get('stops', False))
        rating = st.slider('Session process score', 1, 5, old.get('rating', 3))
        notes = st.text_area('What I learned / next session adjustment', old.get('notes', ''))
        if st.form_submit_button('Save daily review', type='primary'):
            journal.update_session(saved, {'planned': planned, 'risk_limits': risk, 'stops': stops,
                                           'rating': rating, 'notes': notes, 'timezone': timezone,
                                           'date': date.isoformat()})
            st.success('Saved daily review.')
