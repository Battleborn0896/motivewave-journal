"""An open trade follows the market: the watcher measures it at every poll, its result is shown at the last
recorded price, and its chart draws the minute in progress from the study's prints before the platform's
candle for that minute exists."""
import json

from mwjournal import Execution, Journal, PriceSample
from mwjournal.chart import trade_chart
from mwjournal.matching import match
from mwjournal.sync import POLL_SECONDS, SyncWorker


def fill(side, id, price, ts, symbol='NQZ6'):
    return {'version': 1, 'type': 'execution', 'mode': 'live', 'provider': 'motivewave-native:ws:live',
            'account': 'SIM', 'symbol': symbol, 'side': side, 'qty': 1, 'price': price, 'ts': ts,
            'execution_id': id, 'order_id': 'o-' + id, 'commission': 2.0, 'point_value': 20}


def tick(price, ts, symbol='NQ'):
    return {'version': 1, 'type': 'tick', 'mode': 'live', 'provider': 'motivewave-ticks', 'symbol': symbol,
            'price': price, 'ts': ts, 'proxy': False}


def write(path, events):
    with path.open('a', encoding='utf-8', newline='') as fh:
        fh.write(''.join(json.dumps(event) + '\n' for event in events))


def test_an_open_trade_is_measured_again_at_every_poll(tmp_path):
    journal = Journal(tmp_path / 'trades.sqlite3')
    bridge = tmp_path / 'motivewave.ndjson'
    journal.put('profiles', 'native', {'mode': 'bridge', 'path': str(bridge), 'enabled': True})
    write(bridge, [fill('buy', 'in', 20000, '2026-10-07T14:00:00Z'), tick(19995, '2026-10-07T14:00:01Z')])
    worker = SyncWorker(journal)
    worker.poll()
    trade, = journal.trades()
    assert not trade.closed and journal.best_metric(trade)['mae_points'] == 5
    # The next poll, straight after: no waiting for a quieter moment.
    write(bridge, [tick(20030, '2026-10-07T14:00:02Z')])
    worker.poll()
    metric = journal.best_metric(trade)
    assert (metric['mae_points'], metric['mfe_points'], metric['observations']) == (5, 30, 2)
    assert POLL_SECONDS <= 1


def test_the_result_of_an_open_trade_at_a_price():
    long, = match([Execution('t', 'a', 'NQZ6', 'buy', 2, 20000, '2026-10-07T14:00:00Z', commission=4,
                             execution_id='l')], {'NQZ6': 20})
    assert long.net == -4 and long.net_at(20010) == -4 + 10 * 2 * 20 and long.net_at(None) is None
    short, = match([Execution('t', 'a', 'NQZ6', 'sell', 1, 20000, '2026-10-07T14:00:00Z', execution_id='s')],
                   {'NQZ6': 20})
    assert short.net_at(20010) == -200 and short.net_at(19990) == 200
    assert PriceSample('NQ', '2026-10-07T14:00:00Z', 's', 5, 5).last == 5
    bar = dict(symbol='NQ', ts='2026-10-07T14:00:00Z', source='b', high=9, low=1, resolution_seconds=60)
    assert PriceSample(**bar, open=2, close=7).last == 7 and PriceSample(**bar).last is None


def test_the_latest_price_is_the_contracts_own_else_its_products(tmp_path):
    journal = Journal(tmp_path / 'trades.sqlite3')
    sample = lambda symbol, ts, price: PriceSample(symbol, ts, 'motivewave-ticks', price, price)
    journal.ingest_prices([sample('NQ', '2026-10-07T14:00:01Z', 1), sample('NQ', '2026-10-07T14:00:09Z', 2),
                           sample('MNQ', '2026-10-07T14:00:10Z', 99)])
    assert journal.latest_price('NQZ6', 'motivewave-ticks').high == 2
    assert journal.latest_price('NQZ6', 'motivewave-ticks', since='2026-10-07T14:00:10Z') is None
    assert journal.latest_price('NQZ6', 'other') is None and journal.latest_price('ESZ6', 'motivewave-ticks') is None
    # In roll week the contract's own prints win over the continuous chart's, even older ones.
    journal.ingest_prices([sample('NQZ6', '2026-10-07T14:00:05Z', 3)])
    assert journal.latest_price('NQZ6', 'motivewave-ticks').high == 3


def test_the_minute_in_progress_is_drawn_from_prints_after_the_last_platform_candle():
    trade, = match(
        [Execution('t', 'a', 'NQZ6', 'buy', 1, 20000, '2026-10-07T14:00:30Z', execution_id='in')], {'NQZ6': 20})
    bar = lambda minute, o, h, l, c: PriceSample('NQ', f'2026-10-07T14:0{minute}:00Z', 'motivewave-bars:1m', h, l,
                                                 resolution_seconds=60, open=o, close=c)
    bars = [bar(0, 19990, 20010, 19980, 20000), bar(1, 20000, 20020, 19995, 20015)]
    prints = [PriceSample('NQ', ts, 'motivewave-ticks', p, p) for ts, p in [
        ('2026-10-07T14:01:30Z', 1), ('2026-10-07T14:02:05Z', 20016), ('2026-10-07T14:02:20Z', 20030),
        ('2026-10-07T14:02:40Z', 20012)]]
    chart = trade_chart(trade, bars, 'motivewave-bars:1m', prints=prints, prints_source='motivewave-ticks')
    data = chart['series']['data']
    # Its own candle where the platform has one (the stray print inside it is not used), then the prints.
    assert [d['time'] % 3600 for d in data] == [0, 60, 120]
    assert data[1]['close'] == 20015 and data[1]['low'] == 19995
    assert {k: data[2][k] for k in ('open', 'high', 'low', 'close')} == {
        'open': 20016, 'high': 20030, 'low': 20012, 'close': 20012}
    assert any('the one in progress' in note for note in chart['notes'])
    # When the platform's candle for that minute arrives, it replaces the prints.
    chart = trade_chart(trade, bars + [bar(2, 20016, 20031, 20011, 20020)], 'motivewave-bars:1m',
                        prints=prints, prints_source='motivewave-ticks')
    assert chart['series']['data'][-1]['high'] == 20031 and not any('in progress' in n for n in chart['notes'])
    # Without prints the chart is the platform's candles alone.
    assert trade_chart(trade, bars, 'motivewave-bars:1m')['series']['data'] == data[:2]
