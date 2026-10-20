"""A product is its letters. Positions stay per contract; a trade's prices are those of its product,
whatever contract month, chart or replay they were recorded under, and never another product's."""
import dataclasses
import json
import sqlite3

import pytest

from mwjournal import Execution, Journal, PriceSample
from mwjournal import journals
from mwjournal.analytics import grouped
from mwjournal.chart import trade_chart
from mwjournal.importers import apply_preview, preview_bridge
from mwjournal.matching import match
from mwjournal.metrics import excursions
from mwjournal.model import instrument_market, point_value, product, trade_prices
from mwjournal.post_exit import post_exit
from mwjournal.sync import SyncWorker


@pytest.mark.parametrize('symbol,market', [
    # The owner's cases: the month code and its year digits go, whatever their length; so do a
    # leading slash and an exchange suffix. Micro is its own product; CQG's letters stay CQG's.
    ('NQZ6', 'NQ'), ('NQZ26', 'NQ'), ('NQZ2026', 'NQ'), ('/NQZ6', 'NQ'), ('NQZ6.CME', 'NQ'),
    ('MNQZ6', 'MNQ'), ('ENQZ6', 'ENQ'), ('ES', 'ES'), ('AAPL', 'AAPL'), ('6EZ6', '6E'), ('ZNZ6', 'ZN'),
    ('NQ', 'NQ'), ('nqz6', 'NQ'), (' /nq z6 ', 'NQ'), ('NQ=F', 'NQ'), ('NQM2027', 'NQ'), ('NQH27', 'NQ'),
    ('MNQM7', 'MNQ'), ('ESZ6', 'ES'), ('ESH7', 'ES'), ('MESZ6', 'MES'), ('M2KM7', 'M2K'), ('ZBZ6', 'ZB'),
    ('EPZ6', 'EP'), ('ZN', 'ZN'), ('NQMATCH', 'NQMATCH'), ('BTCUSD', 'BTCUSD'), ('UNKNOWNZ6', 'UNKNOWN'),
    # Not a contract month: a month letter needs its year digits ('NQ' itself ends in Q), a share
    # class is not an exchange, and an option keeps its whole name.
    ('NQZ', 'NQZ'), ('BRK.B', 'BRK.B'), ('NQZ6-C-20000', 'NQZ6-C-20000'), ('Z6', 'Z6'),
])
def test_a_product_is_the_symbol_without_its_month_exchange_or_slash(symbol, market):
    assert product(symbol) == market
    assert instrument_market(symbol) == market        # markets are grouped by the same function


def test_point_values_follow_the_product_and_micros_are_not_full_size():
    assert point_value('NQM7') == 20 and point_value('NQZ6.CME') == 20 and point_value('MNQZ6') == 2
    assert point_value('ESZ6') == 50 and point_value('MESH7') == 5 and point_value('/ESZ26') == 50
    # No translation table: CQG letters carry the multiplier MotiveWave sends with each fill.
    assert point_value('ENQZ6') is None and point_value('UNKNOWNZ6') is None


def fill(symbol, side, id, price=100, ts='2026-10-07T14:00:00Z', provider='test'):
    return Execution(provider, 'paper', symbol, side, 1, price, ts, execution_id=id)


def tick(symbol, second, price, source='motivewave-ticks', **more):
    return PriceSample(symbol, f'2026-10-07T14:00:{second:02d}Z', source, price, price, **more)


def round_trip(journal, symbol='NQZ6', entry=20000, exit=20010):
    journal.ingest([(fill(symbol, 'buy', symbol + '-in', entry), {}),
                    (fill(symbol, 'sell', symbol + '-out', exit, ts='2026-10-07T14:01:00Z'), {})])
    return next(t for t in journal.trades() if t.symbol == symbol)


def test_buy_old_expiry_and_sell_new_expiry_are_two_open_positions_not_a_round_trip():
    trades = match([fill('NQZ6', 'buy', 'old'), fill('NQM7', 'sell', 'new')])
    assert len(trades) == 2 and all(not t.closed for t in trades)
    assert {t.symbol for t in trades} == {'NQZ6', 'NQM7'}
    assert trades[0].direction == 'long' and trades[1].direction == 'short'


def test_grouped_market_analytics_pool_expiries_not_micros():
    rows = [{'symbol': symbol, 'status': 'closed', 'closed': '2026-10-07T14:01:00Z', 'net': net}
            for symbol, net in [('NQZ6', 10), ('NQM7', -5), ('MNQZ6', 2), ('ESZ6', 3), ('ESH7', 4)]]
    groups = {r['group']: r for r in grouped(rows, 'market')}
    assert groups['NQ']['count'] == 2 and groups['NQ']['net'] == 5
    assert groups['ES']['count'] == 2 and groups['ES']['net'] == 7
    assert groups['MNQ']['count'] == 1 and groups['MNQ']['net'] == 2
    assert len(grouped(rows, 'symbol')) == 5


def test_an_nqz6_trade_is_measured_from_the_prices_its_chart_recorded_as_nq(tmp_path):
    """The owner's case: an NQZ6 chart whose prices MotiveWave labelled 'NQ'."""
    journal = Journal(tmp_path / 'trades.sqlite3')
    trade = round_trip(journal)
    nq = [tick('NQ', 10, 19990), tick('NQ', 30, 20030), tick('NQ', 50, 20005)]
    journal.ingest_prices(nq)
    assert journal.price_sources('NQZ6', trade) == ['motivewave-ticks']
    window = {'start': trade.opened, 'end': trade.closed}
    assert journal.prices('NQZ6', 'motivewave-ticks', **window) == nq
    assert journal.prices('nqz6', 'motivewave-ticks') == nq
    metric = excursions(trade, journal.prices(trade.symbol, 'motivewave-ticks', **window), 'motivewave-ticks')
    assert (metric['precision'], metric['observations']) == ('observed-ticks', 3)
    assert (metric['mae_points'], metric['mfe_points']) == (10, 30)
    assert (metric['mae'], metric['mfe']) == (200, 600)          # NQ's $20 a point, from the product
    chart = trade_chart(trade, journal.prices(trade.symbol, 'motivewave-ticks'), 'motivewave-ticks')
    assert chart['series']['data'][0]['high'] == 20030 and chart['series']['data'][0]['low'] == 19990
    # After the exit, the same rule: the runner analysis reads the chart's prints too.
    journal.ingest_prices([PriceSample('NQ', '2026-10-07T14:02:00Z', 'motivewave-ticks', 20040, 20040)])
    runner = post_exit(trade, journal.prices(trade.symbol, 'motivewave-ticks', start=trade.closed),
                       'motivewave-ticks', minutes=5)
    assert runner['observations'] == 1 and runner['extra_mfe'] == 600
    # A gap the chart reported under its own label concerns this trade.
    journal.ingest_gaps([{'provider': 'motivewave-ticks', 'symbol': 'NQ', 'dropped': 2,
                          'start_ts': '2026-10-07T14:00:20Z', 'end_ts': '2026-10-07T14:00:21Z', 'ts': trade.closed}])
    assert journal.capture_gaps('motivewave-ticks', symbol='NQZ6', **window)[0]['dropped'] == 2
    assert not journal.capture_gaps('motivewave-ticks', symbol='MNQZ6', **window)


def test_micro_and_full_size_never_use_each_others_prices(tmp_path):
    journal = Journal(tmp_path / 'trades.sqlite3')
    full, micro = round_trip(journal, 'NQZ6'), round_trip(journal, 'MNQZ6')
    nq, mnq = [tick('NQ', 20, 20030)], [tick('MNQ', 20, 20090), tick('MNQZ6', 40, 19950)]
    journal.ingest_prices(mnq)
    # Only micro prices exist: the full-size trade has none, whatever they cost.
    assert journal.price_sources('NQZ6', full) == [] and journal.prices('NQZ6', 'motivewave-ticks') == []
    assert excursions(full, mnq, 'motivewave-ticks')['precision'] == 'pending'
    journal.ingest_prices(nq)
    assert journal.prices('NQZ6', 'motivewave-ticks') == nq
    assert journal.prices('MNQZ6', 'motivewave-ticks') == [mnq[1]]   # its own contract's print, as below
    assert trade_prices(nq + mnq, 'MNQZ6', 'motivewave-ticks') == [mnq[1]]
    assert trade_prices(nq + mnq[:1], 'MNQZ6', 'motivewave-ticks') == mnq[:1]
    assert excursions(micro, nq, 'motivewave-ticks')['precision'] == 'pending'
    assert excursions(full, nq + mnq, 'motivewave-ticks')['observations'] == 1


def test_in_roll_week_the_exact_contract_wins_over_another_month_of_the_same_product(tmp_path):
    journal = Journal(tmp_path / 'trades.sqlite3')
    trade = round_trip(journal)
    december = [tick('NQZ6', 15, 20002), tick('NQZ6', 45, 20012)]
    march = [tick('NQH7', 15, 20202), tick('NQH7', 45, 20212)]      # trades about 200 points higher
    journal.ingest_prices(december + march)
    assert journal.prices('NQZ6', 'motivewave-ticks') == december
    assert journal.prices('NQH7', 'motivewave-ticks') == march
    measured = excursions(trade, december + march, 'motivewave-ticks')
    assert measured['observations'] == 2 and measured['mfe_points'] == 12
    # Decided per source and window: where only the other month was recorded, that is the product's price.
    bars = PriceSample('NQH7', '2026-10-07T14:00:00Z', 'motivewave-bars:1m', 20215, 20195, 60,
                       open=20200, close=20210, volume=7)
    journal.ingest_prices([bars])
    assert journal.price_sources('NQZ6', trade) == ['motivewave-bars:1m', 'motivewave-ticks']
    assert journal.prices('NQZ6', 'motivewave-bars:1m') == [bars]


def test_the_same_print_recorded_under_two_labels_counts_once(tmp_path):
    journal = Journal(tmp_path / 'trades.sqlite3')
    trade = round_trip(journal)
    prints = [(10, 19995, 3), (30, 20025, 1), (50, 20004, 2)]
    # Two charts of one market: the contract's own chart and a continuous one.
    exact = [tick('NQZ6', s, p, volume=v) for s, p, v in prints]
    continuous = [tick('NQ', s, p, volume=v, proxy=True) for s, p, v in prints]
    journal.ingest_prices(exact + continuous)
    assert journal.prices('NQZ6', 'motivewave-ticks') == exact
    assert excursions(trade, exact + continuous, 'motivewave-ticks')['observations'] == 3
    # Neither label is the trade's own: the prints still count once, bars by their slot.
    other = Journal(tmp_path / 'other.sqlite3')
    trade = round_trip(other)
    exchange = [tick('NQZ6.CME', s, p, volume=v) for s, p, v in prints]
    minute = [PriceSample(label, '2026-10-07T14:00:00Z', 'motivewave-bars:1m', 20025, 19995, 60,
                          open=19996, close=20004, volume=6) for label in ('NQ', 'NQZ6.CME')]
    other.ingest_prices(continuous + exchange + minute)
    once = other.prices('NQZ6', 'motivewave-ticks')
    assert len(once) == 3 and [s.high for s in once] == [19995, 20025, 20004]
    assert excursions(trade, continuous + exchange, 'motivewave-ticks')['observations'] == 3
    candles = trade_chart(trade, other.prices('NQZ6', 'motivewave-bars:1m'), 'motivewave-bars:1m')['series']['data']
    assert [c.get('volume') for c in candles] == [6]
    ticks = trade_chart(trade, other.prices('NQZ6', 'motivewave-ticks'), 'motivewave-ticks')['series']['data']
    assert ticks[0]['volume'] == 6                                # 3 + 1 + 2, not twice that


def lines(events):
    return ''.join(json.dumps(e) + '\n' for e in events)


def bridge_fill(id, side, price, ts, *, mode='live', run='run-a'):
    return {'version': 1, 'type': 'execution', 'mode': mode,
            'provider': 'motivewave-native:workspace:' + ('replay:' + run if mode == 'replay' else 'live'),
            'account': 'paper', 'symbol': 'NQZ6', 'execution_id': id, 'side': side, 'qty': 1, 'price': price,
            'ts': ts, 'point_value': 20, 'commission': 2, 'commission_known': False, 'currency': 'USD'}


def bridge_tick(symbol, price, ts, provider='motivewave-ticks', mode='live', proxy=False):
    return {'version': 1, 'type': 'tick', 'mode': mode, 'provider': provider, 'symbol': symbol,
            'price': price, 'ts': ts, 'proxy': proxy}


def test_the_watcher_measures_an_nqz6_trade_again_when_nq_prices_arrive(tmp_path):
    from common import metric_options
    journal = Journal(tmp_path / 'trades.sqlite3')
    path = tmp_path / 'motivewave.ndjson'
    journal.put('profiles', 'native', {'mode': 'bridge', 'path': str(path), 'enabled': True})
    path.write_text(lines([bridge_fill('in', 'buy', 20000, '2026-10-07T14:00:00Z'),
                           bridge_fill('out', 'sell', 20010, '2026-10-07T14:01:00Z')]),
                    encoding='utf-8', newline='')
    SyncWorker(journal).poll()
    trade, = journal.trades()
    assert journal.metric_options(trade) == []
    # Another product's prices arrive first: nothing for this trade.
    with path.open('a', encoding='utf-8', newline='') as fh:
        fh.write(lines([bridge_tick('MNQ', 20500, '2026-10-07T14:00:30Z')]))
    SyncWorker(journal).poll()
    assert journal.metric_options(trade) == []
    # Then its own chart's, labelled with the product only and, recorded as they traded, not proxy: what
    # the study writes on a continuous NQ chart.
    with path.open('a', encoding='utf-8', newline='') as fh:
        fh.write(lines([bridge_tick('NQ', 19995, '2026-10-07T14:00:20Z'),
                        bridge_tick('NQ', 20030, '2026-10-07T14:00:40Z')]))
    SyncWorker(journal).poll()
    metric, = journal.metric_options(trade)
    assert (metric['source'], metric['precision'], metric['observations']) == ('motivewave-ticks', 'observed-ticks', 2)
    assert (metric['mae_points'], metric['mfe_points'], metric['mae'], metric['mfe']) == (5, 30, 100, 600)
    # Shown with the default accuracy policy, recorded ticks only.
    assert journal.display_settings()['excursion_policy'] == 'ticks-only'
    assert metric_options(journal, trade) == [metric]


def test_a_replay_nqz6_trade_on_a_continuous_chart_is_measured_from_its_own_runs_prints_by_default(tmp_path):
    """What the study writes with its defaults on a replay of a continuous NQ chart: prints labelled as the
    chart is, real ticks of the run (Real Tick Data is on by default) and not proxy (only the user's Proxy
    Prices setting marks prints recorded as they trade). The default accuracy policy shows the result."""
    from common import metric_options
    journal = Journal(tmp_path / 'replay.sqlite3', dataset='replay')
    path = tmp_path / 'motivewave.replay.ndjson'
    journal.put('profiles', 'native', {'mode': 'bridge', 'path': str(path), 'enabled': True})
    own, other = 'motivewave-replay:run-a:ticks', 'motivewave-replay:run-b:ticks'
    events = [bridge_fill('in', 'buy', 20000, '2026-10-07T14:00:00Z', mode='replay'),
              bridge_tick('NQ', 19990, '2026-10-07T14:00:15Z', own, 'replay'),
              bridge_tick('NQ', 20025, '2026-10-07T14:00:45Z', own, 'replay'),
              # The same moments in another run: never this trade's.
              bridge_tick('NQ', 19000, '2026-10-07T14:00:30Z', other, 'replay'),
              bridge_tick('NQ', 21000, '2026-10-07T14:05:00Z', other, 'replay')]
    path.write_text(lines(events), encoding='utf-8', newline='')
    SyncWorker(journal).poll()
    trade, = journal.trades()
    assert not trade.closed
    # Still open: its clock is the latest print of its own run, whatever that print is labelled.
    assert journal.trade_clock(trade).isoformat() == '2026-10-07T14:00:45.001000+00:00'
    assert journal.price_sources('NQZ6', trade) == [own]
    metric, = journal.metric_options(trade)
    assert (metric['source'], metric['observations'], metric['mae_points'], metric['mfe_points']) == (own, 2, 10, 25)
    assert (metric['precision'], metric['provenance']) == ('observed-ticks', 'replay-ticks')
    assert journal.display_settings()['excursion_policy'] == 'ticks-only'
    assert metric_options(journal, trade) == [metric]
    with path.open('a', encoding='utf-8', newline='') as fh:
        fh.write(lines([bridge_fill('out', 'sell', 20010, '2026-10-07T14:01:00Z', mode='replay')]))
    SyncWorker(journal).poll()
    trade, = journal.trades()
    metric, = journal.metric_options(trade)
    assert (metric['observations'], metric['mae'], metric['mfe'], metric['precision']) == (2, 200, 500, 'observed-ticks')
    assert metric_options(journal, trade) == [metric]
    assert excursions(trade, journal.prices('NQZ6', other), other)['mfe'] is None
    # Prices the user marked with Proxy Prices are an estimate, which the default policy leaves out.
    marked = [dataclasses.replace(sample, proxy=True) for sample in journal.prices('NQZ6', own)]
    assert excursions(trade, marked, own)['precision'] == 'proxy-estimate'


def test_a_named_journal_receives_the_prices_of_its_trades_products(tmp_path, monkeypatch):
    monkeypatch.setenv('MWJ_HOME', str(tmp_path))
    capture = Journal()
    trade = round_trip(capture)
    capture.ingest_prices([tick('NQ', 30, 20030), tick('MNQ', 30, 20090), tick('ES', 30, 5000)])
    target = journals.open_named(capture, journals.create(capture, 'Paper', [('test', 'paper')]))
    assert target.prices(trade.symbol, 'motivewave-ticks') == [tick('NQ', 30, 20030)]
    assert target.price_sources('MNQZ6') == [] and target.price_sources('ESZ6') == []


def as_before_the_product_rule(path):
    """Put a journal back as app 0.4.4 left it: its trades imported, but measured only with prices under
    their exact symbol (there are none here, so not at all), and no measurement rules recorded."""
    db = sqlite3.connect(path)
    with db:
        db.execute('DELETE FROM metrics')
        db.execute("DELETE FROM journal_meta WHERE key='measurement_rules'")
    db.close()


def test_journals_measured_before_the_product_rule_are_measured_again_once_when_the_watcher_starts(tmp_path):
    """The owner's case: an NQZ6 position still open, imported before the product rule with its chart's
    prints labelled NQ, and so never measured. Each journal, live and replay, measures every trade again
    the first time its watcher starts, then records that it did and never repeats it."""
    from common import metric_options
    journals, trades = {}, {}
    for dataset, source in (('live', 'motivewave-ticks'), ('replay', 'motivewave-replay:run-a:ticks')):
        journal = journals[dataset] = Journal(tmp_path / dataset / 'trades.sqlite3', dataset=dataset)
        assert not journal.recalculation_due()                 # a new journal has nothing older to measure
        path = tmp_path / dataset / 'motivewave.ndjson'
        path.write_text(lines([bridge_fill('in', 'buy', 20000, '2026-10-07T14:00:00Z', mode=dataset),
                               bridge_tick('NQ', 19995, '2026-10-07T14:00:20Z', source, dataset),
                               bridge_tick('NQ', 20030, '2026-10-07T14:00:40Z', source, dataset)]),
                        encoding='utf-8', newline='')
        journal.put('profiles', 'native', {'mode': 'bridge', 'path': str(path), 'enabled': True})
        SyncWorker(journal).poll()
        as_before_the_product_rule(journal.path)
        trades[dataset], = journal.trades()
        assert not trades[dataset].closed and journal.metric_options(trades[dataset]) == []
        assert journal.recalculation_due()
    # The live watcher starts: nothing new in its file, yet its trade is measured. The replay journal waits
    # for a watcher of its own.
    SyncWorker(Journal(journals['live'].path)).poll()
    assert not journals['live'].recalculation_due() and journals['replay'].recalculation_due()
    assert journals['replay'].metric_options(trades['replay']) == []
    SyncWorker(Journal(journals['replay'].path, dataset='replay')).poll()
    for dataset, journal in journals.items():
        metric, = journal.metric_options(trades[dataset])
        assert (metric['precision'], metric['observations'], metric['mae_points'], metric['mfe_points']) == (
            'observed-ticks', 2, 5, 30)
        assert journal.display_settings()['excursion_policy'] == 'ticks-only'
        assert metric_options(journal, trades[dataset]) == [metric]
        assert not journal.recalculation_due()
    # Once only: with the measurements gone, the next start of either watcher measures nothing again.
    for dataset, journal in journals.items():
        with journal.connect() as db:
            db.execute('DELETE FROM metrics')
        SyncWorker(Journal(journal.path, dataset=dataset)).poll()
        assert journal.metric_options(trades[dataset]) == [] and not journal.recalculation_due()
