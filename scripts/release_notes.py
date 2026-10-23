"""The text of a tester release on GitHub: what is new in this version (from README.md), then how to update
from an older version and how to install for the first time. The release workflow runs it with the tag's
version: python3 scripts/release_notes.py 0.4.12 > release-notes.md"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

GUIDE = """## Updating from an older version

Any older version updates straight to this one; there is no need to install the ones in between.

1. Quit MotiveWave Journal, then install this version over the old one. On a Mac, drag it into
   Applications and choose Replace. On Windows, extract the new ZIP and use its `MotiveWave Journal.exe`.
   Your trades, reviews, tags and settings stay: they live in the app's data folder, not inside the app,
   and the new version brings them up to date by itself the first time it opens.
2. Open the app. If Settings says the study is out of date, click **Install MotiveWave Study**. A study from
   0.3.6 on hands over to the new one by itself within a few seconds. With an older one, Settings tells you to
   quit MotiveWave completely and open it again, once.

## First install

Use a MotiveWave simulation account or Replay first, not a live account. The study is read-only: it records
fills, stop orders and chart prices on your computer and cannot place, change or cancel orders.

- **macOS** (Apple Silicon): drag the app to Applications. It is not notarized by Apple yet, so the first time,
  Control-click it and choose **Open**.
- **Windows**: extract the entire ZIP, then run `MotiveWave Journal.exe`; keep it beside its `_internal`
  folder. If SmartScreen warns, choose **More info → Run anyway**.

Then open the app, click **Install MotiveWave Study** in Settings, and add **Journal → MotiveWave Journal** to the
chart you trade (the contract's own or a continuous one). There is nothing to set. If the study is not in
MotiveWave's study list yet, quit MotiveWave and open it again.
"""


def notes(version: str) -> str:
    """This version's 'What's new' section of README.md, if it has one, then the guide."""
    readme = (ROOT / 'README.md').read_text(encoding='utf-8')
    found = re.search(rf"^## What's new in {re.escape(version)}\b.*?(?=^## |\Z)", readme, re.S | re.M)
    return (found.group(0).strip() + '\n\n' if found else '') + GUIDE


if __name__ == '__main__':
    tag = sys.argv[1] if len(sys.argv) > 1 else ''
    sys.stdout.reconfigure(encoding='utf-8')               # '→' and '−' on a console that is not UTF-8 (Windows)
    print(notes(tag.removeprefix('v').split('-')[0]))      # v0.4.12-alpha.1 -> 0.4.12
