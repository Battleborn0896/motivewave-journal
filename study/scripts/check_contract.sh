#!/usr/bin/env bash
# macOS only. Checks that the MotiveWave installed on this Mac still has the public read methods the
# fill capture calls, so a MotiveWave update that renames one is noticed here and not in the middle of
# a session.
#
# What it does: runs JournalAccountCaptureTest on the Java launcher that ships inside MotiveWave.app,
# with MotiveWave's own jars on the classpath and -Dmwj.checkNativeContract=true. That switch makes
# the test call JournalNativeAccess.connect(), which looks each class up WITHOUT initialising it and
# resolves the allow-listed public method names by reflection.
#
# What it does not do: it never runs MotiveWave code. The reader that connect() returns is not invoked,
# no MotiveWave class is initialised, the application is not started, and no workspace, login, account
# or order is touched. Nothing is decompiled or copied either: only method names are resolved.
#
# Passing means the names resolve in this install. It does not prove that the methods still return
# what the capture expects; only a session inside MotiveWave shows that.
set -euo pipefail

script_dir="$(cd "$(dirname "$0")" && pwd -P)"
study_dir="$(dirname "$script_dir")"
app="${MOTIVEWAVE_APP:-/Applications/MotiveWave.app}"
launcher="$app/Contents/Plugins/Java.runtime/Contents/Home/bin/MotiveWave"

if [[ "$(uname -s)" != "Darwin" ]]; then
  echo "check_contract.sh is macOS only: it needs the Java launcher inside MotiveWave.app." >&2
  exit 1
fi
if [[ ! -x "$launcher" ]]; then
  echo "MotiveWave's bundled Java launcher not found at: $launcher" >&2
  exit 1
fi

"$script_dir/test.sh" compile

classpath="$study_dir/build/classes:$study_dir/build/test-classes:$app/Contents/Java/*"
while IFS= read -r jar; do
  classpath="$classpath:$jar"
done < <(find "$app/Contents" -name 'javafx*.jar' -type f | LC_ALL=C sort)

suite="org.mwjournal.study.JournalAccountCaptureTest"
echo "--- $suite (native contract, $("$launcher" -version 2>&1 | head -n 1))"
"$launcher" -ea -Djava.awt.headless=true -Dmwj.checkNativeContract=true -cp "$classpath" "$suite"
echo "Native contract resolved against $app"
