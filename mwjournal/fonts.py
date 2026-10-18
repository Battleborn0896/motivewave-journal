"""The journal's one typeface: IBM Plex Sans, from the files in app/static/fonts.

The fonts are bundled (SIL Open Font License 1.1, see app/static/fonts/IBM-Plex-Sans-LICENSE.txt) and
never fetched from the internet: the app works offline and sends nothing out. Streamlit serves
app/static at <app address>/app/static/ once server.enableStaticServing is on.

.streamlit/config.toml declares these faces for Streamlit's own widgets. The desktop launchers pass the
same settings as flags (streamlit_flags), so a backend that does not read that file still serves and
uses the fonts. tests/test_fonts.py keeps all of them in step.
"""

from __future__ import annotations

import json

FAMILY = 'IBM Plex Sans'
# What comes after IBM Plex Sans is only for the moment before a file has loaded, or a glyph it lacks.
STACK = f'"{FAMILY}", -apple-system, BlinkMacSystemFont, "Segoe UI", system-ui, sans-serif'
FOLDER = 'fonts'                           # inside app/static
URL = 'app/static/fonts/'                  # where Streamlit serves them, relative to the app's address
LICENSE = 'IBM-Plex-Sans-LICENSE.txt'
# (file, weight, style): everything the journal draws with.
FACES = (
    ('IBMPlexSans-Regular.woff2', 400, 'normal'),
    ('IBMPlexSans-Italic.woff2', 400, 'italic'),
    ('IBMPlexSans-Medium.woff2', 500, 'normal'),
    ('IBMPlexSans-SemiBold.woff2', 600, 'normal'),
    ('IBMPlexSans-Bold.woff2', 700, 'normal'),
)


def font_faces() -> list[dict]:
    """The [[theme.fontFaces]] tables of .streamlit/config.toml."""
    return [{'family': FAMILY, 'url': URL + file, 'weight': weight, 'style': style}
            for file, weight, style in FACES]


def streamlit_flags() -> list[str]:
    """Static serving on and every Streamlit font set to IBM Plex Sans, as `streamlit run` flags."""
    return ['--server.enableStaticServing=true',
            f'--theme.font={FAMILY}', f'--theme.headingFont={FAMILY}', f'--theme.codeFont={FAMILY}',
            '--theme.fontFaces=' + json.dumps(font_faces(), separators=(',', ':'))]
