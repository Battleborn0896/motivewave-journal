"""Export: the trades and reviews as JSON or CSV, and a complete copy of the database. Each file is built when
its button is pressed, not when the tab is drawn."""

from __future__ import annotations

import json

import pandas as pd
import streamlit as st

import common
import theme
from mwjournal import humanize, paths, merging

CSV_COLUMNS = ('id', 'account', 'symbol', 'direction', 'opened', 'closed', 'entry_price', 'exit_price', 'net', 'r',
               'mae', 'mfe', 'mae_r', 'mfe_r', 'precision', 'setup', 'tags')
# Beside the UTC columns: the same entry and exit as a person reads them, in the display time zone.
HUMAN_COLUMNS = ('entry_time', 'exit_time', 'time_zone')


def files(journal, rows=None) -> list[tuple[str, str, object, str]]:
    """(button label, file name, a function that builds the content, media type) for everything this
    environment's journal holds. `rows` are its trade rows (common.rows), built here when not given. The
    functions read nothing from the page: Streamlit runs them on a thread of their own when a button is pressed."""
    rows = common.rows_for(journal) if rows is None else rows
    zone = common.display_zone(journal)
    name = 'motivewave-journal' + ('-replay' if journal.dataset == 'replay' else '')

    def everything():
        original = getattr(journal, 'original', journal)
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
        return json.dumps(exported, indent=2, allow_nan=False)

    def table():
        return pd.DataFrame([{**{k: r.get(k) for k in CSV_COLUMNS},
                              'entry_time': humanize.to_zone(r['opened'], zone).strftime('%Y-%m-%d %H:%M:%S'),
                              'exit_time': humanize.to_zone(r['closed'], zone).strftime('%Y-%m-%d %H:%M:%S') if r.get('closed') else '',
                              'time_zone': zone} for r in rows], columns=[*CSV_COLUMNS, *HUMAN_COLUMNS]).to_csv(index=False)

    return [('Download Journal JSON', name + '.json', everything, 'application/json'),
            ('Download Trades CSV', name + '-trades.csv', table, 'text/csv'),
            # SQLite's own snapshot, so a reader that is importing right now cannot leave the copy inconsistent.
            ('Download Full Backup', name + '.sqlite3', journal.backup, 'application/vnd.sqlite3')]


HELP = {'json': 'Every fill, trade, review, tag, playbook and setting. Also keeps every original measurement, '
                'including excluded estimates.',
        'csv': 'One row per trade. Times are UTC, with entry and exit also in your display time zone. Excursions '
               'follow your accuracy setting, in account currency.',
        'sqlite3': 'A complete copy of the journal database, safe to take while trades are being captured.'}


def render(journal, rows) -> None:
    theme.section('Export & Backup', 'the whole journal of this environment: every currency, no filters')
    for label, name, build, mime in files(journal, common.rows(journal)):
        st.download_button(label, build, file_name=name, mime=mime, key=common.key(journal, 'export-' + name),
                           on_click='ignore', help=HELP[name.rsplit('.', 1)[-1]])
