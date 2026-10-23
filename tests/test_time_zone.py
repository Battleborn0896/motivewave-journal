"""One time zone for every trading day: the journal's Time Zone (Settings), a day running midnight to midnight there. A
trade late in the evening is on that evening's date everywhere a day is shown: the trade list's day header, its Daily
Report Card, its day tags and the calendar. Disposable journals only, in a temporary data home."""
import datetime as dt

import pytest

import common
import runtime
from mwjournal import Execution, Journal, merging, tagging
from test_app import open_app, press, show, widget
from test_workspace import calendar_cells

LA = 'America/Los_Angeles'


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    monkeypatch.setenv('MWJ_HOME', str(tmp_path))
    for dataset in ('live', 'replay'):
        Journal(dataset=dataset).save_preferences(display_timezone=LA)
    yield tmp_path
    runtime.stop_all()


def evening(journal, n=0, minute=0):
    """An ES trade at 10 PM on Wednesday Oct 7 in Los Angeles, 10 points × $50: already Thursday in New York and UTC."""
    journal.ingest([(Execution('motivewave', 'paper', 'ESZ6', 'buy', 1, 5000, f'2026-10-08T05:{minute:02d}:00Z',
                               execution_id=f'in-{n}'), {}),
                    (Execution('motivewave', 'paper', 'ESZ6', 'sell', 1, 5010, f'2026-10-08T05:{minute + 3:02d}:00Z',
                               execution_id=f'out-{n}'), {})])
    return journal.trades()[-1]


def test_a_trade_at_10_pm_in_los_angeles_is_on_that_date_everywhere(home):
    journal = Journal()
    trade = evening(journal)
    assert journal.time_zone() == LA and tagging.day_key(trade.opened, LA) == '2026-10-07'
    app = open_app()
    # The trade list's day header: Wednesday, the only day.
    assert [b.key for b in app.button if b.key and b.key.startswith('open-day-')] == ['open-day-2026-10-07-live']
    header = app.button(key='open-day-2026-10-07-live')
    assert header.label == 'Daily Report Card for Wed, Oct 7'
    # Its Daily Report Card: that day's result, and no time zone of its own to choose.
    header.click().run()
    assert widget(app.date_input, 'Trading day').value == dt.date(2026, 10, 7)
    assert widget(app.metric, 'Trades').value == '1' and widget(app.metric, 'Net P&L').value == '+$500.00'
    assert not any('zone' in box.label.lower() for box in app.selectbox)
    # Its day tags: chosen on the card, worn by the trade, stored under that date alone.
    widget(app.pills, 'Day').set_value(['Had a Plan', 'Respected My Stop']).run()
    widget(app.pills, 'Rating').set_value(4).run()
    assert list(journal.get('sessions')) == ['2026-10-07'] and journal.get('sessions')['2026-10-07']['rating'] == 4
    assert common.rows_for(journal)[0]['tags'] == ['Had a Plan', 'Respected My Stop']
    assert common.rows_for(journal)[0]['entry_day'] == '2026-10-07'
    # On the trade they are not offered again: the Day category is chosen on its card.
    press(app, '← Back to Trades')
    assert not any(p.label == 'Day' for p in app.pills) and any(p.label == 'Rating' for p in app.pills)
    # The calendar: the result is on the 7th, and the 7th opens the same card.
    show(app, 'Dashboard')
    cells = calendar_cells(app)
    assert cells['7'] == (['mwj-cal', 'up', 'l3'], '+$500', '1 trade') and cells['8'] == (['mwj-cal'], None, None)
    next(b for b in app.button if b.label.startswith('7  \n')).click().run()
    assert not app.exception and widget(app.date_input, 'Trading day').value == dt.date(2026, 10, 7)
    assert widget(app.pills, 'Day').value == ['Had a Plan', 'Respected My Stop']
    assert widget(app.pills, 'Rating').value == 4


def test_a_draft_a_tag_click_and_a_merge_read_the_evenings_day_in_the_time_zone(home):
    journal = Journal()
    library = journal.tag_library()
    patient = next(iter(tagging.ids(library, ['Patient'])))
    journal.edit_tag(patient, levels=['trade', 'day'])
    journal.edit_tag_group('Mindset', exclusive=True)
    journal.set_day_tag_group('2026-10-07', 'Mindset', ['Patient'])
    draft = journal.save_draft('Plan', provider='motivewave', account='paper', symbol='ESZ6',
                               start='2026-10-08T05:00:00Z', end='2026-10-08T05:01:00Z', tags=['FOMO'])
    first = evening(journal)
    journal.reconcile_drafts()
    # The draft's FOMO wins over its day's Patient in that one-choice category: the day it read is Oct 7.
    assert journal.workspace()['drafts'][draft]['state'] == 'attached'
    assert journal.annotations()[first.id]['day_tag_exclusions'] == [patient]
    assert common.rows_for(journal)[0]['tags'] == ['FOMO']
    # A second trade that evening wears the day's Patient until Mindset is chosen on it; a merge of the two keeps what
    # each wears.
    second = evening(journal, 1, minute=10)
    assert common.rows_for(journal)[1]['tags'] == ['Patient']
    journal.set_trade_tag_group(second, 'Mindset', ['Calm'])
    assert journal.annotations()[second.id]['day_tag_exclusions'] == [patient]
    merged = merging.create(journal, [first.id, second.id])
    assert set(journal.annotations()[merged.id]['tags']) == {'FOMO', 'Calm'}
