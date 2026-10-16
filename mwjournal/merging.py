"""Reversible review-only merging. Original fills, position IDs and leg reviews never change.

Excursions are reconstructed from combined inventory and recorded prices, not summed leg maxima.
Unknown history, contract/currency mixing and ambiguous cross-leg fill ordering are rejected.
"""
import datetime as dt
import json
from dataclasses import replace

from .model import Trade, digest, instant
from .metrics import measurements
from . import tagging

KEY = 'trade_merges'


def definitions(journal):
    with journal.connect() as db:
        row = db.execute('SELECT value FROM journal_meta WHERE key=?', (KEY,)).fetchone()
        return json.loads(row[0]) if row else {}


def compound(id, legs):
    if len(legs) < 2 or any(not t.complete or not t.closed for t in legs):
        raise ValueError('Merge at least two complete closed trades. Missing fills/prices must be reconciled first.')
    scope = {(t.provider, t.account, t.symbol, t.currency, t.direction, t.point_value) for t in legs}
    if len(scope) != 1:
        raise ValueError('Merged trades need the same provider, account, exact contract, currency, direction and multiplier.')
    opened, closed = min(t.opened for t in legs), max(t.closed for t in legs)
    if (instant(closed) - instant(opened)).total_seconds() > 86400:
        raise ValueError('Merge reviews within one 24-hour window, not unrelated weeks of trading.')
    fills = [(fill, index) for index, t in enumerate(legs) for fill in t.fills]
    instants = {}
    for fill, index in fills:
        instants.setdefault(fill['ts'], []).append((fill['role'], index))
    if any(len({index for role, index in marks}) > 1 and len({role for role, index in marks}) > 1 for marks in instants.values()):
        raise ValueError('Entry and exit fills of different legs share a timestamp. Their relative order is unknown; keep these reviews separate.')
    ordered = sorted((fill for fill, _ in fills), key=lambda f: f['ts'])
    qty = peak = 0.
    for fill in ordered:
        qty += fill['qty'] * (1 if fill['role'] == 'entry' else -1)
        if qty < -1e-8:
            raise ValueError('Combined fills imply an exit before available entry inventory.')
        peak = max(peak, qty)
    if abs(qty) > 1e-8:
        raise ValueError('The selected legs do not close flat.')
    first = legs[0]
    result = Trade(id, first.provider, first.account, first.symbol, first.direction, first.currency, opened,
                   closed=closed, entry_qty=sum(t.entry_qty for t in legs), exit_qty=sum(t.exit_qty for t in legs),
                   entry_value=sum(t.entry_value for t in legs), exit_value=sum(t.exit_value for t in legs),
                   open_qty=0., max_qty=peak, average_cost=0., realised_points=sum(t.realised_points for t in legs),
                   commission=sum(t.commission for t in legs), point_value=first.point_value, fills=ordered,
                   flags=list(dict.fromkeys(flag for t in legs for flag in t.flags)))
    result.merge_leg_ids = [t.id for t in legs]
    result.merge_overlapping = any(instant(a.opened) < instant(b.closed) and instant(b.opened) < instant(a.closed)
                                  for index, a in enumerate(legs) for b in legs[index + 1:])
    return result


def mask_overlap_points(trade, metric):
    if not getattr(trade, 'merge_overlapping', False):
        return metric
    metric = {**metric, 'mae_points': None, 'mfe_points': None, 'point_precision': 'pending',
              'point_definition': 'Unavailable: overlapping original positions have different held-entry bases.',
              'path': [{**point, 'point_low': None, 'point_high': None, 'average_cost': None, 'realised_pnl': None}
                       for point in metric.get('path', [])],
              'notes': [*metric.get('notes', []), 'Currency equity remains combined realised + unrealised P&L. '
                        'Points/ticks and intermediate cost/realised attribution are unavailable for overlapping merged positions; inspect original legs.']}
    return metric


def projected(journal):
    raw = journal.trades()
    by_id = {t.id: t for t in raw}
    used, reviews, warnings = set(), [], []
    for id, definition in definitions(journal).items():
        if not definition['active']:
            continue
        ids = definition['leg_ids']
        try:
            if not set(ids) <= set(by_id) or set(ids) & used:
                raise ValueError('A leg is no longer available or overlaps another merged review.')
            reviews.append(compound(id, [by_id[key] for key in ids]))
            used.update(ids)
        except ValueError as exc:
            warnings.append('Merged review needs attention: ' + str(exc))
    return sorted([*reviews, *[t for t in raw if t.id not in used]], key=lambda t: t.opened), warnings


def create(journal, ids):
    ids = list(dict.fromkeys(ids))
    raw = {t.id: t for t in journal.trades()}
    if not set(ids) <= set(raw):
        raise ValueError('Choose original unmerged trades from this journal.')
    id = 'merged-' + digest(sorted(ids))[:24]
    review = compound(id, [raw[key] for key in ids])
    with journal.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        row = db.execute('SELECT value FROM journal_meta WHERE key=?', (KEY,)).fetchone()
        data = json.loads(row[0]) if row else {}
        if any(d['active'] and set(d['leg_ids']) & set(ids) for d in data.values()):
            raise ValueError('A selected trade already belongs to a merged review. Split it first.')
        data[id] = {'id': id, 'leg_ids': ids, 'active': True, 'at': dt.datetime.now(dt.timezone.utc).isoformat()}
        db.execute('INSERT OR REPLACE INTO journal_meta VALUES(?,?)', (KEY, json.dumps(data, allow_nan=False)))
        library = tagging.library(db)
        tags = set()
        for leg in (raw[key] for key in ids):
            row = db.execute('SELECT payload FROM annotations WHERE trade_id=?', (leg.id,)).fetchone()
            annotation = json.loads(row[0]) if row else {}
            day = db.execute('SELECT payload FROM sessions WHERE key=?', (tagging.session_key(library, leg.opened),)).fetchone()
            tags.update(tagging.effective(library, annotation, json.loads(day[0]) if day else {}))
        # Restoring a split merge retains its own prior notes/risk/tags. Original leg reviews stay untouched.
        db.execute('INSERT OR IGNORE INTO annotations VALUES(?,?)', (id, json.dumps({'tag_ids': sorted(tags),
                                                                                    'tags': tagging.names(library, tags)}, allow_nan=False)))
    return review


def split(journal, id):
    with journal.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        row = db.execute('SELECT value FROM journal_meta WHERE key=?', (KEY,)).fetchone()
        data = json.loads(row[0]) if row else {}
        if id not in data:
            raise ValueError('Unknown merged review.')
        data[id]['active'] = False
        db.execute('INSERT OR REPLACE INTO journal_meta VALUES(?,?)', (KEY, json.dumps(data, allow_nan=False)))


class ReviewJournal:
    """Only review rendering sees compound trades. Capture matching and the bridge stay original."""
    def __init__(self, journal):
        self.original = journal
        self._trades, self.merge_warnings = projected(journal)
        self._metrics = {}

    def __getattr__(self, name):
        return getattr(self.original, name)

    def trades(self):
        # Imports can append/relabel fills within the current render. Never let a review snapshot
        # cause recalculation to measure the pre-import position or leave a newly closed trade open.
        self._trades, self.merge_warnings = projected(self.original)
        return self._trades

    def metric_options(self, trade):
        if not getattr(trade, 'merge_leg_ids', None):
            return self.original.metric_options(trade)
        if trade.basis not in self._metrics:
            result = []
            for source in self.original.price_sources(trade.symbol, trade):
                samples = self.original.prices(trade.symbol, source, start=trade.opened, end=trade.closed)
                result.extend(measurements(trade, samples, source))
            for metric in result:
                metric['notes'].append('Merged review: cumulative realised + unrealised equity across all selected legs; '
                                       'price points/ticks use the currently held combined average entry. Original legs are preserved.')
            self._metrics[trade.basis] = [mask_overlap_points(trade, metric) for metric in result]
        return self._metrics[trade.basis]

    def protection_events(self, trade):
        if not getattr(trade, 'merge_leg_ids', None):
            return self.original.protection_events(trade)
        raw = {t.id: t for t in self.original.trades()}
        return [replace(event, trade_id=trade.id) for id in trade.merge_leg_ids
                for event in self.original.protection_events(raw[id])]

    def trade_media(self, id):
        trade = next((t for t in self._trades if t.id == id), None)
        ids = [id, *getattr(trade, 'merge_leg_ids', [])]
        media = [asset for key in ids for asset in self.original.trade_media(key)]
        seen, result = set(), []
        for asset in media:
            identity = (asset.get('path'), asset.get('role'), asset.get('state'))
            if identity not in seen:
                result.append(asset)
                seen.add(identity)
        return result

    def start_checklist(self, id, trade_id=None):
        from .workspace import Workspace
        return Workspace.start_checklist(self, id, trade_id)

    def record_checkin(self, *args, **kwargs):
        from .workspace import Workspace
        return Workspace.record_checkin(self, *args, **kwargs)

    def detached_reviews(self):
        valid = {t.id for t in self._trades}
        return {kind: [id for id in ids if id not in valid] for kind, ids in self.original.detached_reviews().items()}
