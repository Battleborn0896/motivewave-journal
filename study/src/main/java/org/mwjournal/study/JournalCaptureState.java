package org.mwjournal.study;

import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.StandardCopyOption;
import java.util.Map;
import java.util.Properties;

/** Routing identities only, never an "already delivered" flag. Re-emission is safe because
 * execution IDs retain their original provider and the journal deduplicates them transactionally.
 *
 * That only holds while a fill is re-sent exactly as it was first written, so its label is kept here
 * too. A fill with no label entry was written under the plain label: every fill of study 0.1 and 0.2,
 * and every ordinary fill since. */
final class JournalCaptureState {
  private final Path path;
  private final Path clockPath;
  JournalCaptureState(Path bridge) {
    path = bridge.resolveSibling(bridge.getFileName() + ".capture-state");
    clockPath = bridge.resolveSibling(bridge.getFileName() + ".replay-clock");
  }
  String read(Map<String,String> providers, Map<String,String> labels) throws IOException {
    if (!Files.exists(path)) return null;
    if (Files.size(path) > 64L * 1024 * 1024) throw new IOException("Capture state exceeds safety limit.");
    Properties data = new Properties();
    try (var input = Files.newInputStream(path)) { data.load(input); }
    if (!"1".equals(data.getProperty("version"))) throw new IOException("Unsupported capture state.");
    if (data.getProperty("run") == null) throw new IOException("Incomplete capture state; reconciliation required.");
    for (String key : data.stringPropertyNames()) {
      if (key.startsWith("fill.")) providers.put(key.substring(5), data.getProperty(key));
      else if (key.startsWith("label.")) labels.put(key.substring(6), data.getProperty(key));
    }
    return data.getProperty("run");
  }
  void save(String run, Map<String,String> providers, Map<String,String> labels) throws IOException {
    Files.createDirectories(path.getParent());
    Properties data = new Properties();
    data.setProperty("version", "1"); data.setProperty("run", run);
    providers.forEach((key, value) -> data.setProperty("fill." + key, value));
    labels.forEach((key, value) -> data.setProperty("label." + key, value));
    saveAtomic(path, data, "Local execution routing; no credentials");
  }
  String readClock(Map<String,Long> clocks) throws IOException {
    if (!Files.exists(clockPath)) return null;
    if (Files.size(clockPath) > 1024 * 1024) throw new IOException("Replay clock exceeds safety limit.");
    Properties data = new Properties();
    try (var input = Files.newInputStream(clockPath)) { data.load(input); }
    if (!"1".equals(data.getProperty("version")) || data.getProperty("run") == null)
      throw new IOException("Invalid replay clock; reconciliation required.");
    for (String key : data.stringPropertyNames()) if (key.startsWith("clock."))
      clocks.put(key.substring(6), Long.parseLong(data.getProperty(key)));
    return data.getProperty("run");
  }
  void saveClock(String run, Map<String,Long> clocks) throws IOException {
    Properties data = new Properties();
    data.setProperty("version", "1"); data.setProperty("run", run);
    clocks.forEach((key, value) -> data.setProperty("clock." + key, value.toString()));
    saveAtomic(clockPath, data, "Local execution routing; no credentials");
  }
  static void saveAtomic(Path destination, Properties data, String comment) throws IOException {
    Files.createDirectories(destination.getParent());
    Path temporary = Files.createTempFile(destination.getParent(), "mwj-capture-", ".tmp");
    try {
      try (var output = Files.newOutputStream(temporary)) { data.store(output, comment); }
      try { Files.move(temporary, destination, StandardCopyOption.ATOMIC_MOVE, StandardCopyOption.REPLACE_EXISTING); }
      catch (java.nio.file.AtomicMoveNotSupportedException exc) {
        Files.move(temporary, destination, StandardCopyOption.REPLACE_EXISTING);
      }
    } finally { Files.deleteIfExists(temporary); }
  }
}
