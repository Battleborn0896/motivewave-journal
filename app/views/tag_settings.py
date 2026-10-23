"""Tags, Playbooks & Checklists, a Settings page: click-first, non-destructive tag and playbook management, and the
checklist editor. Running a checklist happens on Trades, in the selected trade's Checklist tab."""
import streamlit as st

import common
import theme
from mwjournal import tagging
from views import checklists

TABS = ('Tags', 'Categories', 'Playbooks', 'Checklists')


def render(journal, rows):
    theme.section('Tags, Playbooks & Checklists', help='Categories such as Setup or Mindset hold reusable tags. '
                  'Renaming and retiring never change a trade\'s history; only an unused tag or an empty category can '
                  'be deleted. Checklists are built here and run on Trades, in a trade\'s Checklist tab.')
    library = journal.tag_library()
    tags_tab, groups_tab, books_tab, checks_tab = common.tabs(TABS, common.key(journal, 'tag-settings-tabs'))
    with tags_tab:
        create(journal, library)
        ordered = sorted(library['tags'], key=lambda id: (library['tags'][id]['retired'], library['tags'][id]['group'],
                                                         library['tags'][id]['order']))
        if ordered:
            tag_editor(journal, library, ordered)
        else:
            st.info('No tags yet. Create one above.')
    with groups_tab:
        if library['groups']:
            group_editor(journal, library)
        else:
            st.info('No categories yet. Create one under Tags.')
    with books_tab:
        playbooks(journal, library)
    if checks_tab.open:                        # the node editor is only drawn when its tab is open
        with checks_tab:
            checklists.render(journal, rows)


def create(journal, library):
    """A new category and a new tag, side by side."""
    with st.container(border=True):
        st.markdown('### Create')
        category_col, tag_col = st.columns(2)
        with category_col:
            with st.form(common.key(journal, 'settings-new-category'), clear_on_submit=True):
                category_name = st.text_input('New category name', max_chars=40, placeholder='Orderflow')
                if st.form_submit_button('Create Category', type='primary'):
                    try:
                        journal.add_tag_group(category_name)
                        st.rerun()
                    except ValueError as exc:
                        st.error(str(exc))
        with tag_col:
            with st.form(common.key(journal, 'settings-new-tag'), clear_on_submit=True):
                new_name = st.text_input('New tag name', max_chars=64, placeholder='Absorption at lows')
                new_group = st.selectbox('Category', list(library['groups']))
                if st.form_submit_button('Create Tag', type='primary'):
                    try:
                        journal.add_tag(new_name, new_group or '')
                        st.rerun()
                    except ValueError as exc:
                        st.error(str(exc))


def playbooks(journal, library):
    books = library['playbooks']
    choice = st.selectbox('Edit playbook', ['＋ New playbook', *books],
                          format_func=lambda id: books[id]['name'] if id in books else '+ New playbook',
                          key=common.key(journal, 'edit-playbook'))
    old = books.get(choice, {})
    with st.form(common.key(journal, 'playbook-editor-' + choice)):
        name = st.text_input('Playbook name', old.get('name', ''), max_chars=64,
                             help='Choose the tags that fit within each category: any one of them will do. '
                                  'Every category with a chosen tag must match; other categories are free. This '
                                  'describes a setup, not a claim that it makes money.')
        color = st.color_picker('Playbook color', old.get('color', '#65CA98'),
                                help='Its name is drawn in this color where Stats compares playbooks.')
        retired = st.checkbox('Retire playbook · keep its definition', value=old.get('retired', False))
        rules = {}
        for group, tags in tagging.ordered_groups(library, include_retired=True).items():
            options = [t['id'] for t in tags]
            selected = st.multiselect(group, options,
                                       default=[id for id in old.get('rules', {}).get(group, []) if id in options],
                                       format_func=lambda id: library['tags'][id]['name'])
            if selected:
                rules[group] = selected
        if st.form_submit_button('Save Playbook', type='primary'):
            try:
                journal.save_playbook(name, rules, id=choice if choice in books else None, retired=retired, color=color)
                st.rerun()
            except ValueError as exc:
                st.error(str(exc))


def tag_editor(journal, library, ordered):
    if st.session_state.get(common.key(journal, 'edit-tag')) not in ordered:
        st.session_state.pop(common.key(journal, 'edit-tag'), None)     # a tag deleted a moment ago
    id = st.selectbox('Edit tag', ordered, format_func=lambda id: library['tags'][id]['name']
                     + (' · retired' if library['tags'][id]['retired'] else ''), key=common.key(journal, 'edit-tag'))
    tag = library['tags'][id]
    with st.form(common.key(journal, 'tag-editor-' + id)):
        name = st.text_input('Tag name', tag['name'], max_chars=64)
        group = st.selectbox('Category', list(library['groups']), index=list(library['groups']).index(tag['group']))
        color = st.color_picker('Selected tag color', tag['color'])
        score = st.number_input('Tag score · positive or negative', value=float(tag.get('score', 0)))
        trade = st.checkbox('Available on trades', value='trade' in tag['levels'])
        day = st.checkbox('Available on days', value='day' in tag['levels'])
        retired = st.checkbox('Retired · keep history', value=tag['retired'])
        if st.form_submit_button('Save Tag', type='primary'):
            try:
                journal.edit_tag(id, name=name, group=group, color=color,
                                 levels=(['trade'] if trade else []) + (['day'] if day else []), retired=retired, score=score)
                st.rerun()
            except ValueError as exc:
                st.error(str(exc))
    left, right, delete = st.columns(3)
    if left.button('↑ Move Tag Up', key=common.key(journal, 'tag-up')):
        journal.move_tag(id, -1)
        st.rerun()
    if right.button('↓ Move Tag Down', key=common.key(journal, 'tag-down')):
        journal.move_tag(id, 1)
        st.rerun()
    if delete.button('Delete Tag', key=common.key(journal, 'tag-delete'),
                     help='Only a tag no trade, day, playbook, filter, checklist or draft uses; retire a used one instead.'):
        try:
            journal.delete_tag(id)
            st.rerun()
        except ValueError as exc:
            st.error(str(exc))


def group_editor(journal, library):
    if st.session_state.get(common.key(journal, 'edit-group')) not in library['groups']:
        st.session_state.pop(common.key(journal, 'edit-group'), None)   # a category deleted a moment ago
    group = st.selectbox('Edit category', sorted(library['groups'], key=lambda g: library['groups'][g]['order']),
                          key=common.key(journal, 'edit-group'))
    with st.form(common.key(journal, 'group-editor-' + group)):
        group_name = st.text_input('Category name', group, max_chars=40)
        exclusive = st.checkbox('One choice at a time', value=library['groups'][group]['exclusive'],
                                help='Choosing a tag in this category clears the others on that trade. Trades that '
                                     'already have two keep them until you choose again.')
        if st.form_submit_button('Save Category'):
            try:
                journal.edit_tag_group(group, name=group_name, exclusive=exclusive)
                st.rerun()
            except ValueError as exc:
                st.error(str(exc))
    left, right, delete = st.columns(3)
    if left.button('↑ Move Category Up', key=common.key(journal, 'group-up')):
        journal.edit_tag_group(group, move=-1)
        st.rerun()
    if right.button('↓ Move Category Down', key=common.key(journal, 'group-down')):
        journal.edit_tag_group(group, move=1)
        st.rerun()
    if delete.button('Delete Category', key=common.key(journal, 'group-delete'),
                     help='Only an empty category that no playbook, timer or checklist names.'):
        try:
            journal.delete_tag_group(group)
            st.rerun()
        except ValueError as exc:
            st.error(str(exc))
