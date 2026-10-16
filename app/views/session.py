"""Read-only live/replay planning tools. Timers never re-arm on restart."""
import datetime as dt
import time
from zoneinfo import ZoneInfo

import pandas as pd
import streamlit as st

import common
import tag_controls
import theme
from views import checklists
from mwjournal import tagging


def clock_fields(journal, prefix, title, default=None):
    default = (default or dt.datetime.now(ZoneInfo('America/New_York'))).astimezone(ZoneInfo('America/New_York'))
    cols = st.columns([1.3, 1, 1.5])
    day = cols[0].date_input(title + ' date', default.date(), key=common.key(journal, prefix + '-date'))
    clock = cols[1].time_input(title + ' time', default.time().replace(tzinfo=None), key=common.key(journal, prefix + '-time'))
    zone = cols[2].selectbox(title + ' timezone', common.TIMEZONES, key=common.key(journal, prefix + '-zone'))
    from mwjournal.model import timestamp
    try:
        return timestamp(dt.datetime.combine(day, clock).isoformat(), zone)
    except ValueError:
        st.warning('This local time is ambiguous or nonexistent across a daylight-saving transition. Use UTC for an exact alignment.')
        return None


def render(journal, rows):
    theme.section('Live session' if journal.dataset == 'live' else 'Replay session', 'plan · check in · review without placing orders')
    timer, drafts = st.tabs(['Session & timer', 'Pre-trade drafts'])
    with timer:
        active = [t for t in journal.trades() if not t.closed]
        left, right = st.columns([1, 1.5])
        with left:
            timer_panel(journal, active)
            checklists.run_panel(journal, prefix='session')
        with right:
            if active:
                trade = st.selectbox('Open position', active, format_func=lambda t: f'{t.account} · {t.symbol} {t.direction}',
                                     key=common.key(journal, 'session-position'))
                tag_controls.render(journal, trade)
            else:
                st.info('No captured open position. You can prepare a draft or run a checklist without creating a fake trade.')
            state = journal.workspace()
            events = sorted(state['checkins'].values(), key=lambda e: e['at'], reverse=True)[:30]
            if events:
                st.dataframe([{'Time (UTC)': e['at'], 'Group': e['group'],
                               'Tags': ', '.join(tagging.names(journal.tag_library(), e['tag_ids'])),
                               'Trade': e['trade_id'] or 'Session'} for e in events], hide_index=True, height=230)
    with drafts:
        draft_panel(journal, rows)


@st.fragment(run_every='2s')
def timer_panel(journal, active):
    state = journal.workspace()
    preferences = state['preferences']
    groups = list(tagging.ordered_groups(journal.tag_library()))
    interval = st.number_input('Check-in interval · minutes', min_value=1, max_value=120,
                               value=int(preferences.get('timer_minutes', 5)), key=common.key(journal, 'timer-minutes'))
    watched = st.multiselect('Check-in tag groups', groups,
                             default=[g for g in preferences.get('timer_groups', ['Mindset']) if g in groups],
                             key=common.key(journal, 'timer-groups'))
    if interval != preferences.get('timer_minutes', 5) or watched != preferences.get('timer_groups', ['Mindset']):
        journal.save_preferences(timer_minutes=interval, timer_groups=watched)
    key = common.key(journal, 'session-timer')
    running = st.session_state.get(key)
    cols = st.columns(4)
    if cols[0].button('Start timer', key=key + '-start'):
        st.session_state[key] = {'next': time.monotonic() + interval * 60, 'ack': []}
        running = st.session_state[key]
    if cols[1].button('Resume timer' if running and running.get('paused') else 'Pause timer', key=key + '-pause', disabled=not running):
        if running.get('paused'):
            running.update(next=time.monotonic() + running['remaining'], paused=False)
        else:
            running.update(remaining=max(0, running['next'] - time.monotonic()), paused=True)
    if cols[2].button('Reset interval', key=key + '-reset', disabled=not running):
        st.session_state[key] = {'next': time.monotonic() + interval * 60, 'ack': []}
        running = st.session_state[key]
    if cols[3].button('Stop timer', key=key + '-stop', disabled=not running):
        st.session_state.pop(key, None)
        running = None
    if not running:
        st.caption('Timer off · no sound, background notification or recording · never auto-starts.')
        return
    remaining = int(running['remaining']) if running.get('paused') else max(0, int(running['next'] - time.monotonic()))
    st.metric('Next check-in', f'{remaining // 60:02d}:{remaining % 60:02d}')
    if running.get('paused'):
        st.caption('Paused · check-ins remain saved · resume when ready')
        return
    if remaining:
        return
    library = journal.tag_library()
    due = [g for g in watched if g not in running['ack']]
    if not due:
        running.update(next=time.monotonic() + interval * 60, ack=[])
        return
    st.info('Time to check in · choose your current state')
    target = st.selectbox('Attach check-in to', [None, *[t.id for t in active]],
                          format_func=lambda id: next(f'{t.account} · {t.symbol}' for t in active if t.id == id) if id else 'Session only',
                          key=key + '-target')
    for group in due:
        options = [t['name'] for t in tagging.ordered_groups(library)[group]]
        choice = st.pills(group, options, key=key + '-check-' + group)
        if choice and st.button('Save ' + group + ' check-in', key=key + '-save-' + group):
            journal.record_checkin(group, [choice], trade_id=target)
            running['ack'].append(group)
            st.rerun(scope='fragment')


def draft_panel(journal, rows):
    st.caption('A draft is a plan, not a fill. Automatic attachment requires an exact provider/account/contract and an explicit '
               'platform-clock window. Only trades captured after arming qualify. Ambiguous matches attach nothing. '
               'Existing reviewed groups, notes and risk take precedence.')
    scopes = sorted({(r['provider'], r['account'], r['symbol']) for r in rows})
    if scopes:
        scope = st.selectbox('Next-trade source', scopes, format_func=lambda x: ' · '.join(x), key=common.key(journal, 'draft-scope'))
        latest = max(instant for instant in (pd.Timestamp(r['opened']).to_pydatetime() for r in rows))
        with st.form(common.key(journal, 'draft-form')):
            name = st.text_input('Draft name', 'Next setup', max_chars=80)
            start = clock_fields(journal, 'draft-start', 'Window starts', latest if journal.dataset == 'replay' else None)
            end = clock_fields(journal, 'draft-end', 'Window ends', (latest if journal.dataset == 'replay' else dt.datetime.now(ZoneInfo('America/New_York'))) + dt.timedelta(minutes=30))
            library = journal.tag_library()
            tags = []
            for group, choices in tagging.ordered_groups(library).items():
                options = [t['name'] for t in choices]
                values = st.pills(group, options, selection_mode='single' if library['groups'][group]['exclusive'] else 'multi',
                                  key=common.key(journal, 'draft-tags-' + group))
                tags.extend([values] if isinstance(values, str) else values or [])
            risk = st.number_input('Planned total risk · 0 leaves unknown', min_value=0., value=0.)
            notes = st.text_area('Pre-trade note', height=90)
            if st.form_submit_button('Arm this scoped draft', type='primary'):
                try:
                    journal.save_draft(name, provider=scope[0], account=scope[1], symbol=scope[2], start=start, end=end,
                                        tags=tags, notes=notes, risk=risk)
                    st.rerun()
                except ValueError as exc:
                    st.error(str(exc))
    else:
        st.info('Capture a trade first so the app knows your exact provider, account and contract. No source is guessed.')
    drafts = journal.workspace()['drafts']
    if drafts:
        st.dataframe([{'Plan': d['name'], 'Account': d['account'], 'Contract': d['symbol'], 'From': d['start'],
                       'Until': d['end'], 'State': d['state'], 'Trade': d['trade_id']} for d in drafts.values()], hide_index=True)
        active = [id for id, d in drafts.items() if d['state'] in ('armed', 'ambiguous', 'needs review')]
        if active:
            id = st.selectbox('Cancel draft', active, format_func=lambda id: drafts[id]['name'])
            if st.button('Cancel automatic attachment', key=common.key(journal, 'cancel-draft')):
                journal.cancel_draft(id)
                st.rerun()


