"""Live and Replay share one set of tags and settings, kept in the Live journal; trades and reviews stay in each.
Disposable journals in a test's own data home only."""
import json
import sqlite3
import zipfile

import pytest

import common
import runtime
from mwjournal import Journal, backup, paths, tagging
from mwjournal.workspace import question
from test_app import open_app, widget
from test_chart import round_trip


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    monkeypatch.setenv('MWJ_HOME', str(tmp_path))
    yield tmp_path
    runtime.stop_all()


def tag_id(journal, name):
    return next(iter(tagging.ids(journal.tag_library(), [name])))


def meta(path, key):
    with sqlite3.connect(path) as db:
        row = db.execute('SELECT value FROM journal_meta WHERE key=?', (key,)).fetchone()
    return row and row[0]


def stored_workspace(path):
    return json.loads(meta(path, 'workspace') or '{}')


def test_what_is_set_in_live_is_set_in_replay_and_the_other_way_round(home):
    live = Journal()
    live.add_tag('Gap Fill', 'Setup')
    book = live.save_playbook('A+', {'Setup': ['Gap Fill']})
    live.save_filter('Longs', {'directions': ['long'], 'tags_all': ['Gap Fill']})
    page = live.save_dashboard('Mine', [{'kind': 'Equity'}])
    checklist = live.save_checklist('Before entry', [question('Is it my setup?')])
    live.save_preferences(display_timezone='Asia/Tokyo', timer_minutes=7)
    live.set_excursion_unit('points')
    live.set_tick_size('XYZ6', .5)
    replay = Journal(dataset='replay')
    assert replay.shared.path == live.path == paths.database('live')
    assert 'Gap Fill' in replay.tag_catalog() and replay.tag_library()['playbooks'][book]['name'] == 'A+'
    state = replay.workspace()
    assert state['filters']['Longs']['tags_all'] == [tag_id(live, 'Gap Fill')]
    assert page in state['dashboards'] and checklist in state['checklists']
    assert replay.time_zone() == 'Asia/Tokyo' and state['preferences']['timer_minutes'] == 7
    assert replay.display_settings() == {'excursion_unit': 'points', 'tick_sizes': {'XYZ6': .5}}
    # The Replay file keeps none of it: there is one copy, in Live.
    for key in ('tag_library', 'tag_catalog', 'display_settings'):
        assert meta(replay.path, key) is None
    assert not set(stored_workspace(replay.path)) & {'dashboards', 'filters', 'checklists', 'preferences'}
    replay.add_tag('Absorption', 'Orderflow')
    replay.save_filter('Shorts', {'directions': ['short']})
    replay.set_excursion_unit('ticks')
    common.set_display_zone(replay, 'Europe/London')
    fresh = Journal()
    assert fresh.tag_catalog()['Absorption'] == 'Orderflow' and 'Shorts' in fresh.workspace()['filters']
    assert fresh.display_settings()['excursion_unit'] == 'ticks' and fresh.time_zone() == 'Europe/London'
    assert meta(replay.path, 'tag_library') is None and meta(replay.path, 'display_settings') is None


def test_only_replay_used_makes_the_live_journal_that_keeps_the_settings(home):
    replay = Journal(dataset='replay')
    assert paths.database('live').is_file() and replay.shared.dataset == 'live'
    replay.add_tag('Gap Fill', 'Setup')
    assert 'Gap Fill' in Journal().tag_catalog()


def test_a_tag_on_a_replay_trade_or_day_or_draft_is_not_deleted_from_live(home):
    live, replay = Journal(), Journal(dataset='replay')
    trade = round_trip(replay)
    replay.set_trade_tag_group(trade, 'Setup', ['Breakout'])
    with pytest.raises(ValueError, match='Breakout is on 1 trade'):
        live.delete_tag(tag_id(live, 'Breakout'))
    replay.set_day_tag_group('2026-10-07', 'Day', ['Had a Plan'])
    with pytest.raises(ValueError, match='Had a Plan is on 1 day'):
        live.delete_tag(tag_id(live, 'Had a Plan'))
    replay.save_draft(provider='motivewave', account='paper', symbol='ESZ6', tags=['Calm'])
    with pytest.raises(ValueError, match='Calm is named in a filter, a checklist or Tag My Next Trade'):
        live.delete_tag(tag_id(live, 'Calm'))
    with pytest.raises(ValueError, match='Setup holds'):
        live.delete_tag_group('Setup')
    live.delete_tag(tag_id(live, 'Range'))                     # on nothing anywhere: it goes
    assert 'Range' not in replay.tag_catalog()


def test_a_journal_never_tagged_anywhere_is_offered_the_starters_once_and_one_tagged_in_replay_is_not(home):
    live, replay = Journal(), Journal(dataset='replay')
    trade = round_trip(replay)
    replay.annotate(trade.id, {'tags': ['My own tag']})       # a review that names its tag only by name
    library = live.tag_library()
    assert not library.get('starters') and 'My own tag' in live.tag_catalog()
    assert replay.annotations()[trade.id]['tags'] == ['My own tag']


def test_reviews_days_runs_check_ins_and_drafts_stay_in_each_environment(home):
    live, replay = Journal(), Journal(dataset='replay')
    shown, replayed = round_trip(live), round_trip(replay)      # one trade id in both journals
    assert shown.id == replayed.id
    live.update_annotation(shown.id, {'notes': 'Live review'}, add_tags=['Patient'])
    replay.update_annotation(replayed.id, {'notes': 'Replay review'}, add_tags=['FOMO'])
    live.update_session('2026-10-07', {'rating': 4})
    replay.update_session('2026-10-07', {'rating': 2})
    checklist = live.save_checklist('Before entry', [question('Is it my setup?')])
    live_run, replay_run = live.start_checklist(checklist, shown.id), replay.start_checklist(checklist, replayed.id)
    live.record_checkin('Mindset', ['Calm'])
    replay.record_checkin('Mindset', ['Tilted'])
    replay.save_draft(provider='motivewave', account='paper', symbol='NQZ6', tags=['Breakout'])
    assert live.annotations()[shown.id]['tags'] == ['Patient'] and live.annotations()[shown.id]['notes'] == 'Live review'
    assert replay.annotations()[replayed.id]['tags'] == ['FOMO']
    assert live.get('sessions')['2026-10-07']['rating'] == 4 and replay.get('sessions')['2026-10-07']['rating'] == 2
    live_state, replay_state = live.workspace(), replay.workspace()
    assert set(live_state['runs']) == {live_run} and set(replay_state['runs']) == {replay_run}
    assert [c['tag_ids'] for c in live_state['checkins'].values()] == [[tag_id(live, 'Calm')]]
    assert [c['tag_ids'] for c in replay_state['checkins'].values()] == [[tag_id(live, 'Tilted')]]
    assert not live_state['drafts'] and len(replay_state['drafts']) == 1
    assert live_state['checklists'] == replay_state['checklists']
    # Live's rows and Replay's rows both follow a tag renamed on either side.
    before = common.rows_signature(replay)
    live.edit_tag(tag_id(live, 'FOMO'), name='Fear of missing out')
    assert common.rows_signature(replay) != before
    assert common.rows_for(replay)[0]['tags'] == ['Fear of missing out']


def test_units_and_time_zone_chosen_in_live_show_in_replay(home):
    round_trip(Journal())
    app = open_app('Settings')
    widget(app.selectbox, 'MAE / MFE Units').set_value('points').run()
    widget(app.selectbox, 'Time Zone').set_value('Asia/Tokyo').run()
    app.radio(key='journal-environment').set_value('Replay').run()
    assert not app.exception
    assert widget(app.selectbox, 'MAE / MFE Units').value == 'points'
    assert widget(app.selectbox, 'Time Zone').value == 'Asia/Tokyo'
    widget(app.selectbox, 'MAE / MFE Units').set_value('ticks').run()
    app.radio(key='journal-environment').set_value('Live').run()
    assert widget(app.selectbox, 'MAE / MFE Units').value == 'ticks'


# ---------------------------------------------------------------- the move from 0.4.17, where each kept its own

def separate(home):
    """Live and Replay as 0.4.17 left them: each with its own tags, filters, dashboards, checklists, preferences and
    Units. Each is opened by its path, so each keeps its own, as both did then."""
    live = Journal(home / 'trades.sqlite3')
    replay = Journal(home / 'replay' / 'trades.sqlite3', dataset='replay')
    # Live renamed the starter Breakout; Replay kept it, made its own breakout, and a category Live lacks.
    live.edit_tag(tag_id(live, 'Breakout'), name='Break & Retest')
    replay.add_tag('breakout', 'Orderflow')
    replay.add_tag('Absorption', 'Orderflow')
    # One playbook name in both, under two ids (Replay renamed one); and a filter that uses it.
    ours = live.save_playbook('A+', {'Setup': ['Pullback']})
    theirs = replay.save_playbook('Old', {'Setup': ['Breakout']})
    replay.save_playbook('A+', {'Setup': ['Breakout']}, id=theirs)
    live.save_filter('Longs', {'directions': ['long']})
    replay.save_filter('Longs', {'directions': ['short']})                       # same name, another rule
    replay.save_filter('Mine', {'tags_all': ['breakout'], 'playbook': theirs})
    live.save_filter('Same', {'directions': ['long']})
    replay.save_filter('Same', {'directions': ['long']})
    replay.save_dashboard('Replay page', [{'kind': 'Summary', 'filters': {'tags_any': ['breakout', 'Absorption']}}])
    checklist = replay.save_checklist('Replay check', [question('Read the tape?', yes={'tag_ids': ['breakout']})])
    live.save_preferences(display_timezone='America/New_York')
    replay.save_preferences(display_timezone='Asia/Tokyo', timer_minutes=9)
    live.set_excursion_unit('points')
    live.set_tick_size('XYZ6', .5)
    replay.set_excursion_unit('ticks')
    replay.set_tick_size('ABC6', .25)
    trade = round_trip(replay)
    replay.update_annotation(trade.id, {'notes': 'Replay review'}, add_tags=['breakout', 'Breakout'])
    replay.save_draft(provider='motivewave', account='paper', symbol='NQZ6', tags=['breakout'])
    run = replay.start_checklist(checklist, trade.id)
    replay.answer_checklist(run, 'q1-yes')
    with sqlite3.connect(replay.path) as db:          # a review that names its tag only by name, as 0.3 stored it
        db.execute('INSERT INTO annotations VALUES(?,?)', ('old-trade', json.dumps({'tags': ['Legacy Name']})))
    return live, replay, ours, theirs, trade, checklist


def snapshot(path):
    with sqlite3.connect(path) as db:
        return (db.execute('SELECT key, value FROM journal_meta WHERE key NOT IN (?, ?) ORDER BY key',
                           ('schema_upgrade', 'notes_seen')).fetchall(),
                db.execute('SELECT * FROM annotations ORDER BY trade_id').fetchall(),
                db.execute('SELECT * FROM sessions ORDER BY key').fetchall())


def test_replays_settings_join_lives_once_with_a_backup_first(home):
    live, replay, ours, theirs, trade, checklist = separate(home)
    breakout = tag_id(live, 'Break & Retest')
    own = tag_id(replay, 'breakout')
    assert own != breakout and not (home / 'backups').exists()
    opened = Journal(dataset='replay')
    assert opened.shared.path == live.path
    library = opened.tag_library()
    tags = {tag['name']: tag for tag in library['tags'].values()}
    # One id: Live's name. A clash of names in another case: one tag, Live's, which keeps Replay's name too.
    assert 'Breakout' not in tags and 'breakout' not in tags
    assert {'Breakout', 'breakout', 'Break & Retest'} <= set(tags['Break & Retest']['aliases'])
    assert tags['Absorption']['group'] == 'Orderflow'
    groups = sorted(library['groups'], key=lambda g: library['groups'][g]['order'])
    assert groups == ['Setup', 'Execution', 'Mindset', 'Day', 'Orderflow', 'Other tags']    # Replay's after Live's
    assert tags['Legacy Name']['group'] == 'Other tags'
    # Replay's rows name Live's tag now; its review, draft, run and check-in stay in Replay.
    assert opened.annotations()[trade.id]['tags'] == ['Break & Retest']
    assert opened.annotations()['old-trade']['tags'] == ['Legacy Name']
    stored = json.loads(sqlite3.connect(opened.path).execute(
        'SELECT payload FROM annotations WHERE trade_id=?', (trade.id,)).fetchone()[0])
    assert stored['tag_ids'] == [breakout] and stored['notes'] == 'Replay review'
    state = opened.workspace()
    assert [d['tag_ids'] for d in state['drafts'].values()] == [[breakout]]
    assert [r['tag_ids'] for r in state['runs'].values()] == [[breakout]]
    assert state['checklists'][checklist]['graph']['nodes'][1]['tag_ids'] == [breakout]
    # The playbook named as Live's is Live's; a filter that named Replay's names Live's.
    assert list(library['playbooks']) == [ours] and theirs not in library['playbooks']
    filters = state['filters']
    assert filters['Longs'] == live.workspace()['filters']['Longs'] and filters['Longs']['directions'] == ['long']
    assert filters['Longs (Replay)']['directions'] == ['short']
    assert filters['Mine']['tags_all'] == [breakout] and filters['Mine']['playbook'] == ours
    assert 'Same (Replay)' not in filters
    page, = (p for p in state['dashboards'].values() if p['name'] == 'Replay page')
    assert page['widgets'][0]['filters']['tags_any'] == sorted([breakout, tag_id(opened, 'Absorption')])
    # Live's preferences and Units win; tick sizes of both are kept.
    assert opened.time_zone() == 'America/New_York' and state['preferences']['timer_minutes'] == 9
    assert opened.display_settings() == {'excursion_unit': 'points', 'tick_sizes': {'XYZ6': .5, 'ABC6': .25}}
    # Replay keeps nothing of its own settings; Live says so once, with the backup taken first.
    for key in ('tag_library', 'tag_catalog', 'display_settings'):
        assert meta(opened.path, key) is None
    assert set(stored_workspace(opened.path)) & {'dashboards', 'filters', 'checklists', 'preferences'} == set()
    copy, = (home / 'backups').glob('before-shared-settings-*.zip')
    with zipfile.ZipFile(copy) as archive:
        assert {'live.sqlite3', 'replay.sqlite3'} <= set(archive.namelist())
    assert backup.read(copy)['trades'] == {'live': 0, 'replay': 1}
    note = Journal().unseen_notes()['shared_settings']
    assert note.startswith('Live and Replay now share one set of tags') and 'Longs (Replay)' in note
    assert opened.unseen_notes() == {}


def test_the_move_is_harmless_twice_and_one_cut_short_finishes_at_the_next_open(home):
    separate(home)
    replay_file = paths.database('replay')
    kept = home / 'replay-before.sqlite3'
    with sqlite3.connect(replay_file) as db, sqlite3.connect(kept) as copy:
        db.backup(copy)
    Journal(dataset='replay')
    after = snapshot(paths.database('live')), snapshot(replay_file)
    Journal(dataset='replay')                              # again: nothing changes, no second backup
    assert (snapshot(paths.database('live')), snapshot(replay_file)) == after
    assert len(list((home / 'backups').glob('*.zip'))) == 1
    # Live took Replay's settings, but Replay's step was cut short: it still holds its own.
    with sqlite3.connect(kept) as copy, sqlite3.connect(replay_file) as db:
        copy.backup(db)
    assert meta(replay_file, 'tag_library') is not None
    Journal(dataset='replay')
    assert (snapshot(paths.database('live')), snapshot(replay_file)) == after
    assert len(list((home / 'backups').glob('*.zip'))) == 1


def test_a_new_replay_journal_takes_no_backup_and_says_nothing(home):
    Journal().add_tag('Gap Fill', 'Setup')
    Journal(dataset='replay')
    assert not (home / 'backups').exists() and Journal().unseen_notes() == {}
    assert meta(paths.database('live'), 'shared_settings') is not None


def test_a_restored_backup_from_0_4_17_is_moved_too(home):
    separate(home)
    copy = backup.write(home / 'old.zip')
    Journal(dataset='replay').add_tag('Made since', 'Setup')
    backup.restore(copy)
    live, replay = Journal(), Journal(dataset='replay')
    assert 'Absorption' in live.tag_catalog() and 'Made since' not in live.tag_catalog()
    assert meta(replay.path, 'tag_library') is None
    assert 'Longs (Replay)' in replay.workspace()['filters']
