"""Journal views over the one journal of each environment, and named journals of 0.4.17 becoming views. Disposable
journals in a test's own data home only."""
import json
import sqlite3
import uuid
import zipfile

import pytest

import common
import runtime
from mwjournal import Execution, Journal, backup, journals, merging, paths, tagging
from mwjournal.workspace import question
from test_app import listed, open_app, press, said, widget
from views import preferences
from test_chart import round_trip, seed_preview


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    monkeypatch.setenv('MWJ_HOME', str(tmp_path))
    yield tmp_path
    runtime.stop_all()


def trade_on(journal, account, day, *, provider='motivewave', symbol='NQZ6', hour=14, name=None):
    """A one-contract long held one minute, on `account`, on October `day`; returns it."""
    name = name or f'{account}-{day}-{hour}'
    journal.ingest([(Execution(provider, account, symbol, 'buy', 1, 20000, f'2026-10-{day:02d}T{hour}:00:00Z',
                               execution_id=name + '-in'), {}),
                    (Execution(provider, account, symbol, 'sell', 1, 20001, f'2026-10-{day:02d}T{hour}:01:00Z',
                               execution_id=name + '-out'), {})])
    return next(t for t in journal.trades() if t.fills[0]['execution_id'] == name + '-in')


def tag_id(journal, name):
    return next(iter(tagging.ids(journal.tag_library(), [name])))


def old_named(env, name, scopes=(), *, archived=False, clone=None):
    """A named journal as 0.4.17 made one beside the environment's journal `env`: a database of its own holding a copy
    of env's fills on `scopes` (every account when empty), with reviews and settings of its own, listed in env's
    registry; and env as 0.4.17 left it, not yet folded. Returns (the named journal, its id)."""
    id = uuid.uuid4().hex
    path = env.path.parent / 'journals' / id / 'trades.sqlite3'
    path.parent.mkdir(parents=True)
    if clone:
        path.write_bytes(Journal(env.path.parent / 'journals' / clone / 'trades.sqlite3', dataset=env.dataset).backup())
    named = Journal(path, dataset=env.dataset)
    with env.connect() as db:
        fills = [(Execution(**json.loads(payload)), json.loads(raw))
                 for payload, raw in db.execute('SELECT payload, raw FROM executions ORDER BY rowid')]
    named.ingest([(fill, raw) for fill, raw in fills if not scopes or (fill.provider, fill.account) in scopes])
    with env.connect() as db:
        row = db.execute("SELECT value FROM journal_meta WHERE key='journal_registry'").fetchone()
        state = json.loads(row[0]) if row else {'account_aliases': {}}
        state.setdefault('journals', {})[id] = {'id': id, 'name': name, 'scopes': [list(s) for s in scopes],
                                                'archived': archived, 'clone_of': clone}
        db.execute("INSERT OR REPLACE INTO journal_meta VALUES('journal_registry',?)", (json.dumps(state),))
        db.execute("DELETE FROM journal_meta WHERE key='journal_views'")
    return named, id


def meta(path, key):
    with sqlite3.connect(path) as db:
        row = db.execute('SELECT value FROM journal_meta WHERE key=?', (key,)).fetchone()
    return row and row[0]


def snapshot(path):
    with sqlite3.connect(path) as db:
        return [db.execute(f'SELECT * FROM {table} ORDER BY 1').fetchall()
                for table in ('executions', 'annotations', 'sessions', 'instruments', 'journal_meta')]


def zips(home):
    return sorted((home / 'backups').glob('before-journal-views-*.zip')) if (home / 'backups').exists() else []


# ---------------------------------------------------------------- views

def test_a_view_shows_its_accounts_trades_with_the_reviews_of_all_accounts(home):
    journal = seed_preview(home)                          # paper · ESZ6, reviewed
    paper, = journal.trades()
    sim = trade_on(journal, 'SIM', 8, provider='motivewave-native:workspace:live')
    journals.account_alias(journal, 'motivewave', 'paper', 'Practice')
    app = open_app()
    assert not app.sidebar.selectbox                      # no view yet: no Journal menu
    app = open_app('Journals')
    assert widget(app.multiselect, 'Accounts').options == ['Practice', 'SIM']
    widget(app.text_input, 'View name').set_value('Practice only')
    widget(app.multiselect, 'Accounts').set_value([('motivewave', 'paper')])
    press(app, 'Create View')
    id, = journals.registry(journal)['views']
    menu = widget(app.sidebar.selectbox, 'Journal')
    assert menu.value == id and menu.options == ['All accounts', 'Practice only']     # All accounts first
    assert said(app.main.caption, 'Shows Practice.')
    app = show_trades(app)
    assert listed(app) == [paper.id]
    assert app.session_state['selected-trade-live'] == paper.id
    widget(app.sidebar.selectbox, 'Journal').set_value(None).run()
    assert not app.exception and set(listed(app)) == {paper.id, sim.id}
    before = journal.annotations()
    # Reviewed in the view, the review is All accounts' too: one review per trade.
    widget(app.sidebar.selectbox, 'Journal').set_value(id).run()
    journal.update_annotation(paper.id, {'notes': 'Seen in the view'})
    assert Journal().annotations()[paper.id]['notes'] == 'Seen in the view'
    journals.rename(journal, id, 'Paper')
    with pytest.raises(ValueError, match='already has that name'):
        journals.create(journal, 'all accounts', [('motivewave', 'paper')])
    with pytest.raises(ValueError, match='at least one account'):
        journals.create(journal, 'Nothing', [])
    journals.delete(journal, id)
    assert journals.registry(journal)['views'] == {}
    assert Journal().trades() == journal.trades() and set(Journal().annotations()) == set(before)


def test_the_session_menu_names_a_trade_the_view_leaves_out(home):
    journal = seed_preview(home)                          # paper · ESZ6
    paper, = journal.trades()
    trade_on(journal, 'SIM', 8)
    journal.add_tag('Clear-headed', 'State')
    journal.record_checkin('State', ['Clear-headed'], trade_id=paper.id)
    id = journals.create(journal, 'SIM only', [('motivewave', 'SIM')])
    app = open_app()
    widget(app.sidebar.selectbox, 'Journal').set_value(id).run()
    app.session_state['session-menu-live'] = True
    app.run()
    assert not app.exception
    checkins = next(d.value for d in app.dataframe if 'Category' in d.value.columns)
    named, = checkins['Trade']
    assert named.startswith('Oct 7, ') and named.endswith(' · ESZ6 Long 2')       # not 'A trade no longer listed'


def test_every_replay_run_of_an_account_is_that_account(home):
    """Each rewind or restarted replay starts a run of its own ('...:replay:<run>'). Tag My Next Trade armed in one
    run goes on the next trade in a later run, and a view and an account's name made in one run cover the next."""
    run_1, run_2 = 'motivewave-native:ws:replay:r1', 'motivewave-native:ws:replay:r2'
    replay = Journal(dataset='replay')
    first = trade_on(replay, 'SIM', 7, provider=run_1)
    with replay.connect() as db:                          # a name given in run 1 by 0.4.17, stored with its run
        db.execute("INSERT INTO journal_meta VALUES('journal_registry',?)",
                   (json.dumps({'views': {}, 'account_aliases': {json.dumps([run_1, 'SIM']): 'Rewinds'}}),))
    replay.save_draft(provider=run_1, account='SIM', symbol='NQZ6', tags=['Patient'])
    id = journals.create(replay, 'Practice', [(run_1, 'SIM')])
    second = trade_on(replay, 'SIM', 8, provider=run_2)
    replay.reconcile_drafts()
    draft, = replay.workspace()['drafts'].values()
    assert (draft['state'], draft['trade_id']) == ('attached', second.id)
    assert 'Patient' in replay.annotations()[second.id]['tags'] and first.id not in replay.annotations()
    assert journals.alias(replay, (run_2, 'SIM')) == 'Rewinds'
    app = open_app(environment='Replay')
    widget(app.sidebar.selectbox, 'Journal').set_value(id).run()
    assert not app.exception and set(listed(app, 'replay')) == {first.id, second.id}
    app.session_state['session-menu-replay'] = True
    app.run()
    assert widget(app.selectbox, 'Contract').options == ['Rewinds · NQZ6']       # one account, not one per run
    # Renamed in run 2, the account has one name, in every run.
    journals.account_alias(replay, run_2, 'SIM', 'Replay')
    assert [journals.alias(replay, (run, 'SIM')) for run in (run_1, run_2)] == ['Replay', 'Replay']
    assert len(journals.registry(replay)['account_aliases']) == 1
    # Armed again in run 2, it replaces the one waiting from run 1.
    waiting = replay.save_draft(provider=run_1, account='SIM', symbol='NQZ6', notes='Wait for the pullback')
    replay.save_draft(provider=run_2, account='SIM', symbol='NQZ6', notes='Only A setups')
    assert replay.workspace()['drafts'][waiting]['state'] == 'cancelled'


def show_trades(app):
    app.segmented_control(key=common.VIEW_KEY).set_value('Trades').run()
    assert not app.exception
    return app


def test_a_view_filters_before_the_saved_filter_and_settings_are_one_set(home):
    journal = seed_preview(home)
    paper, = journal.trades()
    es = trade_on(journal, 'paper', 8, symbol='ESZ6')
    sim = trade_on(journal, 'SIM', 9)
    nq = trade_on(journal, 'paper', 10)
    id = journals.create(journal, 'Paper', [('motivewave', 'paper')])
    journal.save_filter('NQ', {'symbols': ['NQZ6']})
    app = open_app()
    widget(app.sidebar.selectbox, 'Journal').set_value(id).run()
    assert not app.exception and set(listed(app)) == {paper.id, es.id, nq.id}
    app.session_state[preferences.saved_filter_key(journal)] = 'NQ'
    app.run()
    assert not app.exception and listed(app) == [nq.id]               # the view's trades, then the filter's
    widget(app.sidebar.selectbox, 'Journal').set_value(None).run()
    assert not app.exception and set(listed(app)) == {nq.id, sim.id}
    # The Units chosen with the view on screen are the Units of All accounts: one set of settings.
    app = open_app('Settings')
    widget(app.sidebar.selectbox, 'Journal').set_value(id).run()
    widget(app.selectbox, 'MAE / MFE Units').set_value('points').run()
    widget(app.sidebar.selectbox, 'Journal').set_value(None).run()
    assert not app.exception and widget(app.selectbox, 'MAE / MFE Units').value == 'points'


def test_account_names_are_display_only(home):
    journal = seed_preview(home)
    before = journal.executions()
    journals.account_alias(journal, 'motivewave', 'paper', 'Practice')
    assert journals.alias(journal, ('motivewave', 'paper')) == 'Practice'
    assert journal.executions() == before


def test_each_environment_has_its_own_views(home):
    live = seed_preview(home)
    journals.create(live, 'Paper', [('motivewave', 'paper')])
    replay = Journal(dataset='replay')
    assert journals.registry(replay)['views'] == {}
    app = open_app(environment='Replay')
    assert not app.exception and not app.sidebar.selectbox


# ---------------------------------------------------------------- named journals of 0.4.17 become views

def test_a_named_journal_becomes_a_view_and_its_reviews_tags_and_settings_join_all_accounts(home):
    env = seed_preview(home)                              # paper · ESZ6 on Oct 7, reviewed in All accounts
    shown, = env.trades()
    nq = trade_on(env, 'paper', 8)
    trade_on(env, 'other', 9)
    env.save_filter('Longs', {'directions': ['long']})
    env.set_instrument('ESZ6', 50)
    practice, id = old_named(env, 'Practice', [('motivewave', 'paper')])
    assert [t.id for t in practice.trades()] == [shown.id, nq.id]
    # Its own review of a trade All accounts has not reviewed, with a tag and a category of its own.
    practice.add_tag('Absorption', 'Orderflow')
    practice.update_annotation(nq.id, {'notes': 'Practice review', 'rating': 4, 'planned_stop': 19990},
                               add_tags=['Absorption', 'Gap Fill'])
    practice.save_filter('Longs', {'directions': ['short']})                # a name All accounts uses for another
    practice.save_filter('Absorbed', {'tags_all': ['Absorption']})
    page = practice.save_dashboard('Practice page', [{'kind': 'Equity'}])
    checklist = practice.save_checklist('Before entry', [question('Is it my setup?')])
    run = practice.start_checklist(checklist, nq.id)
    practice.record_checkin('Mindset', ['Calm'])
    practice.save_draft(provider='motivewave', account='paper', symbol='NQZ6', tags=['Patient'])
    practice.set_instrument('ESZ6', 5)                                     # All accounts' multiplier stays
    practice.set_instrument('NQZ6', 20)                                    # one All accounts lacks is taken
    folder = practice.path.parent

    journal = Journal()
    view = journals.registry(journal)['views'][id]
    assert view == {'id': id, 'name': 'Practice', 'accounts': [['motivewave', 'paper']]}
    assert 'journals' not in journals.registry(journal) and not folder.exists()
    assert not (home / 'journals').exists()
    review = journal.annotations()[nq.id]
    assert review['notes'] == 'Practice review' and review['rating'] == 4 and review['planned_stop'] == 19990
    assert review['tags'] == ['Absorption', 'Gap Fill']
    assert journal.tag_catalog()['Absorption'] == 'Orderflow'
    state = journal.workspace()
    assert state['filters']['Longs']['directions'] == ['long']
    assert state['filters']['Longs (Practice)']['directions'] == ['short']
    assert state['filters']['Absorbed']['tags_all'] == [tag_id(journal, 'Absorption')]
    assert page in state['dashboards'] and checklist in state['checklists'] and run in state['runs']
    assert [c['tag_ids'] for c in state['checkins'].values()] == [[tag_id(journal, 'Calm')]]
    assert [d['account'] for d in state['drafts'].values()] == ['paper']
    with journal.connect() as db:
        assert dict(db.execute('SELECT symbol, point_value FROM instruments').fetchall()) == {'ESZ6': 50, 'NQZ6': 20}
    # The backup, taken first, holds the named journal and reads back.
    copy, = zips(home)
    with zipfile.ZipFile(copy) as archive:
        assert f'journals/{id}/trades.sqlite3' in archive.namelist()
    assert backup.read(copy)['trades']['live'] == 3
    note = journal.unseen_notes()['journal_views']
    assert note.startswith('Named journals are now views, chosen under Journal in the sidebar') and 'fill' not in note
    assert json.loads(meta(journal.path, 'schema_upgrade'))['journal_views']['backup'] == str(copy)
    # Once only: opening again changes nothing and takes no second backup.
    before = snapshot(journal.path)
    Journal()
    assert snapshot(journal.path) == before and zips(home) == [copy]
    # The view is in the sidebar.
    app = open_app()
    assert widget(app.sidebar.selectbox, 'Journal').options == ['All accounts', 'Practice']


def test_reviews_join_all_accounts_and_a_clone_adds_no_note_twice(home):
    env = seed_preview(home)                              # its review: a note, a Stop and two tags
    trade, = env.trades()
    env.set_trade_tag_group(trade, 'Setup', ['Breakout'])
    env.edit_tag_group('Setup', exclusive=True)
    env.update_session('2026-10-07', {'rating': 3, 'notes': 'All accounts day'})
    first, a = old_named(env, 'First', [])
    first.update_annotation(trade.id, {'notes': 'First review', 'rating': 2, 'planned_stop': 4990},
                            add_tags=['Pullback', 'FOMO'])
    first.update_session('2026-10-07', {'rating': 5, 'notes': 'First day'})
    first.set_day_tag_group('2026-10-07', 'Day', ['Had a Plan'])
    second, b = old_named(env, 'Second', [], clone=a)     # holds First's reviews as they were
    second.update_annotation(trade.id, {}, add_tags=['Calm'])
    second.update_session('2026-10-07', {'notes': 'First day\n\nThe clone went on'})

    journal = Journal()
    review = journal.annotations()[trade.id]
    # All accounts' Stop stays and its empty Rating is taken; its one-choice Setup keeps Breakout.
    assert review['planned_stop'] == 4998 and review['rating'] == 2
    assert review['notes'] == 'Synthetic fixture, not an actual account trade.\n\nFirst review'   # never twice
    assert review['tags'] == sorted(['Breakout', 'Calm', 'FOMO', 'PAPER FIXTURE', 'breakout'])
    day = journal.get('sessions')['2026-10-07']
    assert day['rating'] == 3 and day['day_tag_ids'] == [tag_id(journal, 'Had a Plan')]
    # The clone's note holds First's: only what it adds is added.
    assert day['notes'] == 'All accounts day\n\nFirst day\n\nThe clone went on'
    assert set(journals.registry(journal)['views']) == {a, b}


def test_fills_only_a_named_journal_held_are_added_and_counted(home):
    env = seed_preview(home)
    practice, id = old_named(env, 'Practice', [('motivewave', 'paper')])
    by_hand = trade_on(practice, 'paper', 8, name='by-hand')        # added by hand there, never recorded
    elsewhere = trade_on(practice, 'manual', 9, name='made-up')     # on an account the study never saw
    practice.update_annotation(by_hand.id, {'notes': 'Typed in'})
    a, b = trade_on(practice, 'paper', 10, hour=14), trade_on(practice, 'paper', 10, hour=15)
    merging.create(practice, [a.id, b.id])
    journal = Journal()
    assert {by_hand.id, elsewhere.id} <= {t.id for t in journal.trades()}         # All accounts counts them too
    assert journal.annotations()[by_hand.id]['notes'] == 'Typed in'
    view = journals.registry(journal)['views'][id]
    assert view['accounts'] == [['motivewave', 'manual'], ['motivewave', 'paper']]   # the view still shows them
    merged, = merging.definitions(journal).values()
    assert merged['active'] and merged['leg_ids'] == [a.id, b.id]
    assert '; 8 fills entered only in a named journal now count under All accounts too.' in (
        journal.unseen_notes()['journal_views'])


def test_tags_waiting_in_both_journals_wait_for_the_next_trade_once(home):
    env = seed_preview(home)
    env.save_draft(provider='motivewave', account='paper', symbol='NQZ6', tags=['Patient'])
    practice, _ = old_named(env, 'Practice', [('motivewave', 'paper')])
    trade_on(practice, 'paper', 8, name='by-hand')        # traded before, and only the named journal held it
    practice.save_draft(provider='motivewave', account='paper', symbol='NQZ6', tags=['Calm'])   # armed later
    journal = Journal()
    journal.reconcile_drafts()
    # One contract keeps one waiting, the one armed last; the trade only the named journal held is not the next one.
    assert sorted(d['state'] for d in journal.workspace()['drafts'].values()) == ['armed', 'cancelled']
    # Like any cancelled one, it keeps no list of the trades held when it was armed: that is read only while waiting.
    assert [d for d in journal.workspace()['drafts'].values() if 'existing_trades' in d and d['state'] != 'armed'] == []
    following = trade_on(journal, 'paper', 21)
    journal.reconcile_drafts()
    used, = [d for d in journal.workspace()['drafts'].values() if d['state'] == 'attached']
    assert used['trade_id'] == following.id and journal.annotations()[following.id]['tags'] == ['Calm']


def test_an_archived_journal_is_not_a_view_and_its_reviews_stay(home):
    env = seed_preview(home)
    trade, = env.trades()
    old, id = old_named(env, 'Old', [('motivewave', 'paper')], archived=True)
    old.update_annotation(trade.id, {'notes': 'From the archived journal'})
    folder = old.path.parent
    journal = Journal()
    assert journals.registry(journal)['views'] == {} and not folder.exists()
    assert journal.annotations()[trade.id]['notes'].endswith('\n\nFrom the archived journal')
    with zipfile.ZipFile(zips(home)[0]) as archive:
        assert f'journals/{id}/trades.sqlite3' in archive.namelist()
    # No view, so no Journal menu: the note does not send the trader looking for one.
    assert journal.unseen_notes()['journal_views'] == ("Your named journals' reviews, tags and settings joined All "
                                                       'accounts.')


def test_a_paragraph_is_matched_whole_and_a_note_all_accounts_lacks_is_copied_as_typed(home):
    env = seed_preview(home)                              # its review's note: one paragraph
    trade, = env.trades()
    other = trade_on(env, 'paper', 8)
    env.update_annotation(other.id, {'notes': '   ', 'rating': 3})
    named, _ = old_named(env, 'Practice', [('motivewave', 'paper')])
    named.update_annotation(trade.id, {'notes': 'fixture\n\nSynthetic   fixture, not an actual\naccount trade.'})
    typed = 'Late entry\n  chased it\n\n\nNext time wait'
    named.update_annotation(other.id, {'notes': typed})
    journal = Journal()
    # 'fixture' is a word of All accounts' note, not one of its paragraphs: it is added. The other paragraph is All
    # accounts' own, wrapped differently, and is not said twice.
    assert journal.annotations()[trade.id]['notes'] == 'Synthetic fixture, not an actual account trade.\n\nfixture'
    assert journal.annotations()[other.id]['notes'] == typed and journal.annotations()[other.id]['rating'] == 3


def test_views_are_listed_by_name_and_a_journal_named_all_accounts_keeps_its_own_place(home):
    env = seed_preview(home)
    old_named(env, 'Zulu', [('motivewave', 'paper')])
    old_named(env, 'all accounts', [('motivewave', 'paper')])
    journal = Journal()
    journals.create(journal, 'Mike', [('motivewave', 'paper')])
    names = [view['name'] for view in journals.registry(journal)['views'].values()]
    assert names == ['all accounts (view)', 'Mike', 'Zulu']
    app = open_app()
    assert widget(app.sidebar.selectbox, 'Journal').options == ['All accounts', *names]


def test_a_named_replay_journal_becomes_a_view_of_replay_and_its_tags_join_the_shared_ones(home):
    live = Journal()
    replay = Journal(dataset='replay')
    trade = round_trip(replay)
    named, id = old_named(replay, 'Replay practice', [('motivewave', 'paper')])
    named.add_tag('Absorption', 'Orderflow')
    named.update_annotation(trade.id, {'notes': 'Replay review'}, add_tags=['Absorption'])
    folder = named.path.parent
    assert folder.parent == home / 'replay' / 'journals'
    opened = Journal(dataset='replay')
    assert journals.registry(opened)['views'][id]['accounts'] == [['motivewave', 'paper']]
    assert journals.registry(live)['views'] == {}
    assert opened.annotations()[trade.id]['tags'] == ['Absorption']
    assert live.tag_catalog()['Absorption'] == 'Orderflow' and meta(opened.path, 'tag_library') is None
    assert not folder.exists()
    with zipfile.ZipFile(zips(home)[0]) as archive:
        assert f'replay/journals/{id}/trades.sqlite3' in archive.namelist()
    assert 'journal_views' in opened.unseen_notes() and 'journal_views' not in live.unseen_notes()


def test_a_fold_that_fails_carries_on_at_the_next_open_from_the_same_backup(home, monkeypatch):
    env = seed_preview(home)
    trade, = env.trades()
    first, a = old_named(env, 'First', [])
    trade_on(first, 'paper', 8, name='by-hand')
    second, b = old_named(env, 'Second', [])
    second.update_annotation(trade.id, {'notes': 'Second review'})
    folded = journals._fold_one

    def failing(journal, other, id, name):
        if id == b:
            raise sqlite3.OperationalError('disk I/O error')
        folded(journal, other, id, name)
    monkeypatch.setattr(journals, '_fold_one', failing)
    for _ in range(2):
        with pytest.raises(ValueError, match='The named journal Second could not become a view'):
            Journal()
    copy, = zips(home)                                    # one backup, however often it is tried
    assert first.path.is_file() and second.path.is_file()
    monkeypatch.setattr(journals, '_fold_one', folded)
    journal = Journal()
    assert zips(home) == [copy] and not first.path.exists() and not second.path.exists()
    assert journal.annotations()[trade.id]['notes'].endswith('Second review')
    assert '2 fills entered only' in journal.unseen_notes()['journal_views']       # counted once
    assert meta(journal.path, journals.PENDING) is None


def test_a_fold_whose_backup_was_deleted_meanwhile_backs_up_again_and_keeps_its_count(home, monkeypatch):
    env = seed_preview(home)
    first, a = old_named(env, 'First', [])
    trade_on(first, 'paper', 8, name='by-hand')
    second, b = old_named(env, 'Second', [])
    folded = journals._fold_one

    def failing(journal, other, id, name):
        if id == b:
            raise sqlite3.OperationalError('disk I/O error')
        folded(journal, other, id, name)
    monkeypatch.setattr(journals, '_fold_one', failing)
    with pytest.raises(ValueError, match='The named journal Second could not become a view'):
        Journal()
    zips(home)[0].unlink()                                # the trader cleared the backups folder
    monkeypatch.setattr(journals, '_fold_one', folded)
    journal = Journal()
    assert len(zips(home)) == 1 and not first.path.exists() and not second.path.exists()
    assert '2 fills entered only' in journal.unseen_notes()['journal_views']       # First's, added before


def test_a_backup_that_does_not_read_back_is_removed_and_nothing_changes(home, monkeypatch):
    env = seed_preview(home)
    named, id = old_named(env, 'Practice', [])
    before = snapshot(env.path)

    def damaged(file):
        raise ValueError(backup.DAMAGED)
    monkeypatch.setattr(backup, 'read', damaged)
    with pytest.raises(ValueError, match='could not be backed up before they become views, so nothing changed'):
        Journal()
    assert zips(home) == [] and named.path.is_file() and snapshot(env.path) == before


def test_a_journal_that_never_had_a_named_journal_is_marked_at_once(home):
    journal = Journal()
    assert meta(journal.path, journals.FLAG) == '0' and not (home / 'backups').exists()
    assert journal.unseen_notes() == {}


def test_a_named_journal_whose_database_is_gone_becomes_a_view_of_its_accounts(home):
    env = seed_preview(home)
    named, id = old_named(env, 'Practice', [('motivewave', 'paper')])
    gone, other = old_named(env, 'Everything', [])                 # every account, no database left
    for path in (named.path, gone.path):
        for file in path.parent.iterdir():
            file.unlink()
        path.parent.rmdir()
    journal = Journal()
    assert list(journals.registry(journal)['views']) == [id] and not zips(home)
    assert paths.database('live') == journal.path
