"""Import, on the Data page: one box takes a file and tells what it holds; one fill can also be added by hand.

A bridge file the study wrote (.ndjson, .jsonl, or lines of JSON objects) brings its events, and one from the other
environment is refused. Anything else is read as a CSV of fills, one row per fill: its columns are found by their
names, and Column Mapping opens only when a required one is not found. Times written without an offset are read in
the time zone chosen for the file, which opens on the journal's Time Zone. Everything is previewed first; nothing
reaches the journal until the whole file has parsed.
"""

from __future__ import annotations

import datetime as dt
import hashlib

import pandas as pd
import streamlit as st

import common
import theme
from mwjournal import Execution, humanize
from mwjournal.importers import (LABELS, apply_preview, csv_rows, is_bridge, mapping_for, missing_columns,
                                 preview_bridge, preview_csv)
from mwjournal.model import timestamp


def render(journal, rows) -> None:
    theme.section('Import Trades')
    st.caption('For trades the study did not record: a CSV with one row per fill, or a bridge file. Never import '
               'fills the study already recorded: they would count twice.')
    upload = st.file_uploader('Fills CSV or Bridge File', type=['csv', 'txt', 'jsonl', 'ndjson'],
                              key=common.key(journal, 'import-file'))
    if upload:
        try:
            text = upload.getvalue().decode('utf-8-sig')
            preview = (preview_bridge(text, dataset=journal.dataset) if is_bridge(upload.name, text)
                       else fills_preview(journal, text))
            st.write(f'{preview.count} records ready' + (f' · {preview.skipped} skipped' if preview.skipped else ''))
            for error in preview.errors[:10]:
                st.error(error)
            if st.button('Import Into My Journal', disabled=bool(preview.errors) or not preview.count,
                         type='primary', key=common.key(journal, 'import-apply')):
                common.show_result(apply_preview(journal, preview))
                common.recalculate(journal)
        except (ValueError, UnicodeError) as exc:
            st.error(str(exc))
    manual_fill(journal)


def fills_preview(journal, text: str):
    """A CSV of fills: the file's time zone, its columns under their plain names, its first rows, and the preview."""
    headers, raw = csv_rows(text)
    zone = common.display_zone(journal)
    choices = common.timezone_choices(zone)
    timezone = st.selectbox('Time Zone of the File', choices, index=choices.index(zone), format_func=common.zone_label,
                            key=common.key(journal, 'import-tz'),
                            help='Times written without an offset, such as 10:00:23, are read in this zone.')
    guessed = mapping_for(headers)
    found = hashlib.sha256('\n'.join(headers).encode()).hexdigest()[:8]     # another file's columns: new boxes
    options = ['', *headers]
    with st.expander('Column Mapping', expanded=bool(missing_columns(guessed))):
        columns = st.columns(3)
        mapping = {name: columns[i % 3].selectbox(label, options, index=options.index(guessed[name]),
                                                  key=common.key(journal, f'map-{found}-{name}'))
                   for i, (name, label) in enumerate(LABELS.items())}
    st.dataframe(pd.DataFrame(raw).head(12), hide_index=True)
    return preview_csv(text, timezone=timezone, mapping=mapping)


def manual_fill(journal) -> None:
    """One fill typed in, its date and time read in the journal's Time Zone."""
    zone = common.display_zone(journal)
    with st.expander('Add One Fill by Hand'):
        # The suggested day and time are fixed once per visit: a default that moved with the clock would make the
        # boxes new widgets on every run, and what was typed into them would be dropped on submit.
        day_key, time_key = common.key(journal, 'manual-day'), common.key(journal, 'manual-time')
        now = humanize.to_zone(dt.datetime.now(dt.timezone.utc), zone)
        st.session_state.setdefault(day_key, now.date())
        st.session_state.setdefault(time_key, now.time().replace(second=0, microsecond=0))
        with st.form(common.key(journal, 'manual-fill')):
            c1, c2, c3 = st.columns(3)
            account = c1.text_input('Account', placeholder='SIM')
            symbol = c2.text_input('Contract', placeholder='ESZ6')
            side = c3.selectbox('Side', ['buy', 'sell'], format_func=str.title)
            c1, c2, c3 = st.columns(3)
            contracts = c1.number_input('Contracts', min_value=.0001, value=1.)
            price = c2.number_input('Price', value=0., format='%.4f')
            fees = c3.number_input('Fees', min_value=0., value=0.)
            c1, c2, _ = st.columns(3)
            day = c1.date_input('Date', key=day_key)
            clock = c2.time_input(f'Time · {zone}', key=time_key, step=60)
            if st.form_submit_button('Record Fill'):
                try:
                    if not account.strip() or not symbol.strip():
                        raise ValueError('Enter the account and the contract.')
                    fill = Execution('manual', account, symbol, side, contracts, price,
                                     timestamp(f'{day.isoformat()} {clock.isoformat()}', zone), commission=fees)
                    count = journal.ingest([(fill, {'entered_manually': True})])
                    common.recalculate(journal)
                    st.success(f'Recorded {count} new fill.' if count else 'That fill is already in the journal.')
                except ValueError as exc:
                    st.error(str(exc))
