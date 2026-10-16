#!/usr/bin/env bash
# Builds the MotiveWave Journal study as one jar of its own: study/build/libs/motivewave-journal.jar.
# `build.sh deploy` also copies the jar into MotiveWave's extensions folder (MOTIVEWAVE_EXTENSIONS, by
# default ~/MotiveWave Extensions); MotiveWave loads it on its next restart.
#
# Needs JDK 25 (see jdk.sh) and the SDK jar that ships with MotiveWave (MOTIVEWAVE_SDK to point elsewhere).
# Nothing is downloaded, and everything it writes goes under study/build/.
set -euo pipefail

script_dir="$(cd "$(dirname "$0")" && pwd -P)"
study_dir="$(dirname "$script_dir")"
default_sdk="/Applications/MotiveWave.app/Contents/Java/mwave_sdk.jar"
sdk_jar="${MOTIVEWAVE_SDK:-$default_sdk}"

. "$script_dir/jdk.sh"
resolve_jdk
javac_bin="$JDK_HOME/bin/javac"
jar_bin="$JDK_HOME/bin/jar"
if [[ ! -f "$sdk_jar" ]]; then
  echo "MotiveWave SDK not found at: $sdk_jar (set MOTIVEWAVE_SDK)" >&2
  exit 1
fi

build_dir="$study_dir/build"
classes_dir="$build_dir/classes"
libs_dir="$build_dir/libs"
sources_file="$build_dir/sources.list"
manifest="$build_dir/MANIFEST.MF"
jar_name="motivewave-journal.jar"
mkdir -p "$classes_dir" "$libs_dir"
find "$classes_dir" -type f -delete
# Quote every entry: javac argfiles split on unquoted whitespace, and the checkout path may have spaces.
find "$study_dir/src/main/java" -name '*.java' -type f \
  | LC_ALL=C sort | sed 's/"/\\"/g; s/^/"/; s/$/"/' > "$sources_file"

# One version number: the jar's manifest carries the one the study sends in its heartbeat.
version="$(sed -n 's/.*CONNECTOR_VERSION = "\([^"]*\)".*/\1/p' \
  "$study_dir/src/main/java/org/mwjournal/study/JournalPaths.java" | head -n 1)"
if [[ -z "$version" ]]; then
  echo "CONNECTOR_VERSION not found in JournalPaths.java" >&2
  exit 1
fi
printf 'Manifest-Version: 1.0\nImplementation-Title: MotiveWave Journal\nImplementation-Version: %s\n' \
  "$version" > "$manifest"

"$javac_bin" --release 25 -encoding UTF-8 -Xlint:all -cp "$sdk_jar" -d "$classes_dir" @"$sources_file"
"$jar_bin" --create --file "$libs_dir/$jar_name" --manifest "$manifest" -C "$classes_dir" .
echo "Built $libs_dir/$jar_name"

if [[ "${1:-}" == "deploy" ]]; then
  extension_dir="${MOTIVEWAVE_EXTENSIONS:-$HOME/MotiveWave Extensions}"
  mkdir -p "$extension_dir"
  # Swap the jar in with a rename rather than writing over it: a running MotiveWave keeps reading the
  # old file undisturbed until it is restarted.
  cp "$libs_dir/$jar_name" "$extension_dir/.$jar_name.new"
  mv -f "$extension_dir/.$jar_name.new" "$extension_dir/$jar_name"
  touch "$extension_dir/.last_updated"
  echo "Deployed to $extension_dir/$jar_name (restart MotiveWave to load it)"
fi
