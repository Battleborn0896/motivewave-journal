package org.mwjournal.study;

import java.nio.file.InvalidPathException;
import java.nio.file.Path;

/** Where the study and the app meet. The app implements the same folder rule (docs/protocol.md), so
 * with nothing configured both sides find the same bridge file. */
final class JournalPaths {
  /** Sent in every heartbeat, so the app can tell which writer and which version produced a file. */
  static final String CONNECTOR = "motivewave-journal-study", CONNECTOR_VERSION = "0.3.8";
  static final String APP_FOLDER = "MotiveWave Journal";

  private JournalPaths() {}

  /** The data home: MWJ_HOME when it is set and not blank, otherwise the per-user folder of this OS. */
  static Path home(String configured, String osName, Path userHome, String localAppData) {
    if (configured != null && !configured.isBlank()) return Path.of(configured.strip());
    String os = osName == null ? "" : osName.toLowerCase(java.util.Locale.ROOT);
    // macOS first: "darwin" contains "win".
    if (os.contains("mac") || os.contains("darwin"))
      return userHome.resolve("Library").resolve("Application Support").resolve(APP_FOLDER);
    if (os.contains("win")) {
      Path local = localAppData == null || localAppData.isBlank()
          ? userHome.resolve("AppData").resolve("Local") : Path.of(localAppData.strip());
      return local.resolve(APP_FOLDER);
    }
    return userHome.resolve(".local").resolve("share").resolve("motivewave-journal");
  }

  static Path bridge(Path home) {
    return home.resolve("bridge").resolve("motivewave.ndjson");
  }

  /** The live bridge file on this machine. Never throws: it runs while the study class loads. */
  static String defaultBridge() {
    Path user = Path.of(System.getProperty("user.home", "."));
    String os = System.getProperty("os.name", "");
    try {
      return bridge(home(System.getenv("MWJ_HOME"), os, user, System.getenv("LOCALAPPDATA"))).toString();
    } catch (InvalidPathException exc) {
      // An unusable MWJ_HOME or LOCALAPPDATA must not stop the study from loading; the path stays editable.
      return bridge(home(null, os, user, null)).toString();
    }
  }
}
