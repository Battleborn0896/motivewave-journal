"""Setup's settings: Time Zone and MAE / MFE Units side by side, then MAE / MFE accuracy and hiding weekends
under a small Advanced heading. Also the saved filter every page uses, which is chosen and saved in the
Filters menus of Trades and Stats, where filtering lives. Each choice saves the moment it is made. Nothing
here changes a fill or a review."""
import streamlit as st

import common
import theme
from mwjournal.workspace import combine_filters, filter_sample

GLOBAL_FILTER = 'global-saved-filter'


def saved_filter_key(journal) -> str:
    return common.key(journal, GLOBAL_FILTER)


def chosen_filter(journal, saved: dict):
    """The saved filter every page uses, or None for all trades. A deleted filter falls back to None.

    The choice is kept under its own key, not the selectbox's: the selectbox is drawn only inside a Filters
    menu, and Streamlit forgets a widget's value on a page that does not draw it."""
    choice_key = saved_filter_key(journal)
    if st.session_state.get(choice_key) not in [None, *saved]:
        st.session_state[choice_key] = None
    return st.session_state.get(choice_key)


def render(journal, rows):
    current = common.display_zone(journal)
    choices = common.timezone_choices(current)
    zone_key = common.key(journal, 'display-zone')
    if st.session_state.get(zone_key) != current:
        st.session_state[zone_key] = current          # follows a change made in the other environment

    def save_zone():
        common.set_display_zone(journal, st.session_state[zone_key])

    zone_col, unit_col = st.columns(2)
    zone_col.selectbox('Time Zone', choices, format_func=common.zone_label, key=zone_key, on_change=save_zone,
                       help='Every time in the journal is shown in this zone: the trade list, charts, stats and '
                            'exports. Stored fill times stay UTC. Applies to Live and Replay. The day-tag time zone '
                            'under Tags, Playbooks & Checklists is separate, so existing day tags stay on their days.')
    common.unit_selector(journal, unit_col)
    theme.subsection('Advanced')
    policy_col, weekend_col = st.columns(2, vertical_alignment='bottom')
    common.policy_selector(journal, policy_col)
    weekend_key = common.key(journal, 'hide-weekends')
    st.session_state.setdefault(weekend_key, bool(journal.workspace()['preferences'].get('hide_weekends', False)))
    weekend_col.checkbox('Hide Weekends in Calendar', key=weekend_key,
                         on_change=lambda: journal.save_preferences(hide_weekends=st.session_state[weekend_key]),
                         help='Leaves Saturday and Sunday out of the calendar widget on Stats → Dashboards.')


def filter_menu(journal, rows) -> list[dict]:
    """What a Filters menu holds, on Trades and on Stats: the saved filter every page uses, this page's own
    filters, and a name and Save Filter right under them. Returns the trades this page's filters keep.
    `rows` have already passed the saved filter."""
    saved = journal.workspace()['filters']
    choice_key = saved_filter_key(journal)
    box_key = choice_key + '-box'
    # '' stands for all trades: a selectbox whose value is None shows an empty box instead of the words.
    st.session_state[box_key] = chosen_filter(journal, saved) or ''

    def choose():
        st.session_state[choice_key] = st.session_state[box_key] or None

    st.selectbox('Saved Filter on Every Page', ['', *saved], format_func=lambda name: name or 'All trades',
                 key=box_key, on_change=choose,
                 help='Shows only the trades this filter keeps, on Trades, Stats and every other page. It '
                      'combines with the filters below and with dashboard filters, and never changes your fills.')
    rule = common.filter_rule(journal, rows)
    if rows:
        save_row(journal, rule, saved.get(st.session_state.get(choice_key)) or {})
    return filter_sample(rows, rule)


def save_row(journal, rule, old: dict) -> None:
    """Saves the trades shown as one filter: the saved filter in use (`old`) and this page's filters (`rule`).
    That filter is then the one in use and this page's filters are cleared, so the list stays the same.
    Enter in the name box saves too."""
    name_key = common.key(journal, 'save-filter-name')
    with st.form(common.key(journal, 'save-filter'), border=False):
        name_col, button_col = st.columns([3, 2], vertical_alignment='bottom')
        name = name_col.text_input('Filter Name', max_chars=80, key=name_key, placeholder='Filter Name',
                                   label_visibility='collapsed')
        if button_col.form_submit_button('Save Filter', type='primary', width='stretch',
                                         help='Saves the trades shown now as a filter for every page: the saved '
                                              'filter in use together with the filters above. Saving under an '
                                              'existing name replaces that filter.'):
            try:
                st.session_state[saved_filter_key(journal)] = journal.save_filter(name, combine_filters(old, rule))
            except ValueError as exc:
                st.error(str(exc))
            else:
                for widget in [*common.filter_keys(journal), name_key]:
                    st.session_state.pop(widget, None)
                st.rerun()
