package org.mwjournal.study;

import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.concurrent.atomic.AtomicReference;
import org.mwjournal.study.JournalNativeAccess.Fill;
import org.mwjournal.study.JournalNativeAccess.ProtectiveOrder;
import org.mwjournal.study.JournalNativeAccess.Snapshot;

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
    var stop = order("sl", "parent", "position", "working", 4998, TIME, false);
    // A buy stop beside a long is on the position's own side: never linked, never written.
    var unlinked = order("unlinked", "", "", true, "working", 5005, TIME, false);
    var snapshot = new AtomicReference<>(new JournalNativeAccess.Snapshot("workspace", List.of(a, b, r), List.of(stop, unlinked)));
    var capture = JournalAccountCapture.fixture(file, snapshot::get);
    String originalRun = capture.replayRun("ESZ6", TIME);
    assert capture.replayRun("ESZ6", TIME + 1000).equals(originalRun);
    capture.poll(); capture.poll();
    assert capture.failure == null;
    snapshot.set(new JournalNativeAccess.Snapshot("workspace", List.of(a, b, r),
        List.of(order("sl", "parent", "position", "working", 4999, TIME + 30000, false))));
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
          && s.contains("\"connector_version\":\"0.3.5\"") && s.contains("\"provider\":\"motivewave-native\"")
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
    unchangedFillsBuildNothing(folder);
    replayClock(folder);
    positions();
    propStops(folder);
    stopsThatMoveAndFill(folder);
    restartedStops(folder);
    simStops(folder);
    backToBack(folder);
    ownSideBesideHistory(folder);
    leftWorkingAfterFlat(folder);
    cancelledAndReplaced(folder);
    replayStops(folder);
    if (Boolean.getBoolean("mwj.checkNativeContract")) {
      // Resolve only public method contracts; NEVER invoke the reader outside MotiveWave.
      assert JournalNativeAccess.connect(JournalAccountCaptureTest.class.getClassLoader()) != null;
      System.out.println("Native contract checked: every allow-listed public read method resolved; none was invoked.");
    }
    // Only a passing run tidies up; a failing one leaves its files to look at.
    try (var left = Files.walk(folder)) {
      for (var path : left.sorted(java.util.Comparator.reverseOrder()).toList()) Files.delete(path);
    }
    System.out.println("Journal account capture passed: separate environments, manual fills, linked stops (prop "
        + "fills with no position id, SIM position ids with a fill MotiveWave does not hold, the stop's own side, a moved, "
        + "a replaced and a filled stop, back-to-back positions, a stop left working after a flat, a restart, a replay "
        + "rewind), no event built for "
        + "an unchanged fill, the replay clock saved on a rewind or every 10 s, rewinds, restart identities, "
        + "conflicts and visible failures.");
  }

  /** A fill that comes back as it was is not built into an event again; one that changed is. */
  private static void unchangedFillsBuildNothing(Path folder) throws Exception {
    var a = fill("a", false, true, 1, 5000, TIME, "parent", "position");
    var b = fill("b", false, false, 1, 5002, TIME + 120000, "exit", "position");
    var snapshot = new AtomicReference<>(new Snapshot("workspace", List.of(a, b), List.of()));
    var capture = JournalAccountCapture.fixture(folder.resolve("unchanged.ndjson"), snapshot::get);
    capture.poll();
    assert capture.eventsBuilt == 2 : capture.eventsBuilt;
    for (int i = 0; i < 5; i++) capture.poll();
    assert capture.eventsBuilt == 2 : "Unchanged fills build no event: " + capture.eventsBuilt;
    // MotiveWave changing a fill after it was written: built once to tell, a conflict, then passed over again.
    snapshot.set(new Snapshot("workspace", List.of(a, fill("b", false, false, 2, 5002, TIME + 120000, "exit", "position")),
        List.of()));
    capture.poll(); capture.poll();
    assert capture.eventsBuilt == 3 && capture.warnings.equals("1 fill record changed after capture (not duplicated)")
        : capture.eventsBuilt + " " + capture.warnings;
    capture.close();
    var executions = Files.readAllLines(folder.resolve("unchanged.ndjson")).stream()
        .filter(line -> line.contains("\"type\":\"execution\"")).count();
    assert executions == 2 : executions;
  }

  /** The replay clock is saved at once for a new run (a rewind), otherwise at most every 10 seconds, and
   * when the study closes. */
  private static void replayClock(Path folder) throws Exception {
    var file = folder.resolve("clock.ndjson");
    var capture = JournalAccountCapture.fixture(file, () -> new Snapshot("workspace", List.of(), List.of()));
    long now = System.nanoTime(), second = 1_000_000_000L;
    String first = capture.replayRun("ESZ6", TIME);
    capture.poll(now);
    assert clock(file).equals(Map.of("run", first, "ESZ6", TIME)) : "The first poll saves the clock";
    capture.replayRun("ESZ6", TIME + 1000);
    capture.poll(now + second);
    capture.poll(now + 9 * second);
    assert clock(file).get("ESZ6").equals(TIME) : "Not on every poll";
    capture.poll(now + 11 * second);
    assert clock(file).get("ESZ6").equals(TIME + 1000) : "At most every 10 s";
    String rewound = capture.replayRun("ESZ6", TIME - 5000);
    assert !rewound.equals(first);
    capture.poll(now + 12 * second);
    assert clock(file).equals(Map.of("run", rewound, "ESZ6", TIME - 5000)) : "A rewind is saved at once";
    capture.replayRun("ESZ6", TIME - 4000);
    capture.poll(now + 13 * second);
    assert clock(file).get("ESZ6").equals(TIME - 5000);
    capture.close();
    assert clock(file).get("ESZ6").equals(TIME - 4000) : "Closing saves the last replay time";
  }

  private static Map<String, Object> clock(Path file) throws Exception {
    var times = new HashMap<String, Long>();
    String run = new JournalCaptureState(file).readClock(times);
    var result = new HashMap<String, Object>(times);
    result.put("run", run);
    return result;
  }

  /** The positions the stop link sees: flat to flat per account and contract, a reversal splitting at the
   * fill that crosses, and at one instant MotiveWave's entry first, as matching.py orders them. */
  private static void positions() {
    var fills = List.of(
        prop("p1", false, 1, 100, TIME), prop("p2", true, 2, 99, TIME + 1000),   // short 1, then reversed long 1
        prop("p3", false, 1, 101, TIME + 2000),                                   // flat
        prop("p4", true, 1, 100, TIME + 3000),                                    // long again, still open
        new Fill("other", "LFE0002", "MNQZ6", true, 1, 100, TIME, 1, 2, "USD", "o", "", false, false, true));
    var books = JournalAccountCapture.positions(fills, fill -> "motivewave-native:workspace:live");
    var book = books.get(new JournalAccountCapture.Book(false, "LFE0001", "MNQZ6", ""));
    assert book.size() == 3 : book;
    assert !book.get(0).longSide() && book.get(0).first().id().equals("p1") && !book.get(0).open()
        && book.get(0).to() == TIME + 1000;
    assert book.get(1).longSide() && book.get(1).first().id().equals("p2") : "The crossing fill starts the next position";
    assert book.get(1).to() == TIME + 2000 && !book.get(1).open();
    assert book.get(2).open() && book.get(2).first().id().equals("p4") && book.get(2).from() == TIME + 3000;
    assert books.get(new JournalAccountCapture.Book(false, "LFE0002", "MNQZ6", "")).size() == 1 : "Accounts apart";
    var unwritten = JournalAccountCapture.positions(fills, fill -> fill.id().equals("p1") ? null : "provider");
    assert unwritten.get(new JournalAccountCapture.Book(false, "LFE0001", "MNQZ6", "")).getFirst().first().id().equals("p2")
        : "A fill not written yet is left out";
    var tied = JournalAccountCapture.positions(List.of(
        fill("exit", false, false, 1, 5001, TIME, "x", "tie"), fill("entry", false, true, 1, 5000, TIME, "e", "tie")),
        fill -> "provider");
    var tie = tied.get(new JournalAccountCapture.Book(false, "paper", "ESZ6", ""));
    assert tie.size() == 1 && tie.getFirst().longSide() && tie.getFirst().first().id().equals("entry") && !tie.getFirst().open()
        : "At one instant the fill MotiveWave calls an entry comes first: " + tie;
  }

  /** His prop account's fills: no position id and no entry flag, an exchange order number, and a stop with no
   * parent. The stop is linked by account, contract and closing side alone; a stop on the position's own side
   * and one on a contract with no position are not, and after the grace the heartbeat and the log say why. */
  private static void propStops(Path folder) throws Exception {
    var file = folder.resolve("prop.ndjson");
    var fills = new ArrayList<Fill>(List.of(prop("e1", false, 1, 21000, TIME)));
    var orders = new ArrayList<ProtectiveOrder>(List.of(
        stop("8812", "", "", true, "working", 21010, TIME + 40),           // buy stop above the short: its stop
        stop("8813", "", "", false, "working", 20950, TIME + 50),          // sell stop below it: the same side
        new ProtectiveOrder("8814", "LFE0001", "MESZ6", false, "", "", "working", 5900, TIME, false))); // no position
    var capture = JournalAccountCapture.fixture(file,
        () -> new Snapshot("workspace", List.copyOf(fills), List.copyOf(orders), Map.of(),
            new JournalNativeAccess.OrderCounts(5, 4, 1, 0, 0), Map.of()));
    long now = System.nanoTime();
    capture.poll(now);
    assert capture.warnings.equals("1 fill with an assumed currency") : "Within the grace nothing is said: " + capture.warnings;
    capture.poll(now + JournalAccountCapture.UNLINKED_GRACE_NANOS);
    assert capture.warnings.equals("1 fill with an assumed currency; 2 working stop orders not linked to a position: "
        + "1 no position on that account and contract, 1 the open position is on the stop's own side (orders: 5 read, 4 stop, "
        + "1 new, 0 without instrument, 0 without state, 2 with a position, 1 on the closing side, 1 linked)")
        : capture.warnings;
    var logged = new ArrayList<String>();
    capture.drainLog(logged::add);
    var said = logged.stream().filter(line -> line.contains("is not linked")).toList();
    assert said.size() == 2 && said.stream().anyMatch(line -> line.contains("\"8813\"")
        && line.contains("the open position is on the stop's own side") && line.contains("account \"LFE0001\"")
        && line.contains("symbol \"MNQZ6\"") && line.contains("sell, working")
        && line.contains("live positions open: \"LFE0001\" \"MNQZ6\" short")) : said;
    capture.poll(now + 2 * JournalAccountCapture.UNLINKED_GRACE_NANOS);
    logged.clear();
    capture.drainLog(logged::add);
    assert logged.stream().noneMatch(line -> line.contains("is not linked")) : "Once per order: " + logged;
    // Flat: the stop is cancelled. It stays with its position.
    fills.add(prop("x1", true, 1, 20990, TIME + 60000));
    orders.set(0, stop("8812", "", "", true, "cancelled", 21010, TIME + 60010));
    orders.remove(2);
    orders.remove(1);
    capture.poll(now + 3 * JournalAccountCapture.UNLINKED_GRACE_NANOS);
    assert capture.warnings.equals("2 fills with an assumed currency") : "A cancelled stop is not a warning: " + capture.warnings;
    capture.close();
    var lines = protection(file);
    assert lines.size() == 2 : lines;
    assert lines.stream().allMatch(line -> line.contains("\"order_id\":\"8812\"") && line.contains("\"entry_execution_id\":\"e1\"")
        && line.contains("\"role\":\"stop\"") && line.contains("\"provider\":\"motivewave-native:workspace:live\"")
        && line.contains("\"account\":\"LFE0001\"") && line.contains("\"symbol\":\"MNQZ6\""));
    assert lines.get(0).contains("\"state\":\"working\"") && lines.get(1).contains("\"state\":\"cancelled\"");
  }

  /** A stop moved twice, then filled a moment after the exit fill: every line goes to the same position, even
   * the filled one, whose time is after the position's last fill. */
  private static void stopsThatMoveAndFill(Path folder) throws Exception {
    var file = folder.resolve("moved.ndjson");
    var fills = new ArrayList<Fill>(List.of(prop("m1", true, 2, 5000, TIME)));
    var orders = new ArrayList<ProtectiveOrder>(List.of(stop("s1", "", "", false, "working", 4990, TIME + 10)));
    var capture = JournalAccountCapture.fixture(file, () -> new Snapshot("workspace", List.copyOf(fills), List.copyOf(orders)));
    capture.poll();
    orders.set(0, stop("s1", "", "", false, "working", 4995, TIME + 30000));
    capture.poll();
    fills.add(prop("m2", false, 2, 4995, TIME + 90000));
    orders.set(0, stop("s1", "", "", false, "filled", 4995, TIME + 90003));
    capture.poll();
    capture.close();
    var lines = protection(file);
    assert lines.size() == 3 && lines.stream().allMatch(line -> line.contains("\"entry_execution_id\":\"m1\"")) : lines;
    assert lines.get(2).contains("\"state\":\"filled\"") && lines.get(2).contains("\"price\":4995.0");
  }

  /** After a restart the study meets old stops for the first time. A cancelled stop goes to the position held
   * when it was last changed, or to none, and never to a later position; a working stop goes to the open one. */
  private static void restartedStops(Path folder) throws Exception {
    var file = folder.resolve("restart.ndjson");
    var fills = List.of(prop("r1", true, 1, 5000, TIME), prop("r2", false, 1, 5010, TIME + 60000),  // closed by target
        prop("r3", true, 1, 5020, TIME + 120000));                                                   // open now
    var orders = List.of(
        stop("old-oco", "", "", false, "cancelled", 4990, TIME + 60005),    // cancelled by the target, after the fill
        stop("old-moved", "", "", false, "cancelled", 4992, TIME + 30000),  // cancelled while the first was held
        stop("new", "", "", false, "working", 5010, TIME + 120010));
    var before = JournalAccountCapture.fixture(file, () -> new Snapshot("workspace", fills, List.of()));
    before.poll(); before.close();
    var after = JournalAccountCapture.fixture(file, () -> new Snapshot("workspace", fills, orders));
    after.poll(); after.close();
    var lines = protection(file);
    assert lines.size() == 2 : lines;
    assert lines.stream().noneMatch(line -> line.contains("\"old-oco\"")) : "Never linked to the later position";
    assert lines.stream().anyMatch(line -> line.contains("\"old-moved\"") && line.contains("\"entry_execution_id\":\"r1\""));
    assert lines.stream().anyMatch(line -> line.contains("\"new\"") && line.contains("\"entry_execution_id\":\"r3\""));
  }

  /** His SIM account's fills carry MotiveWave's position id, and on its main contract one position's exit was
   * never kept (a SIM reset, say): netted flat to flat, every later position of that account and contract is off
   * by one contract, a long looking flat and the time after its exit looking short. Grouped by position id as
   * the app groups them, each position keeps its own stop. */
  private static void simStops(Path folder) throws Exception {
    var file = folder.resolve("sim.ndjson");
    var fills = new ArrayList<Fill>(List.of(
        sim("stale", false, 1, 21000, TIME - 86_400_000L, "P0", true),    // a short whose exit MotiveWave never kept
        sim("l1", true, 1, 21010, TIME, "P1", true)));                     // a long now
    var orders = new ArrayList<ProtectiveOrder>(List.of(simStop("below", false, "working", 21000, TIME + 20)));
    var capture = JournalAccountCapture.fixture(file, () -> new Snapshot("workspace", List.copyOf(fills), List.copyOf(orders)));
    capture.poll();
    fills.add(sim("l2", false, 1, 21020, TIME + 60_000, "P1", false));
    orders.set(0, simStop("below", false, "cancelled", 21000, TIME + 60_010));
    fills.add(sim("s1", false, 1, 21015, TIME + 120_000, "P2", true));     // then a short
    orders.add(simStop("above", true, "working", 21025, TIME + 120_020));
    capture.poll();
    capture.close();
    var lines = protection(file);
    assert lines.size() == 3 : lines;
    assert lines.stream().filter(line -> line.contains("\"below\"")).allMatch(line -> line.contains("\"entry_execution_id\":\"l1\""))
        && lines.stream().anyMatch(line -> line.contains("\"above\"") && line.contains("\"entry_execution_id\":\"s1\""))
        : "Each stop with its own position: " + lines;
    var book = JournalAccountCapture.positions(fills, fill -> "provider")
        .get(new JournalAccountCapture.Book(false, "sim", "NQZ6", ""));
    assert book.size() == 3 && !book.get(0).longSide() && !book.get(0).open() : "The stale short was replaced: " + book;
    assert book.get(1).longSide() && book.get(1).first().id().equals("l1") && book.get(1).to() == TIME + 60_000;
    assert !book.get(2).longSide() && book.get(2).first().id().equals("s1") && book.get(2).open();
    // A position whose first kept fill is an exit: its entry is not kept, so it is never open.
    var missing = JournalAccountCapture.positions(List.of(sim("x", false, 1, 21000, TIME, "P5", false)), fill -> "provider")
        .get(new JournalAccountCapture.Book(false, "sim", "NQZ6", "")).getFirst();
    assert missing.longSide() && !missing.open() : missing;
  }

  /** A long stopped out and a new long at the same instant, both closed when the study first sees their stops
   * (after a restart). The filled stop stamped at that instant goes to the long it closed; a stop still working
   * that was stamped then, as a bracket's stop is when its entry fills, to the long that began; a stop
   * cancelled at the second long's exit to that long. */
  private static void backToBack(Path folder) throws Exception {
    var file = folder.resolve("back-to-back.ndjson");
    var fills = List.of(prop("b1", true, 1, 5000, TIME), prop("b2", false, 1, 4990, TIME + 60_000),
        prop("b3", true, 1, 4990, TIME + 60_000), prop("b4", false, 1, 5005, TIME + 120_000));
    var orders = List.of(stop("first", "", "", false, "filled", 4990, TIME + 60_000),
        stop("left", "", "", false, "working", 4980, TIME + 60_000),
        stop("second", "", "", false, "cancelled", 4985, TIME + 120_000));
    var capture = JournalAccountCapture.fixture(file, () -> new Snapshot("workspace", fills, orders));
    capture.poll();
    capture.close();
    var lines = protection(file);
    assert lines.size() == 3 && lines.stream().anyMatch(line -> line.contains("\"first\"") && line.contains("\"entry_execution_id\":\"b1\""))
        && lines.stream().anyMatch(line -> line.contains("\"left\"") && line.contains("\"entry_execution_id\":\"b3\""))
        && lines.stream().anyMatch(line -> line.contains("\"second\"") && line.contains("\"entry_execution_id\":\"b3\"")) : lines;
  }

  /** A buy stop above a long, on an account that was short earlier: the reason given is the long open now. */
  private static void ownSideBesideHistory(Path folder) throws Exception {
    var file = folder.resolve("own-side.ndjson");
    var fills = List.of(prop("o1", false, 1, 5000, TIME), prop("o2", true, 1, 4995, TIME + 60_000),
        prop("o3", true, 1, 4990, TIME + 120_000));
    var orders = List.of(stop("add", "", "", true, "working", 5010, TIME + 130_000));
    var capture = JournalAccountCapture.fixture(file, () -> new Snapshot("workspace", fills, orders));
    long now = System.nanoTime();
    capture.poll(now);
    capture.poll(now + JournalAccountCapture.UNLINKED_GRACE_NANOS);
    assert capture.warnings.contains("1 working stop order not linked to a position: the open position is on the "
        + "stop's own side (orders: 1 read, 1 stop, 0 new, 0 without instrument, 0 without state, 1 with a position, "
        + "1 on the closing side, 0 linked)") : capture.warnings;
    capture.close();
    assert protection(file).isEmpty();
  }

  /** A long closed by hand with its stop left working, then another long with a stop of its own: the stop left
   * working stays with the long it was placed for, says nothing while flat, and the new stop goes to the new long. */
  private static void leftWorkingAfterFlat(Path folder) throws Exception {
    var file = folder.resolve("left-working.ndjson");
    var fills = new ArrayList<Fill>(List.of(prop("w1", true, 1, 5000, TIME)));
    var orders = new ArrayList<ProtectiveOrder>(List.of(stop("left", "", "", false, "working", 4990, TIME + 10)));
    var capture = JournalAccountCapture.fixture(file, () -> new Snapshot("workspace", List.copyOf(fills), List.copyOf(orders)));
    long now = System.nanoTime();
    capture.poll(now);
    fills.add(prop("w2", false, 1, 5004, TIME + 60_000));
    capture.poll(now + JournalAccountCapture.UNLINKED_GRACE_NANOS);
    capture.poll(now + 2 * JournalAccountCapture.UNLINKED_GRACE_NANOS);
    assert capture.warnings.equals("2 fills with an assumed currency") : "Linked, so nothing to say: " + capture.warnings;
    fills.add(prop("w3", true, 1, 5002, TIME + 180_000));
    orders.add(stop("new", "", "", false, "working", 4995, TIME + 180_010));
    orders.set(0, stop("left", "", "", false, "working", 4991, TIME + 180_020));   // moved during the new long
    capture.poll(now + 3 * JournalAccountCapture.UNLINKED_GRACE_NANOS);
    capture.close();
    var lines = protection(file);
    assert lines.stream().filter(line -> line.contains("\"left\"")).count() == 2
        && lines.stream().filter(line -> line.contains("\"left\"")).allMatch(line -> line.contains("\"entry_execution_id\":\"w1\""))
        && lines.stream().anyMatch(line -> line.contains("\"new\"") && line.contains("\"entry_execution_id\":\"w3\"")) : lines;
  }

  /** A stop moved by cancelling it and placing another: both go to the position, and the new one takes it out. */
  private static void cancelledAndReplaced(Path folder) throws Exception {
    var file = folder.resolve("replaced.ndjson");
    var fills = new ArrayList<Fill>(List.of(prop("c1", false, 1, 5000, TIME)));
    var orders = new ArrayList<ProtectiveOrder>(List.of(stop("first", "", "", true, "working", 5010, TIME + 10)));
    var capture = JournalAccountCapture.fixture(file, () -> new Snapshot("workspace", List.copyOf(fills), List.copyOf(orders)));
    capture.poll();
    orders.set(0, stop("first", "", "", true, "cancelled", 5010, TIME + 30_000));
    orders.add(stop("second", "", "", true, "working", 5004, TIME + 30_001));
    capture.poll();
    fills.add(prop("c2", true, 1, 5004, TIME + 50_000));
    orders.set(1, stop("second", "", "", true, "filled", 5004, TIME + 50_002));
    capture.poll();
    capture.close();
    var lines = protection(file);
    assert lines.size() == 4 && lines.stream().allMatch(line -> line.contains("\"entry_execution_id\":\"c1\"")) : lines;
  }

  /** Replay stops go to the positions of the replay run they are seen in: after a rewind a new stop goes to the new
   * run's position, filed under that run, and the stop of the run before stays with its own position. */
  private static void replayStops(Path folder) throws Exception {
    var file = folder.resolve("replay-stops.ndjson");
    var fills = new ArrayList<Fill>(List.of(fill("first", true, true, 1, 4800, TIME, "e1", "")));
    var orders = new ArrayList<ProtectiveOrder>(List.of(order("before", "", "", "working", 4790, TIME + 10, true)));
    var capture = JournalAccountCapture.fixture(file, () -> new Snapshot("workspace", List.copyOf(fills), List.copyOf(orders)));
    String run = capture.replayRun("ESZ6", TIME + 20);
    capture.poll();
    String rewound = capture.replayRun("ESZ6", TIME - 60_000);
    orders.set(0, order("before", "", "", "cancelled", 4790, TIME + 30, true));
    fills.add(fill("again", true, true, 1, 4805, TIME - 50_000, "e2", ""));
    orders.add(order("after", "", "", "working", 4795, TIME - 49_990, true));
    capture.poll();
    capture.close();
    var lines = protection(JournalAccountCapture.replayFile(file));
    assert lines.size() == 3 : lines;
    assert lines.stream().filter(line -> line.contains("\"before\"")).allMatch(line -> line.contains(":replay:" + run + "\"")
        && line.contains("\"entry_execution_id\":\"first\""));
    assert lines.stream().anyMatch(line -> line.contains("\"after\"") && line.contains(":replay:" + rewound + "\"")
        && line.contains("\"entry_execution_id\":\"again\"")) : lines;
  }

  /** A SIM fill as MotiveWave gives it: its position id, and whether it opened or added to that position. */
  private static Fill sim(String id, boolean buy, double qty, double price, long time, String position, boolean entry) {
    return new Fill(id, "sim", "NQZ6", buy, qty, price, time, 2.5, 20, "USD", "o-" + id, position, entry, false);
  }

  private static ProtectiveOrder simStop(String id, boolean buy, String state, double price, long time) {
    return new ProtectiveOrder(id, "sim", "NQZ6", buy, "", "", state, price, time, false);
  }

  private static List<String> protection(Path file) throws Exception {
    return Files.readAllLines(file).stream().filter(line -> line.contains("\"type\":\"protection\"")).toList();
  }

  /** A prop-account fill as MotiveWave gives it: no position id, no entry flag, the account currency assumed. */
  private static Fill prop(String id, boolean buy, double qty, double price, long time) {
    return new Fill(id, "LFE0001", "MNQZ6", buy, qty, price, time, 1.5, 2, "USD", "9" + id, "", false, false, true);
  }

  private static ProtectiveOrder stop(String id, String parent, String position, boolean buy, String state,
                                      double price, long time) {
    return new ProtectiveOrder(id, "LFE0001", "MNQZ6", buy, parent, position, state, price, time, false);
  }
  private static JournalNativeAccess.Fill fill(String id, boolean replay, boolean buy, double qty, double price,
                                               long time, String order, String position) {
    return new JournalNativeAccess.Fill(id, replay ? "REPLAY-1" : "paper", "ESZ6", buy, qty, price,
        time, 2, 50, "USD", order, position, buy, replay);
  }
  private static ProtectiveOrder order(String id, String parent, String position, String state, double price,
                                       long time, boolean replay) {
    return order(id, parent, position, false, state, price, time, replay);
  }
  private static ProtectiveOrder order(String id, String parent, String position, boolean buy, String state,
                                       double price, long time, boolean replay) {
    return new ProtectiveOrder(id, replay ? "REPLAY-1" : "paper", "ESZ6", buy, parent, position, state, price, time, replay);
  }
}
