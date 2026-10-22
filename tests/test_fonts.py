"""IBM Plex Sans is the journal's only typeface, loaded from the bundled files and never from the internet."""
import base64
import importlib.util
import io
import json
import os
from pathlib import Path
import re
import shutil
import socket
import subprocess
import sys
import time
import tokenize
import tomllib

import plotly.express as px
import plotly.io as pio
import pytest

import script_widget
import tag_controls
import theme
import tvchart
from mwjournal import desktop, fonts
from views import trades

ROOT = Path(__file__).resolve().parent.parent
FONTS = ROOT / 'app' / 'static' / fonts.FOLDER
COMPONENTS = ROOT / 'app' / 'components'
PLEX = ('"IBM Plex Sans"', "'IBM Plex Sans'")
MONOSPACE = ('monospace', 'ui-monospace', 'sf mono', 'menlo', 'consolas', 'courier', 'jetbrains', 'roboto mono',
             'source code')


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


windows_launcher = load('mwj_fonts_windows_launcher', ROOT / 'desktop' / 'windows' / 'launcher.py')
desktop_builder = load('mwj_fonts_desktop_builder', ROOT / 'scripts' / 'build_desktop.py')
windows_builder = load('mwj_fonts_windows_builder', ROOT / 'scripts' / 'build_windows.py')


def config():
    return tomllib.loads((ROOT / '.streamlit' / 'config.toml').read_text(encoding='utf-8'))


def bundled():
    """Repository-relative paths of the five faces and their licence."""
    return [f'app/static/{fonts.FOLDER}/{file}' for file, _, _ in fonts.FACES] + [f'app/static/{fonts.FOLDER}/{fonts.LICENSE}']


# ---------------------------------------------------------------- the files

def test_the_five_faces_are_bundled_woff2_files_with_their_licence_and_notice():
    assert [(weight, style) for _, weight, style in fonts.FACES] == [
        (400, 'normal'), (400, 'italic'), (500, 'normal'), (600, 'normal'), (700, 'normal')]
    for file, _, _ in fonts.FACES:
        data = (FONTS / file).read_bytes()
        assert data[:4] == b'wOF2' and len(data) > 10_000, file
    licence = (FONTS / fonts.LICENSE).read_text(encoding='utf-8')
    assert 'SIL Open Font License' in licence and 'Reserved Font Name "Plex"' in licence and 'IBM Corp' in licence
    notices = (ROOT / 'THIRD_PARTY_NOTICES.md').read_text(encoding='utf-8')
    section = notices[notices.index('## IBM Plex Sans'):]
    assert 'SIL Open Font License, Version 1.1' in section and 'Reserved Font Name "Plex"' in section
    assert 'unmodified' in section and fonts.LICENSE in section and '2017 IBM Corp.' in section


def test_streamlit_serves_the_files_and_uses_plex_for_text_headings_and_code():
    settings = config()
    assert settings['server']['enableStaticServing'] is True
    assert settings['theme']['font'] == settings['theme']['headingFont'] == settings['theme']['codeFont'] == fonts.FAMILY
    faces = settings['theme']['fontFaces']
    assert faces == fonts.font_faces() and len(faces) == 5
    for face in faces:
        # Streamlit serves the static folder beside app.py (app/static) at <app address>/app/static/.
        assert face['family'] == fonts.FAMILY and face['url'].startswith('app/static/')
        assert (ROOT / 'app' / face['url'].removeprefix('app/')).is_file()


# ---------------------------------------------------------------- the desktop backends

def flag_options(arguments):
    return dict(argument[2:].split('=', 1) for argument in arguments if argument.startswith('--') and '=' in argument)


def test_desktop_launchers_pass_static_serving_and_the_faces_as_flags(tmp_path):
    """Both desktop backends work even where Streamlit does not read .streamlit/config.toml."""
    assert windows_launcher.FONT_FAMILY == fonts.FAMILY and windows_launcher.FONT_FACES == fonts.FACES
    assert windows_launcher.font_arguments() == fonts.streamlit_flags()
    for arguments in (desktop.streamlit_arguments(9123), windows_launcher.streamlit_arguments(tmp_path, 9123)):
        assert all(flag in arguments for flag in fonts.streamlit_flags())
        options = flag_options(arguments)
        assert options['server.enableStaticServing'] == 'true'
        assert options['theme.font'] == options['theme.headingFont'] == options['theme.codeFont'] == fonts.FAMILY
        assert json.loads(options['theme.fontFaces']) == config()['theme']['fontFaces']


def free_port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


@pytest.mark.parametrize('launcher', ['macos', 'windows'])
def test_a_backend_started_without_the_settings_file_still_serves_the_fonts(tmp_path, launcher):
    project = tmp_path / 'journal'
    for name in ('app', 'mwjournal'):
        shutil.copytree(ROOT / name, project / name, ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
    assert not (project / '.streamlit').exists()
    port = free_port()
    command = ([sys.executable, '-m', 'streamlit', *desktop.streamlit_arguments(port)] if launcher == 'macos' else
               [sys.executable, '-m', *windows_launcher.streamlit_arguments(project, port)])
    environment = {**os.environ, 'PYTHONDONTWRITEBYTECODE': '1', 'MWJ_HOME': str(tmp_path / 'home')}
    process = subprocess.Popen(command, cwd=project, env=environment, stdin=subprocess.DEVNULL,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    url = f'http://127.0.0.1:{port}/'
    try:
        deadline = time.monotonic() + 90
        while True:
            assert process.poll() is None, 'The backend exited.'
            try:
                if desktop.http_bytes(url + '_stcore/health').strip() == b'ok':
                    break
            except OSError:
                pass
            assert time.monotonic() < deadline, 'The backend did not start.'
            time.sleep(.2)
        for file, _, _ in fonts.FACES:
            assert desktop.http_bytes(url + fonts.URL + file) == (FONTS / file).read_bytes()
    finally:
        process.terminate()
        try:
            process.wait(timeout=15)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)


# ---------------------------------------------------------------- nothing else draws text

def declared_families(text):
    """Every font family list a stylesheet or a drawing script names: font-family, the font shorthand,
    an SVG font-family attribute, a chart option and a canvas font."""
    found = [m.group(1) for m in re.finditer(r'font-family\s*:\s*([^;}<]+)', text)]
    found += [m.group(1) for m in re.finditer(r"'font-family'\s*:\s*([^,}]+)", text)]
    for m in re.finditer(r'(?<![-\w.])font\s*:\s*([^;}<]+)', text):
        value = m.group(1)
        size = re.search(r'\d+(?:\.\d+)?px(?:/[\d.]+)?\s+', value)
        found.append(value[size.end():] if size else value)
    found += [m.group(1) for m in re.finditer(r'fontFamily\s*:\s*([^,}]+)', text)]
    found += [m.group(1) for m in re.finditer(r"\.font\s*=\s*'[^']*'\s*\+\s*([\w.]+)", text)]
    return [value.replace('!important', '').strip() for value in found]


def plex_first(value):
    return value.startswith(PLEX) or value in ('inherit', 'var(--font)', 't.font', 'spec.theme.font', 'input.font')


def stylesheets():
    """What the app writes into its pages and components, as the browser receives it."""
    yield 'theme.CSS', theme.CSS
    yield 'tag_controls.PANEL_CSS', tag_controls.PANEL_CSS
    yield 'views/trades.CSS', trades.CSS
    yield 'script widget', script_widget.document([], {}, script_widget.STARTER)
    for component in sorted(COMPONENTS.glob('*/index.html')):
        yield component.parent.name, component.read_text(encoding='utf-8')


def test_every_stylesheet_and_component_names_ibm_plex_sans_first_and_no_monospace():
    assert not hasattr(theme, 'MONO')
    assert theme.FONT == fonts.STACK and theme.FONT.startswith('"IBM Plex Sans", ')
    seen = 0
    for name, text in stylesheets():
        families = declared_families(text)
        assert families, name
        for value in families:
            assert plex_first(value), f'{name}: {value}'
            seen += 1
        lowered = text.lower()
        assert not [word for word in MONOSPACE if word in lowered], name
    assert seen > 30


def test_numbers_that_sit_in_columns_keep_tabular_figures():
    css = re.sub(r'\s+', ' ', theme.CSS + trades.CSS)
    for selector in ('[data-testid="stMetricValue"] {', '.mwj-trade {', '.mwj-day {', 'input {',
                     '.js-plotly-plot .plotly text {', '.mwj-cal {', '.mwj-cal-month {'):
        start = re.search(r'(?:\}|<style>) ' + re.escape(selector), css)    # the rule for it, not a longer selector
        rule = css[start.end():].split('}', 1)[0]
        assert 'font-variant-numeric:tabular-nums' in rule, selector
    chart = (COMPONENTS / 'tvchart' / 'index.html').read_text(encoding='utf-8')
    assert chart.count('font-variant-numeric: tabular-nums') >= 2          # the legend and the fill labels


def test_every_plotly_text_uses_plex():
    theme.register_template()
    layout = pio.templates[theme.TEMPLATE].layout
    for font in (layout.font, layout.xaxis.tickfont, layout.yaxis.tickfont, layout.xaxis.title.font,
                 layout.yaxis.title.font, layout.hoverlabel.font, layout.legend.font, layout.legend.title.font,
                 layout.title.font):
        assert font.family == theme.FONT

    def families(node):
        if isinstance(node, dict):
            for key, value in node.items():
                if key == 'family':
                    yield value
                else:
                    yield from families(value)
        elif isinstance(node, list):
            for value in node:
                yield from families(value)
    # The template every figure gets (the dark base and ours), and a figure built as the views build them.
    merged = pio.templates[pio.templates.default].to_plotly_json()
    assert set(families(merged)) == {theme.FONT}
    assert px.histogram(x=[1, 2, 3]).layout.template.layout.font.family == theme.FONT


def face_rules(html):
    rules = set()
    for body in re.findall(r'@font-face\s*\{([^}]*)\}', html):
        weight = int(re.search(r'font-weight\s*:\s*(\d+)', body).group(1))
        style = re.search(r'font-style\s*:\s*(\w+)', body).group(1)
        url = re.search(r'url\("([^"]+)"\)', body).group(1)
        assert re.search(r'font-family\s*:\s*"IBM Plex Sans"', body)
        rules.add((url, weight, style))
    return rules


@pytest.mark.parametrize('component', ['tvchart', 'checklist'])
def test_components_load_the_five_faces_from_the_apps_own_static_folder(component):
    """A component is <app>/component/<module>.<name>/index.html; the app serves app/static at <app>/app/static/."""
    html = (COMPONENTS / component / 'index.html').read_text(encoding='utf-8')
    assert face_rules(html) == {('../../' + fonts.URL + file, weight, style) for file, weight, style in fonts.FACES}
    assert 'http://' not in html.replace('http://www.w3.org/2000/svg', '') and 'https://' not in html
    if "Content-Security-Policy" in html:
        assert "font-src 'self'" in html


def test_the_chart_waits_for_the_font_before_drawing_on_its_canvas():
    assert tvchart._theme()['font'] == theme.FONT and 'mono' not in tvchart._theme()
    chart = (COMPONENTS / 'tvchart' / 'index.html').read_text(encoding='utf-8')
    assert "document.fonts.load(face)" in chart and '"IBM Plex Sans"' in chart
    handler = chart[chart.index("window.addEventListener('message'"):]
    assert handler.index('fontReady.then') < handler.index('render(d.args)')
    assert 'fontFamily: t.font' in chart and "'font-family':spec.theme.font" in chart


def test_script_widgets_carry_plex_inside_their_network_denied_page():
    page = script_widget.document([], {}, script_widget.STARTER)
    assert "font-src data:" in page and "connect-src 'none'" in page
    encoded = re.search(r'url\(data:font/woff2;base64,([A-Za-z0-9+/=]+)\)', page).group(1)
    assert base64.b64decode(encoded) == (FONTS / fonts.FACES[0][0]).read_bytes()
    assert "document.fonts.load('12px '+input.font).then(draw,draw)" in page
    assert json.dumps(fonts.STACK) in page


def test_the_mac_startup_page_uses_the_bundled_plex():
    swift = (ROOT / 'desktop' / 'macos' / 'Main.swift').read_text(encoding='utf-8')
    assert 'journal/app/static/fonts' in swift and 'IBMPlexSans-Regular.woff2' in swift
    assert "font:16px 'IBM Plex Sans'," in swift


def test_the_browser_tab_icon_is_drawn_locally():
    """A Material icon as the page icon makes the browser fetch it from fonts.gstatic.com."""
    app = (ROOT / 'app' / 'app.py').read_text(encoding='utf-8')
    assert 'page_icon=theme.ICON' in app and ':material/' not in app
    assert theme.ICON.startswith('<svg xmlns="http://www.w3.org/2000/svg"') and 'href' not in theme.ICON


# Characters beyond ASCII that IBM Plex Sans draws itself, each checked in Chrome against the bundled files
# (CSS.getPlatformFontsForNode reported IBM Plex Sans, a web font, for every one). It has no '★', '⚠', '●'
# or full-width '＋': the browser drew those in another font, so the app writes Material icons, drawn dots
# or '+' instead. Before adding a character here, check that the bundled Plex has it.
PLEX_DRAWS = set('£¥·÷–—’“”…›₩€₹™←↑→↓✓−×±≥≤°½')


def written_strings(path):
    """The string literals of a Python module (comments left out), or the text of a component page."""
    text = path.read_text(encoding='utf-8')
    if path.suffix == '.html':
        yield re.sub(r'<!--.*?-->', '', text, flags=re.S)
        return
    kinds = {tokenize.STRING} | {getattr(tokenize, name) for name in ('FSTRING_MIDDLE', 'TSTRING_MIDDLE')
                                 if hasattr(tokenize, name)}
    for token in tokenize.generate_tokens(io.StringIO(text).readline):
        if token.type in kinds:
            yield token.string


def test_every_character_the_app_writes_is_one_ibm_plex_sans_draws():
    sources = sorted([*(ROOT / 'app').rglob('*.py'), *(ROOT / 'mwjournal').rglob('*.py'),
                      *COMPONENTS.glob('*/index.html')])
    assert len(sources) > 30
    for path in sources:
        written = ''.join(written_strings(path))
        foreign = {c for c in written if ord(c) > 127} - PLEX_DRAWS - {'＋'}
        assert not foreign, f'{path.relative_to(ROOT)}: {sorted(foreign)} are not in the bundled IBM Plex Sans'
        # A full-width plus survives only as a stored choice that is shown with an ordinary '+'.
        for choice in re.findall(r'＋ [\w ]+', written):
            assert '+' + choice[1:] in written, f'{path.relative_to(ROOT)}: {choice!r} is shown as written'


# ---------------------------------------------------------------- packaging

def test_every_build_ships_the_fonts_and_the_settings_that_serve_them(tmp_path):
    required = {'.streamlit/config.toml', *bundled()}
    assert required <= set(desktop_builder.REQUIRED_FILES)
    assert set(windows_builder.REQUIRED_FILES) == set(desktop_builder.REQUIRED_FILES)
    assert {'app', '.streamlit'} <= set(desktop_builder.SOURCE_DIRECTORIES) & set(windows_builder.SOURCE_DIRECTORIES)
    # The developer desktop app, and the DMG built from it, copy the source tree this way.
    mac = desktop_builder.copy_source(tmp_path / 'mac' / 'journal')
    jar = tmp_path / 'motivewave-journal.jar'
    jar.write_bytes(b'prebuilt study')
    windows = windows_builder.copy_payload(ROOT, tmp_path / 'windows', jar=jar)
    for project in (mac, windows):
        for name in required:
            assert (project / name).read_bytes() == (ROOT / name).read_bytes(), name
    workflow = (ROOT / '.github' / 'workflows' / 'tester-builds.yml').read_text(encoding='utf-8')
    assert "IBMPlexSans-{0}" in workflow and r"journal/\.streamlit/config\.toml" in workflow
    assert all(f"'{face}'" in workflow for face in ('Regular', 'Italic', 'Medium', 'SemiBold', 'Bold'))


def test_a_build_without_the_fonts_stops(tmp_path):
    source = tmp_path / 'source'
    for name in desktop_builder.SOURCE_DIRECTORIES:
        shutil.copytree(ROOT / name, source / name, ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
    for name in desktop_builder.SOURCE_FILES:
        shutil.copy2(ROOT / name, source / name)
    (source / 'app' / 'static' / fonts.FOLDER / 'IBMPlexSans-Bold.woff2').unlink()
    with pytest.raises(FileNotFoundError, match='IBMPlexSans-Bold.woff2'):
        desktop_builder.copy_source(tmp_path / 'mac', root=source)
    jar = tmp_path / 'motivewave-journal.jar'
    jar.write_bytes(b'prebuilt study')
    with pytest.raises(FileNotFoundError, match='IBMPlexSans-Bold.woff2'):
        windows_builder.copy_payload(source, tmp_path / 'windows', jar=jar)
