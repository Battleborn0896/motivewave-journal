"""Data, a Settings page: everything about where trades come from and where they go, one tab each.

Import Trades, Export & Backup (one zip that backs up the whole journal and puts it back, and this
environment's trades as a CSV), Journals & Accounts, and Connection (what the study reports, installing it
again, and troubleshooting). Only the open tab is drawn: reading a chosen backup
is not free. Settings links each tab from its tile, and common.navigation opens any of them by its old name.
"""

from __future__ import annotations

import common
import theme
from views import connection, export, importing, journal_settings

TABS = ('Import Trades', 'Export & Backup', 'Journals & Accounts', 'Connection')
VIEWS = dict(zip(TABS, (importing, export, journal_settings, connection)))


def render(journal, rows, *, capture=None) -> None:
    """`capture` is the environment's own journal when a named journal is open: the connection belongs to it."""
    theme.section('Data', 'imports, backups, journals and the MotiveWave connection')
    # Per environment, not per named journal: creating or choosing a journal on Journals & Accounts switches
    # the journal on screen, and the page stays on that tab.
    for label, panel in zip(TABS, common.tabs(TABS, f'data-tabs-{journal.dataset}')):
        if panel.open:
            with panel:
                if label == 'Connection' and capture is not None:
                    connection.render(capture, common.rows(capture))
                else:
                    VIEWS[label].render(journal, rows)
