"""Simple setup: make the automatic MotiveWave capture loop understandable at a glance.

The normal view deliberately contains no bridge paths or import controls.  A parent view can put
diagnostics or less common controls behind the collapsed Advanced setup section by passing its
renderer as ``advanced``.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import streamlit as st

import common
import theme
from views import connection


AdvancedRenderer = Callable[[Any, list[dict]], None]


STATUS_COPY = {
    'Connected': 'MotiveWave is sending data. Leave the journal open and trade normally.',
    'Waiting For Study': 'Waiting for the MotiveWave Journal study to start sending data.',
    'Stale': 'The study was connected but has gone quiet. Make sure MotiveWave and the chart study are running.',
    'No Heartbeat': 'Trade data was found, but the study heartbeat is missing. Capture may be incomplete.',
    'Paused': 'Automatic capture is paused. Open Advanced setup when you are ready to resume it.',
    'Needs Attention': 'Automatic capture needs attention. Open Advanced setup for diagnostics.',
}


def render(journal, rows: list[dict], *, advanced: AdvancedRenderer | None = None,
           show_status: bool = True) -> None:
    """Draw the zero-path MVP setup page.

    ``advanced`` receives ``(journal, rows)`` inside a collapsed expander.  This keeps the simple
    screen independent of whichever diagnostics/settings page the parent application chooses.
    """
    theme.section('Automatic capture', 'MotiveWave → Journal, with no export or import step')
    link = None
    if show_status:
        link = render_status(getattr(journal, 'capture_journal', journal))

    st.markdown(
        '### Start collecting trades\n'
        '1. Add **Journal → MotiveWave Journal** to the chart for the exact contract you trade.\n'
        '2. Take the trade in MotiveWave. Fills and observed ticks are sent to this journal automatically.\n'
        '3. When the position is flat, open **Trades** to review and tag the completed trade.'
    )
    if (link is None or link.state != 'Connected') and connection.JAR.is_file():
        if st.button('Install MotiveWave study', type='primary', key='simple-study-install'):
            try:
                target, _ = connection.install_study()
                st.success(f'Installed {target.name}. Restart MotiveWave, then add Journal → MotiveWave Journal to your chart.')
            except (OSError, ValueError) as exc:
                st.error('The study could not be installed: ' + str(exc))

    st.info(
        '**One trade means flat-to-flat for one provider, account, and exact contract.** '
        'The first fill that moves the position away from zero opens it. Scale-ins and partial exits stay '
        'inside that same trade. The fill that returns the position to zero closes it. A reversal closes the '
        'old trade at the crossing fill and opens a new one; other accounts or contracts remain separate.'
    )
    st.caption(
        'Replay works the same way and is kept in the separate Replay journal. MAE/MFE is tick-accurate only '
        'when the MotiveWave replay itself is running from real tick data.'
    )

    with st.expander('Advanced setup', expanded=False):
        if advanced is None:
            st.caption('The parent app can place diagnostics and uncommon capture controls here.')
        else:
            advanced(journal, rows)


def render_status(journal) -> None:
    """Render controlled status copy without exposing a bridge path or raw path-bearing error."""
    _, _, _, link = common.study_link(journal)
    note = STATUS_COPY.get(link.state, 'Waiting for the MotiveWave study to report its capture status.')
    theme.status(link.label, link.tone, link.state, notes=[(note, link.tone == 'critical')], big=True)
    return link

