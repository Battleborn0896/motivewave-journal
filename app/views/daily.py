"""The Daily Report Card: one day's tags, Rating and note beside that day's result, and each checklist that runs before
the session, answered for that day. A day is a date in the journal's Time Zone (Settings), from midnight to midnight,
as in the trade list and the calendar. Every box saves when it changes.

It opens on Trades, in place of a trade, from a day's header in the trade list or a day in the calendar on Stats
(Overview, or a Calendar widget). Its own date box picks any other day."""

from __future__ import annotations

import datetime as dt
from zoneinfo import ZoneInfo

import streamlit as st

import common
import tag_controls
import theme
from common import money
from views import checklists
from mwjournal.analytics import summary


def render(journal, rows) -> None:
    theme.section('Daily Report Card')
    zone = common.display_zone(journal)
    days = [common.local_day(r['opened'], zone) for r in rows]
    # Opens on the day that was clicked, else the latest day that has trades: in Replay that is rarely today.
    # The day lives in session state rather than in the box's default, so a clicked day is never overridden.
    day_key = common.key(journal, 'review-day')
    latest = max(days) if days else dt.datetime.now(ZoneInfo(zone)).date()
    st.session_state.setdefault(day_key, latest)
    date = st.columns(2)[0].date_input('Trading day', value=None, key=day_key) or latest
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
    saved = date.isoformat()
    old = journal.get('sessions').get(saved, {})
    tag_controls.day_panel(journal, date)

    # Each box saves when it changes, as a trade's notes do: nothing typed is lost by clicking a trade or
    # another day. The keys carry the day, so each day shows its own review.
    def save(field, widget):
        value = st.session_state.get(widget)
        journal.update_session(saved, {field: value.strip() if isinstance(value, str) else value, 'date': saved})

    def box(field):
        widget = common.key(journal, f'day-{field}-{saved}')
        return {'key': widget, 'on_change': save, 'args': (field, widget)}

    tag_controls.stars(old.get('rating'), **box('rating'))
    st.text_area('What I learned / next session adjustment', old.get('notes', ''), help='Saves when you leave the box.',
                 **box('notes'))
    if checklists.offered(journal, 'session'):
        theme.section('Checklist')
        checklists.run_panel(journal, day=saved)
