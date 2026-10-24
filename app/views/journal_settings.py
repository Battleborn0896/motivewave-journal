"""Journals & Accounts, a Data tab: the views the Journal menu in the sidebar offers, each a name and the accounts it
shows, and the names accounts are shown by. Nothing here changes a trade or its review."""
import streamlit as st
import common
import theme
from mwjournal import journals


def render(journal, rows):
    views = journals.registry(journal)['views']
    known = sorted({(fill.provider, fill.account) for fill in journal.executions()})
    theme.section('Journals & Accounts', help='A view shows only the accounts you choose, with the same reviews, tags '
                                              'and settings as All accounts.')
    theme.subsection('New View')
    if not known:
        st.caption('A view needs an account with trades: record or import a trade first.')
    else:
        with st.form(common.key(journal, 'new-view')):
            name = st.text_input('View name', max_chars=80, key=common.key(journal, 'new-view-name'))
            accounts = st.multiselect('Accounts', known, format_func=lambda scope: journals.alias(journal, scope),
                                      key=common.key(journal, 'new-view-accounts'))
            if st.form_submit_button('Create View', type='primary'):
                try:
                    id = journals.create(journal, name, accounts)
                    st.session_state[common.key(journal, 'journal-choice-next')] = id     # shown in the sidebar
                    st.rerun()
                except ValueError as exc:
                    st.error(str(exc))
    if views:
        theme.subsection('Views')
        selected = st.selectbox('View', list(views), format_func=lambda id: views[id]['name'],
                                key=common.key(journal, 'manage-view'))
        shown = ', '.join(journals.alias(journal, tuple(account)) for account in views[selected]['accounts'])
        st.caption(f'Shows {shown}. Deleting a view removes only the view: its trades and reviews stay in All '
                   'accounts.')
        with st.form(common.key(journal, 'rename-view-' + selected)):
            name = st.text_input('New name', views[selected]['name'], max_chars=80,
                                 key=common.key(journal, 'rename-view-name-' + selected))
            if st.form_submit_button('Rename View'):
                try:
                    journals.rename(journal, selected, name)
                    st.rerun()
                except ValueError as exc:
                    st.error(str(exc))
        if st.button('Delete View', key=common.key(journal, 'delete-view-' + selected)):
            journals.delete(journal, selected)
            st.rerun()
    if known:
        theme.section('Account Names')
        scope = st.selectbox('Account to rename', known, format_func=lambda s: journals.alias(journal, s))
        with st.form(common.key(journal, 'account-alias-form')):
            name = st.text_input('Display name', journals.alias(journal, scope), max_chars=80,
                                 help='A label only. Provider and account IDs in fills and order links stay unchanged.')
            if st.form_submit_button('Save Display Name'):
                try:
                    journals.account_alias(journal, *scope, name)
                    st.rerun()
                except ValueError as exc:
                    st.error(str(exc))
