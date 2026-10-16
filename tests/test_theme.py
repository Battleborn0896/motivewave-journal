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
