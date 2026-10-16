"""Explicit live/replay routing; native replay runs never share a price stream."""

LIVE_TICKS = 'motivewave-ticks'                 # prints the study wrote down as they traded
HISTORY_TICKS = 'motivewave-ticks:history'      # the same real prints, loaded from the platform's history afterwards
# Where a price stream came from. A label beside a measurement, never a ranking: two streams of
# real prints stay two measurements of one trade, and are not added together.
PROVENANCE = {'recorded-live': 'Recorded live', 'history-backfill': 'History backfill',
              'replay-ticks': 'Replay ticks', 'replay-simulated': 'Replay, generated from bars',
              'platform-bars': 'Platform candles', 'imported': 'Imported or other source'}


def event_mode(event):
    provider = str(event.get('provider', ''))
    inferred = 'replay' if provider.startswith('motivewave-replay:') or ':replay:' in provider else 'live'
    mode = event.get('mode', inferred)
    if mode not in ('live', 'replay'):
        raise ValueError('Bridge mode must be live or replay.')
    if inferred == 'replay' and mode != inferred:
        raise ValueError('Replay provider cannot be labelled live.')
    return mode


def allows_source(trade, source):
    if not source.startswith('motivewave-replay:'):
        return True  # Prices the user imported themselves are also usable in replay.
    if ':replay:' not in trade.provider:
        return False
    run = trade.provider.rsplit(':replay:', 1)[1]
    return source.startswith('motivewave-replay:' + run + ':')


def provenance(source: str) -> str:
    """A key of PROVENANCE for a price source, also for measurements stored before the key existed."""
    if source == LIVE_TICKS:
        return 'recorded-live'
    if source == HISTORY_TICKS:
        return 'history-backfill'
    if source.startswith('motivewave-replay:'):
        return 'replay-simulated' if source.endswith(':simulated') else 'replay-ticks'
    if source.startswith('motivewave-bars:'):
        return 'platform-bars'
    return 'imported'
