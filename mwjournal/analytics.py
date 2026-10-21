"""Statistics for closed trades only; open positions and missing multipliers never count as wins."""

from collections import defaultdict

from .model import product


def summary(rows: list[dict]) -> dict:
    closed = [r for r in rows if r['status'] == 'closed' and r.get('net') is not None]
    values = [r['net'] for r in closed]
    wins = [v for v in values if v > 0]
    losses = [v for v in values if v < 0]
    r_values = [r['r'] for r in closed if r.get('r') is not None]
    gross_loss = abs(sum(losses))
    equity, peak, drawdown = 0., 0., 0.
    for row in sorted(closed, key=lambda r: r['closed']):
        equity += row['net']
        peak = max(peak, equity)
        drawdown = max(drawdown, peak - equity)
    return {'count': len(values), 'net': sum(values),
            'win_rate': len(wins) / len(values) * 100 if values else None,
            'expectancy': sum(values) / len(values) if values else None,
            'profit_factor': sum(wins) / gross_loss if gross_loss else None,
            'avg_win': sum(wins) / len(wins) if wins else None,
            'avg_loss': sum(losses) / len(losses) if losses else None,
            'risk_recorded': len(r_values), 'expectancy_r': sum(r_values) / len(r_values) if r_values else None,
            'drawdown': drawdown}


def grouped(rows: list[dict], field: str) -> list[dict]:
    groups = defaultdict(list)
    for row in rows:
        if field == 'market':
            values = [product(row['symbol'])]
        else:
            values = (row.get(field) or ['Untagged' if field == 'tags' else 'No matching playbook']) if field in ('tags', 'playbooks') else [row.get(field) or 'Unspecified']
        for value in values:
            groups[value].append(row)
    return [{'group': key, **summary(trades)} for key, trades in sorted(groups.items())]


def cost_summary(rows: list[dict]) -> dict:
    """Gross-to-net attribution for one currency. Recorded fees, never inferred slippage.

    Missing/estimated fees may be present numerically; preserve the trade's uncertainty flag.
    A rebate is a negative cost, not clipped to zero. R costs need the risk to the trade's stop.
    """
    closed = [row for row in rows if row['status'] == 'closed'
              and row.get('gross') is not None and row.get('net') is not None]
    if len({row.get('currency', 'USD') for row in closed}) > 1:
        raise ValueError('Cost attribution requires a single currency.')
    fees = [row['gross'] - row['net'] for row in closed]
    risk_fees = [fee / row['initial_risk'] for row, fee in zip(closed, fees)
                 if row.get('initial_risk') is not None and row['initial_risk'] > 0]
    return {'count': len(closed), 'gross': sum(row['gross'] for row in closed),
            'net': sum(row['net'] for row in closed), 'fees': sum(fees),
            'fee_per_trade': sum(fees) / len(fees) if fees else None,
            'fee_per_r': sum(risk_fees) / len(risk_fees) if risk_fees else None,
            'risk_recorded': len(risk_fees),
            'unverified_fees': sum('fees not provided' in row.get('flags', ()) for row in closed)}
