"""Market rollups never merge expiry positions or borrow another contract's prices."""
import pytest

from mwjournal import Execution, Journal, PriceSample
from mwjournal.analytics import grouped
from mwjournal.matching import match
from mwjournal.metrics import excursions
from mwjournal.model import futures_root, instrument_market, point_value


@pytest.mark.parametrize('symbol,market', [
    ('NQZ6', 'NQ'), ('NQM2027', 'NQ'), ('NQH27', 'NQ'), ('NQZ', 'NQ'), ('NQM', 'NQ'),
    (' /nq z6 ', 'NQ'), ('NQ=F', 'NQ'), ('MNQZ6', 'MNQ'), ('MNQM7', 'MNQ'),
    ('ESZ6', 'ES'), ('ESH7', 'ES'), ('MESZ6', 'MES'), ('M2KM7', 'M2K'),
    ('ZNZ6', 'ZN'), ('ZBZ6', 'ZB'), ('NQ', 'NQ'), ('NQMATCH', 'NQMATCH'),
    ('AAPL', 'AAPL'), ('BTCUSD', 'BTCUSD'), ('UNKNOWNZ6', 'UNKNOWNZ6'),
])
def test_only_known_futures_symbols_are_grouped(symbol, market):
    assert instrument_market(symbol) == market


def test_futures_multipliers_remain_contract_specific_and_micros_are_not_full_size():
    assert futures_root('NQM') == 'NQ'
    assert point_value('NQM7') == 20 and point_value('MNQZ6') == 2
    assert point_value('ESZ6') == 50 and point_value('MESH7') == 5
    assert futures_root('UNKNOWNZ6') is None


def fill(symbol, side, id):
    return Execution('test', 'paper', symbol, side, 1, 100, '2026-10-07T14:00:00Z', execution_id=id)


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


def test_price_queries_and_excursions_do_not_cross_rollovers(tmp_path):
    journal = Journal(tmp_path / 'trades.sqlite3')
    journal.ingest([(fill('NQZ6', 'buy', 'entry'), {}),
                    (Execution('test', 'paper', 'NQZ6', 'sell', 1, 101,
                               '2026-10-07T14:01:00Z', execution_id='exit'), {})])
    old = PriceSample('NQZ6', '2026-10-07T14:00:30Z', 'ticks', 102, 102)
    new = PriceSample('NQM7', '2026-10-07T14:00:30Z', 'ticks', 999, 999)
    journal.ingest_prices([old, new])
    trade = journal.trades()[0]
    assert journal.prices(trade.symbol, 'ticks') == [old]
    measured = excursions(trade, [old, new], 'ticks')
    assert measured['mfe'] == 40
    assert trade.symbol == 'NQZ6'
