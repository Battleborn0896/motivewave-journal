package org.mwjournal.study;

import java.nio.file.Path;

/** The folder rule the study shares with the app (docs/protocol.md). If this changes, the app must too. */
public final class JournalPathsTest {
  private JournalPathsTest() {}

  public static void main(String[] args) {
    Path user = Path.of(System.getProperty("user.home")).resolve("fixture-user");
    Path mac = user.resolve("Library").resolve("Application Support").resolve("MotiveWave Journal");
    Path linux = user.resolve(".local").resolve("share").resolve("motivewave-journal");
    assert JournalPaths.home(null, "Mac OS X", user, null).equals(mac);
    assert JournalPaths.home(null, "Darwin", user, null).equals(mac) : "'darwin' contains 'win' and is still macOS";
    assert JournalPaths.home(null, "Linux", user, null).equals(linux);
    assert JournalPaths.home(null, "FreeBSD", user, null).equals(linux) : "Anything else uses the XDG-style folder";
    assert JournalPaths.home(null, null, user, null).equals(linux);

    Path local = user.resolve("AppData").resolve("Local");
    assert JournalPaths.home(null, "Windows 11", user, null).equals(local.resolve("MotiveWave Journal"));
    assert JournalPaths.home(null, "Windows 11", user, " ").equals(local.resolve("MotiveWave Journal"))
        : "A blank LOCALAPPDATA falls back to the profile folder";
    Path roaming = user.resolve("Elsewhere");
    assert JournalPaths.home(null, "Windows 11", user, roaming.toString()).equals(roaming.resolve("MotiveWave Journal"));

    // MWJ_HOME wins on every OS, but only when it says something.
    Path chosen = user.resolve("journal data");
    for (String os : new String[]{"Mac OS X", "Windows 11", "Linux"}) {
      assert JournalPaths.home(chosen.toString(), os, user, roaming.toString()).equals(chosen);
      assert JournalPaths.home("  " + chosen + " ", os, user, null).equals(chosen) : "Stray spaces are not part of the path";
      Path unset = JournalPaths.home(null, os, user, null);
      assert JournalPaths.home("", os, user, null).equals(unset) && JournalPaths.home("   ", os, user, null).equals(unset)
          : "Blank counts as not set";
    }

    // The files inside the home: live bridge, and the replay bridge beside it.
    Path bridge = JournalPaths.bridge(mac);
    assert bridge.equals(mac.resolve("bridge").resolve("motivewave.ndjson"));
    assert JournalAccountCapture.replayFile(bridge).equals(mac.resolve("bridge").resolve("motivewave.replay.ndjson"));

    // On this machine, with this environment.
    Path actual = Path.of(JournalPaths.defaultBridge());
    assert actual.getFileName().toString().equals("motivewave.ndjson") && actual.getParent().getFileName().toString().equals("bridge");
    String configured = System.getenv("MWJ_HOME");
    if (configured != null && !configured.isBlank())
      assert actual.equals(JournalPaths.bridge(Path.of(configured.strip()))) : "MWJ_HOME must decide the default file";
    else assert actual.isAbsolute() : "Without MWJ_HOME the default is a full path";

    assert JournalPaths.CONNECTOR.equals("motivewave-journal-study") && JournalPaths.CONNECTOR_VERSION.equals("0.3.3")
        : "The heartbeat identity is part of the contract with the app";
    System.out.println("Journal paths passed: MWJ_HOME, macOS, Windows, Linux, the bridge files, and the heartbeat identity ("
        + actual + ").");
  }
}
