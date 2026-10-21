"""What a first-time user sees on Trades: starter tags to click, a day-by-day list, one way to pick a
trade, and money and times written for people. Disposable journals only, in a temporary data home."""
import json

import pytest

import common
import runtime
from mwjournal import Execution, Journal, merging
from test_app import chosen, listed, open_app, pick, press, said, show, widget
from test_chart import round_trip, seed_preview

NY = 'America/New_York'


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    monkeypatch.setenv('MWJ_HOME', str(tmp_path))
    for dataset in ('live', 'replay'):
        Journal(dataset=dataset).save_preferences(display_timezone=NY)     # times below are New York times
    yield tmp_path
    runtime.stop_all()


def fill(id, side, qty, price, ts, symbol='ESZ6', **more):
    return Execution('motivewave', 'paper', symbol, side, qty, price, ts, execution_id=id, **more), {}


def rows_html(app):
    return [m.value for m in app.main.markdown if "class='mwj-trade" in m.value]


def days_html(app):
    return [m.value for m in app.main.markdown if "class='mwj-day'" in m.value]


def everything_said(app):
    return ' '.join(e.value for kind in ('markdown', 'caption', 'info') for e in getattr(app.main, kind))


# ---------------------------------------------------------------- starter tags

def test_a_first_trade_opens_on_clickable_starter_tags_with_nothing_to_set_up():
    journal = Journal()
    trade = round_trip(journal)
    app = open_app()
    assert widget(app.pills, 'Setup').options == ['Breakout', 'Pullback', 'Reversal', 'Range']
    assert widget(app.pills, 'Execution').options == ['Followed Plan', 'Chased Entry', 'Exited Early',
                                                      'Let Winner Run', 'Moved Stop']
    assert widget(app.pills, 'Mindset').options == ['Calm', 'Patient', 'FOMO', 'Hesitant', 'Tilted']
    assert not said(app.info, 'Start by creating a category')
    assert not any(t.label == 'Show starter examples' for t in app.toggle)    # nothing hidden to reveal
    labels = [p.proto.popover.label for p in app.main.get('popover')]
    assert '+ Category' in labels and '+ Tag' in labels                       # still there, but secondary
    widget(app.pills, 'Mindset').set_value(['Calm']).run()                      # one click applies and saves
    assert journal.annotations()[trade.id]['tags'] == ['Calm']
    widget(app.pills, 'Mindset').set_value([]).run()                            # a second click removes it
    assert journal.annotations()[trade.id]['tags'] == []


def test_a_library_that_only_has_the_old_hidden_suggestions_still_opens_on_tags(home):
    from mwjournal import tagging
    from mwjournal.tags import LEGACY_CATALOG
    journal = Journal()
    round_trip(journal)
    old = {'version': 1, 'groups': {}, 'playbooks': {}, 'session_timezone': NY, 'tags': {}}  # as 0.4.0 stored it
    for name, group in LEGACY_CATALOG.items():
        old['tags'][tagging.identity(name)] = {'id': tagging.identity(name), 'name': name, 'aliases': [name],
                                               'group': group, 'retired': False, 'levels': ['trade'],
                                               'color': '#65CA98', 'order': len(old['tags'])}
        old['groups'].setdefault(group, {'exclusive': False, 'order': len(old['groups'])})
    with journal.connect() as db:
        tagging.save(db, old)
        stored = db.execute("SELECT value FROM journal_meta WHERE key='tag_library'").fetchone()[0]
    app = open_app()
    assert widget(app.pills, 'Setup').options == ['Breakout', 'Pullback', 'Reversal', 'Range']
    assert widget(app.pills, 'Execution').options[0] == 'Followed plan'            # its own tags, untouched
    assert not any(t.label == 'Show starter examples' for t in app.toggle)
    with journal.connect() as db:
        assert db.execute("SELECT value FROM journal_meta WHERE key='tag_library'").fetchone()[0] == stored


def test_hint_captions_that_repeat_the_screen_are_gone_and_tag_help_moved_to_tooltips(home):
    seed_preview(home)
    app = open_app()
    text = everything_said(app)
    for gone in ('captured automatically', 'click tags to save', 'Click tags to autosave', 'Saved locally',
                 'day tags use', 'Create your own categories', 'Blue = entry', 'Review only; no order entry',
                 'Setup and notes save automatically', '(UTC)', ' UTC'):
        assert gone not in text, gone
    assert said(app.main.caption, common.CREDIT)


def test_an_empty_other_tags_category_is_not_a_card_but_an_empty_category_someone_made_is(home):
    journal = Journal()
    trade = round_trip(journal)
    journal.add_tag_group('Other tags')                  # what older versions left behind after a note was saved
    journal.add_tag_group('Orderflow')
    app = open_app()
    titles = [m.value for m in app.main.markdown if 'mwj-card-title' in m.value]
    assert any('Orderflow' in t for t in titles) and not any('Other tags' in t for t in titles)
    assert 'Other tags' in journal.tag_library()['groups']            # hidden on the panel, never deleted
    journal.update_annotation(trade.id, {}, add_tags=['Absorption'])  # once it holds a tag it is a card again
    app.run()
    assert widget(app.pills, 'Other tags').options == ['Absorption']


# ---------------------------------------------------------------- the trade list

def test_trades_are_grouped_by_day_with_the_days_result_and_a_scannable_row_each():
    journal = Journal()
    journal.ingest([fill('a1', 'buy', 2, 5000, '2026-10-07T14:00:23Z'), fill('a2', 'sell', 2, 5002, '2026-10-07T14:03:11Z'),
                    fill('b1', 'sell', 1, 5010, '2026-10-07T15:30:00Z'), fill('b2', 'buy', 1, 5013, '2026-10-07T15:31:05Z'),
                    fill('c1', 'buy', 1, 20000, '2026-10-08T13:45:00Z', symbol='NQZ6'),
                    fill('c2', 'sell', 1, 20010, '2026-10-08T15:50:00Z', symbol='NQZ6')])
    app = open_app()
    trades = sorted(journal.trades(), key=lambda t: t.opened, reverse=True)
    assert listed(app) == [t.id for t in trades]                                 # newest first
    days = days_html(app)
    assert 'Thu, Oct 8' in days[0] and '+&#36;200.00' in days[0] and '1 trade<' in days[0]
    assert 'Wed, Oct 7' in days[1] and '+&#36;50.00' in days[1] and '2 trades' in days[1]
    first, second, third = rows_html(app)
    assert '9:45 AM' in first and 'NQZ6' in first and "class='long'>Long</b> 1" in first
    assert '+&#36;200.00' in first and 'mwj-win' in first and '2h 05m' in first
    assert '11:30 AM' in second and "class='short'>Short</b> 1" in second and '-&#36;150.00' in second
    assert 'mwj-loss' in second and '1m 05s' in second
    assert '10:00 AM' in third and "Long</b> 2" in third and '2m 48s' in third
    assert ' sel' in first and ' sel' not in second                              # the open trade is highlighted
    assert not app.main.dataframe or all('Opened (UTC)' not in d.value.columns for d in app.main.dataframe)


def test_unknown_results_keep_a_badge_and_say_unknown():
    journal = Journal()
    journal.ingest([fill('o1', 'sell', 1, 5002, '2026-10-07T15:00:00Z', native_position_id='p9', native_entry=False),
                    fill('x1', 'buy', 1, 100, '2026-10-07T16:00:00Z', symbol='XYZ'),
                    fill('x2', 'sell', 1, 103, '2026-10-07T16:05:00Z', symbol='XYZ'),
                    fill('n1', 'buy', 1, 5000, '2026-10-07T17:00:00Z')])
    app = open_app()
    by_symbol = {}
    for row in rows_html(app):
        by_symbol.setdefault('XYZ' if 'XYZ' in row else 'open' if 'Open<' in row else 'incomplete', row)
    assert 'Incomplete' in by_symbol['incomplete'] and 'Unknown' in by_symbol['incomplete']
    assert 'Unpriced' in by_symbol['XYZ'] and 'Unknown' in by_symbol['XYZ']
    assert "mwj-badge open'>Open<" in by_symbol['open']
    assert 'mwj-win' not in ''.join(by_symbol.values()) and 'mwj-loss' not in ''.join(by_symbol.values())
    incomplete = next(t for t in journal.trades() if t.status == 'incomplete')
    pick(app, incomplete.id)
    result = next(m for m in app.metric if 'P&L' in m.label)
    assert result.value == 'Unknown' and 'opened this position' in result.help


def test_one_way_to_pick_a_trade_a_row_click_or_prev_and_next(home):
    journal = seed_preview(home)
    journal.ingest([fill('n1', 'buy', 1, 20000, '2026-10-08T14:10:00Z', symbol='NQZ6'),
                    fill('n2', 'sell', 1, 20010, '2026-10-08T14:12:00Z', symbol='NQZ6')])
    newer, older = sorted(journal.trades(), key=lambda t: t.opened, reverse=True)
    app = open_app()
    assert not any(s.label == 'Review a trade' for s in app.selectbox)
    assert not any(b.label.startswith('Refresh') for b in app.button)
    assert chosen(app) == newer.id
    pick(app, older.id)
    assert chosen(app) == older.id and ' sel' in rows_html(app)[1] and ' sel' not in rows_html(app)[0]
    assert said(app.main.markdown, 'Oct 7, 10:00:23 AM')                         # the header follows the click
    assert said(app.main.caption, 'Oct 7 · 10:00 AM to 10:03 AM · 2m 48s')
    assert widget(app.button, '← Prev').disabled is False and widget(app.button, 'Next →').disabled
    press(app, '← Prev')
    assert chosen(app) == newer.id and said(app.main.caption, '1 of 2')


def test_options_hold_the_chart_settings_and_merging_next_to_filters(home):
    journal = seed_preview(home)
    journal.ingest([fill('n1', 'buy', 1, 5010, '2026-10-07T15:10:00Z'), fill('n2', 'sell', 1, 5012, '2026-10-07T15:12:00Z')])
    app = open_app()
    popovers = [p.proto.popover.label for p in app.main.get('popover')]
    assert popovers[3:5] == ['Filters', 'Options']                 # after the session tools' three menus
    assert not any(label in popovers for label in ('More', 'Chart settings', 'Trade filters'))
    assert widget(app.selectbox, 'Chart price source').value == 'motivewave-bars:1m'
    assert widget(app.selectbox, 'Chart price source').format_func('motivewave-bars:1m') == 'Platform candles'
    assert widget(app.selectbox, 'Context each side').value == 15
    assert not any(s.label in ('Chart timezone', 'Review a trade') for s in app.selectbox)
    widget(app.multiselect, 'Original trades to merge').set_value([t.id for t in journal.trades()])
    press(app, 'Merge Selected')
    assert len(merging.ReviewJournal(journal).trades()) == 1 and len(listed(app)) == 1
    assert said(app.main.markdown, 'Merged')
    press(app, 'Split Into Original Trades')
    assert len(listed(app)) == 2


def test_the_chart_draws_in_the_display_zone(home):
    seed_preview(home)
    app = open_app()
    chart = next(json.loads(c.proto.json_args)['spec'] for c in app.main.get('component_instance')
                 if 'journal-candles' in c.proto.id)
    assert chart['timezone'] == NY and chart['title'].endswith('EDT')
    Journal().save_preferences(display_timezone='Europe/Berlin')
    app.run()
    chart = next(json.loads(c.proto.json_args)['spec'] for c in app.main.get('component_instance')
                 if 'journal-candles' in c.proto.id)
    assert chart['timezone'] == 'Europe/Berlin'
    assert said(app.main.caption, 'Oct 7 · 4:00 PM to 4:03 PM')


def test_new_captures_and_updates_leave_the_selection_tab_and_notes_alone(home):
    journal = seed_preview(home)
    trade = journal.trades()[0]
    app = open_app()
    before = common.changes(journal)
    widget(app.text_area, 'Review notes').set_value('Half typed')
    journal.ingest([fill('n1', 'buy', 1, 5010, '2026-10-08T14:10:00Z')])          # the watcher adds a trade
    assert common.changes(journal) != before
    app.run()                                                                   # what the page's watcher does
    assert not app.exception and len(listed(app)) == 2
    assert chosen(app) == trade.id and widget(app.text_area, 'Review notes').value == 'Half typed'
    relabelled = common.changes(journal)
    journal.ingest([(Execution('motivewave', 'paper', 'ESZ6', 'buy', 1, 5010, '2026-10-08T14:10:00Z',
                               execution_id='n1', quality='native'), {})])        # the same fill, described again
    assert common.changes(journal) != relabelled


def test_money_and_time_are_human_on_the_trades_page_and_in_exports(home):
    from views import export
    journal = seed_preview(home)
    app = open_app()
    assert widget(app.metric, 'Net P&L').value == '+$242.00'
    journal.set_excursion_policy('best-available')
    app.run()
    assert widget(app.metric, 'MAE').value.startswith('$') and widget(app.metric, 'MFE').value.startswith('$')
    show(app, 'Edge')
    assert widget(app.metric, 'Largest Drawdown').value == '$0.00'
    assert not any(s.label == 'Analysis timezone' for s in app.selectbox)
    table = export.files(journal)[1][2].splitlines()
    header, row = table[0].split(','), table[1].split(',')
    assert header[-3:] == ['entry_time', 'exit_time', 'time_zone']
    assert row[header.index('opened')].startswith('2026-10-07T14:00:23')                  # raw stays UTC
    assert row[-3:] == ['2026-10-07 10:00:23', '2026-10-07 10:03:11', NY]


def test_date_filters_compare_days_in_the_display_zone(home):
    from mwjournal.workspace import filter_sample
    journal = Journal()
    journal.ingest([fill('e1', 'buy', 1, 5000, '2026-10-08T02:00:00Z'), fill('e2', 'sell', 1, 5001, '2026-10-08T02:05:00Z')])
    rows = common.rows_for(journal)
    assert rows[0]['entry_day'] == '2026-10-07'                                 # 10 PM in New York
    assert filter_sample(rows, {'start': '2026-10-07', 'end': '2026-10-07'}) == rows
    assert not filter_sample([{k: v for k, v in rows[0].items() if k != 'entry_day'}],
                             {'start': '2026-10-07', 'end': '2026-10-07'})      # rows without one: UTC, as before


def test_starter_tags_can_be_deleted_in_settings_and_used_ones_are_kept(home):
    from mwjournal import tagging
    journal = Journal()
    trade = round_trip(journal)
    journal.set_trade_tag_group(trade, 'Setup', ['Breakout'])
    app = open_app('Tags')
    library = journal.tag_library()
    ids = {t['name']: id for id, t in library['tags'].items()}
    widget(app.selectbox, 'Edit tag').set_value(ids['Breakout']).run()
    press(app, 'Delete Tag')
    assert said(app.error, 'Retire it instead') and 'Breakout' in journal.tag_catalog()
    for name in ('Calm', 'Patient', 'FOMO', 'Hesitant', 'Tilted'):
        widget(app.selectbox, 'Edit tag').set_value(ids[name]).run()
        press(app, 'Delete Tag')
    widget(app.selectbox, 'Edit category').set_value('Mindset').run()
    press(app, 'Delete Category')
    assert 'Mindset' not in journal.tag_library()['groups'] and not app.exception
    for id, tag in list(journal.tag_library()['tags'].items()):
        if tag['name'] != 'Breakout':
            journal.delete_tag(id)
    for group in [g for g in journal.tag_library()['groups'] if g != 'Setup']:
        journal.delete_tag_group(group)
    journal.edit_tag(ids['Breakout'], retired=True)
    app.run()
    assert not app.exception
    show(app, 'Trades')
    assert not app.exception and journal.annotations()[trade.id]['tags'] == ['Breakout']
    assert set(tagging.ordered_groups(journal.tag_library())) == set()          # nothing comes back


def test_the_display_time_zone_is_chosen_once_in_settings_for_live_and_replay(home):
    app = open_app('General')
    choice = widget(app.selectbox, 'Time Zone')
    assert choice.value == NY and not any(s.label == 'Chart timezone' for s in app.selectbox)
    choice.set_value('Asia/Tokyo').run()                    # saved the moment it is chosen
    assert not app.exception
    assert common.display_zone(Journal()) == common.display_zone(Journal(dataset='replay')) == 'Asia/Tokyo'
    show(app, 'Trades')
    assert not app.exception
