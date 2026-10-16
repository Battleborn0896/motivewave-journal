"""Click-first, non-destructive tag and playbook management."""
import streamlit as st

import common
from mwjournal import tagging


def render(journal, rows):
    st.markdown('## Tag library')
    st.caption('Build categories such as Orderflow or Psychology, then put reusable tags inside them. '
               'Renaming and retiring never delete a trade\'s history.')
    library = journal.tag_library()
    with st.container(border=True):
        st.markdown('### Create')
        category_col, tag_col = st.columns(2)
        with category_col:
            with st.form(common.key(journal, 'settings-new-category'), clear_on_submit=True):
                category_name = st.text_input('New category name', max_chars=40, placeholder='Orderflow')
                if st.form_submit_button('Create category', type='primary'):
                    try:
                        journal.add_tag_group(category_name)
                        st.rerun()
                    except ValueError as exc:
                        st.error(str(exc))
        with tag_col:
            with st.form(common.key(journal, 'settings-new-tag'), clear_on_submit=True):
                new_name = st.text_input('New tag name', max_chars=64, placeholder='Absorption at lows')
                new_group = st.selectbox('Category', list(library['groups']))
                if st.form_submit_button('Create tag', type='primary'):
                    try:
                        journal.add_tag(new_name, new_group)
                        st.rerun()
                    except ValueError as exc:
                        st.error(str(exc))
    tags_tab, groups_tab, books_tab = st.tabs(['Tags', 'Groups', 'Playbooks'])
    with tags_tab:
        ordered = sorted(library['tags'], key=lambda id: (library['tags'][id]['retired'], library['tags'][id]['group'],
                                                         library['tags'][id]['order']))
        id = st.selectbox('Edit tag', ordered, format_func=lambda id: library['tags'][id]['name']
                         + (' · retired' if library['tags'][id]['retired'] else ''), key=common.key(journal, 'edit-tag'))
        tag = library['tags'][id]
        with st.form(common.key(journal, 'tag-editor-' + id)):
            name = st.text_input('Tag name', tag['name'], max_chars=64)
            group = st.selectbox('Tag group', list(library['groups']), index=list(library['groups']).index(tag['group']))
            color = st.color_picker('Selected tag color', tag['color'])
            score = st.number_input('Tag score · positive or negative', value=float(tag.get('score', 0)))
            trade = st.checkbox('Available on trades', value='trade' in tag['levels'])
            day = st.checkbox('Available on days', value='day' in tag['levels'])
            retired = st.checkbox('Retired · keep history', value=tag['retired'])
            if st.form_submit_button('Save tag', type='primary'):
                try:
                    journal.edit_tag(id, name=name, group=group, color=color,
                                     levels=(['trade'] if trade else []) + (['day'] if day else []), retired=retired, score=score)
                    st.rerun()
                except ValueError as exc:
                    st.error(str(exc))
        left, right = st.columns(2)
        if left.button('↑ Move tag earlier', key=common.key(journal, 'tag-up')):
            journal.move_tag(id, -1)
            st.rerun()
        if right.button('↓ Move tag later', key=common.key(journal, 'tag-down')):
            journal.move_tag(id, 1)
            st.rerun()
    with groups_tab:
        group = st.selectbox('Edit group', sorted(library['groups'], key=lambda g: library['groups'][g]['order']),
                              key=common.key(journal, 'edit-group'))
        with st.form(common.key(journal, 'group-editor-' + group)):
            group_name = st.text_input('Group name', group, max_chars=40)
            exclusive = st.checkbox('One choice at a time · exclusive group', value=library['groups'][group]['exclusive'])
            st.caption('Existing conflicting historical tags are preserved; choosing a tag resolves that trade’s group.')
            if st.form_submit_button('Save group'):
                try:
                    journal.edit_tag_group(group, name=group_name, exclusive=exclusive)
                    st.rerun()
                except ValueError as exc:
                    st.error(str(exc))
        left, right = st.columns(2)
        if left.button('↑ Move group earlier', key=common.key(journal, 'group-up')):
            journal.edit_tag_group(group, move=-1)
            st.rerun()
        if right.button('↓ Move group later', key=common.key(journal, 'group-down')):
            journal.edit_tag_group(group, move=1)
            st.rerun()
        timezone = st.selectbox('Day-tag timezone', common.TIMEZONES,
                                 index=common.TIMEZONES.index(library['session_timezone']), key=common.key(journal, 'day-tag-zone'))
        if timezone != library['session_timezone']:
            journal.set_tag_timezone(timezone)
            st.rerun()
        st.caption('Days mean the calendar date of entry in this timezone, not an exchange-session roll. '
                   'Changing it does not migrate existing day reviews or tags.')
    with books_tab:
        books = library['playbooks']
        choice = st.selectbox('Edit playbook', ['＋ New playbook', *books],
                              format_func=lambda id: books[id]['name'] if id in books else id,
                              key=common.key(journal, 'edit-playbook'))
        old = books.get(choice, {})
        with st.form(common.key(journal, 'playbook-editor-' + choice)):
            name = st.text_input('Playbook name', old.get('name', ''), max_chars=64)
            retired = st.checkbox('Retire playbook · keep its definition', value=old.get('retired', False))
            st.caption('Choose acceptable alternatives within a group (OR). Every participating group must match (AND). '
                       'Other groups are unrestricted. This defines a setup, not a claim of profitability.')
            rules = {}
            for group, tags in tagging.ordered_groups(library, include_retired=True).items():
                options = [t['id'] for t in tags]
                selected = st.multiselect(group, options,
                                           default=[id for id in old.get('rules', {}).get(group, []) if id in options],
                                           format_func=lambda id: library['tags'][id]['name'])
                if selected:
                    rules[group] = selected
            if st.form_submit_button('Save playbook', type='primary'):
                try:
                    journal.save_playbook(name, rules, id=choice if choice in books else None, retired=retired)
                    st.rerun()
                except ValueError as exc:
                    st.error(str(exc))
