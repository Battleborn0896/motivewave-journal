"""Reversible review grouping must preserve originals and never sum excursion maxima."""
import json
import pytest

import common
import runtime
from mwjournal import Execution, Journal, PriceSample
from mwjournal import merging, journals
from mwjournal.model import instant
from test_app import open_app, widget, press
from test_chart import seed_preview
from test_positions import native, fills


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    monkeypatch.setenv('MWJ_HOME', str(tmp_path))
    yield tmp_path
    runtime.stop_all()


def second(journal, *, symbol='ESZ6', side='buy', account='paper'):
    journal.ingest([(Execution('motivewave', account, symbol, side, 1, 5000, '2026-10-07T14:05:00Z', execution_id='second-in'), {}),
                    (Execution('motivewave', account, symbol, 'sell' if side == 'buy' else 'buy', 1, 5001,
                               '2026-10-07T14:06:00Z', execution_id='second-out'), {})])


def test_merged_pnl_qty_tags_and_split_preserve_raw_fills_reviews_and_risk(home):
    journal = seed_preview(home)
    second(journal)
    raw = journal.executions()
    original_reviews = journal.annotations()
    original_trades = journal.trades()
    trade = merging.create(journal, [t.id for t in original_trades])
    view = merging.ReviewJournal(journal)
    assert len(view.trades()) == 1 and trade.net == sum(t.net for t in original_trades)
    assert trade.max_qty == 2 and trade.entry_qty == 3
    rows = common.rows_for(view)
    assert rows[0]['r'] is None  # one of the two trades has no stop, so their risk together is unknown
    assert 'PAPER FIXTURE' in rows[0]['tags']
    view.update_annotation(trade.id, {'notes': 'Merged-only note', 'initial_risk': 100})
    merging.split(journal, trade.id)
    assert len(merging.ReviewJournal(journal).trades()) == 2
    assert journal.executions() == raw
    assert {id: journal.annotations()[id] for id in original_reviews} == original_reviews
    restored = merging.create(journal, [t.id for t in original_trades])
    assert restored.id == trade.id and journal.annotations()[trade.id]['notes'] == 'Merged-only note'


def test_compound_excursions_use_recorded_combined_equity_not_sum_of_leg_extrema(home):
    journal = seed_preview(home)
    second(journal)
    samples = [PriceSample('ESZ6', ts, 'fixture-ticks', price, price) for ts, price in [
        ('2026-10-07T14:00:24Z', 4999), ('2026-10-07T14:01:00Z', 5004),
        ('2026-10-07T14:02:00Z', 5005), ('2026-10-07T14:05:10Z', 4998), ('2026-10-07T14:05:30Z', 5002)]]
    journal.ingest_prices(samples)
    compound = merging.create(journal, [t.id for t in journal.trades()])
    view = merging.ReviewJournal(journal)
    metric = common.best_display_metric(view, compound)
    assert metric['precision'] == 'observed-ticks'
    assert metric['mae'] == 100 and metric['mfe'] == 400
    assert compound.net == 292
    journal.set_excursion_unit('points')
    # The 5-point price peak happens after a partial exit; currency peaked earlier at 2 × 4 × $50.
    assert common.display_excursion_rows(view, common.rows_for(view))[0]['display_mfe'] == 5


@pytest.mark.parametrize('kwargs', [{'symbol': 'ESM6'}, {'side': 'sell'}, {'account': 'other'}])
def test_merge_rejects_contract_direction_and_account_mixing(home, kwargs):
    journal = seed_preview(home)
    second(journal, **kwargs)
    with pytest.raises(ValueError):
        merging.create(journal, [t.id for t in journal.trades()])
    assert not merging.definitions(journal)


def test_overlapping_native_positions_keep_currency_but_do_not_invent_shared_point_basis(home):
    journal = Journal()
    events = [native('1', 'buy', 1, 100, '2026-10-07T14:00:00Z', 'p', True),
              native('2', 'buy', 1, 110, '2026-10-07T14:01:00Z', 'q', True),
              native('3', 'sell', 1, 105, '2026-10-07T14:02:00Z', 'p', False),
              native('4', 'sell', 1, 115, '2026-10-07T14:03:00Z', 'q', False)]
    journal.ingest([(fill, event) for fill, event in zip(fills(events), events)])
    journal.ingest_prices([PriceSample('NQZ6', '2026-10-07T14:02:10Z', 'fixture-ticks', 120, 120)])
    raw = journal.trades()
    merged = merging.create(journal, [t.id for t in raw])
    view = merging.ReviewJournal(journal)
    metric = common.best_display_metric(view, merged)
    assert merged.net == sum(t.net for t in raw) == 200
    assert metric['mfe'] == 300 and metric['mfe_points'] is None
    journal.set_excursion_unit('points')
    assert common.display_excursion_rows(view, common.rows_for(view))[0]['display_mfe'] is None


def test_unavailable_or_ambiguous_legs_leave_raw_reviews_visible(home):
    journal = seed_preview(home)
    second(journal)
    merged = merging.create(journal, [t.id for t in journal.trades()])
    with journal.connect() as db:
        definition = merging.definitions(journal)
        definition[merged.id]['leg_ids'].append('missing-leg')
        db.execute('UPDATE journal_meta SET value=? WHERE key=?', (json.dumps(definition), merging.KEY))
    view = merging.ReviewJournal(journal)
    assert len(view.trades()) == 2 and view.merge_warnings


def test_merge_ui_compound_tagging_checklists_and_the_originals_stay_stored(home):
    journal = seed_preview(home)
    second(journal)
    app = open_app()
    widget(app.multiselect, 'Original trades to merge').set_value([t.id for t in journal.trades()])
    press(app, 'Merge Selected')
    assert widget(app.metric, 'Net P&L').value == '+$292.00'
    widget(app.toggle, 'Show starter examples').set_value(True).run()
    widget(app.pills, 'Mindset').set_value(['Patient']).run()
    view = merging.ReviewJournal(journal)
    merged = view.trades()[0]
    assert 'Patient' in journal.annotations()[merged.id]['tags']
    from views.checklists import starter
    definition = journal.save_checklist('Plan', starter())
    assert view.start_checklist(definition, merged.id)
    assert len(view.trades()) == 1 and len(journal.trades()) == 2      # the merge is a view: both originals stay
    press(app, 'Split Into Original Trades')
    assert len(merging.ReviewJournal(journal).trades()) == 2


def test_named_journal_scoped_draft_reconciles_against_new_mirrored_fills(home):
    capture = seed_preview(home)
    id = journals.create(capture, 'Practice', [('motivewave', 'paper')])
    target = journals.open_named(capture, id)
    draft = target.save_draft('Next trade', provider='motivewave', account='paper', symbol='ESZ6',
                               start='2026-10-07T14:05:00Z', end='2026-10-07T14:07:00Z', tags=['Patient'])
    second(capture)
    target = journals.open_named(capture, id)
    assert target.workspace()['drafts'][draft]['state'] == 'attached'
