"""Local parity workflows on isolated paper fixtures; no broker, recording or network."""
import copy
import datetime as dt
import itertools
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
from mwjournal.workspace import (validate_graph, replay_run, tag_score, rating, filter_sample, combine_filters,
                                 validate_filter)
from test_app import open_app, show, press, widget, said
from test_chart import seed_preview
from views.checklists import starter


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    monkeypatch.setenv('MWJ_HOME', str(tmp_path))
    yield tmp_path
    runtime.stop_all()


def test_workspace_reads_do_not_save_definitions_or_rearm_anything(home):
    journal = Journal()
    assert journal.workspace()['dashboards'] == {}
    journal.reconcile_drafts()
    with journal.connect() as db:
        assert not db.execute("SELECT 1 FROM journal_meta WHERE key='workspace'").fetchone()


def test_signed_tag_scores_and_conditional_weighted_ratings_preserve_raw_trade(home):
    journal = seed_preview(home)
    trade = journal.trades()[0]
    before = journal.executions()
    lib = journal.tag_library()
    patient = next(iter(tagging.ids(lib, ['Patient'])))
    fomo = next(iter(tagging.ids(lib, ['FOMO'])))
    journal.edit_tag(patient, score=2)
    journal.edit_tag(fomo, score=-3)
    lib = journal.tag_library()
    assert tag_score(lib, [patient, fomo]) == -1
    assert tag_score(lib, [patient, fomo], [patient]) == 2
    model = journal.save_rating('Process', [
        {'name': 'Calm', 'weight': 2, 'rules': {'Mindset': ['Patient']}},
        {'name': 'Setup', 'weight': 1, 'rules': {'Setup': ['Pullback']}, 'when': {'Mindset': ['Patient']}}])
    definition = journal.workspace()['ratings'][model]
    assert rating(lib, [], definition) == 0
    assert rating(lib, [patient], definition) == pytest.approx(200 / 3)
    journal.set_trade_tag_group(trade, 'Mindset', ['Patient'])
    assert common.rows_for(journal)[0]['ratings'][model] == pytest.approx(200 / 3)
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
        with pytest.raises(ValueError, match='Process ratings'):
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
    assert not app.exception and said(app.main.caption, 'Saved filter: Old')


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
    assert journal.workspace() == before


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
    journal = Journal()
    graph = starter()
    if change == 'unknown-tag':
        graph['nodes'][1]['tag_ids'] = ['Not a tag']
    elif change == 'unknown-link':
        graph['nodes'][1]['next'] = ['missing']
    elif change == 'duplicate':
        graph['nodes'].append(copy.deepcopy(graph['nodes'][1]))
    elif change == 'automatic-loop':
        graph['nodes'][1]['next'] = ['yes']
    else:
        graph['nodes'][0]['next'] = ['plan']
    with pytest.raises(ValueError):
        journal.save_checklist('Invalid', graph)
    assert not journal.workspace()['checklists']


def test_checklist_branches_tags_counter_loops_undo_and_definition_snapshots(home):
    journal = seed_preview(home)
    trade = journal.trades()[0]
    graph = starter()
    graph['nodes'][1].update(next=['counter'], tag_ids=['Patient'])
    graph['nodes'].append({'id': 'counter', 'kind': 'counter', 'text': 'Plans checked', 'next': ['plan']})
    definition = journal.save_checklist('Plan', graph)
    run = journal.start_checklist(definition, trade.id)
    result = journal.answer_checklist(run, 'yes')
    assert result['current'] == 'plan' and result['counters'] == {'counter': 1}
    assert result['answer_events'][0]['action'] == 'answer' and result['answer_events'][0]['at']
    assert tagging.names(journal.tag_library(), result['tag_ids']) == ['Patient']
    assert 'Patient' not in journal.annotations()[trade.id]['tags']  # no silent apply
    journal.answer_checklist(run, 'yes')
    assert journal.workspace()['runs'][run]['counters']['counter'] == 2
    journal.answer_checklist(run, undo=True)
    assert journal.workspace()['runs'][run]['counters']['counter'] == 1
    assert journal.workspace()['runs'][run]['answer_events'][-1]['action'] == 'undo'
    changed = starter()
    changed['nodes'][0]['text'] = 'Changed question'
    journal.save_checklist('Plan revised', changed, id=definition)
    assert journal.workspace()['runs'][run]['graph']['nodes'][0]['text'] != 'Changed question'
    assert len(journal.workspace()['checklists'][definition]['backups']) == 1
    result = journal.answer_checklist(run, 'no')
    assert result['complete']
    with pytest.raises(ValueError):
        journal.answer_checklist(run, 'yes')


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
    id = journal.save_draft('Plan', provider='motivewave', account='paper', symbol='ESZ6',
                            start='2026-10-07T14:00:00Z', end='2026-10-07T14:05:00Z', tags=['Patient'],
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
    id = journal.save_draft('Plan', **args, start='2026-10-07T14:00:00Z', end='2026-10-07T14:05:00Z', tags=['Patient'])
    journal.reconcile_drafts()
    assert journal.workspace()['drafts'][id]['state'] == 'armed'
    assert 'Patient' not in journal.annotations()[trade.id]['tags']


def test_overlapping_drafts_are_ambiguous_and_cancellation_resolves_without_data_loss(home):
    journal = Journal()
    ids = [journal.save_draft('Plan ' + str(i), provider='motivewave', account='paper', symbol='ESZ6',
                             start='2026-10-07T14:00:00Z', end='2026-10-07T14:05:00Z', tags=['Patient']) for i in range(2)]
    journal = seed_preview(home)
    journal.reconcile_drafts()
    assert all(journal.workspace()['drafts'][id]['state'] == 'ambiguous' for id in ids)
    journal.cancel_draft(ids[0])
    journal.reconcile_drafts()
    assert journal.workspace()['drafts'][ids[1]]['state'] == 'attached'
    assert journal.workspace()['drafts'][ids[0]]['state'] == 'cancelled'


def test_pretrade_draft_never_silently_retags_a_trade_already_in_the_journal(home):
    journal = seed_preview(home)
    trade = journal.trades()[0]
    id = journal.save_draft('Plan', provider=trade.provider, account=trade.account, symbol=trade.symbol,
                            start='2026-10-07T14:00:00Z', end='2026-10-07T14:05:00Z', tags=['Patient'])
    journal.reconcile_drafts()
    assert journal.workspace()['drafts'][id]['state'] == 'armed'
    assert 'Patient' not in journal.annotations()[trade.id]['tags']


@pytest.mark.parametrize('view', ['Dashboard', 'Checklists', 'Live session', 'Ratings', 'Settings'])
def test_new_views_render_without_recording_orders_or_raw_trade_changes(home, view):
    journal = seed_preview(home)
    before = journal.executions()
    app = open_app(view)
    assert not app.exception
    assert journal.executions() == before


def test_checklist_ui_build_run_branch_undo_and_apply_tags(home):
    journal = seed_preview(home)
    graph = starter()
    graph['nodes'][1]['tag_ids'] = ['Patient']
    journal.save_checklist('Plan', graph)
    app = open_app()
    press(app, 'Start New Run')
    press(app, 'Yes — continue my plan')
    assert said(app.success, 'Checklist complete')
    press(app, 'Undo Last Answer')
    assert not said(app.success, 'Checklist complete')
    press(app, 'Yes — continue my plan')
    press(app, 'Apply Collected Tags')
    assert 'Patient' in journal.annotations()[journal.trades()[0].id]['tags']


def test_dashboard_calendar_navigation_and_widget_save(home):
    journal = seed_preview(home)
    app = open_app('Dashboard')
    widget(app.text_input, 'Page name').set_value('My dashboard')
    press(app, 'Save Page Layout')
    assert widget(app.selectbox, 'Dashboard page').value in journal.workspace()['dashboards']
    widget(app.selectbox, 'Widget type').set_value('Tag score')
    press(app, 'Save Widget')
    page = journal.workspace()['dashboards'][widget(app.selectbox, 'Dashboard page').value]
    assert page['widgets'][-1]['kind'] == 'Tag score'
    button = next(b for b in app.button if b.label.startswith('7  \n'))
    button.click().run()
    assert not app.exception
    assert widget(app.date_input, 'Trading day').value == dt.date(2026, 10, 7)


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
    journal = Journal()
    journal.save_preferences(display_timezone='America/New_York')
    trade_on(journal, '2026-10-05', 50)                  # Mon +$1,000: the month's biggest day
    trade_on(journal, '2026-10-06', -10)                 # Tue two losers, -$300 in all: under a third of it
    trade_on(journal, '2026-10-06', -5, 1)
    trade_on(journal, '2026-10-07', 25)                  # Wed +$500: half of it
    trade_on(journal, '2026-10-08', 0)                   # Thu break-even; Fri no trades
    trade_on(journal, '2026-10-13', -20)                 # the next Tuesday, -$400
    app = open_app('Dashboard')
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
    assert captions[captions.index('Mon'):][:8] == ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun', 'Week']
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


def test_a_hidden_weekend_still_counts_in_its_weeks_total(home):
    journal = Journal()
    journal.save_preferences(display_timezone='America/New_York', hide_weekends=True)
    trade_on(journal, '2026-10-09', 10)                  # Fri +$200
    trade_on(journal, '2026-10-11', 5)                   # Sun +$100, in a column that is hidden
    cells = calendar_cells(open_app('Dashboard'))
    assert '9' in cells and '10' not in cells and '11' not in cells
    assert cells['Week 2'] == (['mwj-cal', 'up', 'l3'], '+$300', '2 trades')
    assert cells['Month'][1:] == ('+$300', '2 trades')


def test_a_break_even_day_stays_neutral_when_its_trades_add_up_to_a_crumb(home):
    journal = Journal()
    journal.save_preferences(display_timezone='America/New_York')
    # Crude oil, $10 a tick: 10 ticks won, then 10 lost at another price. The two nets add up to about 1e-11.
    journal.ingest([(Execution('motivewave', 'paper', 'CLZ6', side, 1, price, f'2026-10-06T14:{at}Z',
                               execution_id=at, point_value=1000), {})
                    for side, price, at in (('buy', 71.05, '10:00'), ('sell', 71.15, '10:30'),
                                            ('buy', 72.30, '20:00'), ('sell', 72.20, '20:30'))])
    assert 0 < sum(trade.net for trade in journal.trades()) < 1e-9
    cells = calendar_cells(open_app('Dashboard'))
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
    journal.start_checklist(definition)
    with zipfile.ZipFile(backup.write(home / 'backup.zip')) as archive:
        copy = Journal(archive.extract('live.sqlite3', home / 'unzipped'))
    assert len(copy.workspace()['runs']) == 1
    assert definition in copy.workspace()['checklists']


def test_custom_visualization_scripts_are_opt_in_network_denied_and_do_not_get_account_or_fill_data(home):
    import script_widget
    journal = seed_preview(home)
    rendered = script_widget.document(common.rows_for(journal), journal.tag_library()['tags'], script_widget.STARTER)
    assert 'new Worker' in rendered and 'worker.terminate()' in rendered and 'worker-src blob:' in rendered
    assert 'connect-src' in rendered and 'none' in rendered
    assert 'paper' not in rendered and 'execution_id' not in rendered
    id = journal.save_dashboard('Custom', [{'kind': 'Script', 'code': script_widget.STARTER}])
    app = open_app('Dashboard')
    widget(app.selectbox, 'Dashboard page').set_value(id).run()
    assert said(app.caption, 'Not running')
    assert any(b.label == 'Run Local Script' for b in app.button)
