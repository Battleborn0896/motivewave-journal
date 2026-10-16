import pytest

from mwjournal.analytics import cost_summary


def row(gross, net, **more):
    return {'status': 'closed', 'gross': gross, 'net': net, 'currency': 'USD', **more}


def test_cost_attribution_uses_closed_known_results_and_planned_risk_only():
    result = cost_summary([row(100, 95, initial_risk=10), row(-50, -55, flags=['fees not provided']),
                           row(999, 998, status='open'), row(None, None)])
    assert result == {'count': 2, 'gross': 50, 'net': 40, 'fees': 10, 'fee_per_trade': 5,
                      'fee_per_r': .5, 'risk_recorded': 1, 'unverified_fees': 1}


def test_missing_risk_and_empty_results_stay_unavailable_not_zero_risk():
    result = cost_summary([])
    assert result['count'] == 0 and result['fee_per_trade'] is None and result['fee_per_r'] is None
    assert cost_summary([row(10, 9, initial_risk=0)])['fee_per_r'] is None


def test_rebates_remain_negative_costs_and_currencies_cannot_be_summed():
    assert cost_summary([row(10, 11)])['fees'] == -1
    with pytest.raises(ValueError, match='single currency'):
        cost_summary([row(10, 9), row(10, 9, currency='EUR')])
