"""Data, a Settings page: everything about where trades come from and where they go, one tab each.

Import Trades, Export & Backup (one zip that backs up the whole journal and puts it back, and this
environment's trades as a CSV), Journals & Accounts (the views of the Journal menu, and account names), and
Connection (what the study reports, installing it again, and troubleshooting). Only the open tab is drawn: reading
a chosen backup is not free. Settings links each tab from its tile, and common.navigation opens any of them by its
old name.
"""

from __future__ import annotations

import common
import theme
from views import connection, export, importing, journal_settings

TABS = ('Import Trades', 'Export & Backup', 'Journals & Accounts', 'Connection')
VIEWS = dict(zip(TABS, (importing, export, journal_settings, connection)))


def render(journal, rows) -> None:
    theme.section('Data', 'imports, backups, views and the MotiveWave connection')
    # Per environment: making a view on Journals & Accounts shows it in the sidebar, and the page stays on that tab.
    for label, panel in zip(TABS, common.tabs(TABS, f'data-tabs-{journal.dataset}')):
        if panel.open:
            with panel:
                VIEWS[label].render(journal, rows)
