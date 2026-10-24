"""Session tools, at the top of Trades: a check-in timer and three small menus. Read-only towards trading.

The timer is drawn like the loop timer of the journal it is modelled on (docs/research/tradecraft-videos.md):
a large count, play, pause and stop, and a duration box in minutes. When it runs out, the tag groups chosen
under Check-Ins ask for your current state, once each, and the count starts again. It never re-arms on a
restart and has no sound, recording or background notification. The menus beside it hold the check-in
groups and history, pre-trade drafts, and a checklist run for the whole session (a trade's own checklist is
on the trade, under its note).
"""
import datetime as dt
import hashlib
import time
from zoneinfo import ZoneInfo

import pandas as pd
import streamlit as st

import common
from views import checklists
from mwjournal import tagging

TIMER = 'session-timer'
DEFAULT_MINUTES = 5
# A draft's state as the Drafts menu says it: waiting for its trade, used by one, cancelled, or not attached
# because more than one trade or draft matched (ambiguous) or its tags changed since (needs review).
DRAFT_STATES = {'armed': 'Waiting', 'attached': 'Used', 'cancelled': 'Cancelled', 'ambiguous': 'Needs review',
                'needs review': 'Needs review'}


def clock_fields(journal, prefix, title, default=None):
    shown = common.display_zone(journal)
    default = (default or dt.datetime.now(ZoneInfo(shown))).astimezone(ZoneInfo(shown))
    cols = st.columns([1.3, 1, 1.5])
    day = cols[0].date_input(title + ' date', default.date(), key=common.key(journal, prefix + '-date'))
    clock = cols[1].time_input(title + ' time', default.time().replace(tzinfo=None), key=common.key(journal, prefix + '-time'))
    choices = common.timezone_choices(shown)
    zone = cols[2].selectbox(title + ' time zone', choices, index=choices.index(shown), key=common.key(journal, prefix + '-zone'))
    from mwjournal.model import timestamp
    try:
        return timestamp(dt.datetime.combine(day, clock).isoformat(), zone)
    except ValueError:
        st.warning('This local time is ambiguous or nonexistent across a daylight-saving transition. Use UTC for an exact alignment.')
        return None


# ---------------------------------------------------------------- the timer

def timer(journal, open_rows) -> None:
    """The timer row. While it counts down it redraws itself every second; stopped or paused, it is still."""
    running = st.session_state.get(common.key(journal, TIMER))
    if running and not running.get('paused'):
        _ticking(journal, open_rows)
    else:
        _timer(journal, open_rows)


@st.fragment(run_every='1s')
def _ticking(journal, open_rows):
    running = st.session_state.get(common.key(journal, TIMER))
    if not running or running.get('paused'):
        st.rerun()                             # paused or stopped here: the page draws the timer that stands still
    _timer(journal, open_rows)


def minutes(journal) -> int:
    """The saved minutes between check-ins: the one place they are kept. The box under Check-Ins writes them;
    its own state is gone whenever that menu is closed."""
    return int(journal.workspace()['preferences'].get('timer_minutes', DEFAULT_MINUTES))


def remaining(running, interval: int) -> int:
    """Whole seconds left: the full duration while stopped."""
    if not running:
        return interval * 60
    if running.get('paused'):
        return int(running['remaining'])
    return max(0, int(running['next'] - time.monotonic()))


def _timer(journal, open_rows) -> None:
    key = common.key(journal, TIMER)
    interval = minutes(journal)
    running = st.session_state.get(key)
    left = remaining(running, interval)
    state = 'off' if not running else 'paused' if running.get('paused') else 'due' if not left else 'on'

    def play():
        current = st.session_state.get(key)
        if current and current.get('paused'):
            current.update(next=time.monotonic() + current['remaining'], paused=False)
        elif not current:
            st.session_state[key] = {'next': time.monotonic() + minutes(journal) * 60, 'ack': []}

    def pause():
        current = st.session_state.get(key)
        if current and not current.get('paused'):
            current.update(remaining=max(0, current['next'] - time.monotonic()), paused=True)

    with st.container(horizontal=True, vertical_alignment='center', gap='small', key='session-timer'):
        st.markdown(f"<div class='mwj-timer {state}' title='Next check-in'>{left // 60:02d}:{left % 60:02d}</div>",
                    unsafe_allow_html=True)
        st.button('', icon=':material/play_arrow:', key=key + '-play', on_click=play,
                  disabled=state in ('on', 'due'), help='Resume the check-in timer' if state == 'paused'
                  else 'Start the check-in timer. When it runs out, the groups chosen under Check-Ins ask how you are doing.')
        st.button('', icon=':material/pause:', key=key + '-pause', on_click=pause, disabled=state not in ('on', 'due'),
                  help='Pause the timer. Check-ins already made stay saved.')
        st.button('', icon=':material/stop:', key=key + '-stop', on_click=lambda: st.session_state.pop(key, None),
                  disabled=not running, help='Stop the timer. Play starts a full interval again.')
    # The check-in sits in one placeholder, so the redrawn timer never leaves an old one on screen.
    slot = st.empty()
    if state != 'due':
        slot.empty()
        return
    with slot.container():
        check_in(journal, open_rows, running, interval)


def check_in(journal, open_rows, running, interval) -> None:
    """The groups still due ask for your current state, one row each: a click on a tag is the check-in. Once
    every group has answered, the timer starts over. Attaching it to an open position is offered only while
    there is one."""
    key = common.key(journal, TIMER)
    library = journal.tag_library()
    due = [g for g in groups(journal) if g not in running['ack']]
    cycle = running.get('cycle', 0)
    if not due:
        running.update(next=time.monotonic() + interval * 60, ack=[], cycle=cycle + 1)
        return
    target_key = key + '-target'

    def record(group, pills_key, cycle):
        # Once per group and interval: a browser can send a tag that was clicked a moment ago again with the
        # next redraw, so a click that does not belong to the check-in now due is not recorded twice.
        current, choice = st.session_state.get(key), st.session_state.get(pills_key)
        if not choice or not current or current.get('cycle', 0) != cycle or group in current['ack']:
            return
        journal.record_checkin(group, [choice], trade_id=st.session_state.get(target_key) if open_rows else None)
        current['ack'].append(group)

    if open_rows:
        st.selectbox('Attach Check-In To', [None, *[r['id'] for r in open_rows]],
                     format_func=lambda id: next(f"{r['account_label']} · {r['symbol']}" for r in open_rows if r['id'] == id)
                     if id else 'Session only', key=target_key,
                     help='Check-ins belong to the session unless you attach them to a position that is open now.')
    for group in due:
        pills_key = f'{key}-check-{group}-{cycle}'          # a fresh choice every interval
        with st.container(key='session-check-in-' + hashlib.sha256(group.encode()).hexdigest()[:12]):
            st.pills('Check In · ' + group, [t['name'] for t in tagging.ordered_groups(library)[group]], key=pills_key,
                     on_change=record, args=(group, pills_key, cycle),
                     help='Click how you are doing now. That click is the check-in.')


def groups(journal) -> list[str]:
    """The tag groups that ask at each check-in."""
    chosen = journal.workspace()['preferences'].get('timer_groups', ['Mindset'])
    return [g for g in chosen if g in tagging.ordered_groups(journal.tag_library())]


# ---------------------------------------------------------------- the menus

MENUS = {'Check-Ins': 'Which tag groups ask how you are doing when the timer runs out, and the latest check-ins.',
         'Drafts': 'Pre-trade drafts: a plan that attaches its tags, note and stop to the next trade captured on '
                   'that account and contract.',
         'Checklist': 'Run a checklist for the whole session. A trade\'s own checklist is on the trade, under its note.'}


def menus(journal, rows) -> None:
    """Check-Ins, Pre-Trade Drafts and the session Checklist, as small menus in the current row. Each is drawn
    only while it is open: the trade beside it has tag groups, a checklist and forms of its own."""
    panels = {'Check-Ins': lambda: checkins_panel(journal, rows), 'Drafts': lambda: draft_panel(journal, rows),
              'Checklist': lambda: checklists.run_panel(journal, prefix='session')}
    for label, explained in MENUS.items():
        menu = st.popover(label, help=explained, key=common.key(journal, 'session-menu-' + label.lower()),
                          on_change='rerun')
        if menu.open:
            with menu:
                panels[label]()


def checkins_panel(journal, rows) -> None:
    # How long the timer counts down: a setting, kept here with the groups that ask, so the timer row holds
    # only the count and its buttons.
    widget = common.key(journal, 'timer-minutes')
    st.number_input('Minutes Between Check-Ins', min_value=1, max_value=120, step=1, value=minutes(journal),
                    key=widget, on_change=lambda: journal.save_preferences(timer_minutes=st.session_state[widget]),
                    help='How long the timer counts down. Takes effect the next time it starts.')
    every = list(tagging.ordered_groups(journal.tag_library()))
    groups_key = common.key(journal, 'timer-groups')
    st.session_state.setdefault(groups_key, groups(journal))
    st.multiselect('Check-In Tag Groups', every, key=groups_key,
                   on_change=lambda: journal.save_preferences(timer_groups=st.session_state[groups_key]),
                   help='Each chosen group asks for one tag every time the timer runs out.')
    events = sorted(journal.workspace()['checkins'].values(), key=lambda e: e['at'], reverse=True)[:30]
    if not events:
        st.caption('No check-ins yet.')
        return
    zone = common.display_zone(journal)
    names = {r['id']: common.trade_name(r, zone) for r in rows}
    st.dataframe([{'Time': common.moment(e['at'], zone), 'Group': e['group'],
                   'Tags': ', '.join(tagging.names(journal.tag_library(), e['tag_ids'])),
                   'Trade': names.get(e['trade_id'], 'A trade no longer listed') if e['trade_id'] else 'Session'}
                  for e in events], hide_index=True, height=230)


def draft_panel(journal, rows):
    # Each account and contract once, by the account's name ('SIM · NQZ6'), the most recently traded first: a
    # replay run or another workspace is the same account to the trader, and the latest is where the next trade is.
    scopes = {}
    for r in sorted(rows, key=lambda r: r['opened'], reverse=True):
        scopes.setdefault(f"{r['account_label']} · {r['symbol']}", (r['provider'], r['account'], r['symbol']))
    labels = {scope: label for label, scope in scopes.items()}
    if scopes:
        scope = st.selectbox('Next-trade source', list(scopes.values()), format_func=labels.get,
                             key=common.key(journal, 'draft-scope'),
                             help='A draft is a plan, not a fill. It attaches its tags, note and stop to the next trade '
                                  'on this account and contract inside the time window. Only trades after arming '
                                  'qualify; when more than one could, it attaches to none and says Needs review. '
                                  'Anything already reviewed on the trade wins.')
        latest = max(instant for instant in (pd.Timestamp(r['opened']).to_pydatetime() for r in rows))
        with st.form(common.key(journal, 'draft-form')):
            name = st.text_input('Draft name', 'Next setup', max_chars=80)
            start = clock_fields(journal, 'draft-start', 'Window starts', latest if journal.dataset == 'replay' else None)
            end = clock_fields(journal, 'draft-end', 'Window ends', (latest if journal.dataset == 'replay' else dt.datetime.now(dt.timezone.utc)) + dt.timedelta(minutes=30))
            library = journal.tag_library()
            tags = []
            for group, choices in tagging.ordered_groups(library).items():
                options = [t['name'] for t in choices]
                values = st.pills(group, options, selection_mode='single' if library['groups'][group]['exclusive'] else 'multi',
                                  key=common.key(journal, 'draft-tags-' + group))
                tags.extend([values] if isinstance(values, str) else values or [])
            stop = st.text_input('Stop · optional', placeholder='Your stop price',
                                 help='The stop R uses for the trade, unless you change it in the trade\'s Stop box. Blank: the '
                                      'stop order you place with the trade counts.')
            notes = st.text_area('Pre-trade note', height=90)
            if st.form_submit_button('Arm Draft', type='primary'):
                try:
                    journal.save_draft(name, provider=scope[0], account=scope[1], symbol=scope[2], start=start, end=end,
                                        tags=tags, notes=notes, stop=stop.replace(',', '').strip() or None)
                    st.rerun()
                except ValueError as exc:
                    st.error(str(exc))
    else:
        st.info('Capture a trade first so the app knows your account and contract. No account is guessed.')
    drafts = journal.workspace()['drafts']
    if drafts:
        zone = common.display_zone(journal)
        names = {r['id']: common.trade_name(r, zone) for r in rows}
        st.dataframe([{'Plan': d['name'], 'Account': common.account_name(journal, d['provider'], d['account']),
                       'Contract': d['symbol'], 'From': common.moment(d['start'], zone, seconds=False),
                       'Until': common.moment(d['end'], zone, seconds=False), 'Stop': d.get('stop'),
                       'Status': DRAFT_STATES.get(d['state'], d['state'].title()),
                       'Trade': names.get(d['trade_id'], 'A trade no longer listed') if d['trade_id'] else ''}
                      for d in drafts.values()], hide_index=True)
        active = [id for id, d in drafts.items() if d['state'] in ('armed', 'ambiguous', 'needs review')]
        if active:
            id = st.selectbox('Cancel draft', active, format_func=lambda id: drafts[id]['name'])
            if st.button('Cancel Draft', key=common.key(journal, 'cancel-draft')):
                journal.cancel_draft(id)
                st.rerun()

