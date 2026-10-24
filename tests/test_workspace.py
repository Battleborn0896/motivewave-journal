"""Local parity workflows on isolated paper fixtures; no broker, recording or network."""
import copy
import datetime as dt
import itertools
import json
import re
import zipfile
from concurrent.futures import ThreadPoolExecutor

import pytest

import common
import runtime
import theme
from mwjournal import Execution, Journal
from mwjournal import backup, tagging
from mwjournal import workspace
from mwjournal.workspace import (validate_graph, replay_run, tag_score, filter_sample, combine_filters,
                                 validate_filter, checklist_questions, compile_checklist, question)
from test_app import menu_label, open_app, show, press, widget, said
from test_chart import seed_preview, seed_ticks
from views import checklists
from views.checklists import starter


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    monkeypatch.setenv('MWJ_HOME', str(tmp_path))
    yield tmp_path
    runtime.stop_all()


def test_workspace_reads_do_not_save_definitions_or_rearm_anything(home):
    journal = Journal()

    def stored():
        with journal.connect() as db:
            return db.execute("SELECT value FROM journal_meta WHERE key='workspace'").fetchone()[0]
    made = json.loads(stored())                        # a new journal holds only this computer's time zone
    assert made.pop('preferences') == {'display_timezone': journal.time_zone()}
    assert all(not value for name, value in made.items() if name != 'version')
    made = stored()
    assert journal.workspace()['dashboards'] == {}
    journal.reconcile_drafts()
    assert stored() == made


def test_signed_tag_scores_preserve_raw_trade(home):
    journal = seed_preview(home)
    trade = journal.trades()[0]
    before = journal.executions()
    lib = journal.tag_library()
    assert not tagging.scored(lib)
    patient = next(iter(tagging.ids(lib, ['Patient'])))
    fomo = next(iter(tagging.ids(lib, ['FOMO'])))
    journal.edit_tag(patient, score=2)
    journal.edit_tag(fomo, score=-3)
    lib = journal.tag_library()
    assert tagging.scored(lib)
    assert tag_score(lib, [patient, fomo]) == -1
    assert tag_score(lib, [patient, fomo], [patient]) == 2
    journal.set_trade_tag_group(trade, 'Mindset', ['Patient'])
    assert common.rows_for(journal)[0]['tag_score'] == 2
    assert journal.executions() == before
    with pytest.raises(ValueError):
        journal.edit_tag(patient, score=float('nan'))


def test_saved_filters_remain_attached_after_rename_and_layer_by_intersection(home):
    journal = seed_preview(home)
    trade = journal.trades()[0]
    journal.set_trade_tag_group(trade, 'Setup', ['Pullback'])
    journal.save_filter('Pullbacks', {'tags_all': ['Pullback'], 'accounts': ['paper']})
    lib = journal.tag_library()
    id = next(iter(tagging.ids(lib, ['Pullback'])))
    journal.edit_tag(id, name='VWAP pullback')
    assert len(filter_sample(common.rows_for(journal), journal.workspace()['filters']['Pullbacks'])) == 1
    assert not filter_sample(filter_sample(common.rows_for(journal), {'accounts': ['paper']}), {'directions': ['short']})
    with pytest.raises(ValueError):
        journal.save_filter('Bad', {'tags_all': ['Not a tag']})


def test_a_saved_filter_is_renamed_to_a_free_name_and_deleted(home):
    journal = Journal()
    journal.save_filter('Longs', {'directions': ['long']})
    journal.save_filter('Shorts', {'directions': ['short']})
    assert journal.rename_filter('Longs', '  Long trades ') == 'Long trades'          # label(): trimmed
    assert journal.workspace()['filters']['Long trades']['directions'] == ['long']
    assert set(journal.workspace()['filters']) == {'Long trades', 'Shorts'}
    for old, new, message in [('Long trades', 'Shorts', 'already named Shorts'), ('Gone', 'Other', 'no saved filter'),
                              ('Shorts', ' ', 'Use a name'), ('Shorts', 'x' * 81, 'Use a name')]:
        with pytest.raises(ValueError, match=message):
            journal.rename_filter(old, new)
    assert journal.rename_filter('Shorts', 'Shorts') == 'Shorts'                       # its own name is not taken
    journal.delete_filter('Shorts')
    assert set(journal.workspace()['filters']) == {'Long trades'}
    with pytest.raises(ValueError, match='no saved filter'):
        journal.delete_filter('Shorts')
    assert Journal(dataset='replay').workspace()['filters'] == {}


def test_mae_and_mfe_limits_are_read_in_the_chosen_units(home):
    journal = seed_ticks(seed_preview(home))              # MAE 1 point ($100), MFE 2.5 points ($250)
    rule = {'mae_max': 2, 'mfe_min': 2}
    assert not filter_sample(common.rows_for(journal), rule)                        # $100 and $250
    journal.set_excursion_unit('points')
    rows = common.rows_for(journal)
    assert (rows[0]['display_mae'], rows[0]['display_mfe']) == (1, 2.5)
    assert filter_sample(rows, rule) == rows                                        # the same limit, in points
    journal.set_excursion_unit('ticks')
    assert not filter_sample(common.rows_for(journal), rule)                        # 4 and 10 ticks


def test_numeric_filters_exclude_missing_values_and_reject_reversed_bounds(home):
    journal = seed_preview(home)
    journal.save_filter('Positive R', {'r_min': 1, 'net_min': 0, 'duration_max': 10})
    assert len(filter_sample(common.rows_for(journal), journal.workspace()['filters']['Positive R'])) == 1
    assert not filter_sample(common.rows_for(journal), {'mfe_min': 0})  # tick-only: no valid prices, not zero
    journal.update_annotation(journal.trades()[0].id, {'planned_stop': None})      # and no stop order: no R
    assert not filter_sample(common.rows_for(journal), {'r_min': 0})
    with pytest.raises(ValueError):
        journal.save_filter('Invalid', {'net_min': 100, 'net_max': 0})


def test_saved_filters_keep_trades_by_playbook_process_rating_and_status(home):
    journal = seed_preview(home)
    trade = journal.trades()[0]
    book = journal.save_playbook('Calm breakout', {'Setup': ['Breakout'], 'Mindset': ['Patient']})
    assert not filter_sample(common.rows_for(journal), {'playbook': book})
    journal.set_trade_tag_group(trade, 'Setup', ['Breakout'])
    journal.set_trade_tag_group(trade, 'Mindset', ['Patient'])
    journal.update_annotation(trade.id, {'rating': 4})
    journal.save_filter('Calm fours', {'playbook': book, 'ratings': [5, 4], 'status': ['closed']})
    rule = journal.workspace()['filters']['Calm fours']
    assert rule['playbook'] == book and rule['ratings'] == [4, 5]
    rows = common.rows_for(journal)
    assert filter_sample(rows, rule) == rows
    assert not filter_sample(rows, {**rule, 'ratings': [5]})
    assert not filter_sample(rows, {**rule, 'status': ['open', 'incomplete']})
    assert not filter_sample([{k: v for k, v in rows[0].items() if k != 'playbook_ids'}], rule)
    journal.save_playbook('Calm breakout', {'Setup': ['Breakout'], 'Mindset': ['Patient']}, id=book, retired=True)
    assert filter_sample(common.rows_for(journal), rule)            # a retired playbook still names its trades
    with pytest.raises(ValueError, match='unknown playbook'):
        journal.save_filter('Bad', {'playbook': 'not a playbook'})
    for ratings in ([0], [6], ['4'], [True], [4.5], 4):
        with pytest.raises(ValueError, match='Ratings must be whole numbers from 1 to 5'):
            journal.save_filter('Bad', {'ratings': ratings})
    assert 'Bad' not in journal.workspace()['filters']


def sample_rows():
    """Synthetic rows varied in every field a filter reads."""
    rows = []
    fields = itertools.product(['a', 'b', 'c'], ['ESZ6', 'NQZ6'], ['long', 'short'], [None, 4, 5],
                               [[], ['x'], ['x', 'y', 'p'], ['q', 'n'], ['y', 'm', 'p']],
                               ['2026-10-03', '2026-10-07', '2026-11-02'], [-20.0, 60.0, 700.0])
    for i, (account, symbol, direction, process, tags, day, net) in enumerate(fields):
        rows.append({'id': str(i), 'account': account, 'symbol': symbol, 'market': symbol[:2], 'direction': direction,
                     'status': 'open' if i % 7 == 0 else 'closed', 'rating': process, 'tag_ids': tags,
                     'playbook_ids': ['book'] if 'x' in tags else [], 'entry_day': day, 'net': net, 'r': net / 20,
                     'mae': None, 'mfe': None, 'max_qty': 1 + i % 3, 'tag_score': 0,
                     'opened': day + 'T14:00:00Z', 'closed': day + f'T14:{i % 9:02d}:30Z'})
    return rows


FIRST = {'accounts': ['a', 'b'], 'symbols': ['ESZ6', 'NQZ6'], 'markets': [], 'directions': ['long'],
         'status': ['closed', 'open'], 'ratings': [4, 5], 'tags_all': ['x'], 'tags_any': ['p', 'q'],
         'tags_none': ['n'], 'playbook': 'book', 'start': '2026-10-01', 'end': '2026-10-31',
         'net_min': 0.0, 'r_max': 30.0, 'quantity_min': 2.0}
SECOND = {'accounts': ['b', 'c'], 'symbols': [], 'markets': ['NQ'], 'directions': ['long', 'short'],
          'status': ['closed'], 'ratings': [5], 'tags_all': ['y'], 'tags_any': ['p', 'q', 'r'], 'tags_none': ['m'],
          'playbook': 'book', 'start': '2026-10-05', 'end': '2026-11-30', 'net_min': 50.0, 'net_max': 500.0,
          'r_max': 20.0}


def test_two_filters_combine_into_one_rule_field_by_field():
    assert combine_filters(FIRST, SECOND) == {
        'accounts': ['b'], 'symbols': ['ESZ6', 'NQZ6'], 'markets': ['NQ'], 'directions': ['long'],
        'status': ['closed'], 'ratings': [5], 'tags_all': ['x', 'y'], 'tags_any': ['p', 'q'], 'tags_none': ['m', 'n'],
        'playbook': 'book', 'start': '2026-10-05', 'end': '2026-10-31', 'net_min': 50.0, 'net_max': 500.0,
        'r_max': 20.0, 'quantity_min': 2.0}
    assert combine_filters(SECOND, FIRST) == combine_filters(FIRST, SECOND)
    assert combine_filters({}, FIRST) == combine_filters(FIRST, {}) == FIRST       # nothing in use: the rule itself


def test_any_of_one_tag_is_that_tag_required_and_a_choice_a_required_tag_meets_asks_nothing_more():
    # Neither is two different 'any of these tags' choices, so both save as one filter instead of being refused.
    empty = combine_filters({}, {})
    assert (combine_filters({'tags_any': ['p', 'q']}, {'tags_any': ['y']})
            == combine_filters({'tags_any': ['y']}, {'tags_any': ['p', 'q']})
            == {**empty, 'tags_all': ['y'], 'tags_any': ['p', 'q']})
    assert (combine_filters({'tags_any': ['p', 'q']}, {'tags_all': ['p'], 'tags_any': ['m', 'n']})
            == {**empty, 'tags_all': ['p'], 'tags_any': ['m', 'n']})
    assert combine_filters({}, {'tags_any': ['p']})['tags_all'] == ['p']


@pytest.mark.parametrize('first, second', [
    (FIRST, SECOND), ({'accounts': ['a']}, {'directions': ['short'], 'tags_any': ['x', 'p']}),
    ({'tags_all': ['y'], 'end': '2026-10-07'}, {'tags_none': ['m'], 'net_max': 60.0}),
    ({'markets': ['ES', 'NQ'], 'ratings': [4]}, {'markets': ['ES'], 'quantity_max': 2, 'tags_any': ['p']}),
    ({'tags_any': ['x', 'y', 'p']}, {'tags_any': ['x', 'y']}),     # one choice inside the other joins
    ({'tags_any': ['x', 'y', 'p']}, {'tags_any': ['x']}),          # any of one tag is that tag required
    ({'tags_any': ['p', 'q']}, {'tags_all': ['p'], 'tags_any': ['m', 'n']}),     # met by a required tag
])
def test_the_combined_rule_keeps_exactly_the_trades_both_rules_keep(first, second):
    rows = sample_rows()
    kept = filter_sample(filter_sample(rows, first), second)
    assert kept and filter_sample(rows, combine_filters(first, second)) == kept


@pytest.mark.parametrize('first, second, message', [
    ({'tags_any': ['p', 'q']}, {'tags_any': ['q', 'x']}, "Two different 'any of these tags' choices cannot be saved"),
    ({'playbook': 'one'}, {'playbook': 'two'}, 'Two different playbooks cannot be saved as one filter.'),
    ({'directions': ['long']}, {'directions': ['short']}, 'No trade can pass all of these filters'),
    ({'ratings': [1]}, {'ratings': [5]}, 'No trade can pass all of these filters'),
    ({'start': '2026-10-10'}, {'end': '2026-10-09'}, 'No trade can pass all of these filters'),
    ({'net_min': 10}, {'net_max': 5}, 'No trade can pass all of these filters'),
])
def test_two_filters_no_single_rule_can_say_are_refused_in_one_line(first, second, message):
    with pytest.raises(ValueError) as refused:
        combine_filters(first, second)
    assert message in str(refused.value) and '\n' not in str(refused.value)


def test_filters_saved_before_playbooks_and_process_ratings_still_load_combine_and_save(home):
    journal = seed_preview(home)
    rows = common.rows_for(journal)
    old = {'accounts': ['paper'], 'symbols': [], 'markets': [], 'directions': [], 'status': [],
           'tags_all': rows[0]['tag_ids'][:1], 'tags_any': [], 'tags_none': [], 'net_min': 0.0}   # as 0.4.5 saved it
    with journal.connect() as db:
        state = workspace.read(db)
        state['filters']['Old'] = old
        workspace.write(db, state)
    saved = journal.workspace()['filters']['Old']
    assert filter_sample(rows, saved) == rows
    assert validate_filter(saved, journal.tag_library()) == {**old, 'ratings': []}
    journal.save_filter('Old longs', combine_filters(saved, {'directions': ['long'], 'ratings': []}))
    assert filter_sample(rows, journal.workspace()['filters']['Old longs']) == rows
    app = open_app(review=False)
    widget(app.selectbox, 'Saved Filter on Every Page').set_value('Old').run()
    assert not app.exception and menu_label(app) == 'Filters · 1'


def test_dashboard_pages_widgets_clone_and_partial_writes_are_dataset_local(home):
    journal = Journal()
    id = journal.save_dashboard('Overview', [{'kind': 'Equity', 'metric': 'R'}, {'kind': 'Calendar'}], columns=2)
    page = journal.workspace()['dashboards'][id]
    assert len({w['id'] for w in page['widgets']}) == 2
    journal.save_filter('Longs', {'directions': ['long']})
    journal.save_dashboard('Renamed', page['widgets'][::-1], id=id, columns=1)
    assert journal.workspace()['filters']['Longs']['directions'] == ['long']
    assert Journal(dataset='replay').workspace()['dashboards'] == {}
    before = journal.workspace()
    with pytest.raises(ValueError):
        journal.save_dashboard('Bad', [{'kind': 'eval arbitrary code'}])
    with pytest.raises(ValueError, match='up to 24 widgets'):
        journal.save_dashboard('Crowded', [{'kind': 'Summary'}] * 25)
    assert journal.workspace() == before
    # A page can be empty: a new one starts that way, and its last widget can be removed.
    empty = journal.save_dashboard('Empty', [])
    assert journal.workspace()['dashboards'][empty]['widgets'] == []
    journal.save_dashboard('Renamed', [], id=id, columns=1)
    assert journal.workspace()['dashboards'][id]['widgets'] == []


def test_the_calendar_decides_its_weekends_and_there_is_nothing_to_set_for_them(home):
    journal = Journal()
    with pytest.raises(ValueError, match='Unknown workspace preference'):
        journal.save_preferences(hide_weekends=True)
    assert 'hide_weekends' not in journal.workspace()['preferences']


def test_concurrent_workspace_preferences_and_filters_do_not_clobber(home):
    journal = Journal()
    def write(i):
        if i % 2:
            journal.save_filter('filter-' + str(i), {'directions': ['long']})
        else:
            journal.save_preferences(timer_minutes=5 + i)
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(write, range(8)))
    assert len(journal.workspace()['filters']) == 4
    assert journal.workspace()['preferences']['timer_minutes'] >= 5


@pytest.mark.parametrize('change', ['unknown-tag', 'unknown-link', 'duplicate', 'automatic-loop', 'question-to-question'])
def test_graph_validation_rejects_unknowns_and_automatic_loops(home, change):
    graph = compile_checklist(starter())
    if change == 'unknown-tag':
        graph['nodes'][1]['tag_ids'] = ['Not a tag']
    elif change == 'unknown-link':
        graph['nodes'][1]['next'] = ['missing']
    elif change == 'duplicate':
        graph['nodes'].append(copy.deepcopy(graph['nodes'][1]))
    elif change == 'automatic-loop':
        graph['nodes'][1]['next'] = ['q1-yes']
    else:
        graph['nodes'][0]['next'] = ['q1']
    with pytest.raises(ValueError):
        validate_graph(graph, Journal().tag_library())


@pytest.mark.parametrize('questions, says', [
    ([], 'A checklist has 1 to 40 questions.'),
    ([question('   ')], 'Type question 1, in at most 400 characters.'),
    ([question('Planned?', no={'go': 2})], 'Question 1 branches to a question this checklist does not have.'),
    ([question('Planned?', yes={'tag_ids': ['Not a tag']})], 'Checklist references an unknown tag.')])
def test_a_checklist_with_an_empty_question_or_a_branch_to_nowhere_is_not_saved(home, questions, says):
    journal = Journal()
    with pytest.raises(ValueError, match=re.escape(says)):
        journal.save_checklist('Plan', questions)
    assert not journal.workspace()['checklists']


def test_the_list_compiles_to_the_graph_and_back_unchanged(home):
    patient = tagging.identity('Patient')
    questions = [question('Did I wait for my setup?', yes={'tag_ids': [patient]}, no={'go': 3, 'count': True}),
                 question('Is my stop placed?', no={'go': 'finish'}),
                 question('Did I trade fewer contracts after a loss?', yes={'go': 1, 'count': True})]
    graph = compile_checklist(questions)
    assert [(n['id'], n['kind'], n['next']) for n in graph['nodes']] == [
        ('q1', 'question', ['q1-yes', 'q1-no']), ('q1-yes', 'answer', ['q2']),
        ('q1-no', 'answer', ['q1-no-count']), ('q1-no-count', 'counter', ['q3']),
        ('q2', 'question', ['q2-yes', 'q2-no']), ('q2-yes', 'answer', ['q3']), ('q2-no', 'answer', []),
        ('q3', 'question', ['q3-yes', 'q3-no']), ('q3-yes', 'answer', ['q3-yes-count']),
        ('q3-yes-count', 'counter', ['q1']), ('q3-no', 'answer', [])]
    assert graph['start'] == 'q1' and not any(n['kind'] == 'pass' for n in graph['nodes'])
    assert next(n for n in graph['nodes'] if n['id'] == 'q1-yes')['tag_ids'] == [patient]   # tags sit on the answer
    assert checklist_questions(graph) == questions
    stored = validate_graph(graph, Journal().tag_library())         # as saved: positions and colours added
    assert checklist_questions(stored) == questions and compile_checklist(checklist_questions(stored)) == graph
    # Going on to the next question, or finishing after the last, is the default however it was said.
    said_long = [question('One?', yes={'go': 2}), question('Two?', no={'go': 'finish'})]
    assert checklist_questions(compile_checklist(said_long)) == [question('One?'), question('Two?')]


def test_checklist_branches_tags_counter_loops_undo_and_definition_snapshots(home):
    journal = seed_preview(home)
    trade = journal.trades()[0]
    definition = journal.save_checklist('Plan', [question('Plan checked?', yes={'go': 1, 'tag_ids': ['Patient'],
                                                                                 'count': True})])
    assert journal.workspace()['checklists'][definition]['runs_on'] == 'trade'
    run = journal.start_checklist(definition, trade.id)
    result = journal.answer_checklist(run, 'q1-yes')
    assert result['current'] == 'q1' and result['counters'] == {'q1-yes-count': 1}   # asked again
    assert result['answer_events'][0]['action'] == 'answer' and result['answer_events'][0]['at']
    assert tagging.names(journal.tag_library(), result['tag_ids']) == ['Patient']
    assert 'Patient' not in journal.annotations()[trade.id]['tags']  # no silent apply
    journal.answer_checklist(run, 'q1-yes')
    assert journal.workspace()['runs'][run]['counters']['q1-yes-count'] == 2
    journal.answer_checklist(run, undo=True)
    assert journal.workspace()['runs'][run]['counters']['q1-yes-count'] == 1
    assert journal.workspace()['runs'][run]['answer_events'][-1]['action'] == 'undo'
    journal.save_checklist('Plan revised', [question('Changed question')], id=definition)
    assert journal.workspace()['runs'][run]['graph']['nodes'][0]['text'] == 'Plan checked?'   # the run's own copy
    assert len(journal.workspace()['checklists'][definition]['backups']) == 1
    result = journal.answer_checklist(run, 'q1-no')
    assert result['complete']
    with pytest.raises(ValueError):
        journal.answer_checklist(run, 'q1-yes')
    # Deleting the checklist leaves the run, with its copy.
    journal.delete_checklist(definition)
    assert not journal.workspace()['checklists'] and journal.workspace()['runs'][run]['complete']


def test_a_run_is_on_one_trade_or_one_day(home):
    journal = seed_preview(home)
    definition = journal.save_checklist('Morning', starter(), runs_on='session')
    id = journal.start_checklist(definition, day='2026-10-07')
    run = journal.workspace()['runs'][id]
    assert run['day'] == '2026-10-07' and 'trade_id' not in run          # stored under its date
    for wrong in ({}, {'trade_id': journal.trades()[0].id, 'day': '2026-10-07'}):
        with pytest.raises(ValueError, match='one trade or on one day'):
            journal.start_checklist(definition, **wrong)
    with pytest.raises(ValueError, match='runs on each trade or before the session'):
        journal.save_checklist('Morning', starter(), runs_on='weekly')


def test_checkin_clock_groups_and_trade_scope_are_explicit(home):
    journal = seed_preview(home)
    trade = journal.trades()[0]
    id = journal.record_checkin('Mindset', ['Patient'], trade_id=trade.id, at=trade.opened)
    assert journal.workspace()['checkins'][id]['at'] == trade.opened
    with pytest.raises(ValueError):
        journal.record_checkin('Mindset', ['Breakout'])
    with pytest.raises(ValueError):
        Journal(dataset='replay').record_checkin('Mindset', ['Patient'], trade_id=trade.id)


def test_scoped_draft_attaches_once_and_never_changes_raw_fills_or_existing_review(home):
    journal = Journal()
    id = journal.save_draft(provider='motivewave', account='paper', symbol='ESZ6', tags=['Patient'],
                            notes='Draft note', stop=4990)
    journal = seed_preview(home)
    trade = journal.trades()[0]
    before = journal.executions()
    journal.reconcile_drafts()
    assert journal.workspace()['drafts'][id]['state'] == 'attached'
    assert 'Patient' in journal.annotations()[trade.id]['tags']
    assert journal.annotations()[trade.id]['planned_stop'] == 4998          # the stop already saved wins
    assert journal.annotations()[trade.id]['notes'] == 'Synthetic fixture, not an actual account trade.'
    journal.update_annotation(trade.id, {}, remove_tags=['Patient'])
    Journal().reconcile_drafts()
    assert 'Patient' not in journal.annotations()[trade.id]['tags']
    assert journal.executions() == before


@pytest.mark.parametrize('field,value', [('provider', 'another'), ('account', 'another'), ('symbol', 'ESM6')])
def test_draft_does_not_match_root_alias_or_other_account_provider(home, field, value):
    journal = seed_preview(home)
    trade = journal.trades()[0]
    args = {'provider': trade.provider, 'account': trade.account, 'symbol': trade.symbol}
    args[field] = value
    id = journal.save_draft(**args, tags=['Patient'])
    # The next trade comes on the trade's own provider, account and contract: not the draft's.
    journal.ingest([(Execution(trade.provider, trade.account, trade.symbol, side, 1, 5000, at,
                               execution_id='next-' + side), {})
                    for side, at in (('buy', '2026-10-08T14:00:00Z'), ('sell', '2026-10-08T14:01:00Z'))])
    journal.reconcile_drafts()
    assert journal.workspace()['drafts'][id]['state'] == 'armed'
    assert not any('Patient' in review['tags'] for review in journal.annotations().values())


def test_a_draft_attaches_to_the_next_new_trade_on_its_contract_not_to_one_already_there(home):
    journal = seed_preview(home)
    existing = journal.trades()[0]
    where = dict(provider=existing.provider, account=existing.account, symbol=existing.symbol)
    id = journal.save_draft(**where, tags=['Patient'])
    journal.reconcile_drafts()
    assert journal.workspace()['drafts'][id]['state'] == 'armed'           # no trade came yet
    journal.ingest([(Execution(*where.values(), side, 1, 5000, at, execution_id='next-' + side), {})
                    for side, at in (('buy', '2026-10-08T14:00:00Z'), ('sell', '2026-10-08T14:01:00Z'))])
    new, = (t for t in journal.trades() if t.id != existing.id)
    journal.reconcile_drafts()
    draft = journal.workspace()['drafts'][id]
    assert (draft['state'], draft['trade_id']) == ('attached', new.id)
    assert 'Patient' in journal.annotations()[new.id]['tags']
    assert 'Patient' not in journal.annotations()[existing.id]['tags']


def test_arming_a_second_draft_on_a_contract_cancels_the_first(home):
    journal = Journal()
    first = journal.save_draft(provider='motivewave', account='paper', symbol='ESZ6', tags=['Patient'])
    other = journal.save_draft(provider='motivewave', account='paper', symbol='NQZ6', tags=['Patient'])
    second = journal.save_draft(provider='motivewave', account='paper', symbol='esz6', tags=['FOMO'])
    drafts = journal.workspace()['drafts']
    assert [drafts[id]['state'] for id in (first, other, second)] == ['cancelled', 'armed', 'armed']
    journal = seed_preview(home)
    journal.reconcile_drafts()
    drafts = journal.workspace()['drafts']
    assert drafts[first]['state'] == 'cancelled' and drafts[second]['state'] == 'attached'
    assert 'FOMO' in journal.annotations()[journal.trades()[0].id]['tags']
    with pytest.raises(ValueError, match='Choose a tag, or type a Stop or a note'):
        journal.save_draft(provider='motivewave', account='paper', symbol='ESZ6')
    with pytest.raises(ValueError):
        journal.cancel_draft(second)                                       # used: it stays on its trade


def test_with_two_new_trades_in_one_poll_the_draft_goes_on_the_one_opened_first(home):
    journal = Journal()
    id = journal.save_draft(provider='motivewave', account='paper', symbol='ESZ6', tags=['Patient'])
    # The later trade's fills arrive first: it is still the earlier opened that takes the draft.
    journal.ingest([(Execution('motivewave', 'paper', 'ESZ6', side, 1, 5000, at, execution_id=name), {})
                    for name, side, at in (('late-in', 'buy', '2026-10-07T15:00:00Z'),
                                           ('late-out', 'sell', '2026-10-07T15:01:00Z'),
                                           ('early-in', 'buy', '2026-10-07T14:00:00Z'),
                                           ('early-out', 'sell', '2026-10-07T14:01:00Z'))])
    early, late = sorted(journal.trades(), key=lambda t: t.opened)
    journal.reconcile_drafts()
    assert journal.workspace()['drafts'][id]['trade_id'] == early.id
    assert 'Patient' in journal.annotations()[early.id]['tags'] and late.id not in journal.annotations()


def test_a_draft_whose_tag_was_retired_since_needs_review_and_names_its_trade(home):
    journal = Journal()
    id = journal.save_draft(provider='motivewave', account='paper', symbol='ESZ6', tags=['Patient'], notes='Wait')
    journal.edit_tag(next(iter(tagging.ids(journal.tag_library(), ['Patient']))), retired=True)
    journal = seed_preview(home)
    trade = journal.trades()[0]
    journal.reconcile_drafts()
    draft = journal.workspace()['drafts'][id]
    assert (draft['state'], draft['trade_id']) == ('needs review', trade.id)
    assert 'draft_id' not in journal.annotations()[trade.id]               # nothing of it went on the trade
    journal.cancel_draft(id)
    assert journal.workspace()['drafts'][id]['state'] == 'cancelled'


def test_only_a_draft_still_waiting_keeps_the_trades_the_journal_held_when_it_was_armed(home):
    """Tag My Next Trade before every trade for years: each one used or cancelled keeps no copy of the journal's
    trade list, so the saved workspace stays small."""
    journal = seed_preview(home)
    where = dict(provider='motivewave', account='paper')
    replaced = journal.save_draft(symbol='ESZ6', tags=['FOMO'], **where)
    used = journal.save_draft(symbol='ESZ6', tags=['Patient'], **where)
    review = journal.save_draft(symbol='NQZ6', tags=['Hesitant'], **where)
    cancelled = journal.save_draft(symbol='CLZ6', notes='Skip it', **where)
    journal.cancel_draft(cancelled)
    journal.edit_tag(next(iter(tagging.ids(journal.tag_library(), ['Hesitant']))), retired=True)
    journal.ingest([(Execution('motivewave', 'paper', symbol, side, 1, price, at, execution_id=f'{symbol}-{side}'), {})
                    for symbol, price in (('ESZ6', 5000), ('NQZ6', 20000))
                    for side, at in (('buy', '2026-10-08T14:00:00Z'), ('sell', '2026-10-08T14:01:00Z'))])
    journal.reconcile_drafts()
    waiting = journal.save_draft(symbol='ESZ6', notes='The next one', **where)
    drafts = journal.workspace()['drafts']
    assert {drafts[id]['state']: 'existing_trades' in drafts[id] for id in (replaced, used, review, waiting)} == {
        'cancelled': False, 'attached': False, 'needs review': False, 'armed': True}
    assert 'existing_trades' not in drafts[cancelled]
    assert len(drafts[waiting]['existing_trades']) == len(journal.trades())
    journal.cancel_draft(review)
    assert journal.workspace()['drafts'][review]['state'] == 'cancelled'


@pytest.mark.parametrize('view', ['Dashboard', 'Checklists', 'Live session', 'Settings'])
def test_new_views_render_without_recording_orders_or_raw_trade_changes(home, view):
    journal = seed_preview(home)
    before = journal.executions()
    app = open_app(view)
    assert not app.exception
    assert journal.executions() == before


def answer(app, label, choice):
    """Click Yes or No (`choice`, its answer's id) on the checklist question whose label starts with `label`."""
    pills = widget(app.pills, label)
    assert not pills.disabled
    pills.set_value(choice).run()
    assert not app.exception
    return app


def test_checklist_ui_run_branch_undo_and_apply_tags(home):
    journal = seed_preview(home)
    journal.save_checklist('Plan', [question('Setup valid?', no={'go': 3, 'tag_ids': ['Patient']}),
                                    question('Stop placed?'), question('Contracts right?')])
    trade = journal.trades()[0]
    app = open_app()
    asked = [p.label for p in app.pills if '·' in p.label]
    assert asked == ['1 · Setup valid?', '2 · Stop placed?', '3 · Contracts right?']
    assert [widget(app.pills, f'{n} ·').disabled for n in (1, 2, 3)] == [False, True, True]   # the first is due
    assert not journal.workspace()['runs']                                         # a run starts with an answer
    answer(app, '1 · Setup valid?', 'q1-no')
    assert not any(p.label.startswith('2 ·') for p in app.pills)                   # the branch hid question 2
    assert widget(app.pills, '1 ·').value == 'q1-no' and not widget(app.pills, '3 ·').disabled
    assert said(app.caption, 'Collected tags: Patient')
    press(app, 'Undo Last Answer')
    assert any(p.label.startswith('2 ·') for p in app.pills) and widget(app.pills, '1 ·').value is None
    answer(app, '1 · Setup valid?', 'q1-no')
    answer(app, '3 · Contracts right?', 'q3-yes')
    assert said(app.success, 'Checklist complete')
    assert 'Patient' not in journal.annotations().get(trade.id, {}).get('tags', [])    # only Apply applies
    press(app, 'Apply Collected Tags')
    assert 'Patient' in journal.annotations()[trade.id]['tags']
    runs = list(journal.workspace()['runs'].values())
    assert len(runs) == 1 and runs[0]['trade_id'] == trade.id and runs[0]['answers'] == ['q1-no', 'q3-yes']


def editing(app):
    """The checklist editor's boxes, by what they say."""
    return {w.label: w for w in [*app.text_input, *app.selectbox, *app.multiselect, *app.checkbox, *app.radio]}


def test_the_checklist_editor_builds_a_list_with_a_branch_and_saves_it_once(home):
    journal = seed_preview(home)
    app = open_app('Checklists')
    boxes = editing(app)
    assert boxes['Checklist name'].value == 'Pre-trade plan' and boxes['Runs on'].value == 'trade'
    assert boxes['Question 1'].value == checklists.STARTER and said(app.caption, "On every trade's page.")
    assert [e.label for e in app.expander] == ['Add a Branch'] and not journal.workspace()['checklists']
    boxes['Checklist name'].set_value('Morning plan').run()
    press(app, 'Add Question')
    editing(app)['Question 2'].input('Is my stop placed?').run()
    press(app, 'Add Question')
    editing(app)['Question 3'].input('Did I sleep well?').run()
    assert [b.disabled for b in app.button if b.label == 'Move Up'] == [True, False, False]
    folds = [e.key for e in app.expander]
    [b for b in app.button if b.label == 'Move Up'][1].click().run()
    assert editing(app)['Question 1'].value == 'Is my stop placed?'        # question 2 moved up
    # Each Add a Branch is keyed by its question, so an open one moves with it and stays open when the line saying
    # what its branches do appears above it.
    assert all(folds) and [e.key for e in app.expander] == [folds[1], folds[0], folds[2]]
    no_goes = [s for s in app.selectbox if s.label == 'No goes to']
    no_goes[0].set_value(next(o for o in no_goes[0].options if o == 'Question 3')).run()
    assert said(app.caption, 'No goes to Question 3')
    collects = [m for m in app.multiselect if m.label == 'No collects tags'][0]
    collects.set_value([tagging.identity('Patient')]).run()
    [c for c in app.checkbox if c.label == 'Count this answer'][1].check().run()
    press(app, 'Save Checklist')
    (id, saved), = journal.workspace()['checklists'].items()
    assert saved['name'] == 'Morning plan' and saved['runs_on'] == 'trade'
    assert checklist_questions(saved['graph']) == [
        question('Is my stop placed?', no={'go': 3, 'tag_ids': [tagging.identity('Patient')], 'count': True}),
        question(checklists.STARTER), question('Did I sleep well?')]
    assert app.session_state['checklist-editor-choice-live'] == id and not said(app.caption, 'Not saved yet.')
    editing(app)['Runs on'].set_value('session').run()
    assert said(app.caption, 'On the Daily Report Card, once a day.') and said(app.caption, 'Not saved yet.')
    press(app, 'Save Checklist')
    assert journal.workspace()['checklists'][id]['runs_on'] == 'session'
    press(app, 'Delete Checklist')
    assert not journal.workspace()['checklists'] and editing(app)['Checklist name'].value == 'Pre-trade plan'


def test_dashboard_calendar_navigation_and_widget_save(home):
    journal = seed_preview(home)
    app = open_app('Dashboard')
    # Before a page is saved: one empty page, and one line saying how to fill it. Overview has the rest.
    assert widget(app.selectbox, 'Dashboard page').options == ['My Dashboard']
    assert said(app.info, 'Add a widget under Customize Page.') and not app.get('plotly_chart')
    widget(app.text_input, 'Page name').set_value('Days')
    press(app, 'Save Page Layout')
    page_id = widget(app.selectbox, 'Dashboard page').value
    assert journal.workspace()['dashboards'][page_id]['widgets'] == []
    assert widget(app.selectbox, 'Dashboard page').options == ['Days']           # the built-in page is gone
    widget(app.selectbox, 'Widget type').set_value('Rating')
    press(app, 'Save Widget')
    page = journal.workspace()['dashboards'][page_id]
    assert page['widgets'][-1]['kind'] == 'Rating' and 'rating_id' not in page['widgets'][-1]
    widget(app.selectbox, 'Widget type').set_value('Calendar')
    press(app, 'Save Widget')
    assert said(app.markdown, '### Calendar')
    button = next(b for b in app.button if b.label.startswith('7  \n'))
    button.click().run()
    assert not app.exception
    assert widget(app.date_input, 'Trading day').value == dt.date(2026, 10, 7)


def test_tag_score_is_offered_nowhere_until_a_tag_has_a_score(home):
    import script_widget
    journal = seed_preview(home)
    stored = journal.save_dashboard('Scores', [{'kind': 'Tag score', 'title': 'Old score'}])

    def offered():
        """Where Tag score shows: the tag panel, Filters, a widget's type, measure and tags, a script's trades."""
        app = open_app()
        found = {'panel': said(app.caption, 'Tag score'),
                 'filters': 'Tag Score' in widget(app.multiselect, 'Number Ranges').options}
        show(app, 'Dashboard')
        widget(app.selectbox, 'Dashboard page').set_value(stored).run()
        assert not app.exception and said(app.markdown, '### Old score')     # a widget of that kind still draws
        found.update(kind='Tag score' in widget(app.selectbox, 'Widget type').options,
                     measure='Tag score' in widget(app.selectbox, 'Series metric').options,
                     tags=any(m.label.startswith('Score only these tags') for m in app.multiselect),
                     limit='Tag Score' in app.multiselect(key=f'widget-{stored}-new-numbers-live').options,
                     script='"tag_score"' in script_widget.document(common.rows_for(journal), journal.tag_library(), ''))
        return found
    assert not any(offered().values())
    journal.edit_tag(next(iter(tagging.ids(journal.tag_library(), ['Patient']))), score=1)
    assert all(offered().values())


def test_a_rating_widget_charts_the_trades_stars_and_old_rating_systems_are_ignored(home):
    from views import dashboard
    journal = seed_preview(home)
    trade = journal.trades()[0]
    with journal.connect() as db:                  # a rating system and a widget that used one, as 0.4.16 saved them
        state = workspace.read(db)
        state['ratings'] = {'old': {'id': 'old', 'name': 'Process', 'rules': []}}
        workspace.write(db, state)
    id = journal.save_dashboard('Stars', [{'kind': 'Rating', 'title': 'My stars', 'rating_id': 'old'}])
    widget_ = journal.workspace()['dashboards'][id]['widgets'][0]
    assert dashboard.values(journal, common.rows_for(journal), widget_ | {'metric': 'Rating'}) == []
    journal.update_annotation(trade.id, {'rating': 4})
    assert [r['value'] for r in dashboard.values(journal, common.rows_for(journal), widget_ | {'metric': 'Rating'})] == [4]
    assert 'ratings' not in common.rows_for(journal)[0]
    app = open_app('Dashboard')
    widget(app.selectbox, 'Dashboard page').set_value(id).run()
    assert not app.exception and said(app.markdown, '### My stars')
    assert not any(s.label == 'Rating definition' for s in app.selectbox)


def trade_on(journal, day, points, n=0):
    """One NQ round trip opened at 10:1n New York time on `day`: `points` × $20, no fees."""
    journal.ingest([(Execution('motivewave', 'paper', 'NQZ6', 'buy', 1, 20000, f'{day}T14:1{n}:00Z',
                               execution_id=f'{day}-{n}-in'), {}),
                    (Execution('motivewave', 'paper', 'NQZ6', 'sell', 1, 20000 + points, f'{day}T14:1{n}:30Z',
                               execution_id=f'{day}-{n}-out'), {})])


def calendar_cells(app):
    """Each Trading Calendar cell by its first line ('5', 'Week 2'; the month's total under 'Month'):
    (classes, net result, trade count), the last two None for a cell without trades."""
    cells = {}
    for body in (m.value for m in app.main.markdown if m.value.startswith("<div class='mwj-cal")):
        found = re.fullmatch(r"<div class='([^']*)'(?: title='[^']*')?>(?:<span class='d'>([^<]*)</span>)?"
                             r"(?:<span class='n'>([^<]*)</span><span class='k'>([^<]*)</span>)?</div>", body)
        assert found, body
        classes, label, net, trades = found.groups()
        cells[label or 'Month'] = (classes.split(), net and net.replace('&#36;', '$'), trades)
    return cells


def test_calendar_days_and_weeks_are_tinted_by_their_result_and_say_how_many_trades(home):
    """The calendar on Stats → Overview, under the equity curve."""
    journal = Journal()
    journal.save_preferences(display_timezone='America/New_York')
    trade_on(journal, '2026-10-05', 50)                  # Mon +$1,000: the month's biggest day
    trade_on(journal, '2026-10-06', -10)                 # Tue two losers, -$300 in all: under a third of it
    trade_on(journal, '2026-10-06', -5, 1)
    trade_on(journal, '2026-10-07', 25)                  # Wed +$500: half of it
    trade_on(journal, '2026-10-08', 0)                   # Thu break-even; Fri no trades
    trade_on(journal, '2026-10-13', -20)                 # the next Tuesday, -$400
    app = open_app('Edge')
    cells = calendar_cells(app)
    assert cells['5'] == (['mwj-cal', 'up', 'l3'], '+$1,000', '1 trade')
    assert cells['6'] == (['mwj-cal', 'down', 'l1'], '-$300', '2 trades')
    assert cells['7'] == (['mwj-cal', 'up', 'l2'], '+$500', '1 trade')
    assert cells['8'] == (['mwj-cal'], '$0', '1 trade')                 # break-even stays neutral
    assert cells['9'] == (['mwj-cal'], None, None)                     # so does a day without trades
    # Each week's total ends its row, tinted against the month's biggest week.
    assert cells['Week 1'] == (['mwj-cal'], None, None)
    assert cells['Week 2'] == (['mwj-cal', 'up', 'l3'], '+$1,200', '5 trades')
    assert cells['Week 3'] == (['mwj-cal', 'down', 'l1'], '-$400', '1 trade')
    assert cells['Month'] == (['mwj-cal-month', 'up', 'l3'], '+$800', '6 trades')
    captions = [c.value for c in app.main.caption]
    assert captions[captions.index('Mon'):][:6] == ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Week']    # no weekend trade
    # The tints are the result colours, three steps deep; the amount takes the result colour too.
    css = re.sub(r'\s+', ' ', theme.CSS)
    for tone, colour in (('up', theme.UP), ('down', theme.DOWN)):
        for level, alpha in enumerate(theme.TINTS, 1):
            assert f'.mwj-cal.{tone}.l{level} {{ background:{colour}{alpha};' in css
        assert f':is(.mwj-cal, .mwj-cal-month).{tone} .n {{ color:{colour}; }}' in css
    # A day still opens its Daily Report Card, from anywhere in its cell: the invisible button sits inside its
    # tooltip's wrappers, and they are stretched to the cell too.
    assert '[class*="st-key-calendar-day-"] .stButton :has(button) { height:100%; }' in css
    next(b for b in app.button if b.label.startswith('6  \n')).click().run()
    assert not app.exception and widget(app.date_input, 'Trading day').value == dt.date(2026, 10, 6)


def calendar_columns(app):
    """The calendar's column headings, Mon to Week."""
    captions = [c.value for c in app.main.caption]
    start = captions.index('Mon')
    return captions[start:captions.index('Week', start) + 1]


def test_saturday_and_sunday_show_only_in_a_month_with_a_trade_on_one(home):
    journal = Journal()
    journal.save_preferences(display_timezone='America/New_York')
    trade_on(journal, '2026-09-08', 10)                  # Tue Sep 8 +$200: a month without a weekend trade
    trade_on(journal, '2026-10-09', 10)                  # Fri Oct 9 +$200
    trade_on(journal, '2026-10-11', 5)                   # Sun Oct 11 +$100: October shows its weekends
    trade_on(journal, '2026-11-03', -5)                  # Tue Nov 3 -$100; Sunday Nov 1 starts that month
    app = open_app('Edge')
    month = app.selectbox(key='overview-calendar-live-month')
    assert month.value == dt.date(2026, 11, 1)
    # November: Monday to Friday, and the Sunday that starts the month has no row of its own.
    assert calendar_columns(app) == ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Week']
    cells = calendar_cells(app)
    assert '1' not in cells and cells['3'][1:] == ('-$100', '1 trade') and cells['Week 1'][1:] == ('-$100', '1 trade')
    month.set_value(dt.date(2026, 10, 1)).run()
    assert calendar_columns(app) == ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun', 'Week']
    cells = calendar_cells(app)
    assert cells['10'] == (['mwj-cal'], None, None) and cells['11'][1:] == ('+$100', '1 trade')
    assert cells['Week 2'] == (['mwj-cal', 'up', 'l3'], '+$300', '2 trades')
    assert cells['Month'][1:] == ('+$300', '2 trades')
    app.selectbox(key='overview-calendar-live-month').set_value(dt.date(2026, 9, 1)).run()
    assert calendar_columns(app) == ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Week']
    assert '5' not in calendar_cells(app) and '8' in calendar_cells(app)          # Saturday Sep 5 has no cell


def test_a_break_even_day_stays_neutral_when_its_trades_add_up_to_a_crumb(home):
    journal = Journal()
    journal.save_preferences(display_timezone='America/New_York')
    # Crude oil, $10 a tick: 10 ticks won, then 10 lost at another price. The two nets add up to about 1e-11.
    journal.ingest([(Execution('motivewave', 'paper', 'CLZ6', side, 1, price, f'2026-10-06T14:{at}Z',
                               execution_id=at, point_value=1000), {})
                    for side, price, at in (('buy', 71.05, '10:00'), ('sell', 71.15, '10:30'),
                                            ('buy', 72.30, '20:00'), ('sell', 72.20, '20:30'))])
    assert 0 < sum(trade.net for trade in journal.trades()) < 1e-9
    cells = calendar_cells(open_app('Edge'))
    assert cells['6'] == (['mwj-cal'], '$0', '2 trades')
    assert cells['Week 2'] == (['mwj-cal'], '$0', '2 trades')
    assert cells['Month'] == (['mwj-cal-month'], '$0', '2 trades')


def test_calendar_tint_steps():
    assert theme.tint(None, 100) == theme.tint(0, 100) == theme.tint(0, 0) == ''
    assert theme.tint(1, 100) == 'up l1' and theme.tint(-33, 100) == 'down l1'
    assert theme.tint(34, 100) == 'up l2' and theme.tint(-66, 100) == 'down l2'
    assert theme.tint(67, 100) == 'up l3' and theme.tint(-100, 100) == 'down l3'


def test_the_backup_includes_workflow_definitions_and_run_history(home):
    journal = seed_preview(home)
    definition = journal.save_checklist('Plan', starter())
    journal.start_checklist(definition, day='2026-10-07')
    with zipfile.ZipFile(backup.write(home / 'backup.zip')) as archive:
        copy = Journal(archive.extract('live.sqlite3', home / 'unzipped'))
    assert len(copy.workspace()['runs']) == 1
    assert definition in copy.workspace()['checklists']


def test_custom_visualization_scripts_are_opt_in_network_denied_and_do_not_get_account_or_fill_data(home):
    import script_widget
    journal = seed_preview(home)
    rendered = script_widget.document(common.rows_for(journal), journal.tag_library(), script_widget.STARTER)
    assert 'new Worker' in rendered and 'worker.terminate()' in rendered and 'worker-src blob:' in rendered
    assert 'connect-src' in rendered and 'none' in rendered
    assert 'paper' not in rendered and 'execution_id' not in rendered
    id = journal.save_dashboard('Custom', [{'kind': 'Script', 'code': script_widget.STARTER}])
    app = open_app('Dashboard')
    widget(app.selectbox, 'Dashboard page').set_value(id).run()
    assert said(app.caption, 'Not running')
    assert any(b.label == 'Run Local Script' for b in app.button)
