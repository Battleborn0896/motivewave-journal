"""Read actual Java-produced fixtures, not a Python imitation of the study's wire format.

Run bash scripts/test.sh to build and supply them. No account, login, or trading application is used.
"""
import os
from pathlib import Path

import pytest

from mwjournal import Journal
from mwjournal.chart import trade_chart
from mwjournal.importers import preview_bridge
from mwjournal.model import instant
from mwjournal.scope import PROVENANCE, replay_run
from mwjournal.sync import SyncWorker


@pytest.fixture
def bridges():
    configured = os.environ.get('MWJ_CONTRACT_DIR')
    if not configured:
        pytest.skip('Java bridge fixture required: run bash scripts/test.sh')
    folder = Path(configured)
    files = {dataset: folder / name for dataset, name in (
        ('live', 'motivewave.ndjson'), ('replay', 'motivewave.replay.ndjson'))}
    assert all(file.is_file() for file in files.values()), 'Java bridge fixture is missing'
    return files


# The replay trade is measured with the real prints the study backfilled for its run from MotiveWave's
# history (low 4795.5, high 4806), not with the simulated replay prints (which would give 400 and 500).
@pytest.mark.parametrize('dataset,gross,net,mae,mfe,precision,fill_count,trade_count', [
    ('live', 300, 295, 150, 350, 'observed-ticks', 7, 3),
    ('replay', 400, 395, 500, 550, 'observed-ticks', 3, 1),
])
def test_java_bridge_to_journal_trade_chart_and_excursions(
        tmp_path, bridges, dataset, gross, net, mae, mfe, precision, fill_count, trade_count):
    file = bridges[dataset]
    text = file.read_text(encoding='utf-8')
    parsed = preview_bridge(text, dataset=dataset)
    assert not parsed.errors and len(parsed.fills) == fill_count
    assert preview_bridge(text, dataset='replay' if dataset == 'live' else 'live').errors
    journal = Journal(tmp_path / 'journal.sqlite3', dataset=dataset)
    journal.put('profiles', 'study', {'path': str(file), 'mode': 'bridge', 'enabled': True})
    SyncWorker(journal).poll()
    trade = next(trade for trade in journal.trades() if trade.symbol == 'ESZ6')
    assert trade.closed and trade.symbol == 'ESZ6' and trade.max_qty == 2
    assert trade.gross == gross and trade.net == net and trade.commission == 5
    metric = journal.best_metric(trade)
    assert (metric['mae'], metric['mfe'], metric['precision']) == (mae, mfe, precision)
    assert 'fees not provided' not in trade.flags   # MotiveWave's commission is the fee
    assert not any('fees may be estimated' in flag for flag in trade.flags)
    protection = journal.protection_events(trade)
    assert [event.state for event in protection] == (
        ['working', 'working', 'cancelled'] if dataset == 'live' else ['working', 'filled'])
    assert all(event.order_id != 'resting-limit' for event in protection)
    sources = journal.price_sources('ESZ6', trade)
    if dataset == 'replay':
        run = replay_run(trade.provider)
        history = f'motivewave-replay:{run}:history'
        assert sources == [history, f'motivewave-replay:{run}:simulated']
        assert metric['source'] == history and metric['provenance'] == 'replay-history'
        assert PROVENANCE[metric['provenance']] == 'Backfilled from MotiveWave history'
        prints = journal.prices('ESZ6', history)
        # Five prints inside the window; the one the history gave after the replay clock never reached the file.
        assert [p.high for p in prints] == [4799, 4801.5, 4806, 4795.5, 4797.5]
        assert max(instant(p.ts) for p in prints) < instant('2022-01-03T14:31:43Z')
        simulated = next(m for m in journal.metric_options(trade) if m['source'].endswith(':simulated'))
        assert (simulated['mae'], simulated['mfe'], simulated['precision']) == (400, 500, 'replay-simulated')
    else:
        assert not any(s.startswith('motivewave-replay:') for s in sources)
    source = 'motivewave-bars:1m' if dataset == 'live' else sources[0]
    payload = trade_chart(trade, journal.prices('ESZ6', source), source, events=protection)
    assert len(payload['overlays']['fills']) == 3
    assert payload['overlays']['levels'] and payload['series']['data']
    assert journal.get('sync_status')['study']['heartbeat']['connector'] == 'motivewave-journal-study'
    # App restart, then rewinding the stored read position: neither may double-count a fill.
    SyncWorker(Journal(journal.path, dataset=dataset)).poll()
    journal.set_bridge_offset('study', 'different-file', 0, b'')
    SyncWorker(journal).poll()
    assert journal.execution_count() == fill_count and len(journal.trades()) == trade_count
