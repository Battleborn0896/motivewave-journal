"""Visible grouped tag buttons and local autosave, inspired by Tradecraft's documented workflow."""
import streamlit as st

import common
import theme
from mwjournal import tagging
from mwjournal.analytics import summary
from mwjournal.tags import DEFAULT_CATALOG


def save_field(journal, trade_id, field, widget_key):
    value = st.session_state.get(widget_key)
    journal.update_annotation(trade_id, {field: value.strip() if isinstance(value, str) else value})


def save_group(journal, trade, group, widget_key):
    selected = st.session_state.get(widget_key) or []
    journal.set_trade_tag_group(trade, group, [selected] if isinstance(selected, str) else selected)


def _starter_ids(library):
    """The untouched suggestions shipped with the app; renamed/moved tags are user-owned."""
    return {id for id, tag in library['tags'].items()
            if DEFAULT_CATALOG.get(tag['name']) == tag['group']}


def _referenced_ids(library, annotation, session):
    """Keep starter choices visible on this trade without cluttering every other trade."""
    return tagging.selected(library, annotation) | tagging.inherited(library, session)


def _creation_tools(journal, trade, library, visible_groups):
    left, right = st.columns(2)
    with left.popover('＋ Category', width='stretch'):
        st.markdown('**Create a tag category**')
        st.caption('Examples: Orderflow, Psychology, Entry quality')
        with st.form(common.key(journal, 'new-category-' + trade.id), clear_on_submit=True):
            name = st.text_input('Category name', max_chars=40, placeholder='Orderflow')
            if st.form_submit_button('Create category', type='primary'):
                try:
                    journal.add_tag_group(name)
                    st.rerun()
                except ValueError as exc:
                    st.error(str(exc))
    with right.popover('＋ Tag', width='stretch'):
        st.markdown('**Create a reusable tag**')
        available = list(visible_groups)
        choice = st.selectbox('Tag category', [*available, '＋ New category'],
                              index=0 if available else 0,
                              key=common.key(journal, 'new-tag-group-' + trade.id))
        with st.form(common.key(journal, 'new-tag-' + trade.id), clear_on_submit=True):
            name = st.text_input('Tag name', max_chars=64, placeholder='Absorption at lows')
            chosen_group = (st.text_input('New category name', max_chars=40, placeholder='Orderflow')
                            if choice == '＋ New category' else choice)
            if st.form_submit_button('Create and apply tag', type='primary'):
                try:
                    journal.add_tag(name, chosen_group)
                    journal.update_annotation(trade.id, {}, add_tags=[name])
                    st.rerun()
                except ValueError as exc:
                    st.error(str(exc))


def render(journal, trade):
    annotations = journal.annotations()
    annotation = annotations.get(trade.id, {})
    library = journal.tag_library()
    session = journal.get('sessions').get(tagging.session_key(library, trade.opened), {})
    chosen = tagging.effective(library, annotation, session)
    show_starters_key = common.key(journal, 'show-starter-tags')
    starter = _starter_ids(library)
    referenced = _referenced_ids(library, annotation, session)
    show_starters = bool(st.session_state.get(show_starters_key))
    starter_groups = set(DEFAULT_CATALOG.values())
    ordered = sorted(library['groups'], key=lambda group: library['groups'][group]['order'])
    group_tags = {}
    for group in ordered:
        tags = sorted((tag for tag in library['tags'].values()
                       if tag['group'] == group and 'trade' in tag['levels'] and not tag['retired']),
                      key=lambda tag: tag['order'])
        # Once a category has a used or user-created tag, show its complete set so exclusive groups
        # can switch cleanly between alternatives. Entirely untouched starter categories stay out of the way.
        active_group = any(tag['id'] not in starter or tag['id'] in referenced for tag in tags)
        visible = tags if show_starters or active_group else []
        if visible or group not in starter_groups:
            group_tags[group] = visible
    groups = {group: [t['name'] for t in tags] for group, tags in group_tags.items()}
    st.markdown(theme.tag_group_css(groups), unsafe_allow_html=True)
    st.markdown(theme.tag_colors_css(group_tags), unsafe_allow_html=True)
    with st.container(border=True, height=360, key='tag-workspace-' + trade.id):
        st.markdown('### Tags')
        st.caption('Create your own categories and click tags to apply them. Everything autosaves locally.')
        _creation_tools(journal, trade, library, groups)
        if not groups:
            st.info('Start by creating a category such as Orderflow or Psychology, then add tags to it.')
        for group, names in groups.items():
            if not names:
                st.markdown(f'**{group}**')
                st.caption('No tags in this category yet. Use ＋ Tag above to add one.')
                continue
            group_key = common.key(journal, f'tag-group-{trade.id}-{group}')
            values = [t for t in tagging.names(library, chosen) if t in names]
            exclusive = library['groups'][group]['exclusive']
            if exclusive and len(values) > 1:
                st.caption(f'{group}: conflicting historical choices retained. Selecting one resolves this group.')
            st.session_state[group_key] = values[0] if exclusive and values else None if exclusive else values
            with st.container(key=theme.tag_group_key(group)):
                st.pills(group, names, selection_mode='single' if exclusive else 'multi', key=group_key, on_change=save_group,
                          args=(journal, trade, group, group_key))
        rating_key = common.key(journal, 'rating-' + trade.id)
        st.pills('Process rating', [1, 2, 3, 4, 5], default=annotation.get('rating'), key=rating_key,
                  format_func=lambda n: f'{n}★', on_change=save_field,
                  args=(journal, trade.id, 'rating', rating_key))
        st.caption(f'Saved locally · {len(chosen)} tags · day tags use {library["session_timezone"]}')
        with st.expander('More tagging tools'):
            if starter:
                st.toggle('Show starter examples', key=show_starters_key,
                          help='Shows the optional Setup, Execution and Mindset suggestions that ship with the app.')
            with st.popover('Copy tags from another trade', width='stretch'):
                saved = annotations
                donors = [t for t in journal.trades() if t.id != trade.id and saved.get(t.id, {}).get('tags')]
                if not donors:
                    st.caption('Tag another trade first, then reuse its tags here.')
                else:
                    donor = st.selectbox('Copy from trade', donors,
                                         format_func=lambda t: f'{t.opened[:16]} · {t.symbol} · {len(saved[t.id]["tags"])} tags',
                                         key=common.key(journal, 'tag-donor-' + trade.id))
                    st.caption('Adds their tags to this trade. Existing tags, notes and risk stay intact.')
                    if st.button('Apply copied tags', key=common.key(journal, 'tag-copy-' + trade.id)):
                        try:
                            journal.update_annotation(trade.id, {}, add_tags=saved[donor.id]['tags'])
                            st.rerun()
                        except ValueError as exc:
                            st.error(str(exc))
            from mwjournal.workspace import tag_score, rating
            st.caption(f'Tag score {tag_score(library, chosen):+g} · independent from P&L and manual stars')
            for definition in journal.workspace()['ratings'].values():
                value = rating(library, chosen, definition)
                st.caption(definition['name'] + ': ' + (f'{value:.0f}/100' if value is not None else 'Not applicable'))
            retired = [library['tags'][id]['name'] for id in chosen if library['tags'][id]['retired']]
            if retired:
                st.caption('Retired tags retained: ' + ', '.join(retired))
            st.button('Manage tags & playbooks', on_click=common.go('Tags'), key=common.key(journal, 'manage-tags-' + trade.id))
            playbook_panel(journal, trade, library, chosen)


def playbook_panel(journal, trade, library, chosen):
    books = [p for p in library['playbooks'].values() if not p.get('retired')]
    matching = tagging.matched_playbooks(library, chosen)
    if matching:
        st.success('Matches: ' + ' · '.join(matching))
    if books:
        with st.popover('Apply playbook combination', width='stretch'):
            book = st.selectbox('Playbook', books, format_func=lambda p: p['name'],
                                 key=common.key(journal, 'apply-playbook-' + trade.id))
            st.caption('OR within each group; AND between groups. Existing choices outside these groups stay intact.')
            st.caption('Exclusive alternatives keep your matching choice, or select the first saved alternative.')
            if st.button('Apply combination', key=common.key(journal, 'apply-combo-' + trade.id)):
                try:
                    journal.apply_playbook(trade, book['id'])
                    st.rerun()
                except ValueError as exc:
                    st.error(str(exc))
    with st.popover('Check this tag combination', width='stretch'):
        names = tagging.names(library, chosen)
        options = sorted(set(names) | {t['name'] for t in library['tags'].values() if not t['retired']})
        required = st.multiselect('Required tags · all must match', options, default=names,
                                  key=common.key(journal, 'combo-check-' + trade.id))
        interpretation = st.radio('Combination logic', ['All selected tags', 'OR within groups'],
                                   key=common.key(journal, 'combo-logic-' + trade.id))
        st.caption('These hypothetical choices do not change this trade’s tags. OR accepts alternatives within a group; '
                   'every participating group must still match.')
        if required:
            rules = {}
            for id in tagging.ids(library, required):
                rules.setdefault(library['tags'][id]['group'], []).append(id)
            rows = [r for r in common.rows_for(journal) if r['id'] != trade.id and r['status'] == 'closed'
                    and r['closed'] <= trade.opened and r['currency'] == trade.currency
                    and (set(required) <= set(r['tags']) if interpretation == 'All selected tags'
                         else tagging.matches(library, r['tag_ids'], {'rules': rules}))]
            s = summary(rows)
            win_rate = f"{s['win_rate']:.1f}%" if s['win_rate'] is not None else '—'
            st.write(f"{s['count']} earlier closed trades · win rate {win_rate} · "
                     f"average net {common.money(s['expectancy'])} {trade.currency}")
            st.caption('Excludes this trade and later entries. Describes a sample, not a prediction or proven edge.')


def day_panel(journal, date):
    library = journal.tag_library()
    groups = tagging.ordered_groups(library, level='day')
    if not groups:
        st.caption('No day-level tags yet. Enable “Available on days” in Settings → Tags & playbooks.')
        return
    session = journal.get('sessions').get(f'{date}:{library["session_timezone"]}', {})
    chosen = tagging.inherited(library, session)
    st.markdown('### Day tags')
    st.markdown(theme.tag_colors_css(groups), unsafe_allow_html=True)
    st.caption(f'Inherited by trades opened on {date} in {library["session_timezone"]}, including trades captured later. '
               'Individual trade choices/unchecked inherited tags override the day selection.')
    for group, tags in groups.items():
        choices = [t['name'] for t in tags]
        selected = [t for t in tagging.names(library, chosen) if t in choices]
        exclusive = library['groups'][group]['exclusive']
        key = common.key(journal, 'day-tags-' + str(date) + '-' + group)
        st.session_state[key] = selected[0] if exclusive and selected else None if exclusive else selected
        def save_day(group=group, key=key):
            values = st.session_state.get(key) or []
            journal.set_day_tag_group(date, group, [values] if isinstance(values, str) else values)
        with st.container(key=theme.tag_group_key(group)):
            st.pills(group, choices, selection_mode='single' if exclusive else 'multi', key=key, on_change=save_day)
