"""Import: fills from a CSV with a column mapper, a bridge file by hand, your own prices, one manual fill.

Everything is previewed first; nothing reaches the journal until the whole file has parsed.
"""

from __future__ import annotations

import datetime as dt

import pandas as pd
import streamlit as st

import common
from mwjournal import Execution
from mwjournal.importers import (ALIASES, apply_preview, csv_rows, mapping_for, preview_bridge, preview_csv,
                                 preview_prices_csv)

FORMATS = {'executions': 'Execution CSV · map columns', 'bridge': 'Bridge file · NDJSON'}


def render(journal, rows) -> None:
    st.caption('For trades the study did not capture. One row per fill; an export of cumulative order totals is '
               'not a list of fills. Fills the study already captured must not be imported again from a CSV: '
               'the two sources cannot be recognised as the same trades.')
    mode = st.selectbox('Import format', list(FORMATS), format_func=FORMATS.get, key=common.key(journal, 'import-format'))
    timezone = st.text_input('Export timezone for dates without an offset', 'America/New_York',
                             key=common.key(journal, 'import-tz'))
    provider = st.text_input('Source identity', 'csv', key=common.key(journal, 'import-provider'),
                             help='Keep this the same across imports so repeated execution IDs are recognised.')
    upload = st.file_uploader('Executions / bridge events', type=['csv', 'txt', 'jsonl', 'ndjson'],
                              key=common.key(journal, 'import-file'))
    if upload:
        try:
            text = upload.getvalue().decode('utf-8-sig')
            if mode == 'bridge':
                preview = preview_bridge(text, timezone=timezone, dataset=journal.dataset)
            else:
                headers, raw = csv_rows(text)
                guessed = mapping_for(headers)
                with st.expander('Column mapping', expanded=True):
                    columns = st.columns(3)
                    mapping = {name: columns[i % 3].selectbox(name, [''] + headers,
                               index=([''] + headers).index(guessed.get(name, '')), key='map-' + name)
                               for i, name in enumerate(ALIASES)}
                st.dataframe(pd.DataFrame(raw).head(12), hide_index=True)
                st.session_state[common.MAPPING_KEY] = mapping   # offered again for a watched CSV
                preview = preview_csv(text, timezone=timezone, mapping=mapping, provider=provider)
            st.write(f'{preview.count} records ready · {preview.skipped} empty/unfilled events skipped')
            for error in preview.errors[:10]:
                st.error(error)
            if st.button('Import into my journal', disabled=bool(preview.errors) or not preview.count,
                         type='primary', key=common.key(journal, 'import-apply')):
                common.show_result(apply_preview(journal, preview))
                common.recalculate(journal)
        except (ValueError, UnicodeError) as exc:
            st.error(str(exc))
    with st.expander('Import my own OHLC or tick prices'):
        symbol = st.text_input('Traded instrument symbol', 'ESZ6', key=common.key(journal, 'prices-symbol'))
        source = st.text_input('Price source label', 'my-data', key=common.key(journal, 'prices-source'))
        resolution = st.selectbox('Price file resolution', [0, 1, 60, 300, 900], key=common.key(journal, 'prices-resolution'),
                                  format_func=lambda s: 'Ticks' if s == 0 else f'{s} second bars')
        proxy = st.checkbox('Price file uses a continuous contract/proxy', key=common.key(journal, 'prices-proxy'))
        st.caption('Prices must already be on the traded instrument\'s price scale. Never import ETF or '
                   'other-asset prices as futures prices.')
        prices_file = st.file_uploader('Prices CSV · timestamp, open/high/low/close + volume, or price ticks; '
                                       'optional bid/ask', type=['csv'], key=common.key(journal, 'prices-file'))
        if prices_file and st.button('Import prices and calculate', key=common.key(journal, 'prices-apply')):
            try:
                preview = preview_prices_csv(prices_file.getvalue().decode('utf-8-sig'), symbol, source,
                                             timezone=timezone, resolution_seconds=resolution, proxy=proxy)
                for error in preview.errors[:10]:
                    st.error(error)
                if not preview.errors:
                    common.show_result(apply_preview(journal, preview))
                    common.recalculate(journal)
            except (ValueError, UnicodeError) as exc:
                st.error(str(exc))
    with st.expander('Add a fill manually'):
        # The suggested time is fixed once per visit: a default that moved with the clock would make
        # the box a new widget on every run, and the time typed into it would be dropped on submit.
        stamp = common.key(journal, 'manual-ts')
        st.session_state.setdefault(stamp, dt.datetime.now(dt.timezone.utc).isoformat(timespec='seconds'))
        with st.form(common.key(journal, 'manual-fill')):
            c1, c2, c3 = st.columns(3)
            account = c1.text_input('Account', 'personal')
            symbol = c2.text_input('Symbol', 'ESZ6')
            side = c3.selectbox('Side', ['buy', 'sell'])
            c1, c2, c3 = st.columns(3)
            qty = c1.number_input('Quantity', min_value=.0001, value=1.)
            price = c2.number_input('Fill price', value=0., format='%.4f')
            fees = c3.number_input('Fill commission/fees', min_value=0., value=0.)
            ts = st.text_input('Fill timestamp with timezone', key=stamp)
            if st.form_submit_button('Record fill'):
                try:
                    fill = Execution('manual', account, symbol, side, qty, price, ts, commission=fees)
                    count = journal.ingest([(fill, {'entered_manually': True})])
                    common.recalculate(journal)
                    st.success(f'Recorded {count} new fill.' if count else 'That fill is already in the journal.')
                except ValueError as exc:
                    st.error(str(exc))
