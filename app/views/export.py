"""Export & Backup: one zip that backs up the whole journal, this environment's trades as a CSV, and putting a
backup back. Each download is built when its button is pressed, not when the tab is drawn."""

from __future__ import annotations

import sqlite3
import tempfile

import pandas as pd
import streamlit as st

import common
import runtime
import theme
from mwjournal import backup, humanize, paths

CSV_COLUMNS = ('id', 'account', 'symbol', 'direction', 'opened', 'closed', 'entry_price', 'exit_price', 'net', 'r',
               'mae', 'mfe', 'mae_r', 'mfe_r', 'tags')
# Beside the UTC columns: the same entry and exit as a person reads them, in the display time zone.
HUMAN_COLUMNS = ('entry_time', 'exit_time', 'time_zone')
HELP = {'backup': 'One zip of the whole journal, Live and Replay and every journal you made, safe to take while '
                  'trades are recording.',
        'csv': 'Every trade of this environment, one row each, with UTC times and entry and exit in your display '
               'time zone.',
        'restore': 'Choose a backup zip to put the whole journal back as it was then; your journal as it is now is '
                   'saved first.'}
# The file box's number goes up after a restore, so the box is empty again; the chosen file's summary is read
# once per file, not at every click; what the last restore said is shown once, after the page is drawn again.
UPLOAD_KEY, SUMMARY_KEY, DONE_KEY = 'restore-file', 'restore-summary', 'restore-done'


def backup_zip() -> bytes:
    """backup.write()'s zip of the whole journal, built in a temporary folder that is removed once it is read."""
    with tempfile.TemporaryDirectory(prefix='mwj-backup-') as folder:
        return backup.write(folder).read_bytes()


def trades_csv(rows, zone: str) -> str:
    """One row per trade (common.rows): the stored columns, times in UTC, then entry and exit in `zone`."""
    return pd.DataFrame([{**{k: r.get(k) for k in CSV_COLUMNS},
                          'entry_time': humanize.to_zone(r['opened'], zone).strftime('%Y-%m-%d %H:%M:%S'),
                          'exit_time': humanize.to_zone(r['closed'], zone).strftime('%Y-%m-%d %H:%M:%S') if r.get('closed') else '',
                          'time_zone': zone} for r in rows], columns=[*CSV_COLUMNS, *HUMAN_COLUMNS]).to_csv(index=False)


def described(summary: dict, zone: str) -> str:
    """'Backup of Oct 23, 2026 · 412 live trades, 37 replay trades'."""
    day = humanize.to_zone(summary['created'], zone).date()
    counts = [f"{n:,} {dataset} trade{'' if n == 1 else 's'}" for dataset, n in summary['trades'].items()]
    return f"Backup of {humanize.MONTHS[day.month - 1]} {day.day}, {day.year} · {', '.join(counts)}"


def chosen(upload) -> dict | str:
    """backup.read()'s summary of the chosen file, or the sentence that says why it cannot be restored."""
    held = st.session_state.get(SUMMARY_KEY)
    if held is None or held[0] != upload.file_id:
        try:
            summary = backup.read(upload)
        except ValueError as exc:
            summary = str(exc)
        held = st.session_state[SUMMARY_KEY] = (upload.file_id, summary)
    return held[1]


def restored(upload) -> tuple[bool, str]:
    """Puts the backup back with both watchers stopped, then starts them again; the trade rows are built anew."""
    runtime.stop_all()
    try:
        result = backup.restore(upload)
        return True, f"Restored. Your previous journal was saved to {result['previous']}."
    except (ValueError, OSError, sqlite3.Error) as exc:
        return False, f'The backup could not be restored. {exc}'
    finally:
        for name in [k for k in st.session_state if str(k).startswith('trade-rows-')]:
            del st.session_state[name]
        for dataset in paths.DATASETS:
            try:
                runtime.start(dataset)
            except (ValueError, OSError, sqlite3.Error):
                pass                       # that journal cannot be opened; switching to it says why


def render(journal, rows) -> None:
    theme.section('Export & Backup', "one backup holds Live and Replay; the CSV holds this environment's trades")
    st.download_button('Back Up Journal', backup_zip, file_name=backup.file_name(), mime='application/zip',
                       key='export-backup', on_click='ignore', type='primary', help=HELP['backup'])
    every, zone = common.rows(journal), common.display_zone(journal)          # every currency, no filters
    name = 'motivewave-journal' + ('-replay' if journal.dataset == 'replay' else '') + '-trades.csv'
    st.download_button('Export Trades CSV', lambda: trades_csv(every, zone), file_name=name, mime='text/csv',
                       key=common.key(journal, 'export-csv'), on_click='ignore', help=HELP['csv'])
    done = st.session_state.pop(DONE_KEY, None)
    if done:
        (st.success if done[0] else st.error)(done[1])
    upload = st.file_uploader('Restore From Backup', type='zip', help=HELP['restore'],
                              key=f'{UPLOAD_KEY}-{st.session_state.get(UPLOAD_KEY, 0)}')
    if upload is None:
        return
    summary = chosen(upload)
    if isinstance(summary, str):
        st.error(summary)
        return
    st.markdown(described(summary, zone))
    if st.button('Restore This Backup', type='primary', key='restore-apply'):
        with st.spinner('Restoring…'):
            st.session_state[DONE_KEY] = restored(upload)
        if st.session_state[DONE_KEY][0]:
            st.session_state[UPLOAD_KEY] = st.session_state.get(UPLOAD_KEY, 0) + 1
        st.rerun()
