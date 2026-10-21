"""Runner hypotheses on synthetic prices; no platform access or trade recommendations."""
import datetime as dt

import pytest

from mwjournal import Execution, Journal, PriceSample
from mwjournal.model import instant
from mwjournal.post_exit import best_post_exit, post_exit, runner_groups

START = '2026-10-08T14:00:00+00:00'
EXIT = '2026-10-08T14:01:00+00:00'
LATER = instant(EXIT) + dt.timedelta(minutes=30)


def after(seconds):
    return (instant(EXIT) + dt.timedelta(seconds=seconds)).isoformat()


def trade(tmp_path, side='buy', partial=False, replay=False):
    j = Journal(tmp_path / ('replay.sqlite3' if replay else 'live.sqlite3'), dataset='replay' if replay else 'live')
    provider = 'motivewave-native:w:replay:run-a' if replay else 'test'
    opposite = 'sell' if side == 'buy' else 'buy'
    fills = [Execution(provider, 'paper', 'ESZ6', side, 2 if partial else 1, 5000, START, execution_id='a', point_value=50)]
    if partial:
        fills.append(Execution(provider, 'paper', 'ESZ6', opposite, 1, 5001, after(-30), execution_id='partial', point_value=50))
    fills.append(Execution(provider, 'paper', 'ESZ6', opposite, 1, 5002, EXIT, execution_id='exit', point_value=50))
    j.ingest([(f, {'mode': 'replay'} if replay else {}) for f in fills])
    return j, j.trades()[0]


def ticks(source='prices'):
    return [PriceSample('ESZ6', after(t), source, price, price) for t, price in
            [(1, 5002), (60, 5001), (120, 5006), (300, 5003)]]


def test_peak_adverse_and_fixed_horizon_are_distinct_and_r_needs_planned_risk(tmp_path):
    _, t = trade(tmp_path)
    result = post_exit(t, ticks(), 'prices', minutes=5, now=LATER, initial_risk=100)
    assert result['extra_mfe'] == 200 and result['extra_mae'] == 50
    assert result['horizon_extra'] == 50 and result['minutes_to_peak'] == 2
    assert result['extra_mfe_r'] == 2 and result['coverage'] == 'window-observed'
    assert post_exit(t, ticks(), 'prices', minutes=5, now=LATER)['extra_mfe_r'] is None


def test_short_direction_and_final_partial_quantity(tmp_path):
    _, t = trade(tmp_path, 'sell', partial=True)
    samples = [PriceSample('ESZ6', after(s), 'prices', p, p) for s, p in [(1, 5002), (60, 5003), (300, 4998)]]
    result = post_exit(t, samples, 'prices', minutes=5, now=LATER)
    assert result['exit_qty'] == 1 and t.entry_qty == 2
    assert result['extra_mfe'] == 200 and result['extra_mae'] == 50


def test_bar_boundaries_do_not_borrow_pre_exit_or_future_extremes(tmp_path):
    _, t = trade(tmp_path)
    samples = [PriceSample('ESZ6', after(-30), 'bars', 6000, 4000, 60),
               PriceSample('ESZ6', after(0), 'bars', 5004, 5001, 60, open=5002, close=5003),
               PriceSample('ESZ6', after(60), 'bars', 5006, 5002, 60, open=5003, close=5005),
               PriceSample('ESZ6', after(90), 'bars', 9000, 1, 60)]
    result = post_exit(t, samples, 'bars', minutes=2, now=LATER)
    assert result['extra_mfe'] == 200 and result['extra_mae'] == 50 and result['horizon_extra'] == 150
    assert result['observations'] == 2 and result['precision'] == 'estimated-bars'


def test_missing_horizon_and_reported_gaps_do_not_appear_as_comparable_zero(tmp_path):
    _, t = trade(tmp_path)
    missing = post_exit(t, ticks()[:-1], 'prices', minutes=5, now=LATER)
    assert missing['coverage'] == 'partial-window' and missing['horizon_extra'] is None
    gapped = post_exit(t, ticks(), 'prices', minutes=5, now=LATER, gaps=[{'dropped': 1}])
    assert gapped['coverage'] == 'partial-window'
    assert not runner_groups([{'tags': ['A+'], **missing}, {'tags': ['A+'], **gapped}])
    none = post_exit(t, [], 'prices', minutes=5, now=LATER)
    assert none['extra_mfe'] is None and none['precision'] == 'pending'


def test_replay_run_isolation_simulation_label_and_no_future_peeking(tmp_path):
    journal, t = trade(tmp_path, replay=True)
    correct = 'motivewave-replay:run-a:simulated'
    samples = ticks(correct)
    journal.ingest_prices(samples)
    now = instant(after(60))
    result = post_exit(t, samples, correct, minutes=5, now=now)
    assert result['precision'] == 'replay-simulated' and result['extra_mfe'] == 0
    assert result['coverage'] == 'partial-window' and result['horizon_extra'] is None
    wrong = post_exit(t, ticks('motivewave-replay:run-b:ticks'), 'motivewave-replay:run-b:ticks', now=LATER)
    assert wrong['extra_mfe'] is None
    # Only recorded ticks measure a runner: simulated replay prints are labelled, never used.
    assert best_post_exit(journal, t, minutes=5)['precision'] == 'pending'


def test_groups_separate_quality_deduplicate_tags_and_require_same_currency(tmp_path):
    _, t = trade(tmp_path)
    result = post_exit(t, ticks(), 'prices', minutes=5, now=LATER, initial_risk=100)
    base = {'tags': ['A+', 'A+'], 'currency': 'USD', **result, 'stop_price': 4998}
    groups = runner_groups([base, {**base, 'precision': 'estimated-bars'}])
    assert len(groups) == 2 and all(g['trades'] == 1 for g in groups)
    assert all(g['additional_1R_reached_pct'] == 100 for g in groups)
    with pytest.raises(ValueError, match='one currency'):
        runner_groups([base, {**base, 'currency': 'EUR'}])
    with pytest.raises(ValueError):
        post_exit(t, ticks(), 'prices', minutes=0)


def test_groups_compare_tag_categories_and_include_uncategorized(tmp_path):
    _, t = trade(tmp_path)
    result = post_exit(t, ticks(), 'prices', minutes=5, now=LATER, initial_risk=100)
    base = {'currency': 'USD', **result, 'stop_price': 4998}
    groups = runner_groups([
        {**base, 'tag_categories': ['Orderflow', 'Psychology', 'Orderflow']},
        {**base, 'tag_categories': []},
    ], field='tag_categories')
    assert [(g['group'], g['trades']) for g in groups] == [
        ('Orderflow', 1), ('Psychology', 1), ('Uncategorized', 1)]


def test_stop_touch_ends_tracking_before_later_price_peak(tmp_path):
    _, t = trade(tmp_path)
    samples = [PriceSample('ESZ6', after(s), 'ticks', p, p) for s, p in
               [(1, 5002), (60, 5006), (120, 4998), (180, 5100), (300, 5003)]]
    result = post_exit(t, samples, 'ticks', minutes=5, now=LATER, stop_price=4998, require_stop=True)
    assert result['extra_mfe'] == 200 and result['extra_mfe_upper'] == 200
    assert result['stop_touched'] and result['stop_ts'] == after(120)
    assert result['coverage'] == 'stop-observed' and result['termination'] == 'stop-touch'
    assert result['horizon_extra'] is None and len(result['path']) == 2


def test_stop_bar_cannot_award_a_peak_that_might_have_happened_after_stop(tmp_path):
    _, t = trade(tmp_path)
    sample = PriceSample('ESZ6', after(0), 'bars', 5010, 4995, 60, open=5002, close=5007)
    result = post_exit(t, [sample], 'bars', minutes=5, now=LATER, stop_price=4998)
    assert result['extra_mfe'] == 0 and result['extra_mfe_upper'] == 400
    assert result['coverage'] == 'ambiguous-stop-bar'
    assert not runner_groups([{'tags': ['A+'], **result}])


def test_missing_stop_stays_unavailable_and_cutoff_is_not_the_final_peak(tmp_path):
    journal, t = trade(tmp_path)
    journal.ingest_prices(ticks())
    assert best_post_exit(journal, t, 5, stop_mode='original')['extra_mfe'] is None
    journal.annotate(t.id, {'planned_stop': 4998})
    result = best_post_exit(journal, t, 5, stop_mode='original')
    assert result['extra_mfe'] == 200 and result['termination'] == 'still-running-at-cutoff'
    assert not result['stop_touched'] and result['stop_price'] == 4998


def test_breakeven_hypothesis_stops_earlier_than_original_risk_stop(tmp_path):
    journal, t = trade(tmp_path)
    journal.annotate(t.id, {'planned_stop': 4998})
    source = 'ticks'
    samples = [PriceSample('ESZ6', after(s), source, p, p) for s, p in
               [(1, 5002), (60, 5000), (120, 5008), (300, 5003)]]
    journal.ingest_prices(samples)
    original = best_post_exit(journal, t, 5, stop_mode='original')
    breakeven = best_post_exit(journal, t, 5, stop_mode='breakeven')
    assert original['extra_mfe'] == 300 and not original['stop_touched']
    assert breakeven['extra_mfe'] == 0 and breakeven['stop_touched']
