"""Tag-based weighted process ratings; distinct from manual stars and actual P&L."""
import streamlit as st
import common
import theme
from mwjournal import tagging


def render(journal, rows):
    theme.section('Rating Systems', help='A process rating scores how well a trade followed your rules, 0 to 100, '
                  'from weighted tag criteria. Tag score is the sum of signed tag values. Neither forecasts profit, '
                  'and your own 1 to 5 stars stay separate. Show a rating with a Rating widget on a dashboard.')
    library = journal.tag_library()
    models = journal.workspace()['ratings']
    selected = st.selectbox('Rating system', [None, *models], format_func=lambda id: models[id]['name'] if id else '+ New system',
                             key=common.key(journal, 'rating-model'))
    definition = models.get(selected, {})
    rules_key = common.key(journal, 'rating-draft-' + str(selected))
    st.session_state.setdefault(rules_key, definition.get('rules', []))
    rules = st.session_state[rules_key]
    name = st.text_input('System name', definition.get('name', 'Followed my plan'), key=common.key(journal, 'rating-name-' + str(selected)))
    with st.form(common.key(journal, 'rating-criterion-' + str(selected))):
        criterion = st.text_input('Criterion name', 'Plan followed', max_chars=80,
                                  help='Any chosen tag in a category satisfies that category; every category with '
                                       'a chosen tag is required.')
        weight = st.number_input('Criterion weight', min_value=.01, value=1.)
        combination, when = {}, {}
        for group, tags in tagging.ordered_groups(library).items():
            selected_tags = st.multiselect(group, [t['id'] for t in tags], format_func=lambda id: library['tags'][id]['name'])
            if selected_tags:
                combination[group] = selected_tags
        condition = st.checkbox('Only evaluate when a playbook matches')
        playbooks = library['playbooks']
        book = st.selectbox('Conditional playbook', [None, *playbooks],
                              format_func=lambda id: playbooks[id]['name'] if id else 'Always applicable')
        if st.form_submit_button('Add Criterion'):
            if not combination:
                st.error('Choose at least one tag.')
            elif condition and not book:
                st.error('Choose a conditional playbook.')
            else:
                st.session_state[rules_key] = [*rules, {'name': criterion, 'weight': weight, 'rules': combination,
                                                        'when': playbooks[book]['rules'] if condition else {}}]
                st.rerun()
    if rules:
        st.dataframe([{'Criterion': r['name'], 'Weight': r['weight'],
                       'Tags': ', '.join(tagging.names(library, [id for ids in r['rules'].values() for id in ids])),
                       'Conditional': bool(r.get('when'))} for r in rules], hide_index=True)
        choice = st.selectbox('Remove criterion', range(len(rules)), format_func=lambda i: rules[i]['name'])
        if st.button('Remove Criterion', key=common.key(journal, 'rating-remove')):
            st.session_state[rules_key] = [r for i, r in enumerate(rules) if i != choice]
            st.rerun()
    if st.button('Save Rating System', type='primary', key=common.key(journal, 'rating-save')):
        try:
            journal.save_rating(name, rules, id=selected)
            st.success('Rating system saved. Choose it in a Dashboard Rating widget.')
        except ValueError as exc:
            st.error(str(exc))
