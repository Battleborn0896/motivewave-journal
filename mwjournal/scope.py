"""Explicit live/replay routing; native replay runs never share a price stream."""

LIVE_TICKS = 'motivewave-ticks'                 # prints the study wrote down as they traded
HISTORY_TICKS = 'motivewave-ticks:history'      # the same real prints, loaded from the platform's history afterwards
REPLAY = 'motivewave-replay:'                   # + '<run>:ticks', '<run>:simulated' or '<run>:history'; and REPLAY_BARS
# Real prints of a replay trade's holding window, loaded from MotiveWave's tick history once the trade was
# flat (study 0.3.3). Observed ticks of that one run, never simulated, whatever the replay itself played.
REPLAY_HISTORY = ':history'
# MotiveWave's 1-minute bars of the days a replay plays (study 0.3.7): platform history, the same whichever replay
# run plays them, so they draw the chart of every replay trade they cover. Bars never measure MAE / MFE.
REPLAY_BARS = 'motivewave-replay:bars'


def event_mode(event):
    provider = str(event.get('provider', ''))
    inferred = 'replay' if provider.startswith('motivewave-replay:') or ':replay:' in provider else 'live'
    mode = event.get('mode', inferred)
    if mode not in ('live', 'replay'):
        raise ValueError('Bridge mode must be live or replay.')
    if inferred == 'replay' and mode != inferred:
        raise ValueError('Replay provider cannot be labelled live.')
    return mode


def replay_run(provider: str) -> str | None:
    """The replay run a fill provider names ('motivewave-native:<workspace>:replay:<run>'), or None."""
    return provider.rsplit(':replay:', 1)[1] if ':replay:' in provider else None


def allows_source(trade, source):
    """Whether a price stream may measure a trade. A replay stream ('motivewave-replay:<run>:...', recorded
    or backfilled from history) measures only the trades of its own run, and never a live trade."""
    if not source.startswith(REPLAY):
        return True  # Prices the user imported themselves are also usable in replay.
    run = replay_run(trade.provider)
    if source == REPLAY_BARS:
        return bool(run)  # the platform's history of the replayed days, the same in every run
    return bool(run) and source.startswith(REPLAY + run + ':')


def is_simulated(source: str) -> bool:
    """Replay prints generated from bars. The only replay stream that is not real ticks."""
    return source.startswith(REPLAY) and source.endswith(':simulated')


def is_history(source: str) -> bool:
    """Real prints loaded from MotiveWave's tick history after the trade, live or for one replay run."""
    return source == HISTORY_TICKS or (source.startswith(REPLAY) and source.endswith(REPLAY_HISTORY))


def provenance(source: str) -> str:
    """Where a price source's prices came from: 'recorded-live', 'history-backfill', 'replay-ticks',
    'replay-simulated', 'replay-history', 'platform-bars' or 'imported'. A label beside a measurement, never a
    ranking: two streams of real prints stay two measurements of one trade, and are not added together. The Trades
    caption names the ones that are not the ticks recorded while you traded (trades.PRICES_FROM)."""
    if source == LIVE_TICKS:
        return 'recorded-live'
    if source == HISTORY_TICKS:
        return 'history-backfill'
    if source.startswith(REPLAY):
        if source.endswith(REPLAY_HISTORY):
            return 'replay-history'
        if source.endswith(':bars'):          # REPLAY_BARS, and the one run's bars study 0.3.6 wrote
            return 'platform-bars'
        return 'replay-simulated' if is_simulated(source) else 'replay-ticks'
    if source.startswith('motivewave-bars:'):
        return 'platform-bars'
    return 'imported'
