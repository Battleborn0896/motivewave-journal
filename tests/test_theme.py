"""Theme consistency and safe visual classification, without browser or account data."""
from pathlib import Path
import re
import tomllib

import pandas as pd

import theme
import tvchart


def test_native_chart_and_widgets_share_the_charcoal_palette():
    root = Path(__file__).resolve().parent.parent
    settings = tomllib.loads((root / '.streamlit' / 'config.toml').read_text())['theme']
    assert settings['primaryColor'] == theme.ACCENT
    assert settings['backgroundColor'] == theme.PAGE
    assert settings['secondaryBackgroundColor'] == theme.SURFACE
    assert settings['textColor'] == theme.INK
    assert tvchart._theme()['surface'] == theme.SURFACE
    assert tvchart._theme()['grid'] == theme.HAIRLINE
    native = (root / 'desktop' / 'macos' / 'Main.swift').read_text()
    assert 'background:' + theme.PAGE in native
    assert 'color:' + theme.INK in native


def test_tag_colour_selectors_are_stable_and_do_not_interpolate_custom_names():
    malicious_label = '</style><script>not-code</script>'
    key = theme.tag_group_key(malicious_label)
    assert re.fullmatch(r'tag-colour-[0-9a-f]{12}', key)
    assert key == theme.tag_group_key(malicious_label)
    css = theme.tag_group_css(['Setup', 'Mindset', malicious_label])
    assert malicious_label not in css
    assert '<script>' not in css
    assert key in css
    assert theme.TAG_COLOURS['Setup'] in css and theme.TAG_COLOURS['Mindset'] in css
    assert 'aria-pressed="true"' in css
    assert theme.tag_group_key('Setup') != theme.tag_group_key('setup')


def test_only_known_closed_results_receive_outcome_tints():
    def row(net, status='closed'):
        return pd.Series({'Contract': 'ESZ6', 'Status': status, 'Net P&L': net})
    assert all(theme.UP in s for s in theme.outcome_style(row(100)))
    assert all(theme.DOWN in s for s in theme.outcome_style(row(-100)))
    assert all(theme.ACCENT in s for s in theme.outcome_style(row(0)))
    for result in (row(None), row(float('nan')), row(0, 'open'), row(-100, 'open')):
        assert theme.outcome_style(result) == ['', '', '']


def test_money_axes_read_like_every_other_amount():
    import plotly.graph_objects as go
    figure = go.Figure()
    theme.money_axis(figure, 'USD', [-300, 1200])
    assert figure.layout.yaxis.tickformat == '$,.0f' and figure.layout.yaxis.hoverformat == '$,.2f'
    theme.money_axis(figure, 'USD', [3.5, -2], axis='xaxis')          # a small range keeps its cents
    assert figure.layout.xaxis.tickformat == '$,.2f'
    euro = go.Figure()
    theme.money_axis(euro, 'EUR', [100])
    assert euro.layout.yaxis.tickprefix == '€' and euro.layout.yaxis.tickformat == ',.0f'


def test_a_section_title_keeps_long_explanations_in_its_tooltip(monkeypatch):
    drawn = []
    monkeypatch.setattr(theme.st, 'markdown', lambda body, **kwargs: drawn.append((body, kwargs)))
    theme.section('Import Trades', help='For trades the study did not capture.')
    body, kwargs = drawn[0]
    assert 'Import Trades' in body and 'did not capture' not in body
    assert kwargs['help'] == 'For trades the study did not capture.' and kwargs['unsafe_allow_html']


def test_result_colours_reach_the_paragraph_a_metric_value_is_drawn_in():
    # Streamlit draws a metric's value as Markdown, and every Markdown paragraph is grey: a colour set
    # only on the value's box never shows. Seen in a browser: a red '-$88.10' tile read grey.
    css = re.sub(r'\s+', ' ', theme.CSS)
    for selector in ('.st-key-trade-result-positive [data-testid="stMetricValue"] p',
                     '.st-key-trade-result-negative [data-testid="stMetricValue"] p',
                     '[class*="st-key-tile-"][class*="-positive"] [data-testid="stMetricValue"] p',
                     '[class*="st-key-tile-"][class*="-negative"] [data-testid="stMetricValue"] p'):
        rule = css[css.index(selector):].split('}', 1)[0]
        assert ('color:' + theme.UP) in rule or ('color:' + theme.DOWN) in rule


def test_a_folding_section_inside_a_menu_keeps_the_menus_background_so_its_boxes_show():
    # Text boxes and pickers are drawn in the panel colour. Seen in a browser in 0.4.5: inside Filters → Save a
    # Filter, on the panel colour of a folding section, the Filter name box had no visible edge at all.
    css = re.sub(r'\s+', ' ', theme.CSS)
    selector = '[data-testid="stPopoverBody"] div[data-testid="stExpander"] details'
    rule = css[css.index(selector):].split('}', 1)[0]
    assert 'background:' + theme.PAGE in rule and theme.PAGE != theme.SURFACE
