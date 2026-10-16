#!/usr/bin/env bash
# Builds the study, compiles study/src/test and runs every suite there. A suite is a class named
# *Test with a main(); it is run with assertions on, and it fails by throwing. No MotiveWave session,
# login or broker is involved: the tests only need the SDK jar on the classpath for its interfaces.
#
# `test.sh compile` stops after compiling, for check_contract.sh.
set -euo pipefail

script_dir="$(cd "$(dirname "$0")" && pwd -P)"
study_dir="$(dirname "$script_dir")"
"$script_dir/build.sh"

. "$script_dir/jdk.sh"
resolve_jdk
javac_bin="$JDK_HOME/bin/javac"
java_bin="$JDK_HOME/bin/java"

test_root="$study_dir/src/test/java"
test_classes="$study_dir/build/test-classes"
test_sources="$study_dir/build/test-sources.list"
mkdir -p "$test_classes"
find "$test_classes" -type f -delete
# Quoted for the same reason as in build.sh: the checkout path may have spaces.
find "$test_root" -name '*.java' -type f | LC_ALL=C sort \
  | sed 's/"/\\"/g; s/^/"/; s/$/"/' > "$test_sources"

sdk_jar="${MOTIVEWAVE_SDK:-/Applications/MotiveWave.app/Contents/Java/mwave_sdk.jar}"
classpath="$study_dir/build/classes:$sdk_jar"
"$javac_bin" --release 25 -encoding UTF-8 -Xlint:all -cp "$classpath" -d "$test_classes" @"$test_sources"
if [[ "${1:-}" == "compile" ]]; then exit 0; fi

status=0
ran=0
while IFS= read -r source; do
  suite="${source#"$test_root"/}"
  suite="${suite%.java}"
  suite="${suite//\//.}"
  echo "--- $suite"
  "$java_bin" -ea -Djava.awt.headless=true -cp "$classpath:$test_classes" "$suite" || status=1
  ran=$((ran + 1))
done < <(find "$test_root" -name '*Test.java' -type f | LC_ALL=C sort)
# An empty run must not look like a pass.
if [[ "$ran" -eq 0 ]]; then
  echo "No test suites found under $test_root" >&2
  exit 1
fi
if [[ "$status" -ne 0 ]]; then echo "FAILED: at least one of $ran suites" >&2; else echo "All $ran suites passed."; fi
exit $status
