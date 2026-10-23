"""MAE / MFE display units. A trade has one measurement (metrics.py): its dollars, points, ticks and R are one
moment in four units, so switching units only rescales it."""
import math

from .model import product

UNITS = ('currency', 'points', 'ticks', 'r')
LABELS = {'currency': 'Dollars / account currency', 'points': 'Points', 'ticks': 'Ticks', 'r': 'R · risk to your stop'}
# Outright index futures only, not spread or option ticks. CME contract specifications.
TICK_SIZES = {'ES': .25, 'MES': .25, 'NQ': .25, 'MNQ': .25}
# The one definition (metrics.py), which every unit's description starts with.
DEFINITION = 'How far the position you held went against you (MAE) or for you (MFE) from its average entry, '


def positive(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value > 0


def tick_size(symbol, overrides=None):
    exact = symbol.strip().upper()
    overrides = overrides or {}
    value = overrides.get(exact, TICK_SIZES.get(product(exact)))
    return value if positive(value) else None


def suffix(unit, currency='USD'):
    return {'currency': currency, 'points': 'pts', 'ticks': 'ticks', 'r': 'R'}[unit]


def value(metric, field, unit, *, risk=None, tick=None):
    if unit not in UNITS:
        raise ValueError('Unknown excursion display unit.')
    if unit == 'currency':
        result = metric.get(field)
    elif unit == 'r':
        result = metric.get(field) / risk if metric.get(field) is not None and positive(risk) else None
    else:
        result = metric.get(field + '_points')
        if unit == 'ticks':
            result = result / tick if result is not None and positive(tick) else None
    return result if result is not None and math.isfinite(result) else None


def path_value(point, field, unit, *, risk=None, tick=None):
    if unit == 'currency':
        return point.get(field)
    if unit == 'r':
        return point.get(field) / risk if point.get(field) is not None and positive(risk) else None
    result = point.get('point_' + field)
    return result if unit == 'points' else result / tick if result is not None and positive(tick) else None


def runner_value(value, unit, *, risk=None, tick=None, multiplier=None, quantity=None):
    """Runner hypothesis holds a fixed final-exit quantity, so its price conversion is unambiguous."""
    if value is None:
        return None
    if unit == 'currency':
        return value
    if unit == 'r':
        return value / risk if positive(risk) else None
    if not positive(multiplier) or not positive(quantity):
        return None
    result = value / (multiplier * quantity)
    return result if unit == 'points' else result / tick if positive(tick) else None


def description(unit):
    """The one definition in this unit, in one sentence."""
    return DEFINITION + {'currency': 'in dollars before fees.', 'points': 'in points.',
                         'ticks': 'in ticks of this contract.', 'r': "in R: dollars over your stop's risk."}[unit]
