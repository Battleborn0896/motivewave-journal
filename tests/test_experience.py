"""Requested tick-only and click-first workflows with no platform/trading/recording side effects."""
import datetime as dt
import json
import sqlite3

import pytest

import common
import runtime
import theme
from mwjournal import Journal, PriceSample
from mwjournal import tagging
from mwjournal.metrics import measurements
from mwjournal.model import instant
from mwjournal.post_exit import best_post_exit
from test_app import display_setting, helped, listed, open_app, open_tab, press, said, show, widget
from test_chart import seed_preview


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    monkeypatch.setenv('MWJ_HOME', str(tmp_path))
    yield tmp_path
    runtime.stop_all()


def recorded(journal):
    trade = journal.trades()[0]
    samples = [PriceSample(trade.symbol, (instant(trade.opened) + dt.timedelta(seconds=s)).isoformat(),
                            'fixture-ticks', p, p) for s, p in [(0, 5000), (15, 4999), (50, 5004), (100, 5002)]]
    journal.ingest_prices(samples)
    for metric in measurements(trade, samples, 'fixture-ticks'):
        journal.record_metric(trade, metric)
    return trade


@pytest.mark.parametrize('unit', ['currency', 'points', 'ticks', 'r'])
def test_only_recorded_ticks_measure_in_every_unit_and_raw_estimates_remain_in_backup(home, unit):
    journal = seed_preview(home)
    trade = journal.trades()[0]
    original = journal.best_metric(trade)
    assert original['precision'] == 'estimated-bars'
    journal.set_excursion_unit(unit)
    assert 'excursion_policy' not in journal.display_settings()           # there is no accuracy to choose
    rows = common.rows_for(journal)
    assert rows[0]['mfe'] is None and rows[0]['mae'] is None
    assert common.display_excursion_rows(journal, rows)[0]['display_mfe'] is None
    assert common.best_display_metric(journal, trade) == {}
    from views import export
    payload = json.loads(export.files(journal)[0][2]())
    assert payload['trades'][0]['mfe'] is None
    assert payload['measurements'][trade.id][0]['precision'] == 'estimated-bars'
    backup = home / 'snapshot.sqlite3'
    backup.write_bytes(journal.backup())
    snapshot = sqlite3.connect(backup)  # a WAL-mode snapshot needs a real path, not deserialize(:memory:)
    assert json.loads(snapshot.execute('SELECT payload FROM metrics').fetchone()[0])['mfe'] == original['mfe']
    snapshot.close()
    assert journal.best_metric(trade) == original


def test_ticks_measure_never_platform_reports_and_a_reported_capture_gap_leaves_none(home):
    journal = seed_preview(home)
    trade = recorded(journal)
    journal.record_metric(trade, {'source': 'platform', 'precision': 'platform-reported', 'mfe': 99999, 'mae': 999,
                                  'notes': [], 'path': []})
    assert common.best_display_metric(journal, trade)['source'] == 'fixture-ticks'
    journal.ingest_gaps([{'type': 'gap', 'provider': 'fixture-ticks', 'ts': trade.opened, 'dropped': 1}])
    assert common.best_display_metric(journal, trade) == {}


def test_there_is_no_accuracy_to_choose_and_candles_never_measure_even_where_0_4_7_allowed_them(home):
    journal = seed_preview(home)
    journal._update_display_settings({'excursion_policy': 'best-available'})   # 'Allow labelled estimates', saved by 0.4.7
    app = open_app('General')
    assert not any(s.label == 'MAE / MFE Accuracy' for s in app.selectbox)
    show(app, 'Trades')
    assert widget(app.metric, 'MAE').value == widget(app.metric, 'MFE').value == '—'
    assert said(app.caption, 'Only 1-minute candles were recorded, so MAE / MFE are not measured.')
    display_setting(app, 'MAE / MFE Units', 'ticks', back='Edge')
    assert widget(app.metric, 'Net P&L').value == '+$242.00'
    open_tab(app, 'MAE / MFE')
    assert said(app.caption, '0 of 1 closed trades measured')
    assert widget(app.metric, 'Net P&L').value == '+$242.00'
    assert journal.best_metric(journal.trades()[0])['precision'] == 'estimated-bars'


def test_ticks_survive_unit_change_and_selected_trade_remains_visible_with_list(home):
    journal = seed_preview(home)
    recorded(journal)
    app = open_app()
    assert listed(app) == [journal.trades()[0].id]
    assert widget(app.metric, 'MFE').value != '—'
    display_setting(app, 'MAE / MFE Units', 'points')
    assert widget(app.metric, 'MFE').value == '4.00 pts' and listed(app) == [journal.trades()[0].id]
    display_setting(app, 'MAE / MFE Units', 'ticks')
    assert widget(app.metric, 'MFE').value == '16.00 ticks'
    show(app, 'General')
    assert not any(s.label == 'MAE / MFE Accuracy' for s in app.selectbox)


def test_tag_manager_rename_color_retirement_and_exclusive_group_work_on_existing_trade(home):
    journal = seed_preview(home)
    trade = journal.trades()[0]
    journal.set_trade_tag_group(trade, 'Setup', ['Breakout'])
    id = next(iter(tagging.ids(journal.tag_library(), ['Breakout'])))
    app = open_app('Tags')
    widget(app.selectbox, 'Edit tag').set_value(id).run()
    widget(app.text_input, 'Tag name').set_value('Retest')
    widget(app.color_picker, 'Selected tag color').set_value('#123456')
    widget(app.checkbox, 'Available on days').set_value(True)
    press(app, 'Save Tag')
    assert journal.tag_library()['tags'][id]['color'] == '#123456'
    assert 'Retest' in journal.annotations()[trade.id]['tags']
    widget(app.selectbox, 'Edit category').set_value('Setup').run()
    widget(app.checkbox, 'One choice at a time').set_value(True)
    press(app, 'Save Category')
    show(app, 'Trades')
    widget(app.pills, 'Setup').set_value('Pullback').run()
    assert 'Retest' not in journal.annotations()[trade.id]['tags']
    assert 'Pullback' in journal.annotations()[trade.id]['tags']
    assert '#123456' in theme.tag_colors_css(tagging.ordered_groups(journal.tag_library()))
    show(app, 'Tags')
    widget(app.selectbox, 'Edit tag').set_value(id).run()
    widget(app.checkbox, 'Retired').set_value(True)
    press(app, 'Save Tag')
    assert 'Retest' not in journal.tag_catalog()
    assert not app.exception


def test_day_tags_and_playbook_can_be_clicked_without_retyping_and_daily_notes_do_not_erase(home):
    journal = seed_preview(home)
    trade = journal.trades()[0]
    id = next(iter(tagging.ids(journal.tag_library(), ['Patient'])))
    journal.edit_tag(id, levels=['trade', 'day'])
    journal.save_playbook('Calm pullback', {'Setup': ['Pullback'], 'Mindset': ['Patient']})
    app = open_app('Daily review')
    widget(app.date_input, 'Trading day').set_value(dt.date(2026, 10, 7)).run()
    widget(app.pills, 'Mindset').set_value(['Patient']).run()
    widget(app.text_area, 'What I learned').set_value('Calm day.').run()        # the day's note saves by itself
    assert 'Patient' in common.rows_for(journal)[0]['tags']
    show(app, 'Trades')
    press(app, 'Apply Combination')
    assert common.rows_for(journal)[0]['playbooks'] == ['Calm pullback']
    assert said(app.success, 'Matches: Calm pullback')
    open_tab(show(app, 'Edge'), 'Compare')
    widget(app.selectbox, 'Compare by').set_value('Playbooks').run()
    table = next(d.value for d in app.dataframe if 'Playbook' in d.value.columns)
    assert table.iloc[0]['Playbook'] == 'Calm pullback'


def test_dashboard_preferences_persist_per_environment_and_empty_selection_is_rejected(home):
    journal = seed_preview(home)
    app = open_app('Edge')
    widget(app.multiselect, 'Tabs, in order').set_value(['Compare', 'Overview'])
    press(app, 'Save Tabs')
    assert [t.label for t in app.tabs] == ['Compare', 'Overview', 'Dashboards']        # Dashboards is always last
    assert Journal().display_settings()['dashboard_sections'] == ['Compare', 'Overview']
    assert Journal(dataset='replay').display_settings()['dashboard_sections'] == ['Overview', 'Compare', 'MAE / MFE', 'Runners']
    widget(app.multiselect, 'Tabs, in order').set_value([])
    press(app, 'Save Tabs')
    assert said(app.error, 'at least one')
    assert journal.display_settings()['dashboard_sections'] == ['Compare', 'Overview']


def test_include_any_exclude_filters_and_renamed_aliases_keep_their_identity(home):
    journal = seed_preview(home)
    trade = journal.trades()[0]
    journal.set_trade_tag_group(trade, 'Setup', ['Breakout'])
    app = open_app('Edge')
    widget(app.multiselect, 'Tags').set_value(['Breakout', 'Patient']).run()
    assert said(app.info, 'Statistics need')
    widget(app.radio, 'Tag Match').set_value('Any selected').run()
    assert widget(app.metric, 'Win Rate').value == '100.0%'
    id = next(iter(tagging.ids(journal.tag_library(), ['Breakout'])))
    journal.edit_tag(id, name='Renamed')
    app.run()
    assert widget(app.metric, 'Win Rate').value == '100.0%'
    widget(app.multiselect, 'Exclude Tags').set_value(['Renamed']).run()
    assert said(app.info, 'Statistics need')


def test_the_runner_never_uses_bar_hindsight(home):
    journal = seed_preview(home)
    trade = journal.trades()[0]
    result = best_post_exit(journal, trade, minutes=5)
    assert result['precision'] == 'pending' and result['extra_mfe'] is None


def test_tag_color_text_has_contrast_and_untrusted_css_values_are_rejected():
    assert f'color:{theme.INK} !important' in theme.tag_colors_css({'x': [{'color': '#123456'}]})
    assert f'color:{theme.SIDEBAR} !important' in theme.tag_colors_css({'x': [{'color': '#eeeeee'}]})
    assert theme.tag_colors_css({'<script>': [{'color': 'red;}body{display:none'}]}) == '<style></style>'
