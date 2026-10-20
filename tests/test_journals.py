"""Named-journal isolation, provenance, rollback and non-destructive management on paper data."""
import json
import sqlite3
from pathlib import Path

import pytest

import common
import runtime
from mwjournal import Execution, Journal
from mwjournal import journals
from mwjournal.store import ImportConflict
from test_app import open_app, widget, press
from test_chart import seed_preview


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    monkeypatch.setenv('MWJ_HOME', str(tmp_path))
    yield tmp_path
    runtime.stop_all()


def another(capture, account='other', provider='motivewave', day=8):
    capture.ingest([(Execution(provider, account, 'NQZ6', 'buy', 1, 20000,
                               f'2026-10-{day:02d}T14:00:00Z', execution_id='other-in-' + str(day)), {}),
                    (Execution(provider, account, 'NQZ6', 'sell', 1, 20001,
                               f'2026-10-{day:02d}T14:01:00Z', execution_id='other-out-' + str(day)), {})])


def test_named_journal_mirrors_only_selected_provider_account_and_preserves_original(home):
    capture = seed_preview(home)
    original = capture.executions()
    another(capture)
    another(capture, account='paper', provider='another-provider', day=9)
    id = journals.create(capture, 'My main journal', [('motivewave', 'paper')])
    target = journals.open_named(capture, id)
    assert target.executions() == original
    assert len(target.trades()) == 1
    assert target.annotations() == {}  # independent review, not another journal's assertions
    trade = target.trades()[0]
    target.update_annotation(trade.id, {'notes': 'My new review'}, add_tags=['Patient'])
    assert capture.annotations()[trade.id]['notes'] != 'My new review'
    assert common.key(target, 'filter-tags') != common.key(capture, 'filter-tags')
    assert target.metric_options(trade) == capture.metric_options(trade)
    assert target.prices(trade.symbol, 'motivewave-bars:1m') == capture.prices(trade.symbol, 'motivewave-bars:1m')


def test_new_scoped_fills_arrive_without_overwriting_review_or_live_replay(home):
    capture = seed_preview(home)
    id = journals.create(capture, 'Paper', [('motivewave', 'paper')])
    target = journals.open_named(capture, id)
    trade = target.trades()[0]
    target.update_annotation(trade.id, {'notes': 'Keep me'})
    another(capture, account='paper')
    target = journals.open_named(capture, id)
    assert len(target.trades()) == 2 and target.annotations()[trade.id]['notes'] == 'Keep me'
    assert not journals.registry(Journal(dataset='replay'))['journals']
    with pytest.raises(ValueError):
        journals.open_named(Journal(dataset='replay'), id)


def test_clone_is_independent_archive_restore_keeps_database_and_source(home):
    capture = seed_preview(home)
    id = journals.create(capture, 'First', [])
    first = journals.open_named(capture, id)
    trade = first.trades()[0]
    first.update_annotation(trade.id, {'notes': 'First review'}, add_tags=['Patient'])
    clone = journals.create(capture, 'Clone', [], clone=id)
    second = journals.open_named(capture, clone)
    assert second.annotations() == first.annotations()
    second.update_annotation(trade.id, {'notes': 'Changed independently'})
    assert first.annotations()[trade.id]['notes'] == 'First review'
    journals.rename(capture, clone, 'Second')
    journals.archive(capture, id)
    assert first.path.is_file() and journals.registry(capture)['journals'][id]['archived']
    journals.archive(capture, id, False)
    assert journals.open_named(capture, id).annotations()[trade.id]['notes'] == 'First review'
    assert len(capture.trades()) == 1


def test_execution_conflict_rolls_back_mirror_without_overwriting_either_side(home):
    capture = seed_preview(home)
    id = journals.create(capture, 'First', [])
    first = journals.open_named(capture, id)
    with first.connect() as db:
        row = db.execute('SELECT key,payload FROM executions ORDER BY rowid LIMIT 1').fetchone()
        data = json.loads(row['payload'])
        data['price'] += 1
        db.execute('UPDATE executions SET payload=? WHERE key=?', (json.dumps(data, sort_keys=True), row['key']))
    with pytest.raises(ImportConflict):
        journals.open_named(capture, id)
    updated = next(fill for fill in first.executions() if fill.key == row['key'])
    original = next(fill for fill in capture.executions() if fill.key == row['key'])
    assert updated.price == original.price + 1


def test_source_label_upgrade_can_mirror_without_changing_fill_identity(home):
    capture = seed_preview(home)
    id = journals.create(capture, 'First', [])
    with capture.connect() as db:
        row = db.execute('SELECT key,payload FROM executions LIMIT 1').fetchone()
        data = json.loads(row['payload'])
        data['quality'] = 'A refined platform description'
        db.execute('UPDATE executions SET payload=? WHERE key=?', (json.dumps(data, sort_keys=True), row['key']))
    target = journals.open_named(capture, id)
    assert next(fill for fill in target.executions() if fill.key == row['key']).quality == 'A refined platform description'
    assert [fill.key for fill in target.executions()] == [fill.key for fill in capture.executions()]


@pytest.mark.parametrize('id', ['../trades.sqlite3', 'live', '/tmp/fake', '0' * 33])
def test_named_journal_paths_never_accept_arbitrary_paths(home, id):
    with pytest.raises(ValueError):
        journals.journal_path(Journal(), id)


def test_named_backup_contains_raw_data_and_can_be_opened_as_standalone_journal(home):
    capture = seed_preview(home)
    id = journals.create(capture, 'First', [('motivewave', 'paper')])
    first = journals.open_named(capture, id)
    snapshot = home / 'standalone.sqlite3'
    snapshot.write_bytes(first.backup())
    restored = Journal(snapshot)
    assert restored.executions() == first.executions()
    assert restored.trades() == first.trades()
    assert restored.metric_options(restored.trades()[0]) == first.metric_options(first.trades()[0])


def test_account_alias_is_display_only_and_named_journal_switch_preserves_independent_units(home):
    capture = seed_preview(home)
    before = capture.executions()
    journals.account_alias(capture, 'motivewave', 'paper', 'Practice')
    assert journals.alias(capture, ('motivewave', 'paper')) == 'Practice'
    assert capture.executions() == before
    id = journals.create(capture, 'My journal', [('motivewave', 'paper')])
    named = journals.open_named(capture, id)
    named.set_excursion_unit('points')
    app = open_app('General')
    widget(app.sidebar.selectbox, 'Local journal').set_value(id).run()      # in the sidebar once one exists
    assert not app.exception
    assert widget(app.selectbox, 'MAE / MFE Units').value == 'points'
    widget(app.sidebar.selectbox, 'Local journal').set_value(None).run()
    assert widget(app.selectbox, 'MAE / MFE Units').value == 'currency'


def test_named_journal_creation_and_archive_ui_do_not_remove_files(home):
    capture = seed_preview(home)
    app = open_app('Journals')
    widget(app.text_input, 'New journal name').set_value('Practice')
    press(app, 'Create Journal')
    id = widget(app.selectbox, 'Local journal').value
    assert id in journals.registry(capture)['journals']
    file = journals.journal_path(capture, id)
    press(app, 'Archive Journal')
    assert file.is_file()
    assert app.session_state[common.key(capture, 'named-journal')] is None
