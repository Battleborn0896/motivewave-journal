package org.mwjournal.study;

import java.nio.file.Files;
import java.util.ArrayList;
import java.util.List;
import org.mwjournal.study.JournalTicks.Held;
import static org.mwjournal.study.JournalTicks.ALWAYS;
import static org.mwjournal.study.JournalTicks.IN_POSITION;

/** The whole tick path short of MotiveWave's callback: positions from fixture fills, the gate, the real
 * writer and the bridge files it leaves behind. */
public final class JournalTicksTest {
  private JournalTicksTest() {}
  private static final long TIME = 1641220200000L;
  private static final String REPLAY_SOURCE = "motivewave-replay:run-1:simulated";

  public static void main(String[] args) throws Exception {
    var folder = Files.createTempDirectory("mwj-ticks-fixture-");
    var file = folder.resolve("motivewave.ndjson");
    var replayFile = JournalAccountCapture.replayFile(file);
    var fills = new ArrayList<JournalNativeAccess.Fill>();
    var capture = JournalAccountCapture.fixture(file,
        () -> new JournalNativeAccess.Snapshot("workspace", List.copyOf(fills), List.of()));
    capture.replayRun("ESZ6", TIME);
    // The study's own references to the writers it shares with the account capture.
    var writer = JournalEventWriter.acquire(file);
    var replayWriter = JournalEventWriter.acquire(replayFile);
    var gate = new TickGate<Held>();
    var replayGate = new TickGate<Held>();

    // The test's clock runs ahead of the real one, so the tail a fill leaves can lapse without waiting for it.
    long[] ahead = {0};
    Runnable poll = () -> capture.poll(System.nanoTime() + ahead[0]);
    Runnable settle = () -> {
      ahead[0] += JournalAccountCapture.TRADED_LINGER_NANOS + 1_000_000_000L;
      capture.poll(System.nanoTime() + ahead[0]);
    };

    // What arms the gate. Anything short of a book known to be flat records.
    assert JournalTicks.armed(null, IN_POSITION, false, "ESZ6", "ESZ6", "ESZ6") : "Fill capture off records everything";
    assert JournalTicks.armed(capture, IN_POSITION, false, "ESZ6", "ESZ6", "ESZ6") : "No poll yet records everything";
    poll.run();
    assert !JournalTicks.armed(capture, IN_POSITION, false, "ESZ6", "ESZ6", "ESZ6") : "Known flat skips";
    assert JournalTicks.armed(capture, ALWAYS, false, "ESZ6", "ESZ6", "ESZ6");
    assert JournalTicks.armed(capture, "A_LATER_VERSIONS_CHOICE", false, "ESZ6", "ESZ6", "ESZ6")
        : "An unknown stored value must not silently skip prices";

    // Flat: prints go by and nothing is written.
    for (int n = 0; n < 5; n++) live(gate, writer, capture, n);
    // An entry fill appears at the next poll: the prints from before it are written first, in order.
    fills.add(fill("entry", true, false));
    poll.run();
    live(gate, writer, capture, 5);
    live(gate, writer, capture, 6);
    // Out again. The prints right after the exit are still written; once that tail has lapsed, none are.
    fills.add(fill("exit", false, false));
    poll.run();
    live(gate, writer, capture, 7);
    settle.run();
    live(gate, writer, capture, 8);
    live(gate, writer, capture, 9);
    // A second trade, short this time: armed again, with its own pre-roll.
    fills.add(fill("short-entry", false, false));
    poll.run();
    live(gate, writer, capture, 10);
    fills.add(fill("short-exit", true, false));
    settle.run();
    live(gate, writer, capture, 11);
    // A trade opened and closed between two polls is never seen as a position. Its prints are in the
    // pre-roll, and the two new fills are enough to have them written.
    live(gate, writer, capture, 12);
    fills.add(fill("scalp-in", true, false));
    fills.add(fill("scalp-out", false, false));
    poll.run();
    assert !capture.holding("ESZ6", false) : "The fixture must not show this trade as a position";
    live(gate, writer, capture, 13);
    settle.run();
    live(gate, writer, capture, 14);
    live(gate, writer, capture, 15);

    // Replay has its own book, gate and file. A replay position must not record live prints, or the reverse.
    replay(replayGate, replayWriter, capture, 20);
    fills.add(fill("replay-entry", true, true));
    poll.run();
    replay(replayGate, replayWriter, capture, 21);
    live(gate, writer, capture, 16);
    assert !JournalTicks.armed(capture, IN_POSITION, false, "ESZ6", "ESZ6", "ESZ6");
    assert JournalTicks.armed(capture, IN_POSITION, true, "ESZ6", "ESZ6", "ESZ6");

    // The names a position can be under: the instrument's own symbol, the active contract of a
    // continuous chart, or the contract a proxy chart names. Anything else stays flat.
    fills.add(fill("micro", true, false, "MESZ6"));
    poll.run();
    assert JournalTicks.armed(capture, IN_POSITION, false, "MESZ6", "MESZ6", "MESZ6");
    assert JournalTicks.armed(capture, IN_POSITION, false, "@MES", "MESZ6", "MESZ6") : "Continuous chart, fills on the contract";
    assert JournalTicks.armed(capture, IN_POSITION, false, "ESZ6", "ESZ6", "MESZ6") : "ES chart standing in for MES fills";
    assert !JournalTicks.armed(capture, IN_POSITION, false, "NQZ6", "NQZ6", "NQZ6");
    assert !JournalTicks.armed(capture, IN_POSITION, false, null, null, "NQZ6");
    assert capture.failure == null : capture.failure;

    writer.release();
    replayWriter.release();
    capture.close();
    var lines = Files.readAllLines(file);
    var ticks = lines.stream().filter(s -> s.contains("\"type\":\"tick\"")).toList();
    assert prices(ticks).equals(List.of(0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13))
        : "Pre-roll then position, in order, and nothing from the flat stretch at the end: " + prices(ticks);
    assert ticks.stream().allMatch(s -> s.contains("\"mode\":\"live\"") && s.contains("\"provider\":\"motivewave-ticks\"")
        && s.contains("\"symbol\":\"ESZ6\"") && s.contains("\"proxy\":false"));
    assert ticks.getFirst().contains("\"ts\":\"2022-01-03T14:30:00Z\"") && ticks.getFirst().contains("\"bid\":4999.75");
    var replayLines = Files.readAllLines(replayFile);
    var replayTicks = replayLines.stream().filter(s -> s.contains("\"type\":\"tick\"")).toList();
    assert prices(replayTicks).equals(List.of(20, 21)) : prices(replayTicks);
    assert replayTicks.stream().allMatch(s -> s.contains("\"mode\":\"replay\"")
        && s.contains("\"provider\":\"" + REPLAY_SOURCE + "\""));
    // Three live prints were skipped on purpose. A gap means the queue overflowed, and it did not.
    assert lines.stream().noneMatch(s -> s.contains("\"type\":\"gap\"")) : "A skipped print is not a gap";
    assert replayLines.stream().noneMatch(s -> s.contains("\"type\":\"gap\""));
    assert lines.stream().filter(s -> s.contains("\"type\":\"execution\"")).count() == 7 : "Fills are written whatever the gate does";
    try (var left = Files.walk(folder)) {
      for (var path : left.sorted(java.util.Comparator.reverseOrder()).toList()) Files.delete(path);
    }
    System.out.println("Journal ticks passed: skipped while flat, pre-roll on entry, tail after exit, re-arming, "
        + "a trade between two polls, replay apart from live, symbol aliases, no gap events for skipped prints.");
  }

  private static void live(TickGate<Held> gate, JournalEventWriter target, JournalAccountCapture capture, int n) {
    JournalTicks.record(gate, target, held("motivewave-ticks", n), "live",
        JournalTicks.armed(capture, IN_POSITION, false, "ESZ6", "ESZ6", "ESZ6"));
  }
  private static void replay(TickGate<Held> gate, JournalEventWriter target, JournalAccountCapture capture, int n) {
    JournalTicks.record(gate, target, held(REPLAY_SOURCE, n), "replay",
        JournalTicks.armed(capture, IN_POSITION, true, "ESZ6", "ESZ6", "ESZ6"));
  }
  private static Held held(String source, int n) {
    return new Held("ESZ6", false, source,
        new JournalPriceEvents.Print(TIME + n * 250L, 5000 + n, 4999.75f + n, 5000.25f + n, 1));
  }
  private static List<Integer> prices(List<String> ticks) {
    var result = new ArrayList<Integer>();
    for (String tick : ticks) {
      int at = tick.indexOf("\"price\":") + 8;
      result.add((int)Float.parseFloat(tick.substring(at, tick.indexOf(',', at))) - 5000);
    }
    return result;
  }
  private static JournalNativeAccess.Fill fill(String id, boolean buy, boolean replay) {
    return fill(id, buy, replay, "ESZ6");
  }
  private static JournalNativeAccess.Fill fill(String id, boolean buy, boolean replay, String symbol) {
    return new JournalNativeAccess.Fill(id, replay ? "REPLAY-1" : "paper", symbol, buy, 1, 5000, TIME, 2, 50, "USD",
        "order-" + id, "", buy, replay);
  }
}
