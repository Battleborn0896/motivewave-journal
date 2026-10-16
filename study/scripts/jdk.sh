#!/usr/bin/env bash
# Shared JDK resolution. Sourced by build.sh and test.sh; sets JDK_HOME.
#
# The MotiveWave 7 SDK ships Java 25 bytecode (class file major version 69), so an older JDK cannot
# read it at all and JDK 25+ is a hard requirement. On macOS /usr/bin/javac exists even when no JDK
# is installed -- it is a stub that fails with "Unable to locate a Java Runtime" -- so each candidate
# is probed by actually running it rather than trusted for existing.

usable_jdk() {
  [[ -n "${1:-}" && -x "$1/bin/javac" && -x "$1/bin/jar" && -x "$1/bin/java" ]] \
    && "$1/bin/javac" -version >/dev/null 2>&1
}

resolve_jdk() {
  JDK_HOME=""
  local candidate
  for candidate in \
    "${JAVA_HOME:-}" \
    "$(/usr/libexec/java_home -v 25 2>/dev/null || true)" \
    "$HOME/.local/jdk/temurin-25/Contents/Home" \
    "$HOME"/.local/jdk/*/Contents/Home \
    "$HOME"/.local/jdk/* \
    /opt/homebrew/opt/openjdk@25/libexec/openjdk.jdk/Contents/Home \
    /Library/Java/JavaVirtualMachines/*/Contents/Home
  do
    if usable_jdk "$candidate"; then JDK_HOME="$candidate"; return 0; fi
  done

  echo "JDK 25 is required but no working javac was found." >&2
  echo "Set JAVA_HOME to a JDK 25 installation, or unpack one into ~/.local/jdk/." >&2
  echo "The MotiveWave SDK is Java 25 bytecode, so an older JDK cannot read it." >&2
  return 1
}
