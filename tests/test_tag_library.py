"""Stable tag identities and atomic edits on disposable journals, never a user's trading data."""
import datetime as dt
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

import common
from mwjournal import Journal
from mwjournal import tagging
from test_chart import round_trip


def tag_id(journal, name):
    return next(iter(tagging.ids(journal.tag_library(), [name])))


def test_empty_categories_are_first_class_and_persist(tmp_path):
    journal = Journal(tmp_path / 'live.db')
    journal.add_tag_group('Orderflow')
    assert journal.tag_library()['groups']['Orderflow']['exclusive'] is False
    assert not any(tag['group'] == 'Orderflow' for tag in journal.tag_library()['tags'].values())
    assert 'Orderflow' in Journal(journal.path).tag_library()['groups']
    with pytest.raises(ValueError, match='already exists'):
        journal.add_tag_group('Orderflow')
    with pytest.raises(ValueError):
        journal.add_tag_group('')


def test_rename_preserves_legacy_annotations_and_aliases_without_rewriting_history(tmp_path):
    journal = Journal(tmp_path / 'live.db')
    trade = round_trip(journal)
    journal.annotate(trade.id, {'tags': ['Breakout'], 'notes': 'Keep this', 'initial_risk': 125})
    with journal.connect() as db:
        before = db.execute('SELECT payload FROM annotations').fetchone()[0]
    original = tag_id(journal, 'Breakout')
    journal.edit_tag(original, name='Break & retest', color='#123456')
    assert tag_id(journal, 'Break & retest') == tag_id(journal, 'Breakout') == original
    assert journal.annotations()[trade.id]['tags'] == ['Break & retest']
    assert journal.annotations()[trade.id]['notes'] == 'Keep this'
    with journal.connect() as db:
        assert db.execute('SELECT payload FROM annotations').fetchone()[0] == before
    assert common.rows_for(journal)[0]['tag_ids'] == [original]
    with pytest.raises(ValueError, match='alias'):
        journal.add_tag('Breakout', 'Setup')
    fresh = Journal(journal.path)
    assert fresh.tag_library()['tags'][original]['color'] == '#123456'
    assert fresh.annotations()[trade.id]['initial_risk'] == 125


def test_retirement_hides_choices_but_retains_historical_tags_and_restore(tmp_path):
    journal = Journal(tmp_path / 'live.db')
    trade = round_trip(journal)
    journal.set_trade_tag_group(trade, 'Setup', ['Breakout'])
    id = tag_id(journal, 'Breakout')
    journal.edit_tag(id, retired=True)
    assert 'Breakout' not in journal.tag_catalog()
    assert common.rows_for(journal)[0]['tags'] == ['Breakout']
    with pytest.raises(ValueError):
        journal.set_trade_tag_group(trade, 'Setup', ['Breakout'])
    journal.edit_tag(id, retired=False)
    assert 'Breakout' in journal.tag_catalog()


def test_exclusive_groups_preserve_old_conflicts_until_user_resolves_and_copy_is_atomic(tmp_path):
    journal = Journal(tmp_path / 'live.db')
    trade = round_trip(journal)
    journal.annotate(trade.id, {'tags': ['Breakout', 'Pullback', 'Patient'], 'notes': 'Old review'})
    journal.edit_tag_group('Setup', exclusive=True)
    before = journal.annotations()
    assert set(before[trade.id]['tags']) == {'Breakout', 'Pullback', 'Patient'}
    with pytest.raises(ValueError):
        journal.set_trade_tag_group(trade, 'Setup', ['Breakout', 'Pullback'])
    with pytest.raises(ValueError):
        journal.update_annotation(trade.id, {'notes': 'Must roll back'}, add_tags=['Breakout', 'Pullback'])
    assert journal.annotations() == before
    journal.set_trade_tag_group(trade, 'Setup', ['Pullback'])
    assert journal.annotations()[trade.id]['tags'] == ['Patient', 'Pullback']
    assert journal.annotations()[trade.id]['notes'] == 'Old review'


def test_day_tags_inherit_lazily_and_trade_override_survives_day_review_save(tmp_path):
    journal = Journal(tmp_path / 'live.db')
    trade = round_trip(journal)
    for name in ('Patient', 'FOMO'):
        journal.edit_tag(tag_id(journal, name), levels=['trade', 'day'])
    journal.edit_tag_group('Mindset', exclusive=True)
    journal.set_day_tag_group(dt.date(2026, 10, 7), 'Mindset', ['Patient'])
    assert common.rows_for(journal)[0]['tags'] == ['Patient']
    assert not journal.annotations()  # no destructive per-trade expansion of day tags
    journal.set_trade_tag_group(trade, 'Mindset', ['FOMO'])
    journal.update_session('2026-10-07', {'notes': 'Daily notes'})
    assert common.rows_for(journal)[0]['tags'] == ['FOMO']
    assert journal.get('sessions')['2026-10-07']['day_tag_ids']
    journal.set_trade_tag_group(trade, 'Mindset', [])
    assert common.rows_for(journal)[0]['tags'] == []
    assert journal.get('sessions')['2026-10-07']['notes'] == 'Daily notes'


def test_a_day_is_its_date_in_the_journals_time_zone_and_replay_is_separate(tmp_path):
    journal = Journal(tmp_path / 'live.db')
    journal.edit_tag(tag_id(journal, 'Patient'), levels=['day'])
    journal.save_preferences(display_timezone='America/Los_Angeles')
    assert journal.time_zone() == 'America/Los_Angeles'
    assert tagging.day_key('2026-10-08T02:00:00Z', journal.time_zone()) == '2026-10-07'     # 7 PM the evening before
    assert tagging.day_key('2026-10-08T07:00:00Z', journal.time_zone()) == '2026-10-08'     # midnight starts a day
    journal.set_day_tag_group('2026-10-07', 'Mindset', ['Patient'])
    assert journal.get('sessions') == {'2026-10-07': {'date': '2026-10-07',
                                                      'day_tag_ids': [tag_id(journal, 'Patient')]}}
    replay = Journal(tmp_path / 'replay.db', dataset='replay')
    assert not replay.get('sessions')
    assert 'session_timezone' not in replay.tag_library() and 'session_timezone' not in journal.tag_library()


def test_playbook_or_within_and_across_groups_apply_preserves_review_and_renames(tmp_path):
    journal = Journal(tmp_path / 'live.db')
    trade = round_trip(journal)
    journal.annotate(trade.id, {'notes': 'Keep', 'tags': ['Pullback', 'Followed plan'], 'initial_risk': 100})
    journal.edit_tag_group('Setup', exclusive=True)
    book = journal.save_playbook('Patient setup', {'Setup': ['Breakout', 'Pullback'], 'Mindset': ['Patient']})
    assert not tagging.matched_playbooks(journal.tag_library(), common.rows_for(journal)[0]['tag_ids'])
    journal.apply_playbook(trade, book)
    row = common.rows_for(journal)[0]
    assert set(row['tags']) == {'Pullback', 'Followed plan', 'Patient'}
    assert row['playbooks'] == ['Patient setup']
    assert row['notes'] == 'Keep' and journal.annotations()[trade.id]['initial_risk'] == 100
    journal.edit_tag(tag_id(journal, 'Patient'), name='Calm')
    journal.edit_tag_group('Mindset', name='Psychology')
    assert common.rows_for(journal)[0]['playbooks'] == ['Patient setup']
    assert 'Psychology' in journal.tag_library()['playbooks'][book]['rules']


def test_invalid_playbook_apply_rolls_back_every_group_and_retirement_preserves_definition(tmp_path):
    journal = Journal(tmp_path / 'live.db')
    trade = round_trip(journal)
    journal.annotate(trade.id, {'tags': ['Pullback'], 'notes': 'Before'})
    book = journal.save_playbook('Test', {'Setup': ['Breakout'], 'Mindset': ['Patient']})
    journal.edit_tag(tag_id(journal, 'Patient'), retired=True)
    before = journal.annotations()
    with pytest.raises(ValueError):
        journal.apply_playbook(trade, book)
    assert journal.annotations() == before
    rules = journal.tag_library()['playbooks'][book]['rules']
    journal.save_playbook('Test', rules, id=book, retired=True)
    with pytest.raises(ValueError):
        journal.apply_playbook(trade, book)
    assert journal.tag_library()['playbooks'][book]['rules'] == rules


def test_a_playbook_keeps_its_colour_until_one_is_chosen(tmp_path):
    journal = Journal(tmp_path / 'live.db')
    book = journal.save_playbook('A+', {'Setup': ['Breakout']}, color='#E2CF32')
    assert journal.tag_library()['playbooks'][book]['color'] == '#E2CF32'
    journal.save_playbook('A+', {'Setup': ['Breakout', 'Pullback']}, id=book)        # saved without one: kept
    assert journal.tag_library()['playbooks'][book]['color'] == '#E2CF32'
    journal.save_playbook('A+', {'Setup': ['Breakout']}, id=book, color='#45BF89')
    assert journal.tag_library()['playbooks'][book]['color'] == '#45BF89'
    with pytest.raises(ValueError):
        journal.save_playbook('B', {'Setup': ['Breakout']}, color='green')
    plain = journal.save_playbook('C', {'Setup': ['Range']})
    assert 'color' not in journal.tag_library()['playbooks'][plain]


@pytest.mark.parametrize('operation', ['trade', 'day', 'playbook'])
def test_unknown_choices_are_not_silently_dropped(tmp_path, operation):
    journal = Journal(tmp_path / 'live.db')
    trade = round_trip(journal)
    with pytest.raises(ValueError):
        if operation == 'trade':
            journal.set_trade_tag_group(trade, 'Setup', ['Breakout', 'Not real'])
        elif operation == 'day':
            journal.set_day_tag_group('2026-10-07', 'Setup', ['Not real'])
        else:
            journal.save_playbook('Bad', {'Setup': ['Breakout', 'Not real']})
    assert not journal.annotations() and not journal.get('sessions')


def test_concurrent_group_click_and_notes_merge_and_reordering_is_stable(tmp_path):
    journal = Journal(tmp_path / 'live.db')
    trade = round_trip(journal)
    barrier = threading.Barrier(2)
    def group():
        barrier.wait()
        journal.set_trade_tag_group(trade, 'Setup', ['Breakout'])
    def notes():
        barrier.wait()
        journal.update_annotation(trade.id, {'notes': 'Concurrent'})
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(group), pool.submit(notes)]
        for future in futures:
            future.result()
    assert journal.annotations()[trade.id]['tags'] == ['Breakout']
    assert journal.annotations()[trade.id]['notes'] == 'Concurrent'
    id = tag_id(journal, 'Pullback')
    journal.move_tag(id, -100)
    assert tagging.ordered_groups(journal.tag_library())['Setup'][0]['id'] == id
    journal.edit_tag_group('Mindset', move=-100)
    assert next(iter(tagging.ordered_groups(journal.tag_library()))) == 'Mindset'


# ---------------------------------------------------------------- starter categories

from mwjournal.tags import LEGACY_CATALOG, STARTERS


def stored_library(journal):
    with journal.connect() as db:
        row = db.execute("SELECT value FROM journal_meta WHERE key='tag_library'").fetchone()
    return row[0] if row else None


def test_a_new_journal_opens_on_three_editable_starter_categories_and_day_tags_stored_once(tmp_path):
    journal = Journal(tmp_path / 'live.db')
    assert stored_library(journal) is None              # nothing is written before the library is read
    library = journal.tag_library()
    groups = tagging.ordered_groups(library)
    assert list(groups) == ['Setup', 'Execution', 'Mindset']
    assert [t['name'] for t in groups['Setup']] == ['Breakout', 'Pullback', 'Reversal', 'Range']
    assert [t['name'] for t in groups['Execution']] == ['Followed Plan', 'Chased Entry', 'Exited Early',
                                                        'Let Winner Run', 'Moved Stop']
    assert [t['name'] for t in groups['Mindset']] == ['Calm', 'Patient', 'FOMO', 'Hesitant', 'Tilted']
    # The Day category's tags are chosen on a day's Daily Report Card, never on a trade.
    days = tagging.ordered_groups(library, level='day')
    assert list(days) == ['Day'] and [t['name'] for t in days['Day']] == ['Had a Plan', 'Kept Risk Limits',
                                                                         'Respected My Stop']
    assert all(t['levels'] == ['day'] for t in days['Day'])
    groups = {**groups, **days}
    colours = {group: {t['color'] for t in tags} for group, tags in groups.items()}
    assert all(len(c) == 1 for c in colours.values()) and len(set.union(*colours.values())) == 4
    assert stored_library(journal) is not None and library['starters']
    assert not journal.annotations()                    # offered, never applied
    assert Journal(journal.path).tag_library() == library


def test_a_tag_made_in_day_is_a_day_tag_and_one_made_in_setup_a_trade_tag(tmp_path):
    journal = Journal(tmp_path / 'live.db')
    journal.add_tag('Slept Well', 'Day')
    journal.add_tag('Gap Fill', 'Setup')
    library = journal.tag_library()
    made = {name: library['tags'][tag_id(journal, name)] for name in ('Slept Well', 'Gap Fill')}
    assert made['Slept Well']['levels'] == ['day'] and made['Slept Well']['color'] == '#E48DB9'
    assert made['Gap Fill']['levels'] == ['trade']
    assert 'Slept Well' in [t['name'] for t in tagging.ordered_groups(library, level='day')['Day']]
    assert 'Day' not in tagging.ordered_groups(library)              # never offered on a trade


def test_starter_tags_can_be_renamed_recoloured_retired_and_deleted_and_never_come_back(tmp_path):
    journal = Journal(tmp_path / 'live.db')
    trade = round_trip(journal)
    calm = tag_id(journal, 'Calm')
    journal.edit_tag(calm, name='Composed', color='#123456')
    journal.edit_tag(tag_id(journal, 'Tilted'), retired=True)
    journal.edit_tag_group('Mindset', name='Psychology')
    library = journal.tag_library()
    assert library['tags'][calm]['name'] == 'Composed' and library['tags'][calm]['group'] == 'Psychology'
    journal.set_trade_tag_group(trade, 'Setup', ['Breakout'])
    with pytest.raises(ValueError, match='Retire it instead'):
        journal.delete_tag(tag_id(journal, 'Breakout'))  # used on a trade: history keeps its name
    with pytest.raises(ValueError, match='holds'):
        journal.delete_tag_group('Setup')
    for id, tag in list(journal.tag_library()['tags'].items()):
        if tag['name'] != 'Breakout':
            journal.delete_tag(id)
    for group in ('Execution', 'Psychology', 'Day'):
        journal.delete_tag_group(group)
    assert list(journal.tag_library()['groups']) == ['Setup']
    assert [t['name'] for t in journal.tag_library()['tags'].values()] == ['Breakout']
    assert list(Journal(journal.path).tag_library()['groups']) == ['Setup']      # not offered again
    assert journal.annotations()[trade.id]['tags'] == ['Breakout']


def test_an_existing_library_is_never_given_the_starters_or_rewritten(tmp_path):
    journal = Journal(tmp_path / 'live.db')
    # What 0.4.0 stored, less the day-tag time zone the schema 4 upgrade took out: its hidden suggestions plus a
    # category of the trader's own.
    old = {'version': 1, 'groups': {}, 'playbooks': {}, 'tags': {}}
    for name, group in {**LEGACY_CATALOG, 'Absorption': 'Orderflow'}.items():
        old['tags'][tagging.identity(name)] = {'id': tagging.identity(name), 'name': name, 'aliases': [name],
                                               'group': group, 'retired': False, 'levels': ['trade'],
                                               'color': '#65CA98', 'order': len(old['tags'])}
        old['groups'].setdefault(group, {'exclusive': False, 'order': len(old['groups'])})
    with journal.connect() as db:
        tagging.save(db, old)
    before = stored_library(journal)
    library = journal.tag_library()
    names = {t['name'] for t in library['tags'].values()}
    assert names == {*LEGACY_CATALOG, 'Absorption'} and not library.get('starters')
    assert not names & {'Moved Stop', 'Tilted', 'Followed Plan'}
    assert stored_library(journal) == before                                    # read, never rewritten


@pytest.mark.parametrize('history', ['review', 'day', 'rating'])
def test_a_journal_with_tag_history_keeps_the_old_suggestions_exactly(tmp_path, history):
    journal = Journal(tmp_path / 'live.db')
    trade = round_trip(journal)
    legacy_id = tagging.identity('Focused')
    if history == 'review':
        journal.annotate(trade.id, {'tags': ['My own tag']})
    elif history == 'day':
        journal.update_session('2026-10-07', {'day_tag_ids': [legacy_id]})
    else:
        with journal.connect() as db:                   # a rating that names an old built-in suggestion
            db.execute("INSERT OR REPLACE INTO journal_meta VALUES('workspace',?)",
                       ('{"ratings": {"r": {"rules": [{"rules": {"Mindset": ["%s"]}}]}}}' % legacy_id,))
    library = journal.tag_library()
    assert not library.get('starters') and legacy_id in library['tags']
    assert {t['name'] for t in library['tags'].values()} >= set(LEGACY_CATALOG)
    assert 'Moved Stop' not in {t['name'] for t in library['tags'].values()}


def test_live_and_replay_are_offered_their_own_starters(tmp_path):
    live = Journal(tmp_path / 'live.db')
    replay = Journal(tmp_path / 'replay.db', dataset='replay')
    live.delete_tag(tag_id(live, 'Range'))
    live.add_tag('VWAP reclaim', 'Setup')
    setup = {t['color'] for t in live.tag_library()['tags'].values() if t['group'] == 'Setup'}
    assert len(setup) == 1                              # a new tag wears its category's colour
    assert 'Range' in replay.tag_catalog() and 'VWAP reclaim' not in replay.tag_catalog()
    assert 'Range' not in live.tag_catalog() and live.tag_catalog()['VWAP reclaim'] == 'Setup'


def test_two_first_reads_at_once_store_one_identical_library(tmp_path):
    for attempt in range(5):                            # a race shows up only now and then: try a few times
        journal = Journal(tmp_path / f'live-{attempt}.db')
        barrier = threading.Barrier(6)
        def read():
            barrier.wait()
            return Journal(journal.path).tag_library()
        with ThreadPoolExecutor(max_workers=6) as pool:
            results = [f.result() for f in [pool.submit(read) for _ in range(6)]]
        assert all(r == results[0] for r in results) and results[0]['starters']
        assert [t['name'] for t in tagging.ordered_groups(results[0])['Setup']] == list(STARTERS[0][2])
        assert results[0] == journal.tag_library()


def test_saving_a_note_or_clicking_a_starter_tag_never_adds_an_empty_category(tmp_path):
    journal = Journal(tmp_path / 'live.db')
    trade = round_trip(journal)
    journal.update_annotation(trade.id, {'notes': 'waited for the retest'})
    journal.update_annotation(trade.id, {}, add_tags=['Calm'])
    journal.set_trade_tag_group(trade, 'Setup', ['Pullback'])
    assert list(tagging.ordered_groups(journal.tag_library())) == ['Setup', 'Execution', 'Mindset']
    assert 'Other tags' not in journal.tag_library()['groups']
    journal.update_annotation(trade.id, {}, add_tags=['Absorption'])            # a new tag goes to Other tags
    assert journal.tag_library()['tags'][tag_id(journal, 'Absorption')]['group'] == 'Other tags'
    assert 'Other tags' in journal.tag_library()['groups']
    assert journal.annotations()[trade.id]['notes'] == 'waited for the retest'
