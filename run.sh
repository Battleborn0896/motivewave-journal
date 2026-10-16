#!/usr/bin/env bash
# Starts MotiveWave Journal in your browser. Uses .venv/ when it exists, otherwise python3 on PATH.
# Set MWJ_PYTHON to use a specific interpreter.
set -euo pipefail

root="$(cd "$(dirname "$0")" && pwd -P)"
python="${MWJ_PYTHON:-}"
if [[ -z "$python" ]]; then
  if [[ -x "$root/.venv/bin/python" ]]; then python="$root/.venv/bin/python"; else python="python3"; fi
fi
if ! "$python" -c 'import streamlit, pandas, plotly' 2>/dev/null; then
  echo "Missing packages. Run:  python3 -m venv .venv && .venv/bin/pip install -r requirements.txt" >&2
  exit 1
fi
cd "$root"
exec "$python" -m streamlit run app/app.py "$@"
