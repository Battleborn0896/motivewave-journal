"""Export: the trades and reviews as JSON or CSV, and a complete copy of the database."""

from __future__ import annotations

import json

import pandas as pd
import streamlit as st

import common
from mwjournal import paths, merging

CSV_COLUMNS = ('id', 'account', 'symbol', 'direction', 'opened', 'closed', 'entry_price', 'exit_price', 'net', 'r',
               'mae', 'mfe', 'mae_r', 'mfe_r', 'precision', 'setup', 'tags')


def files(journal) -> list[tuple[str, str, object, str]]:
    """(button label, file name, content, media type) for everything this environment's journal holds."""
    rows = common.rows_for(journal)
    original = getattr(journal, 'original', journal)
    name = 'motivewave-journal' + ('-replay' if journal.dataset == 'replay' else '')
    exported = {'app': paths.APP_NAME, 'version': 1, 'dataset': journal.dataset,
                'executions': [dict(e.__dict__) for e in journal.executions()],
                'trades': rows, 'reviews': journal.annotations(), 'tag_catalog': journal.tag_catalog(),
                'display_settings': journal.display_settings(),
                'tag_library': journal.tag_library(),
                'workspace': journal.workspace(),
                'merged_reviews': merging.definitions(journal),
                'original_trade_rows': [t.row() for t in original.trades()],
                'measurements': {**{t.id: original.metric_options(t) for t in original.trades()},
                                 **{t.id: journal.metric_options(t) for t in journal.trades()}},
                'sessions': journal.get('sessions'),
                'linked_protection': list({json.dumps(e.__dict__, sort_keys=True): e.__dict__
                                           for t in original.trades() for e in original.protection_events(t)}.values())}
    table = pd.DataFrame([{k: r.get(k) for k in CSV_COLUMNS} for r in rows], columns=list(CSV_COLUMNS))
    return [('Download journal JSON', name + '.json', json.dumps(exported, indent=2, allow_nan=False), 'application/json'),
            ('Download trades CSV', name + '-trades.csv', table.to_csv(index=False), 'text/csv'),
            # SQLite's own backup, so a reader that is importing right now cannot leave the copy inconsistent.
            ('Download complete SQLite backup', name + '.sqlite3', journal.backup(), 'application/vnd.sqlite3')]


def render(journal, rows) -> None:
    st.caption('Each file is the whole journal of this environment: every currency, no filters.')
    st.caption('Trade-summary excursions follow your accuracy policy and remain in account currency. JSON also retains '
               'every original measurement, including excluded estimates, plus stable tag identities, playbooks and day tags.')
    for label, name, content, mime in files(journal):
        st.download_button(label, content, file_name=name, mime=mime, key=common.key(journal, 'export-' + name))
