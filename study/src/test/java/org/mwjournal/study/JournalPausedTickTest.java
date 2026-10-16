package org.mwjournal.study;

import java.nio.file.Files;
import java.util.ArrayList;
import java.util.List;
import org.mwjournal.study.JournalTicks.Held;

/** No tick follows the fills: paused replay and a quiet live tape must still release observed prices. */
public final class JournalPausedTickTest {
  private JournalPausedTickTest() {}
  private static final long TIME = 1641220200000L;

  public static void main(String[] args) throws Exception {
    var folder = Files.createTempDirectory("mwj-paused-fixture-");
    var file = folder.resolve("motivewave.ndjson");
    var fills = new ArrayList<JournalNativeAccess.Fill>();
    var capture = JournalAccountCapture.fixture(file,
        () -> new JournalNativeAccess.Snapshot("workspace", List.copyOf(fills), List.of()));
    var live = JournalEventWriter.acquire(file);
    var replay = JournalEventWriter.acquire(JournalAccountCapture.replayFile(file));
    var liveGate = new TickGate<Held>();
    var replayGate = new TickGate<Held>();
    capture.watch(liveGate, replayGate);
    String run = capture.replayRun("ESZ6", TIME);
    String source = "motivewave-replay:" + run + ":ticks";
    capture.poll(); // baseline: flat

    JournalTicks.record(liveGate, live, held("MESZ6", "motivewave-ticks", 4999), "live", false);
    JournalTicks.record(replayGate, replay, held("ESZ6", source, 5003), "replay", false);
    JournalTicks.record(replayGate, replay, held("ESZ6", source, 4998), "replay", false);
    capture.poll(); // no fills: no flat pre-roll is written
    assert liveGate.size() == 1 && replayGate.size() == 2;

    fills.add(fill("r-in", true, true));
    fills.add(fill("r-out", false, true));
    capture.poll(); // whole replay scalp between polls; no subsequent price callback
    assert replayGate.size() == 0 : "Paused replay must flush immediately after seeing fills";
    assert liveGate.size() == 1 : "Replay activity must not flush live prices";

    fills.add(fill("l-in", true, false));
    capture.poll();
    assert liveGate.size() == 0 : "A proxy override must still match the actual chart instrument";
    fills.add(fill("l-out", false, false));
    capture.poll(System.nanoTime() + JournalAccountCapture.TRADED_LINGER_NANOS + 1_000_000_000L);
    // Removing a chart unregisters its gates; the poller cannot retain or write its old buffer.
    capture.unwatch(liveGate, replayGate);
    JournalTicks.record(replayGate, replay, held("ESZ6", source, 5100), "replay", false);
    fills.add(fill("r-next", true, true));
    capture.poll();
    assert replayGate.size() == 1;
    live.release();
    replay.release();
    capture.close();
    var replayTicks = Files.readAllLines(JournalAccountCapture.replayFile(file)).stream()
        .filter(line -> line.contains("\"type\":\"tick\"")).toList();
    assert replayTicks.size() == 2 : "Both extrema, exactly once, even with no next tick";
    assert replayTicks.get(0).contains("\"price\":5003.0") && replayTicks.get(1).contains("\"price\":4998.0");
    assert replayTicks.stream().allMatch(line -> line.contains(source) && line.contains("\"mode\":\"replay\""));
    var liveTicks = Files.readAllLines(file).stream().filter(line -> line.contains("\"type\":\"tick\"")).toList();
    assert liveTicks.size() == 1 && liveTicks.getFirst().contains("\"symbol\":\"MESZ6\"");
    assert capture.failure == null : capture.failure;
    try (var files = Files.walk(folder)) {
      for (var path : files.sorted(java.util.Comparator.reverseOrder()).toList()) Files.delete(path);
    }
    System.out.println("Paused tick capture passed: immediate drain after fills, live/replay isolation, aliases, unregister.");
  }

  private static Held held(String symbol, String source, float price) {
    return new Held(symbol, !symbol.equals("ESZ6"), source,
        new JournalPriceEvents.Print(TIME + 1000, price, price - .25f, price + .25f, 1), "ESZ6", "ESZ6");
  }
  private static JournalNativeAccess.Fill fill(String id, boolean buy, boolean replay) {
    return new JournalNativeAccess.Fill(id, replay ? "REPLAY-1" : "paper", "ESZ6", buy, 1, 5000,
        TIME, 2, 50, "USD", id, "", buy, replay);
  }
}
