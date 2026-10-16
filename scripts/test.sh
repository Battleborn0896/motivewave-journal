#!/usr/bin/env bash
# Java -> real bridge file -> Python watcher -> trade, chart, MAE/MFE. Fixtures only, never a broker.
set -euo pipefail
root="$(cd "$(dirname "$0")/.." && pwd -P)"
python="${MWJ_PYTHON:-$root/.venv/bin/python}"
if [[ ! -x "$python" ]]; then python="${MWJ_PYTHON:-python3}"; fi
if ! "$python" -c 'import pytest, streamlit, pandas, plotly' 2>/dev/null; then
  echo "Install requirements and the test extra first; see README.md. Or set MWJ_PYTHON." >&2
  exit 1
fi
cd "$root"
bash study/scripts/test.sh
. study/scripts/jdk.sh
resolve_jdk
sample_dir="$(mktemp -d "$root/study/build/contract.XXXXXX")"
sdk="${MOTIVEWAVE_SDK:-/Applications/MotiveWave.app/Contents/Java/mwave_sdk.jar}"
"$JDK_HOME/bin/java" -ea -Djava.awt.headless=true \
  -cp "$root/study/build/classes:$root/study/build/test-classes:$sdk" \
  org.mwjournal.study.ContractSample "$sample_dir/motivewave.ndjson"
MWJ_CONTRACT_DIR="$sample_dir" PYTHONDONTWRITEBYTECODE=1 "$python" -m pytest -q -p no:cacheprovider "$@"
