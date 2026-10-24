"""Session tools, at the top of Trades: one Session menu, and the check-in timer's count while it runs. Read-only
towards trading.

The Session menu starts the check-in timer, with its minutes and the categories that ask, lists the latest
check-ins, and tags your next trade (Tag My Next Trade). The timer is drawn like the loop timer of the journal it is
modelled on (docs/research/tradecraft-videos.md): a large count with pause and stop, in the bar only while it runs or
is paused. When it runs out, the categories chosen ask for your current state, once each, and the count starts again.
It never re-arms on a restart and has no sound, recording or background notification.
"""
import hashlib
import time

import streamlit as st

import common
import theme
from mwjournal import tagging
from mwjournal.model import instant
from mwjournal.workspace import contract

TIMER = 'session-timer'
MENU = 'session-menu'
DEFAULT_MINUTES = 5
# What Tag My Next Trade says of what it saved: waiting for its trade, used on it, cancelled, or not used on it
# because a tag was retired, or two came to share a one-choice category, since (needs review).
DRAFT_STATES = {'armed': 'Waiting', 'attached': 'Used', 'cancelled': 'Cancelled', 'needs review': 'Needs review'}


# ---------------------------------------------------------------- the timer

def timer(journal, open_rows) -> None:
    """The count in the bar while the timer runs or is paused, and nothing while it is stopped. Running, it redraws
    itself every second; paused, it is still."""
    running = st.session_state.get(common.key(journal, TIMER))
    if not running:
        return
    if running.get('paused'):
        _timer(journal, open_rows)
    else:
        _ticking(journal, open_rows)


@st.fragment(run_every='1s')
def _ticking(journal, open_rows):
    running = st.session_state.get(common.key(journal, TIMER))
    if not running or running.get('paused'):
        st.rerun()                             # paused or stopped here: the page draws the timer that stands still
    _timer(journal, open_rows)


def minutes(journal) -> int:
    """The saved minutes between check-ins: the one place they are kept. The box in the Session menu writes them;
    its own state is gone whenever that menu is closed."""
    return int(journal.workspace()['preferences'].get('timer_minutes', DEFAULT_MINUTES))


def remaining(running) -> int:
    """Whole seconds left on a timer that runs or is paused."""
    if running.get('paused'):
        return int(running['remaining'])
    return max(0, int(running['next'] - time.monotonic()))


def start(journal) -> None:
    """Start Check-In Timer: a full interval, and the Session menu closes so the count is seen in the bar."""
    st.session_state[common.key(journal, TIMER)] = {'next': time.monotonic() + minutes(journal) * 60, 'ack': []}
    st.session_state[common.key(journal, MENU)] = False


def _timer(journal, open_rows) -> None:
    key = common.key(journal, TIMER)
    interval = minutes(journal)
    running = st.session_state[key]
    left = remaining(running)
    state = 'paused' if running.get('paused') else 'due' if not left else 'on'

    def resume():
        current = st.session_state.get(key)
        if current and current.get('paused'):
            current.update(next=time.monotonic() + current['remaining'], paused=False)

    def pause():
        current = st.session_state.get(key)
        if current and not current.get('paused'):
            current.update(remaining=max(0, current['next'] - time.monotonic()), paused=True)

    with st.container(horizontal=True, vertical_alignment='center', gap='small', key='session-timer'):
        st.markdown(f"<div class='mwj-timer {state}' title='Next check-in'>{left // 60:02d}:{left % 60:02d}</div>",
                    unsafe_allow_html=True)
        if state == 'paused':
            st.button('', icon=':material/play_arrow:', key=key + '-play', on_click=resume,
                      help='Resume the check-in timer where it was paused.')
        else:
            st.button('', icon=':material/pause:', key=key + '-pause', on_click=pause,
                      help='Pause the timer; check-ins already made stay saved.')
        st.button('', icon=':material/stop:', key=key + '-stop', on_click=lambda: st.session_state.pop(key, None),
                  help='Stop the timer; Start Check-In Timer in the Session menu starts a full interval again.')
    # The check-in sits in one placeholder, so the redrawn timer never leaves an old one on screen.
    slot = st.empty()
    if state != 'due':
        slot.empty()
        return
    with slot.container():
        check_in(journal, open_rows, running, interval)


def check_in(journal, open_rows, running, interval) -> None:
    """The categories still due ask for your current state, one row each: a click on a tag is the check-in. Once
    every category has answered, the timer starts over. Attaching it to an open position is offered only while
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
        # Once per category and interval: a browser can send a tag that was clicked a moment ago again with the
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
                     help='Click how you are doing now; that click is the check-in.')


def groups(journal) -> list[str]:
    """The categories that ask at each check-in."""
    chosen = journal.workspace()['preferences'].get('timer_groups', ['Mindset'])
    return [g for g in chosen if g in tagging.ordered_groups(journal.tag_library())]


# ---------------------------------------------------------------- the Session menu

def menu(journal, rows) -> None:
    """The Session menu beside Filters, drawn only while it is open: the trade beside it has categories of tags and
    forms of its own. `rows` are every trade, unfiltered."""
    session = st.popover('Session', key=common.key(journal, MENU), on_change='rerun',
                         help='The check-in timer, your latest check-ins, and tags for your next trade.')
    if session.open:
        with session:
            check_ins(journal)
            next_trade(journal, rows)


def trade_names(journal, zone) -> dict:
    """Every trade of this environment by its id, as a trader recognises it: what a check-in or Tag My Next Trade went
    on is named whichever view of accounts the list shows."""
    return {r['id']: common.trade_name(r, zone) for r in common.rows(journal)}


def check_ins(journal) -> None:
    """Start Check-In Timer, with how long it counts down and the categories that ask, then the latest check-ins."""
    theme.subsection('Check-In Timer')
    # Both boxes open on what is saved, one choice for Live and Replay, on every run.
    widget = common.key(journal, 'timer-minutes')
    st.session_state[widget] = minutes(journal)
    st.number_input('Minutes Between Check-Ins', min_value=1, max_value=120, step=1,
                    key=widget, on_change=lambda: journal.save_preferences(timer_minutes=st.session_state[widget]),
                    help='How long the timer counts down; a change counts from its next start.')
    every = list(tagging.ordered_groups(journal.tag_library()))
    groups_key = common.key(journal, 'timer-groups')
    st.session_state[groups_key] = groups(journal)
    st.multiselect('Check-In Categories', every, key=groups_key,
                   on_change=lambda: journal.save_preferences(timer_groups=st.session_state[groups_key]),
                   help='Each chosen category asks for one tag every time the timer runs out.')
    running = bool(st.session_state.get(common.key(journal, TIMER)))
    st.button('Start Check-In Timer', type='primary', key=common.key(journal, 'timer-start'), on_click=start,
              args=(journal,), disabled=running,
              help='The timer is on: pause, resume or stop it in the bar.' if running else
                   'Counts down in the bar beside this menu, then asks each chosen category how you are doing.')
    theme.subsection('Latest Check-Ins')
    events = sorted(journal.workspace()['checkins'].values(), key=lambda e: e['at'], reverse=True)[:30]
    if not events:
        st.caption('No check-ins yet.')
        return
    zone = common.display_zone(journal)
    names = trade_names(journal, zone)
    st.dataframe([{'Time': common.moment(e['at'], zone), 'Category': e['group'],
                   'Tags': ', '.join(tagging.names(journal.tag_library(), e['tag_ids'])),
                   'Trade': names.get(e['trade_id'], 'A trade no longer listed') if e['trade_id'] else 'Session'}
                  for e in events], hide_index=True, height=230)


def next_trade(journal, rows) -> None:
    """Tag My Next Trade: tags, a Stop and a note for the next trade opened on one account and contract
    (Workspace.save_draft), then what was saved so, the latest first and every one still waiting, with Cancel."""
    theme.subsection('Tag My Next Trade')
    st.caption('These go on the next trade you open on this contract, and replace any still waiting there. What you '
               'choose on the trade itself wins.')
    # Each account and contract once, by the account's name ('SIM · NQZ6'), the most recently traded first. Every
    # replay run of an account is that account (workspace.contract); of two workspaces shown by one name, the latest
    # traded is where the next trade is.
    scopes = {}
    for r in sorted(rows, key=lambda r: r['opened'], reverse=True):
        scopes.setdefault(f"{r['account_label']} · {r['symbol']}", contract(r['provider'], r['account'], r['symbol']))
    labels = {scope: label for label, scope in scopes.items()}
    library = journal.tag_library()
    with st.form(common.key(journal, 'next-trade'), border=False):
        scope = st.selectbox('Contract', list(scopes.values()), format_func=labels.get,
                             key=common.key(journal, 'next-trade-contract'),
                             help='The account and contract of your next trade, the most recently traded first.')
        tags = []
        for group, choices in tagging.ordered_groups(library).items():
            values = st.pills(group, [t['name'] for t in choices],
                              selection_mode='single' if library['groups'][group]['exclusive'] else 'multi',
                              key=common.key(journal, 'next-trade-tags-' + group))
            tags.extend([values] if isinstance(values, str) else values or [])
        stop = st.text_input('Stop · optional', placeholder='Your stop price',
                             help='Blank uses the stop order you place with the trade; a price here is the stop R '
                                  'uses unless you change it on the trade.')
        notes = st.text_area('Note', height=90)
        if st.form_submit_button('Tag My Next Trade', type='primary'):
            try:
                journal.save_draft(provider=scope[0], account=scope[1], symbol=scope[2], tags=tags, notes=notes,
                                   stop=stop.replace(',', '').strip() or None)
                st.rerun()
            except ValueError as exc:
                st.error(str(exc))
    # The latest 30, and every one still waiting however old: what will go on a next trade stays in sight.
    drafts = sorted(journal.workspace()['drafts'].values(), key=lambda d: instant(d['at']), reverse=True)
    drafts = [d for i, d in enumerate(drafts) if i < 30 or d['state'] == 'armed']
    if not drafts:
        return
    zone = common.display_zone(journal)
    names = trade_names(journal, zone)

    def contract_name(draft):
        return f"{common.account_name(journal, draft['provider'], draft['account'])} · {draft['symbol']}"

    def tag_names(draft):
        return ', '.join(tagging.names(library, draft['tag_ids']))

    st.dataframe([{'Contract': contract_name(d), 'Tags': tag_names(d), 'Stop': d.get('stop'),
                   'Status': DRAFT_STATES[d['state']],
                   'Trade': names.get(d['trade_id'], 'A trade no longer listed') if d['trade_id'] else ''}
                  for d in drafts], hide_index=True)
    active = {d['id']: d for d in drafts if d['state'] in ('armed', 'needs review')}
    if active:
        with st.container(horizontal=True, vertical_alignment='bottom', gap='small'):
            id = st.selectbox('Tags to cancel', list(active), key=common.key(journal, 'next-trade-cancel'),
                              format_func=lambda id: ' · '.join(filter(None, [
                                  contract_name(active[id]), DRAFT_STATES[active[id]['state']],
                                  tag_names(active[id])])))
            if st.button('Cancel Tags', key=common.key(journal, 'cancel-draft')):
                journal.cancel_draft(id)
                st.rerun()
