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
    journal.update_session('2026-10-07:America/New_York', {'notes': 'Daily notes'})
    assert common.rows_for(journal)[0]['tags'] == ['FOMO']
    assert journal.get('sessions')['2026-10-07:America/New_York']['day_tag_ids']
    journal.set_trade_tag_group(trade, 'Mindset', [])
    assert common.rows_for(journal)[0]['tags'] == []
    assert journal.get('sessions')['2026-10-07:America/New_York']['notes'] == 'Daily notes'


def test_day_date_uses_explicit_timezone_and_replay_is_separate(tmp_path):
    journal = Journal(tmp_path / 'live.db')
    journal.edit_tag(tag_id(journal, 'Patient'), levels=['day'])
    journal.set_tag_timezone('America/Los_Angeles')
    state = journal.tag_library()
    assert tagging.session_key(state, '2026-10-08T02:00:00Z') == '2026-10-07:America/Los_Angeles'
    journal.set_day_tag_group('2026-10-07', 'Mindset', ['Patient'])
    replay = Journal(tmp_path / 'replay.db', dataset='replay')
    assert not replay.get('sessions')
    assert replay.tag_library()['session_timezone'] == 'America/New_York'


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
    assert row['notes'] == 'Keep' and row['initial_risk'] == 100
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
