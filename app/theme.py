"""The look of MotiveWave Journal: one set of tokens, one chart template and the few page components.

A Tradecraft-inspired workspace: charcoal planes, yellow navigation, green/red outcomes and
colour-coded tag groups. Signs, labels and selected outlines carry meaning alongside colour.
.streamlit/config.toml carries the same colours for Streamlit's own widgets.

One typeface everywhere: IBM Plex Sans, from the bundled files (mwjournal/fonts.py). Numbers that sit in
columns (amounts, times, prices, metric tiles) use its tabular figures so they line up.
"""

from __future__ import annotations

import hashlib
import html
import math
import re

import plotly.graph_objects as go
import plotly.io as pio
import streamlit as st

from mwjournal import fonts

# ---------------------------------------------------------------- tokens

PAGE = '#151617'          # page plane
SURFACE = '#1D1E1F'        # panels and the chart surface
SURFACE_2 = '#272829'      # raised rows, hover
SIDEBAR = '#111213'
HAIRLINE = '#292B2C'       # gridlines
BASELINE = '#414345'       # axes, zero lines
BORDER = 'rgba(255,255,255,0.12)'
INK = '#E8E8E5'            # primary text
INK_2 = '#BEC0BB'          # secondary text
MUTED = '#959A94'          # labels, axis text
ACCENT = '#E2CF32'         # chrome and emphasis only, never data
ACCENT_DEEP = '#49451E'    # fill behind the primary button
PRICE = '#FE9926'          # the last price on a chart's price axis
UP = '#45BF89'
DOWN = '#E16E6B'
SERIES = ['#60A9E8', '#ED9B55', '#45BF89', '#E2CF32', '#DA83B4', '#85BD72', '#AF92DF', '#E16E6B']
STATUS = {'good': UP, 'warning': '#E2CF32', 'critical': DOWN, 'neutral': MUTED}
TAG_COLOURS = {'Setup': '#65CA98', 'Execution': '#EBAB66', 'Mindset': '#BD9DE8', 'Day': '#E48DB9',
               'Other tags': '#73B6EB'}
# A trade's Rating, 5 stars green down to 1 star red, wherever Stats lists or narrows by it.
RATING_COLOURS = {5: UP, 4: SERIES[5], 3: SERIES[3], 2: SERIES[1], 1: DOWN}
FONT = fonts.STACK         # IBM Plex Sans, with system fallbacks only until the bundled files load
TABULAR = 'font-variant-numeric:tabular-nums'   # figures of one width, for numbers that line up
# A Trading Calendar cell's tint (tint() picks the step): the result colour at 8, 15 and 22% over the page, so a
# big day reads deeper than a small one and the month never turns garish.
TINTS = ('14', '26', '38')
TINT_CSS = ''.join(f'.mwj-cal.{tone}.l{level} {{ background:{colour}{alpha}; border-color:{colour}{alpha}; }}\n'
                   for tone, colour in (('up', UP), ('down', DOWN)) for level, alpha in enumerate(TINTS, 1))

# The browser tab's icon: the Mac app's icon (desktop/macos/Main.swift), three yellow candles on charcoal.
# Written into the page as an SVG, because a Material icon here makes the browser fetch it from Google.
ICON = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1024 1024">'
        f'<rect x="55" y="55" width="914" height="914" rx="210" fill="{PAGE}"/><g fill="{ACCENT}">'
        + ''.join(f'<rect x="{x}" y="{1024 - high}" width="98" height="{high - low}" rx="16"/>'
                  f'<rect x="{x + 44}" y="{1024 - high - 66}" width="10" height="{high - low + 132}" rx="5"/>'
                  for x, low, high in ((260, 250, 520), (440, 370, 680), (620, 500, 800)))
        + '</g></svg>')

# ---------------------------------------------------------------- page chrome

CSS = f"""
<style>
html, body, [data-testid="stAppViewContainer"], .stApp {{ background:{PAGE}; font-family:{FONT}; }}
/* The header is kept (it holds the button that reopens a collapsed sidebar) but made invisible and
   click-through; only its menu, deploy and status widgets are hidden. */
[data-testid="stHeader"] {{ background:transparent; pointer-events:none; }}
[data-testid="stHeader"] [data-testid="stExpandSidebarButton"] {{ pointer-events:auto; }}
[data-testid="stToolbarActions"], [data-testid="stMainMenu"], [data-testid="stAppDeployButton"],
[data-testid="stStatusWidget"], [data-testid="stDecoration"], footer {{ display:none !important; }}
/* While the sidebar is collapsed, leave room for the reopen button above the page. */
.stApp:has([data-testid="stExpandSidebarButton"]) .block-container {{ padding-top:3.4rem !important; }}
[data-testid="stExpandSidebarButton"] {{ background:{SURFACE_2} !important; border:1px solid {ACCENT} !important;
    border-radius:6px !important; color:{INK} !important; }}
/* The connection status refreshes every few seconds: never dim the page while it does. */
[data-stale="true"], .stale-element, [data-testid="stElementContainer"][data-stale] {{ opacity:1 !important;
    transition:none !important; filter:none !important; }}
.block-container {{ padding:0.8rem 1.6rem 3rem 1.6rem !important; max-width:none !important; }}
/* An element that only carries a stylesheet takes no room. Without this each one adds a row gap at the top of
   the page, and the trade list starts lower than it needs to. Its styles apply all the same. */
[data-testid="stElementContainer"]:has([data-testid="stMarkdownContainer"] > style:only-child) {{ display:none; }}
[data-testid="stSidebar"] {{ background:{SIDEBAR}; border-right:1px solid {BORDER}; }}
[data-testid="stSidebar"] {{ min-width:230px !important; max-width:230px !important; }}
[data-testid="stSidebarHeader"] {{ height:32px; min-height:32px; }}
[data-testid="stSidebarUserContent"] {{ padding-bottom:16px; }}
[data-testid="stSidebarUserContent"] > [data-testid="stVerticalBlock"] {{ gap:8px; }}
[data-testid="stSidebar"] [data-testid="stButtonGroup"] {{ width:100%; }}
[data-testid="stSidebar"] [data-testid="stButtonGroup"] > div {{ flex-direction:column; gap:2px; }}
[data-testid="stSidebar"] [data-testid="stButtonGroup"] button {{ width:100%; min-width:160px; justify-content:flex-start;
    border:0; background:transparent; padding:6px 10px; min-height:30px; font-family:{FONT}; }}
/* The Trading Calendar: each day and each week's total is a cell tinted by its result; an invisible button lies
   over a day's cell and opens it. */
[class*="st-key-journal-calendar"] [data-testid="stVerticalBlock"] {{ gap:4px; }}
[class*="st-key-journal-calendar"] [data-testid="stHorizontalBlock"] {{ gap:4px; }}
[class*="st-key-journal-calendar"] [data-testid="stMarkdownContainer"] {{ margin-bottom:0 !important; }}
[class*="st-key-calendar-day-"] {{ position:relative; gap:0 !important; }}
[class*="st-key-calendar-day-"] [data-testid="stElementContainer"]:has(.stButton) {{ position:absolute !important;
    inset:0; z-index:2; margin:0 !important; }}
[class*="st-key-calendar-day-"] .stButton, [class*="st-key-calendar-day-"] .stButton button {{ width:100%; height:100%;
    min-height:0 !important; padding:0; opacity:0; cursor:pointer; }}
/* The button sits inside its tooltip's wrappers: they fill the cell too, so all of the cell opens the day. */
[class*="st-key-calendar-day-"] .stButton :has(button) {{ height:100%; }}
.mwj-cal {{ display:flex; flex-direction:column; height:50px; padding:3px; border:1px solid {HAIRLINE};
    border-radius:4px; background:{SURFACE}; font-family:{FONT}; font-size:10.5px; line-height:13px; {TABULAR};
    color:{INK_2}; white-space:nowrap; overflow:hidden; container-type:inline-size; }}
/* In a narrow cell the amount and the count get smaller with it (cqi: a hundredth of the cell's width), down to
   a size still read easily; past that they end in '…', and pointing at the cell shows the whole amount. */
.mwj-cal > span {{ overflow:hidden; text-overflow:ellipsis; }}
.mwj-cal .n {{ font-size:12px; font-size:clamp(9.5px, 22cqi, 12px); line-height:16px; font-weight:650; }}
.mwj-cal .k {{ font-size:10px; font-size:clamp(8.5px, 22cqi, 10px); color:{MUTED}; }}
{TINT_CSS}[class*="st-key-calendar-day-"]:hover .mwj-cal {{ border-color:{ACCENT}; }}
[class*="st-key-calendar-day-"]:has(button:focus-visible) .mwj-cal {{ outline:2px solid {ACCENT}; }}
.mwj-cal-month {{ text-align:right; font-family:{FONT}; font-size:12px; color:{MUTED}; {TABULAR}; white-space:nowrap; }}
.mwj-cal-month .n {{ font-size:15px; font-weight:650; color:{INK_2}; margin-right:6px; }}
:is(.mwj-cal, .mwj-cal-month).up .n {{ color:{UP}; }}
:is(.mwj-cal, .mwj-cal-month).down .n {{ color:{DOWN}; }}
[data-testid="stSidebar"] button[aria-checked="true"] {{ background:{ACCENT_DEEP} !important;
    border-color:{ACCENT} !important; color:{ACCENT} !important; }}
[data-testid="stSidebar"] button[aria-checked="true"] p {{ color:inherit !important; }}
h1, h2, h3 {{ font-family:{FONT}; letter-spacing:-0.01em; }}
h3 {{ font-size:15px !important; font-weight:600 !important; color:{INK} !important; }}
[data-testid="stMarkdownContainer"] p {{ color:{INK_2}; }}
[data-testid="stCaptionContainer"] {{ color:{MUTED} !important; }}
[data-testid="stMetricValue"] {{ font-family:{FONT}; font-size:25px; {TABULAR}; }}
[data-testid="stMetricDelta"] {{ {TABULAR}; }}
[data-testid="stTabs"] [data-baseweb="tab-list"] {{ gap:18px; }}
[data-testid="stTabs"] [data-baseweb="tab"] {{ font-family:{FONT}; font-size:13px; padding:8px 0; }}
.st-key-trade-review-workspace, .st-key-trade-review-workspace [data-testid="stVerticalBlock"] {{ gap:8px; }}
/* A metric's value is drawn as Markdown, so its paragraph needs the colour too: the rule for Markdown
   paragraphs above would otherwise turn every result grey. */
.st-key-trade-result-positive [data-testid="stMetricValue"], .st-key-trade-result-positive [data-testid="stMetricValue"] p {{ color:{UP}; }}
.st-key-trade-result-negative [data-testid="stMetricValue"], .st-key-trade-result-negative [data-testid="stMetricValue"] p {{ color:{DOWN}; }}
/* The connection badge in the sidebar is one button: an invisible button lies over the chip and opens Settings. */
.st-key-connection-badge {{ position:relative; gap:0 !important; border-radius:4px; }}
.st-key-connection-badge [data-testid="stElementContainer"]:has(.stButton) {{ position:absolute !important; inset:0;
    z-index:2; margin:0 !important; }}
.st-key-connection-badge .stButton, .st-key-connection-badge .stButton button {{ width:100%; height:100%;
    min-height:0 !important; padding:0; opacity:0; cursor:pointer; }}
.st-key-connection-badge:hover .mwj-go {{ color:{ACCENT}; }}
.st-key-connection-badge:has(button:focus-visible) {{ outline:2px solid {ACCENT}; outline-offset:2px; }}
.mwj-go {{ color:{MUTED}; font-size:12px; margin-left:8px; white-space:nowrap; }}
/* Headline numbers (Stats, the Daily Report Card, dashboard summaries): results carry their colour. */
.st-key-stat-tiles [data-testid="stMetricValue"], .st-key-day-tiles [data-testid="stMetricValue"] {{ font-size:22px; }}
[class*="st-key-tile-"][class*="-positive"] [data-testid="stMetricValue"],
[class*="st-key-tile-"][class*="-positive"] [data-testid="stMetricValue"] p {{ color:{UP}; }}
[class*="st-key-tile-"][class*="-negative"] [data-testid="stMetricValue"],
[class*="st-key-tile-"][class*="-negative"] [data-testid="stMetricValue"] p {{ color:{DOWN}; }}
.st-key-cost-tiles [data-testid="stMetricValue"] {{ font-size:17px; }}
.st-key-cost-tiles [data-testid="stMetricLabel"] p {{ font-size:12px; }}
/* The three pages under Settings: a tile each, its title a link, the tabs it opens small links under it. */
[class*="st-key-setup-page-"] {{ gap:4px !important; }}
[class*="st-key-setup-page-"] [data-testid="stVerticalBlock"] {{ gap:2px; }}
[class*="st-key-setup-page-"] [data-testid="stHorizontalBlock"] {{ gap:4px 14px !important; }}
[class*="st-key-setup-page-"] .stButton button {{ padding:0 !important; min-height:0 !important; justify-content:flex-start; }}
[class*="st-key-setup-open-"] button p {{ font-size:14.5px; font-weight:650; color:{INK} !important; }}
[class*="st-key-setup-open-"] button:hover p {{ color:{ACCENT} !important; }}
[class*="st-key-setup-tab-"] button p {{ font-size:12.5px; color:{INK_2} !important; }}
[class*="st-key-setup-tab-"] button:hover p {{ color:{ACCENT} !important; }}
.st-key-setup-fine-print [data-testid="stCaptionContainer"], .st-key-setup-fine-print p {{ font-size:11px !important;
    line-height:1.45; }}
.mwj-subsection {{ margin:10px 0 2px; color:{MUTED}; font-family:{FONT}; font-size:11px; font-weight:600;
                  letter-spacing:.1em; text-transform:uppercase; }}
div[data-testid="stExpander"] details {{ background:{SURFACE}; border:1px solid {BORDER}; border-radius:6px; }}
/* Inside a menu (Filters → More Filters) a folding section keeps the menu's own background: on the
   panel colour its text boxes and pickers, which are that colour too, would have no visible edge. */
[data-testid="stPopoverBody"] div[data-testid="stExpander"] details {{ background:{PAGE}; }}
div[role="radiogroup"] label p {{ font-size:13px; }}
[data-testid="stButtonGroup"] button {{ font-family:{FONT}; font-size:12.5px; border-radius:4px; {TABULAR}; }}
.stButton button, .stFormSubmitButton button, .stDownloadButton button, .stLinkButton a {{ border-radius:4px; }}
[data-testid="stBaseButton-primary"], [data-testid="stBaseButton-primaryFormSubmit"] {{
    background:{ACCENT} !important; border:1px solid {ACCENT} !important; color:{SIDEBAR} !important; }}
[data-testid="stBaseButton-primary"] p, [data-testid="stBaseButton-primaryFormSubmit"] p {{ color:inherit !important; }}
[data-testid="stBaseButton-primary"]:hover, [data-testid="stBaseButton-primaryFormSubmit"]:hover {{
    background:#F0DD4B !important; }}
input, textarea {{ font-family:{FONT} !important; }}
input {{ {TABULAR}; }}
/* Plotly's own boxes default to Open Sans; its axis numbers line up like a chart platform's price axis. */
.js-plotly-plot .plotly, .js-plotly-plot .plotly div, .plotly-notifier {{ font-family:{FONT} !important; }}
.js-plotly-plot .plotly text {{ {TABULAR} !important; }}
.mwj-brand {{ font-family:{FONT}; font-size:12px; font-weight:700; letter-spacing:.06em; text-transform:uppercase;
             color:{ACCENT}; margin:2px 0 0; }}
.mwj-brand-sub {{ color:{MUTED}; font-size:12px; margin:2px 0 10px; }}
.mwj-section {{ display:flex; align-items:center; gap:10px; margin:18px 0 8px; color:{INK}; font-family:{FONT};
               font-size:12px; font-weight:700; letter-spacing:.12em; text-transform:uppercase; }}
.mwj-section:before {{ content:''; width:3px; height:12px; background:{ACCENT}; }}
.mwj-section:after {{ content:''; flex:1; height:1px; background:{BORDER}; }}
.mwj-section .hint {{ font-family:{FONT}; text-transform:none; letter-spacing:0; font-weight:400; color:{MUTED};
                     font-size:12.5px; }}
.mwj-chip {{ display:inline-flex; align-items:center; gap:7px; padding:2px 9px; border-radius:3px;
            font-family:{FONT}; font-size:11.5px; font-weight:650; letter-spacing:.04em; white-space:nowrap; }}
.mwj-chip:before {{ content:''; width:7px; height:7px; border-radius:50%; background:currentColor; }}
.mwj-chip.big {{ font-size:14px; padding:5px 12px; }}
.mwj-status-note {{ color:{INK_2}; font-size:12.5px; line-height:1.45; margin:6px 0 0; }}
.mwj-status-note.warn {{ color:{STATUS['warning']}; }}
</style>
"""


def apply() -> None:
    """Page-wide CSS and the chart template. Call once from the entry script."""
    st.markdown(CSS, unsafe_allow_html=True)
    register_template()


def tag_group_key(group: str) -> str:
    """CSS-safe, stable and independent of labels supplied by the user."""
    return 'tag-colour-' + hashlib.sha256(group.encode()).hexdigest()[:12]


def tag_group_css(groups) -> str:
    rules = []
    for group in groups:
        colour = TAG_COLOURS.get(group)
        if colour is None:
            colour = SERIES[int(hashlib.sha256(group.encode()).hexdigest()[:8], 16) % len(SERIES)]
        selector = '.st-key-' + tag_group_key(group)
        rules.append(f'{selector} button[aria-pressed="true"] {{background:{colour} !important; '
                     f'border-color:{colour} !important; color:{SIDEBAR} !important; font-weight:600;}} '
                     f'{selector} button[aria-pressed="false"]:hover {{border-color:{colour} !important;}}')
    return '<style>' + ''.join(rules) + '</style>'


def ink_on(colour) -> str | None:
    """The text colour that reads best on a tag's colour, dark or light; None for anything but a six-digit
    hex colour, so a stored value never reaches a style unchecked."""
    if not isinstance(colour, str) or not re.fullmatch(r'#[0-9a-fA-F]{6}', colour):
        return None
    def luminance(hex_color):
        rgb = [int(hex_color[i:i + 2], 16) / 255 for i in (1, 3, 5)]
        linear = [v / 12.92 if v <= .04045 else ((v + .055) / 1.055) ** 2.4 for v in rgb]
        return sum(v * weight for v, weight in zip(linear, (.2126, .7152, .0722)))
    background = luminance(colour)
    dark = (background + .05) / (luminance(SIDEBAR) + .05)
    light = (luminance(INK) + .05) / (background + .05)
    return SIDEBAR if dark >= light else INK


def tag_colors_css(groups):
    """Per-tag colors scoped by safe group hashes, never by unescaped user labels."""
    rules = []
    for group, tags in groups.items():
        for index, tag in enumerate(tags, 1):
            color = tag['color']
            text_color = ink_on(color)
            if text_color is None:
                continue
            selector = f'.st-key-{tag_group_key(group)} [data-testid="stButtonGroup"] button:nth-of-type({index})'
            rules.append(f'{selector}[aria-pressed="true"],{selector}[aria-checked="true"] '
                         f'{{background:{color} !important;border-color:{color} !important;color:{text_color} !important;}} '
                         f'{selector}:hover {{border-color:{color} !important;}}')
    return '<style>' + ''.join(rules) + '</style>'


def outcome_style(row):
    """Tint closed results without treating an open position or unknown P&L as a win/loss."""
    net = row['Net P&L']
    if row['Status'] != 'closed' or net is None or net != net:
        return [''] * len(row)
    colour, background = (UP, '#233D31') if net > 0 else (DOWN, '#402A29') if net < 0 else (ACCENT, '#393720')
    return [f'background-color: {background}; color: {colour}'] * len(row)


def tint(value, biggest) -> str:
    """A calendar cell's classes: 'up' or 'down' by the result's sign, and a step 'l1' to 'l3' by its size against
    `biggest`, the largest result of its kind that month. No result or break-even: '' (the cell stays neutral)."""
    if not value or not biggest:
        return ''
    return f"{'up' if value > 0 else 'down'} l{min(len(TINTS), math.ceil(len(TINTS) * abs(value) / biggest))}"


def brand(name: str, subtitle: str = '') -> None:
    st.markdown(f"<div class='mwj-brand'>{html.escape(name)}</div>"
                + (f"<div class='mwj-brand-sub'>{html.escape(subtitle)}</div>" if subtitle else ''),
                unsafe_allow_html=True)


def section(title: str, hint: str = '', *, help: str | None = None) -> None:
    """A section title. `hint` is a few words beside it; anything longer belongs in `help`, the ?."""
    st.markdown(f"<div class='mwj-section'>{html.escape(title)}"
                + (f"<span class='hint'>{html.escape(hint)}</span>" if hint else '') + '</div>',
                unsafe_allow_html=True, help=help)


def subsection(title: str, *, help: str | None = None) -> None:
    """A small plain heading inside a section, such as Advanced: never a menu that folds."""
    st.markdown(f"<div class='mwj-subsection'>{html.escape(title)}</div>", unsafe_allow_html=True, help=help)


def chip(text: str, tone: str = 'neutral', *, state: str | None = None, big: bool = False) -> str:
    """A status word with its dot. The word carries the meaning; the colour only repeats it."""
    colour = STATUS.get(tone, MUTED)
    return (f"<span class='mwj-chip{' big' if big else ''}' data-state='{html.escape(state or text, quote=True)}' "
            f"style='background:{colour}26;color:{colour};border:1px solid {colour}55'>{html.escape(text)}</span>")


def status(label: str, tone: str, state: str, notes=(), *, big: bool = False, help: str | None = None) -> None:
    """A status chip and the lines under it, as one element however many lines there are.

    The status is drawn by fragments that redraw themselves every few seconds. A fragment that draws
    fewer elements than last time can leave the old ones on screen (seen in the sidebar: 'Recording'
    above the note of the state before it), so what varies is kept inside a single element.
    `notes` are (text, is_warning) pairs; `help` is the ? beside it.
    """
    st.markdown(chip(label, tone, state=state, big=big) + _notes(notes), unsafe_allow_html=True, help=help)


def notes(lines) -> None:
    """(text, is_warning) lines as one element, drawn as the lines under a status are."""
    st.markdown(_notes(lines), unsafe_allow_html=True)


def _notes(lines) -> str:
    return ''.join(f"<div class='mwj-status-note{' warn' if warn else ''}'>{html.escape(text)}</div>"
                   for text, warn in lines)


# ---------------------------------------------------------------- charts

# Plotly charts behave like a trading platform: scroll to zoom, drag to pan, double-click to reset,
# no toolbar. (The trade chart itself is drawn by tvchart, not Plotly.)
PLOTLY_CONFIG = {'displayModeBar': False, 'scrollZoom': True, 'doubleClick': 'reset'}
TEMPLATE = 'mwjournal'


def money_axis(figure, currency='USD', values=(), *, axis='yaxis') -> None:
    """Axis numbers written as money, '$1,200' and '-$300', like every amount in the journal.
    Small ranges keep their cents so the ticks do not repeat."""
    from mwjournal import humanize
    finite = [abs(v) for v in values if v is not None and v == v]
    decimals = 0 if finite and max(finite) >= 20 else 2
    sign = humanize.symbol(currency)
    if sign == '$':
        # d3's own currency format puts the minus before the dollar sign.
        figure.update_layout({axis: dict(tickformat=f'$,.{decimals}f', hoverformat='$,.2f')})
    else:
        figure.update_layout({axis: dict(tickprefix=sign, tickformat=f',.{decimals}f', hoverformat=',.2f')})


def register_template() -> None:
    if TEMPLATE not in pio.templates:
        # Every text in IBM Plex Sans; theme.CSS gives the axis numbers tabular figures.
        axis = dict(gridcolor=HAIRLINE, gridwidth=1, linecolor=BASELINE, zerolinecolor=BASELINE, zerolinewidth=1,
                    tickfont=dict(family=FONT, color=MUTED, size=10.5),
                    title=dict(font=dict(family=FONT, color=MUTED, size=11)),
                    showspikes=True, spikecolor=BASELINE, spikethickness=1, spikedash='solid', spikemode='across')
        pio.templates[TEMPLATE] = go.layout.Template(layout=dict(
            font=dict(family=FONT, color=INK_2, size=12),
            paper_bgcolor='rgba(0,0,0,0)', plot_bgcolor='rgba(0,0,0,0)', colorway=SERIES,
            xaxis={**axis, 'showgrid': False}, yaxis=axis, hovermode='x unified', dragmode='pan',
            hoverlabel=dict(bgcolor=PAGE, bordercolor=BASELINE, font=dict(family=FONT, color=INK, size=11.5)),
            legend=dict(orientation='h', x=0, y=1.02, yanchor='bottom', font=dict(family=FONT, color=INK_2, size=11),
                        title=dict(font=dict(family=FONT, color=INK_2, size=11)), bgcolor='rgba(0,0,0,0)'),
            margin=dict(l=10, r=10, t=36, b=10),
            title=dict(font=dict(color=INK, size=12.5, family=FONT), x=0, xanchor='left', y=0.98),
        ), data=dict(scatter=[go.Scatter(line=dict(width=2))]))
    # The dark base and ours, merged once: a default named 'plotly_dark+mwjournal' is merged again for every
    # figure, some 16 ms a chart, nearly what a 1,000-trade equity curve takes to build.
    if TEMPLATE + '-dark' not in pio.templates:
        pio.templates[TEMPLATE + '-dark'] = pio.templates['plotly_dark+' + TEMPLATE]
    pio.templates.default = TEMPLATE + '-dark'
