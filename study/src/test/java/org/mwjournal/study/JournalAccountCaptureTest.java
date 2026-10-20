package org.mwjournal.study;

import java.nio.file.Files;
import java.util.List;
import java.util.concurrent.atomic.AtomicReference;

/** Read-only fixtures: never starts MotiveWave, logs in, or touches a broker. */
public final class JournalAccountCaptureTest {
  private JournalAccountCaptureTest() {}
  private static final long TIME = 1641220200000L;
  public static void main(String[] args) throws Exception {
    var folder = Files.createTempDirectory("mwj-native-fixture-");
    var file = folder.resolve("motivewave.ndjson");
    var a = fill("a", false, true, 1, 5000, TIME, "parent", "position");
    var b = fill("b", false, false, 1, 5002, TIME + 120000, "exit", "position");
    var r = fill("r", true, true, 2, 5000, TIME, "replay-parent", "replay-position");
    var stop = order("sl", "parent", "position", "stop", "working", 4998, TIME, false);
    var unlinked = order("unlinked", "", "", "target", "working", 5005, TIME, false);
    var snapshot = new AtomicReference<>(new JournalNativeAccess.Snapshot("workspace", List.of(a, b, r), List.of(stop, unlinked)));
    var capture = JournalAccountCapture.fixture(file, snapshot::get);
    String originalRun = capture.replayRun("ESZ6", TIME);
    assert capture.replayRun("ESZ6", TIME + 1000).equals(originalRun);
    capture.poll(); capture.poll();
    assert capture.failure == null;
    snapshot.set(new JournalNativeAccess.Snapshot("workspace", List.of(a, b, r),
        List.of(order("sl", "parent", "position", "stop", "working", 4999, TIME + 30000, false))));
    capture.poll();
    String nextRun = capture.replayRun("ESZ6", TIME - 1000);
    assert !nextRun.equals(originalRun) : "A rewind gets a distinct price run";
    assert capture.replayRun("NQZ6", TIME - 1000).equals(nextRun) : "Multiple charts must share the run";
    var r2 = fill("r2", true, true, 1, 5001, TIME - 500, "new-parent", "new-position");
    snapshot.set(new JournalNativeAccess.Snapshot("workspace", List.of(a, b, r, r2), List.of()));
    capture.poll(); capture.close();
    var live = Files.readAllLines(file);
    var replay = Files.readAllLines(JournalAccountCapture.replayFile(file));
    assert live.stream().filter(s -> s.contains("\"type\":\"execution\"")).count() == 2;
    assert live.stream().noneMatch(s -> s.contains("\"mode\":\"replay\""));
    assert replay.stream().filter(s -> s.contains("\"type\":\"execution\"")).count() == 2;
    assert replay.stream().noneMatch(s -> s.contains("\"mode\":\"live\""));
    assert replay.stream().anyMatch(s -> s.contains(":replay:" + originalRun) && s.contains("\"execution_id\":\"r\""));
    assert replay.stream().anyMatch(s -> s.contains(":replay:" + nextRun) && s.contains("\"execution_id\":\"r2\""));
    assert live.stream().filter(s -> s.contains("\"type\":\"execution\""))
        .allMatch(s -> s.contains("\"capture_kind\":\"retained\"") && s.contains("\"observed_at\""));
    assert replay.stream().anyMatch(s -> s.contains("\"execution_id\":\"r2\"")
        && s.contains("\"capture_kind\":\"new\"")) : "Replay media needs wall time, not historical market time";
    assert live.stream().filter(s -> s.contains("\"type\":\"protection\"")).count() == 2;
    assert live.stream().noneMatch(s -> s.contains("unlinked"));
    assert live.stream().anyMatch(s -> s.contains("\"entry_execution_id\":\"a\""));
    assert live.stream().anyMatch(s -> s.contains("\"commission_known\":false") && s.contains("\"point_value\":50.0"));
    for (var lines : List.of(live, replay)) {
      var beats = lines.stream().filter(s -> s.contains("\"type\":\"heartbeat\"")).toList();
      assert !beats.isEmpty() : "Each environment gets its own heartbeat";
      assert beats.stream().allMatch(s -> s.contains("\"connector\":\"motivewave-journal-study\"")
          && s.contains("\"connector_version\":\"0.3.4\"") && s.contains("\"provider\":\"motivewave-native\"")
          && s.endsWith("}")) : "Heartbeats name the writer and its version";
    }

    // Reload re-emits stable identities, allowing Python's durable deduplication after any queue/file crash.
    var reload = JournalAccountCapture.fixture(file, snapshot::get);
    assert reload.replayRun("ESZ6", TIME).equals(nextRun);
    reload.poll(); reload.close();
    replay = Files.readAllLines(JournalAccountCapture.replayFile(file));
    assert replay.stream().filter(s -> s.contains(":replay:" + originalRun) && s.contains("\"execution_id\":\"r\""))
        .count() == 2 : "Old executions keep their first run on restart";
    var rewindAfterReload = JournalAccountCapture.fixture(file, snapshot::get);
    assert !rewindAfterReload.replayRun("ESZ6", TIME - 2000).equals(nextRun)
        : "A rewind while the study is unloaded must not reuse future recorded prices";
    rewindAfterReload.poll(); rewindAfterReload.close();

    var correctionsFile = folder.resolve("correction.ndjson");
    snapshot.set(new JournalNativeAccess.Snapshot("workspace", List.of(a), List.of()));
    var conflicts = JournalAccountCapture.fixture(correctionsFile, snapshot::get);
    conflicts.poll();
    snapshot.set(new JournalNativeAccess.Snapshot("workspace", List.of(fill("a", false, true, 2, 5000, TIME, "parent", "position")), List.of()));
    conflicts.poll();
    // Fills are still being read, so this is a warning beside a working capture, not a failure.
    assert conflicts.failure == null && conflicts.warnings.equals("1 fill record changed after capture (not duplicated)")
        : conflicts.warnings;
    conflicts.close();
    assert Files.readAllLines(correctionsFile).stream().filter(s -> s.contains("\"type\":\"execution\"")).count() == 1;
    var unavailable = JournalAccountCapture.fixture(folder.resolve("error.ndjson"), () -> {
      throw new NoSuchMethodException("Fixture unsupported version");
    });
    unavailable.poll(); assert unavailable.failure.contains("NoSuchMethodException"); unavailable.close();
    assert unavailable.warnings == null : "A capture that cannot read fills has an error, not warnings";
    assert Files.readString(folder.resolve("error.ndjson")).contains("\"status\":\"error\"");
    assert JournalNativeAccess.multiplier(12.5, .25) == 50;
    assert Double.isNaN(JournalNativeAccess.multiplier(10, 0));
    try {
      JournalAccountCapture.execution(fill("bad", false, true, 0, 5000, TIME, "", ""), "native");
      throw new AssertionError("Reject invalid quantity");
    } catch (IllegalArgumentException expected) { }
    var tiedFile = folder.resolve("tied.ndjson");
    snapshot.set(new JournalNativeAccess.Snapshot("workspace", List.of(
        fill("z-entry", false, true, 1, 5000, TIME, "entry", "tie"),
        fill("a-exit", false, false, 1, 5001, TIME, "exit", "tie")), List.of()));
    var tied = JournalAccountCapture.fixture(tiedFile, snapshot::get);
    tied.poll(); tied.close();
    var tiedRows = Files.readAllLines(tiedFile).stream().filter(s -> s.contains("\"type\":\"execution\"")).toList();
    assert tiedRows.getFirst().contains("z-entry") : "Native same-time fill order must not be reversed lexically";
    var waitingFile = folder.resolve("waiting.ndjson");
    snapshot.set(new JournalNativeAccess.Snapshot("workspace", List.of(r), List.of()));
    var waiting = JournalAccountCapture.fixture(waitingFile, snapshot::get);
    waiting.poll();
    assert waiting.failure == null && waiting.warnings.equals("1 replay fill waiting for a replay chart with the study")
        : waiting.warnings;
    waiting.replayRun("ESZ6", TIME); waiting.poll(); assert waiting.failure == null && waiting.warnings == null;
    waiting.close();
    assert Files.readAllLines(JournalAccountCapture.replayFile(waitingFile)).stream()
        .filter(s -> s.contains("\"type\":\"execution\"")).count() == 1;
    if (Boolean.getBoolean("mwj.checkNativeContract")) {
      // Resolve only public method contracts; NEVER invoke the reader outside MotiveWave.
      assert JournalNativeAccess.connect(JournalAccountCaptureTest.class.getClassLoader()) != null;
      System.out.println("Native contract checked: every allow-listed public read method resolved; none was invoked.");
    }
    // Only a passing run tidies up; a failing one leaves its files to look at.
    try (var left = Files.walk(folder)) {
      for (var path : left.sorted(java.util.Comparator.reverseOrder()).toList()) Files.delete(path);
    }
    System.out.println("Journal account capture passed: separate environments, manual fills, linked SL/TP, rewinds, restart identities, conflicts and visible failures.");
  }
  private static JournalNativeAccess.Fill fill(String id, boolean replay, boolean buy, double qty, double price,
                                               long time, String order, String position) {
    return new JournalNativeAccess.Fill(id, replay ? "REPLAY-1" : "paper", "ESZ6", buy, qty, price,
        time, 2, 50, "USD", order, position, buy, replay);
  }
  private static JournalNativeAccess.ProtectiveOrder order(String id, String parent, String position,
      String role, String state, double price, long time, boolean replay) {
    return new JournalNativeAccess.ProtectiveOrder(id, replay ? "REPLAY-1" : "paper", "ESZ6", false,
        parent, position, role, state, price, time, replay);
  }
}
