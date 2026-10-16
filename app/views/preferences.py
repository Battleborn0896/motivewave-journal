"""Display-only settings. No recording arm, API key, trade or collector mutation."""
import streamlit as st
import common


def render(journal, rows):
    state = journal.workspace()
    preferences = state['preferences']
    st.markdown('## General settings')
    with st.form(common.key(journal, 'general-settings')):
        zone = st.selectbox('Display timezone', common.TIMEZONES,
                            index=common.TIMEZONES.index(preferences.get('display_timezone', 'America/New_York')))
        weekends = st.checkbox('Hide weekends in calendar', value=preferences.get('hide_weekends', False))
        st.caption('Display timezone affects dashboard calendar and time comparisons; exact fill timestamps remain UTC. '
                   'Day-tag timezone is separate to preserve your existing session associations.')
        if st.form_submit_button('Save general settings', type='primary'):
            journal.save_preferences(display_timezone=zone, hide_weekends=weekends)
            st.success('Display preferences saved.')
