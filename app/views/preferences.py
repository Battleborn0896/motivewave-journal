"""Display settings, on the first Setup page: how times and excursions are shown, and which saved filter
every page uses. Each choice saves the moment it is made. Nothing here changes a fill or a review."""
import streamlit as st

import common

GLOBAL_FILTER = 'global-saved-filter'


def saved_filter_key(journal) -> str:
    return common.key(journal, GLOBAL_FILTER)


def chosen_filter(journal, saved: dict):
    """The saved filter every page uses, or None for all trades. A deleted filter falls back to None.

    The choice is kept under its own key, not the selectbox's: the selectbox is drawn only on Setup, and
    Streamlit forgets a widget's value on a page that does not draw it."""
    choice_key = saved_filter_key(journal)
    if st.session_state.get(choice_key) not in [None, *saved]:
        st.session_state[choice_key] = None
    return st.session_state.get(choice_key)


def render(journal, rows):
    preferences = journal.workspace()['preferences']
    current = common.display_zone(journal)
    choices = common.timezone_choices(current)
    zone_key = common.key(journal, 'display-zone')
    if st.session_state.get(zone_key) != current:
        st.session_state[zone_key] = current          # follows a change made in the other environment

    def save_zone():
        common.set_display_zone(journal, st.session_state[zone_key])

    zone_col, unit_col = st.columns([1.3, 2])
    zone_col.selectbox('Display time zone', choices, format_func=common.zone_label, key=zone_key, on_change=save_zone,
                       help='Every time in the journal is shown in this zone: the trade list, charts, stats and '
                            'exports. Stored fill times stay UTC. Applies to Live and Replay. The day-tag time zone '
                            'under Tags & Playbooks is separate, so existing day tags stay on their days.')
    with unit_col.container(horizontal=True, gap='small'):
        common.unit_selector(journal)                 # MAE / MFE units and accuracy, side by side
    filter_col, edit_col, weekend_col = st.columns([1.3, 1, 1], vertical_alignment='bottom')
    saved = journal.workspace()['filters']
    choice_key = saved_filter_key(journal)
    box_key = choice_key + '-box'
    # '' stands for all trades: a selectbox whose value is None shows an empty box instead of the words.
    st.session_state[box_key] = chosen_filter(journal, saved) or ''

    def choose():
        st.session_state[choice_key] = st.session_state[box_key] or None

    filter_col.selectbox('Saved filter on every page', ['', *saved], format_func=lambda name: name or 'All trades',
                         key=box_key, on_change=choose,
                         help='Shows only the trades this filter keeps, on Trades, Stats and every other page. It '
                              'combines with page and widget filters, and never changes your fills.')
    with edit_col.popover('Save a Filter', width='stretch',
                          help='Name a set of filters to reuse. Saving under an existing name replaces it.'):
        save_filter_form(journal, rows, saved.get(st.session_state.get(choice_key)) or {})
    weekend_key = common.key(journal, 'hide-weekends')
    st.session_state.setdefault(weekend_key, bool(preferences.get('hide_weekends', False)))
    weekend_col.checkbox('Hide weekends in calendar', key=weekend_key,
                         on_change=lambda: journal.save_preferences(hide_weekends=st.session_state[weekend_key]))


def save_filter_form(journal, rows, old: dict) -> None:
    from views import dashboard
    with st.form(common.key(journal, 'global-filter-form'), border=False):
        name = st.text_input('Filter name', max_chars=80)
        rule = dashboard.filter_editor(journal, rows, 'saved-global', old)
        if st.form_submit_button('Save Filter', type='primary'):
            try:
                journal.save_filter(name, rule)
                st.rerun()
            except ValueError as exc:
                st.error(str(exc))
