"""Named local journal and display-only account organization. Capture remains read-only."""
import streamlit as st
import common
from mwjournal import journals


def render(journal, rows):
    capture = getattr(journal, 'capture_journal', journal)
    state = journals.registry(capture)
    st.markdown('## Journals & accounts')
    st.caption('The study always captures to the original Live / Replay journal. Named journals mirror chosen provider/account '
               'pairs into their own local database and have independent notes, tags and analytics. Archived journals are hidden, not deleted.')
    known_scopes = sorted({(fill.provider, fill.account) for fill in capture.executions()})
    with st.form(common.key(capture, 'new-named-journal')):
        name = st.text_input('New journal name', max_chars=80)
        scopes = st.multiselect('Capture accounts · empty mirrors all', known_scopes,
                                 format_func=lambda scope: journals.alias(capture, scope) + ' · ' + scope[0])
        if st.form_submit_button('Create local journal', type='primary'):
            try:
                id = journals.create(capture, name, scopes)
                st.session_state[common.key(capture, 'named-journal-next')] = id
                st.rerun()
            except (ValueError, OSError) as exc:
                st.error(str(exc))
    if state['journals']:
        selected = st.selectbox('Manage journal', list(state['journals']),
                                 format_func=lambda id: state['journals'][id]['name'] + (' · archived' if state['journals'][id]['archived'] else ''))
        old = state['journals'][selected]
        with st.form(common.key(capture, 'rename-named-' + selected)):
            name = st.text_input('Journal name', old['name'], max_chars=80)
            if st.form_submit_button('Rename journal'):
                try:
                    journals.rename(capture, selected, name)
                    st.rerun()
                except ValueError as exc:
                    st.error(str(exc))
        cols = st.columns(2)
        if cols[0].button('Restore journal' if old['archived'] else 'Archive journal', key=common.key(capture, 'archive-named-' + selected)):
            journals.archive(capture, selected, not old['archived'])
            st.rerun()
        with cols[1].popover('Clone journal', width='stretch'):
            with st.form(common.key(capture, 'clone-named-' + selected)):
                name = st.text_input('Cloned journal name', old['name'] + ' copy', max_chars=80)
                if st.form_submit_button('Clone into a separate local database'):
                    try:
                        id = journals.create(capture, name, old['scopes'], clone=selected)
                        st.session_state[common.key(capture, 'named-journal-next')] = id
                        st.rerun()
                    except (ValueError, OSError) as exc:
                        st.error(str(exc))
    if known_scopes:
        st.markdown('### Account display names')
        scope = st.selectbox('Account to rename', known_scopes, format_func=lambda s: ' · '.join(s))
        with st.form(common.key(capture, 'account-alias-form')):
            name = st.text_input('Display name', journals.alias(capture, scope), max_chars=80)
            st.caption('A label only. Provider/account IDs in fills and order links stay unchanged.')
            if st.form_submit_button('Save display name'):
                try:
                    journals.account_alias(capture, *scope, name)
                    st.rerun()
                except ValueError as exc:
                    st.error(str(exc))
