"""The first page of Settings: Time Zone and MAE / MFE Units side by side, nothing else to set. MAE / MFE are measured
from recorded ticks only: there is no accuracy to choose. Also the saved filter every page uses, which is chosen,
saved, renamed and deleted in the Filters menus of Trades and Stats, where filtering lives. Each choice saves the
moment it is made. Nothing here changes a fill or a review."""
import streamlit as st

import common
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
    st.session_state[zone_key] = current              # follows a change made in the other environment

    def save_zone():
        common.set_display_zone(journal, st.session_state[zone_key])

    zone_col, unit_col = st.columns(2)
    zone_col.selectbox('Time Zone', choices, format_func=common.zone_label, key=zone_key, on_change=save_zone,
                       help='Times and trading days, with their day tags and reviews, follow this zone in Live and '
                            'Replay; a day runs midnight to midnight.')
    common.unit_selector(journal, unit_col)


def filters_label(journal, rows) -> str:
    """'Filters · 2', the button that opens a Filters menu, counted before the menu is drawn: the saved filter in
    use counts 1, and so does each filter of the menu's own (common.active_filters). 'Filters' when none is on."""
    count = bool(chosen_filter(journal, journal.workspace()['filters'])) + common.active_filters(
        common.filter_state(journal, rows))
    return f'Filters · {count}' if count else 'Filters'


def filter_menu(journal, rows) -> list[dict]:
    """What a Filters menu holds, on Trades and on Stats: the saved filter every page uses, with Rename and Delete
    beside it while one is chosen, this page's own filters, a name and Save Filter right under them, and Clear
    Filters. Returns the trades this page's filters keep. `rows` have already passed the saved filter."""
    saved = journal.workspace()['filters']
    choice_key = saved_filter_key(journal)
    box_key = choice_key + '-box'
    # '' stands for all trades: a selectbox whose value is None shows an empty box instead of the words.
    chosen = st.session_state[box_key] = chosen_filter(journal, saved) or ''

    def choose():
        st.session_state[choice_key] = st.session_state[box_key] or None

    choice_col, actions = st.columns(2, vertical_alignment='bottom')       # the halves of Markets and Entry Dates
    choice_col.selectbox('Saved Filter on Every Page', ['', *saved], format_func=lambda name: name or 'All trades',
                         key=box_key, on_change=choose,
                         help='Shows only the trades it keeps on every page, together with the filters below and '
                              'dashboard filters; your fills never change.')
    if chosen:
        rename_col, delete_col = actions.columns([3, 1.2], vertical_alignment='bottom')
        with rename_col:
            rename(journal, chosen)

        def delete():
            try:
                journal.delete_filter(chosen)
            except ValueError:
                pass                           # already gone: nothing to delete
            st.session_state[choice_key] = None          # back to All trades

        delete_col.button('Delete', key=common.key(journal, 'delete-filter'), on_click=delete, width='stretch')
    rule = common.filter_rule(journal, rows)
    save_col, clear_col = st.columns([5, 2], vertical_alignment='bottom')
    if rows:
        with save_col:
            save_row(journal, rule, saved.get(chosen) or {})

    def clear():
        common.empty_filters(journal)
        st.session_state[choice_key] = None

    clear_col.button('Clear Filters', key=common.key(journal, 'clear-filters'), on_click=clear, width='stretch',
                     help='Empties these filters and goes back to All trades.')
    return filter_sample(rows, rule)


def rename(journal, name) -> None:
    """A new name for the saved filter in use, which stays in use under it. Enter in the name box renames too."""
    with st.form(common.key(journal, 'rename-filter'), border=False):
        name_col, button_col = st.columns([3, 2], vertical_alignment='bottom')
        # The filter's name is part of the key: once renamed, the box is a new, empty one.
        new = name_col.text_input('New Name', max_chars=80, key=common.key(journal, 'rename-filter-' + name),
                                  placeholder='New Name', label_visibility='collapsed')
        if button_col.form_submit_button('Rename', width='stretch'):
            try:
                st.session_state[saved_filter_key(journal)] = journal.rename_filter(name, new)
            except ValueError as exc:
                st.error(str(exc))
            else:
                st.rerun()


def save_row(journal, rule, old: dict) -> None:
    """Saves the trades shown as one filter: the saved filter in use (`old`) and this page's filters (`rule`).
    That filter is then the one in use and this page's filters are cleared, so the list stays the same.
    Enter in the name box saves too."""
    name_key = common.key(journal, 'save-filter-name' + common.filter_generation(journal))
    with st.form(common.key(journal, 'save-filter'), border=False):
        name_col, button_col = st.columns([3, 2], vertical_alignment='bottom')
        name = name_col.text_input('Filter Name', max_chars=80, key=name_key, placeholder='Filter Name',
                                   label_visibility='collapsed')
        if button_col.form_submit_button('Save Filter', type='primary', width='stretch',
                                         help='Saves the trades shown now, the saved filter in use with the filters '
                                              'above, as a filter; an existing name is replaced.'):
            try:
                st.session_state[saved_filter_key(journal)] = journal.save_filter(name, combine_filters(old, rule))
            except ValueError as exc:
                st.error(str(exc))
            else:
                common.empty_filters(journal)      # new widgets for the menu: the browser would show the old ones'
                st.rerun()
