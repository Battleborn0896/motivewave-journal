#!/usr/bin/env bash
# Java -> real bridge file -> Python watcher -> trade, chart, MAE/MFE. Fixtures only, never a broker.
#
# The study is built and its suites run from a copy in a temporary folder, removed when this script exits,
# so a run leaves the checkout as it was: the committed study jar stays the one the release ships.
set -euo pipefail
root="$(cd "$(dirname "$0")/.." && pwd -P)"
python="${MWJ_PYTHON:-$root/.venv/bin/python}"
if [[ ! -x "$python" ]]; then python="${MWJ_PYTHON:-python3}"; fi
if ! "$python" -c 'import pytest, streamlit, pandas, plotly' 2>/dev/null; then
  echo "Install requirements and the test extra first; see README.md. Or set MWJ_PYTHON." >&2
  exit 1
fi
cd "$root"
work="$(mktemp -d "${TMPDIR:-/tmp}/mwj-test.XXXXXX")"
trap 'rm -rf "$work"' EXIT
cp -R study/scripts study/src "$work/"
bash "$work/scripts/test.sh"
. "$work/scripts/jdk.sh"
resolve_jdk
sample_dir="$work/contract"
mkdir "$sample_dir"
sdk="${MOTIVEWAVE_SDK:-/Applications/MotiveWave.app/Contents/Java/mwave_sdk.jar}"
"$JDK_HOME/bin/java" -ea -Djava.awt.headless=true \
  -cp "$work/build/classes:$work/build/test-classes:$sdk" \
  org.mwjournal.study.ContractSample "$sample_dir/motivewave.ndjson"
MWJ_CONTRACT_DIR="$sample_dir" PYTHONDONTWRITEBYTECODE=1 "$python" -m pytest -q -p no:cacheprovider "$@"
