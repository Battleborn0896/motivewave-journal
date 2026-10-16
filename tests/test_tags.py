"""Local tag autosave, reusable groups and partial-edit isolation."""
from concurrent.futures import ThreadPoolExecutor
import threading

import pytest

from mwjournal import Journal
from mwjournal.tags import tag_groups


def test_tag_add_remove_and_notes_plan_updates_never_overwrite_unrelated_fields(tmp_path):
    journal = Journal(tmp_path / 'test.sqlite3')
    journal.annotate('a', {'tags': ['Original', 'Patient'], 'notes': 'Keep me', 'initial_risk': 100,
                           'planned_stop': 4998, 'rating': 4, 'setup': 'VWAP'})
    journal.update_annotation('a', {}, add_tags=['Breakout'], remove_tags=['Patient'])
    journal.update_annotation('a', {'notes': 'Updated'})
    journal.update_annotation('a', {'planned_target': 5005})
    assert journal.annotations()['a'] == {'tags': ['Breakout', 'Original'], 'notes': 'Updated',
        'initial_risk': 100, 'planned_stop': 4998, 'planned_target': 5005, 'rating': 4, 'setup': 'VWAP'}
    with pytest.raises(ValueError):
        journal.update_annotation('a', {'initial_risk': -1}, add_tags=['Must not persist'])
    assert journal.annotations()['a']['initial_risk'] == 100
    assert 'Must not persist' not in journal.annotations()['a']['tags']


def test_two_simultaneous_autosaves_merge_instead_of_losing_a_field_or_tag(tmp_path):
    journal = Journal(tmp_path / 'test.sqlite3')
    barrier = threading.Barrier(2)
    def save(field, value, tag):
        barrier.wait()
        journal.update_annotation('a', {field: value}, add_tags=[tag])
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(save, 'notes', 'One', 'A'), pool.submit(save, 'setup', 'Two', 'B')]
        for future in futures:
            future.result()
    assert journal.annotations()['a'] == {'notes': 'One', 'setup': 'Two', 'tags': ['A', 'B']}


def test_reusable_tags_and_legacy_tags_remain_available_without_auto_tagging(tmp_path):
    live = Journal(tmp_path / 'live.sqlite3')
    replay = Journal(tmp_path / 'replay.sqlite3', dataset='replay')
    live.annotate('a', {'tags': ['My old tag']})
    groups = tag_groups(live.tag_catalog(), live.annotations())
    assert groups['Other tags'] == ['My old tag']
    assert 'Breakout' in groups['Setup']
    live.add_tag('VWAP reclaim', 'My setups')
    assert live.tag_catalog()['VWAP reclaim'] == 'My setups'
    assert 'VWAP reclaim' not in replay.tag_catalog()
    assert live.annotations()['a']['tags'] == ['My old tag']
    assert Journal(live.path).tag_catalog()['VWAP reclaim'] == 'My setups'
    with pytest.raises(ValueError, match='another group'):
        live.add_tag('VWAP reclaim', 'Other')
    with pytest.raises(ValueError):
        live.add_tag('', 'Custom')


def test_copy_tags_is_additive_and_keeps_both_trade_reviews(tmp_path):
    journal = Journal(tmp_path / 'test.sqlite3')
    journal.annotate('donor', {'tags': ['Patient', 'Breakout'], 'notes': 'Donor'})
    journal.annotate('target', {'tags': ['Original'], 'notes': 'Target', 'initial_risk': 100})
    journal.update_annotation('target', {}, add_tags=journal.annotations()['donor']['tags'])
    assert journal.annotations()['target']['tags'] == ['Breakout', 'Original', 'Patient']
    assert journal.annotations()['target']['notes'] == 'Target'
    assert journal.annotations()['target']['initial_risk'] == 100
    assert journal.annotations()['donor']['notes'] == 'Donor'
