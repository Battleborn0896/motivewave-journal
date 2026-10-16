"""TradingView-style charts for any time series: scroll to zoom, drag to pan, the price scale fits
what is on screen, a crosshair with labels, levels as price lines on the axis.

Drawn by TradingView's Lightweight Charts (components/tvchart, Apache-2.0, bundled so nothing loads
from the internet). The chart persists across Streamlit reruns: live data updates in place and a
view the user has zoomed or panned is kept until they double-click.

    tvchart.show([tvchart.candles(bars), ...], key='prep-ES', intraday=True,
                 lines=[tvchart.level(7750, 'Prior day high', colour)])

Times: intraday data is drawn in New York time (Lightweight Charts shows UTC, so New York wall-clock
times are passed as if they were UTC); daily data is passed as dates.
"""

from __future__ import annotations

import math
from pathlib import Path

import pandas as pd
import streamlit.components.v1 as components

import theme

_COMPONENT = components.declare_component('tvchart', path=str(Path(__file__).resolve().parent / 'components' / 'tvchart'))
NY = 'America/New_York'


def _theme() -> dict:
    return {'surface': theme.SURFACE, 'grid': theme.HAIRLINE, 'border': theme.BASELINE, 'muted': theme.MUTED,
            'ink': theme.INK, 'ink2': theme.INK_2, 'crosshair': theme.MUTED, 'label': theme.SURFACE_2,
            'up': theme.UP, 'down': theme.DOWN, 'price': theme.PRICE, 'mono': theme.MONO,
            # Candles wear TradingView's own colours, so a chart reads like the one traders know.
            'candleUp': '#089981', 'candleDown': '#F23645',
            'watermark': 'rgba(255,255,255,0.045)'}


def times(index, intraday: bool) -> list:
    idx = pd.DatetimeIndex(index)
    if intraday:
        if idx.tz is not None:
            idx = idx.tz_convert(NY).tz_localize(None)
        return (idx.as_unit('s').asi8).tolist()
    return [d.strftime('%Y-%m-%d') for d in idx]


def _clean(s: pd.Series) -> pd.Series:
    s = s.dropna()
    s = s[~s.index.duplicated(keep='last')].sort_index()
    return s[[math.isfinite(float(v)) for v in s.values]] if len(s) else s


def spacing_days(s: pd.Series) -> float:
    idx = pd.DatetimeIndex(s.dropna().index)
    return pd.Series(idx).diff().median() / pd.Timedelta(days=1) if len(idx) > 2 else 1.0


def sparse(s: pd.Series) -> bool:
    """Quarterly or slower: each real point gets a marker, so the eye never reads a trend between two
    releases months apart as data."""
    return spacing_days(s) >= 60


def line(name: str, s: pd.Series, colour: str, intraday: bool = False, width: float = 2, style: str = 'solid',
         colours: pd.Series | None = None, label: str = '', fmt: dict | None = None, scale: str = 'right',
         curved: bool = False, in_legend: bool = True) -> dict:
    """A line; `colours` (same index) paints each point, for a line that changes colour by state."""
    s = _clean(s)
    data = [{'time': t, 'value': float(v)} for t, v in zip(times(s.index, intraday), s.values)]
    if colours is not None:
        c = colours.reindex(s.index)
        for point, colour_at in zip(data, c.values):
            if isinstance(colour_at, str):
                point['color'] = colour_at
    return {'type': 'line', 'name': name, 'color': colour, 'width': width, 'style': style, 'data': data,
            'label': label, 'format': fmt, 'scale': scale, 'curved': curved, 'legend': in_legend,
            'lastValue': in_legend,                   # background lines carry no axis tag of their own
            'points': not intraday and sparse(s)}


def area(name: str, s: pd.Series, colour: str, intraday: bool = False, fmt: dict | None = None,
         width: float = 2) -> dict:
    s = _clean(s)
    return {'type': 'area', 'name': name, 'color': colour, 'format': fmt, 'width': width,
            'points': not intraday and sparse(s),
            'data': [{'time': t, 'value': float(v)} for t, v in zip(times(s.index, intraday), s.values)]}


def histogram(name: str, s: pd.Series, colour: str, intraday: bool = False, colours: pd.Series | None = None,
              fmt: dict | None = None) -> dict:
    s = _clean(s)
    data = [{'time': t, 'value': float(v)} for t, v in zip(times(s.index, intraday), s.values)]
    if colours is not None:
        for point, c in zip(data, colours.reindex(s.index).values):
            if isinstance(c, str):
                point['color'] = c
    return {'type': 'histogram', 'name': name, 'color': colour, 'data': data, 'format': fmt}


def candles(bars: pd.DataFrame, name: str = '', intraday: bool = True, volume: bool = True) -> dict:
    """Candlesticks, with the volume band underneath when the bars carry a Volume column."""
    cols = ['Open', 'High', 'Low', 'Close'] + (['Volume'] if volume and 'Volume' in bars else [])
    b = bars[cols].dropna(subset=['Open', 'High', 'Low', 'Close'])
    b = b[~b.index.duplicated(keep='last')].sort_index()
    has_volume = bool('Volume' in b and b['Volume'].fillna(0).sum() > 0)
    data = []
    for i, t in enumerate(times(b.index, intraday)):
        row = b.iloc[i]
        point = {'time': t, 'open': float(row.Open), 'high': float(row.High), 'low': float(row.Low),
                 'close': float(row.Close)}
        if has_volume and row.Volume == row.Volume:
            point['volume'] = float(row.Volume)
        data.append(point)
    return {'type': 'candles', 'name': name, 'data': data, 'volume': has_volume}


def level(price: float, title: str, colour: str, style: str = 'solid', width: int = 1, axis_label: bool = True) -> dict:
    return {'price': float(price), 'title': title, 'color': colour, 'style': style, 'width': width, 'axisLabel': axis_label}


def show(series: list[dict], *, key: str, height: int = 320, title: str = '', intraday: bool = False,
         precision: int = 2, suffix: str = '', prefix: str = '', watermark: str | None = None,
         lines: list[dict] | None = None, initial_bars: int | None = None, legend: bool = True,
         initial_from: str | None = None, timezone: str | None = None,
         trade_overlays: dict | None = None, visible_range: dict | None = None) -> None:
    """Draw the chart. `lines` are price lines on the first series (levels, a zero line)."""
    series = [s for s in series if s.get('data')]
    if not series:
        return
    if lines:
        series[0] = {**series[0], 'lines': (series[0].get('lines') or []) + lines}
    spec = {'series': series, 'height': height, 'title': title, 'intraday': intraday, 'watermark': watermark,
            'format': {'precision': precision, 'suffix': suffix, 'prefix': prefix}, 'initialBars': initial_bars,
            'initialFrom': initial_from,
            # Opt-in UTC chart payloads, leaving legacy wall-clock charts unchanged.
            'timezone': timezone, 'tradeOverlays': trade_overlays, 'visibleRange': visible_range,
            'legend': legend, 'theme': _theme()}
    _COMPONENT(spec=spec, key=key, default=None)
