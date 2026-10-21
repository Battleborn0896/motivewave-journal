"""The tag panel: every category as a card with all of its tags showing. One click applies or removes
a tag and saves it; new categories and tags are made from the two small buttons at the top."""
import hashlib
import html
from collections import Counter

import streamlit as st

import common
import theme
from mwjournal import tagging
from mwjournal.analytics import summary
from mwjournal.tags import LEGACY_CATALOG

PANEL_CSS = f"""
<style>
[class*="st-key-tag-panel-"] {{ background:{theme.SURFACE}; padding:12px !important; border-radius:6px; gap:10px; }}
[class*="st-key-tag-panel-"] h3 {{ font-size:17px !important; margin:0; }}
[class*="st-key-tag-cards"] {{ display:grid !important; grid-template-columns:repeat(auto-fill, minmax(112px, 1fr));
    gap:8px !important; align-items:start; }}
[class*="st-key-tag-cards"] > * {{ width:auto !important; min-width:0 !important; max-width:none !important; }}
[class*="st-key-tag-cards"] [class*="st-key-tag-colour-"] {{ border:1px solid {theme.BORDER}; border-radius:6px;
    padding:8px 9px 10px !important; background:{theme.PAGE}; gap:6px; height:100%; }}
[class*="st-key-tag-cards"] [data-testid="stButtonGroup"] > div {{ flex-direction:column; align-items:stretch; gap:4px; }}
[class*="st-key-tag-cards"] [data-testid="stButtonGroup"] button {{ width:100%; height:auto !important;
    min-height:28px; justify-content:flex-start; text-align:left; }}
[class*="st-key-tag-cards"] [data-testid="stButtonGroup"] button p {{ white-space:normal; text-align:left;
    line-height:1.25; overflow:visible; }}
[class*="st-key-tag-panel-"] [data-testid="stButtonGroup"] button {{ font-family:{theme.FONT}; border-radius:4px;
    background:transparent; border:1px solid {theme.BASELINE}; padding:4px 8px; }}
[class*="st-key-tag-panel-"] [data-testid="stButtonGroup"] button p {{ color:inherit !important; }}
[class*="st-key-tag-panel-"] button[role="radio"][aria-checked="true"] {{ color:{theme.ACCENT} !important;
    border-color:{theme.ACCENT} !important; background:{theme.ACCENT_DEEP} !important; }}
</style>
"""


def save_field(journal, trade_id, field, widget_key):
    value = st.session_state.get(widget_key)
    journal.update_annotation(trade_id, {field: value.strip() if isinstance(value, str) else value})


def save_group(journal, trade, group, widget_key):
    selected = st.session_state.get(widget_key) or []
    journal.set_trade_tag_group(trade, group, [selected] if isinstance(selected, str) else selected)


def _starter_ids(library):
    """The untouched hidden suggestions of a library from 0.4.0 or earlier; a library that was
    offered the starter categories shows all of its tags, so it has none."""
    if library.get('starters'):
        return set()
    return {id for id, tag in library['tags'].items() if LEGACY_CATALOG.get(tag['name']) == tag['group']}


def _referenced_ids(library, annotation, session):
    """Keep starter choices visible on this trade without cluttering every other trade."""
    return tagging.selected(library, annotation) | tagging.inherited(library, session)


def group_colour(group, tags) -> str:
    """The colour a category is known by: its built-in one, else the colour most of its tags use."""
    if group in theme.TAG_COLOURS:
        return theme.TAG_COLOURS[group]
    colours = Counter(t['color'] for t in tags if t.get('color'))
    if colours and colours.most_common(1)[0][0] != '#65CA98':
        return colours.most_common(1)[0][0]
    return theme.SERIES[int(hashlib.sha256(group.encode()).hexdigest()[:8], 16) % len(theme.SERIES)]


def headers_css(groups) -> str:
    """Each card's title in its category's colour, scoped by the same hashed class as the tags."""
    return '<style>' + ''.join(f'.st-key-{theme.tag_group_key(group)} [data-testid="stWidgetLabel"] p, '
                               f'.st-key-{theme.tag_group_key(group)} .mwj-card-title {{color:{colour} !important; '
                               f'font-weight:650;}}' for group, colour in groups.items()
                               if colour.startswith('#') and len(colour) == 7) + '</style>'


def render(journal, trade):
    annotations = journal.annotations()
    annotation = annotations.get(trade.id, {})
    library = journal.tag_library()
    session = journal.get('sessions').get(tagging.session_key(library, trade.opened), {})
    chosen = tagging.effective(library, annotation, session)
    show_starters_key = common.key(journal, 'show-starter-tags')
    starter = _starter_ids(library)
    referenced = _referenced_ids(library, annotation, session)
    ordered = sorted(library['groups'], key=lambda group: library['groups'][group]['order'])
    every = {group: sorted((tag for tag in library['tags'].values()
                            if tag['group'] == group and 'trade' in tag['levels'] and not tag['retired']),
                           key=lambda tag: tag['order']) for group in ordered}

    # Only a library from 0.4.0 or earlier hides anything: its untouched suggestions, and the empty
    # categories named after them.
    hidden_names = set() if library.get('starters') else set(LEGACY_CATALOG.values())
    # Also an empty 'Other tags': older versions made it whenever a note was saved. It holds tags
    # nobody filed in a category, so with none it is not a category anyone made; Setup still lists it.
    hidden_names.add('Other tags')

    def visible_groups(show_starters):
        groups = {}
        for group, tags in every.items():
            used = any(tag['id'] not in starter or tag['id'] in referenced for tag in tags)
            if show_starters or used or (not tags and group not in hidden_names):
                groups[group] = tags
        return groups

    show_starters = bool(st.session_state.get(show_starters_key))
    group_tags = visible_groups(show_starters)
    fallback = bool(starter) and not any(group_tags.values())
    if fallback:
        group_tags = visible_groups(True)     # never open on an empty panel when suggestions exist
    groups = {group: [t['name'] for t in tags] for group, tags in group_tags.items()}
    st.markdown(PANEL_CSS + theme.tag_group_css(groups) + theme.tag_colors_css(group_tags)
                + headers_css({g: group_colour(g, tags) for g, tags in group_tags.items()}), unsafe_allow_html=True)
    with st.container(key='tag-panel-' + trade.id):
        st.markdown('### Tags', help='Click a tag to apply it, click again to remove it. Changes save at once. '
                                     f'Day tags follow the trading day in {library["session_timezone"]}.')
        if not groups:
            st.info('No tag categories yet. Use + Category below to make one.')
        with st.container(key='tag-cards-' + trade.id):
            for group, names in groups.items():
                exclusive = library['groups'][group]['exclusive']
                with st.container(key=theme.tag_group_key(group)):
                    if not names:
                        st.markdown(f"<div class='mwj-card-title'>{html.escape(group)}</div>", unsafe_allow_html=True)
                        st.caption('No tags yet · + Tag adds one')
                        continue
                    group_key = common.key(journal, f'tag-group-{trade.id}-{group}')
                    values = [t for t in tagging.names(library, chosen) if t in names]
                    st.session_state[group_key] = values[0] if exclusive and values else None if exclusive else values
                    conflict = exclusive and len(values) > 1
                    st.pills(group, names, selection_mode='single' if exclusive else 'multi', key=group_key,
                             on_change=save_group, args=(journal, trade, group, group_key),
                             help='One choice at a time.' + (' This trade has older conflicting choices; picking one '
                                                             'resolves them.' if conflict else '') if exclusive else None)
        rating_key = common.key(journal, 'rating-' + trade.id)
        st.pills('Process rating', [1, 2, 3, 4, 5], default=annotation.get('rating'), key=rating_key,
                 # A Material star icon: IBM Plex Sans has no '★', so the browser drew it in another font.
                 format_func=lambda n: f'{n} :material/star:', on_change=save_field,
                 args=(journal, trade.id, 'rating', rating_key), help='How well you followed your process, 1 to 5.')
        with st.container(horizontal=True, gap='small'):
            _creation_tools(journal, trade, groups)
        with st.expander('More Tagging Tools'):
            if starter and not fallback:
                st.toggle('Show starter examples', key=show_starters_key,
                          help='Shows the Setup, Execution and Mindset suggestions that came with the app.')
            with st.popover('Copy Tags From Another Trade', width='stretch'):
                saved = annotations
                donors = [t for t in journal.trades() if t.id != trade.id and saved.get(t.id, {}).get('tags')]
                if not donors:
                    st.caption('Tag another trade first, then reuse its tags here.')
                else:
                    zone = common.display_zone(journal)
                    donor = st.selectbox('Copy from trade', donors,
                                         format_func=lambda t: f'{common.moment(t.opened, zone, seconds=False)} · {t.symbol} · '
                                                               f'{len(saved[t.id]["tags"])} tags',
                                         key=common.key(journal, 'tag-donor-' + trade.id),
                                         help='Adds that trade’s tags to this one. Tags, notes and stop already here stay.')
                    if st.button('Apply Copied Tags', key=common.key(journal, 'tag-copy-' + trade.id)):
                        try:
                            journal.update_annotation(trade.id, {}, add_tags=saved[donor.id]['tags'])
                            st.rerun()
                        except ValueError as exc:
                            st.error(str(exc))
            from mwjournal.workspace import tag_score, rating
            st.caption(f'Tag score {tag_score(library, chosen):+g}',
                       help='The sum of the scores set on each chosen tag in Setup → Tags, Playbooks & Checklists. Separate from P&L and stars.')
            for definition in journal.workspace()['ratings'].values():
                value = rating(library, chosen, definition)
                st.caption(definition['name'] + ': ' + (f'{value:.0f}/100' if value is not None else 'Not applicable'))
            retired = [library['tags'][id]['name'] for id in chosen if library['tags'][id]['retired']]
            if retired:
                st.caption('Retired tags kept on this trade: ' + ', '.join(retired))
            st.button('Manage Tags & Playbooks', on_click=common.go('Tags'), key=common.key(journal, 'manage-tags-' + trade.id))
            playbook_panel(journal, trade, library, chosen)


def _creation_tools(journal, trade, groups):
    """'+ Category' and '+ Tag' as small text buttons beside the panel title."""
    with st.popover('+ Category', type='tertiary', help='Make a new tag category, such as Orderflow.'):
        with st.form(common.key(journal, 'new-category-' + trade.id), clear_on_submit=True, border=False):
            name = st.text_input('Category name', max_chars=40, placeholder='Orderflow')
            if st.form_submit_button('Create Category', type='primary'):
                try:
                    journal.add_tag_group(name)
                    st.rerun()
                except ValueError as exc:
                    st.error(str(exc))
    with st.popover('+ Tag', type='tertiary', help='Make a new tag and apply it to this trade.'):
        # The choice keeps a full-width plus, which no category name typed as '+ New category' can match;
        # it is shown with '+', which IBM Plex Sans draws.
        choice = st.selectbox('Tag category', [*groups, '＋ New category'], index=0,
                              format_func=lambda g: '+ New category' if g == '＋ New category' else g,
                              key=common.key(journal, 'new-tag-group-' + trade.id))
        with st.form(common.key(journal, 'new-tag-' + trade.id), clear_on_submit=True, border=False):
            name = st.text_input('Tag name', max_chars=64, placeholder='Absorption at lows')
            chosen_group = (st.text_input('New category name', max_chars=40, placeholder='Orderflow')
                            if choice == '＋ New category' else choice)
            if st.form_submit_button('Create and Apply Tag', type='primary'):
                try:
                    journal.add_tag(name, chosen_group)
                    journal.update_annotation(trade.id, {}, add_tags=[name])
                    st.rerun()
                except ValueError as exc:
                    st.error(str(exc))


def playbook_panel(journal, trade, library, chosen):
    books = [p for p in library['playbooks'].values() if not p.get('retired')]
    matching = tagging.matched_playbooks(library, chosen)
    if matching:
        st.success('Matches: ' + ' · '.join(matching))
    if books:
        with st.popover('Apply Playbook', width='stretch'):
            book = st.selectbox('Playbook', books, format_func=lambda p: p['name'],
                                key=common.key(journal, 'apply-playbook-' + trade.id),
                                help='Chooses the playbook’s tags in their categories. Choices in other categories stay. '
                                     'In a one-choice category your matching choice is kept, else its first tag is chosen.')
            if st.button('Apply Combination', key=common.key(journal, 'apply-combo-' + trade.id)):
                try:
                    journal.apply_playbook(trade, book['id'])
                    st.rerun()
                except ValueError as exc:
                    st.error(str(exc))
    with st.popover('Check This Combination', width='stretch'):
        names = tagging.names(library, chosen)
        options = sorted(set(names) | {t['name'] for t in library['tags'].values() if not t['retired']})
        required = st.multiselect('Required tags · all must match', options, default=names,
                                  key=common.key(journal, 'combo-check-' + trade.id),
                                  help='Try other tags here without changing this trade.')
        interpretation = st.radio('Combination logic', ['All selected tags', 'OR within groups'],
                                  key=common.key(journal, 'combo-logic-' + trade.id),
                                  help='OR accepts any of the chosen tags within a category; every category must still match.')
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
            st.write(common.md(f"{s['count']} earlier closed trades · win rate {win_rate} · "
                               f"average net {common.money(s['expectancy'], trade.currency)}"))
            st.caption('Earlier trades only · a description, not a prediction',
                       help='Excludes this trade and anything entered after it. A small sample can mislead.')


def day_panel(journal, date):
    library = journal.tag_library()
    groups = tagging.ordered_groups(library, level='day')
    if not groups:
        st.caption('No day tags yet. Turn on “Available on days” for a tag in Setup → Tags, Playbooks & Checklists.')
        return
    session = journal.get('sessions').get(f'{date}:{library["session_timezone"]}', {})
    chosen = tagging.inherited(library, session)
    day = common.day_label(date, library['session_timezone'], weekday=True)
    st.markdown('### Day Tags', help=f'Every trade opened on {day} ({library["session_timezone"]}) gets these, '
                                     'including trades captured later. A choice made on a trade wins over the day.')
    st.markdown(theme.tag_colors_css(groups), unsafe_allow_html=True)
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
