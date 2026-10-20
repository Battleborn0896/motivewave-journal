package org.mwjournal.study;

import com.motivewave.platform.sdk.common.Bar;
import com.motivewave.platform.sdk.common.Tick;
import java.io.IOException;
import java.lang.reflect.Proxy;
import java.nio.file.Files;
import java.nio.file.Path;
import java.time.Instant;
import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import org.mwjournal.study.JournalTicks.Held;
import static org.mwjournal.study.JournalTicks.IN_POSITION;

/** Writes a small bridge file, and the replay file beside it, with the code the study itself runs:
 * the account capture for fills, linked orders and heartbeats, the tick gate and writer for prints,
 * the history backfill, and the bar and gap serialisers. tests/test_contract.py then reads both files
 * with the app's importer, so a change on either side of the bridge that the other cannot read fails a test.
 *
 * The live file holds three trades in account 'paper', one per instrument:
 * ESZ6, recorded as it happened: long 1 at 5000, one more at 5001, out at 5003.5, with a linked stop,
 *   live prints (motivewave-ticks) and one-minute candles.
 * NQZ6, a past trade MotiveWave already held when the study started, an hour earlier: long 1 at 21000.25,
 *   out a minute later at 21010.5. No live prints; its five ticks come from the history backfill
 *   (motivewave-ticks:history), low 20995.5 and high 21014.
 * MNQZ6, another past trade, half an hour earlier: MotiveWave stored no price for the entry (price 0,
 *   price_known false, the 'platform reported no fill price' label); the exit is at 21020.
 * Because of that one fill every heartbeat is 'connected' with warnings '1 fill with no fill price'.
 * The replay file holds one ESZ6 trade, short 2 from 4800 and 4802 to 4797, and the same heartbeats. Its
 *   replay prints are simulated (motivewave-replay:<run>:simulated). Once it is flat, the replay backfill
 *   loads five real prints of its holding window (motivewave-replay:<run>:history), low 4795.5 and high
 *   4806, with the replay clock three seconds past the exit: the history also answers with a print after
 *   that clock, at 4700, which must never reach the file.
 *
 * Not a suite (its name does not end in Test): scripts/test.sh runs it and passes a fresh temporary path.
 * Fixtures only: no MotiveWave session, account or order is involved. */
public final class ContractSample {
  private ContractSample() {}

  private static final long LIVE = Instant.parse("2026-10-07T14:00:00Z").toEpochMilli();
  private static final long REPLAY = Instant.parse("2022-01-03T14:30:00Z").toEpochMilli();

  public static void main(String[] args) throws Exception {
    if (args.length != 1) throw new IllegalArgumentException("Usage: ContractSample <live bridge file>");
    Path file = Path.of(args[0]).toAbsolutePath().normalize();
    Path replayFile = JournalAccountCapture.replayFile(file);
    Files.createDirectories(file.getParent());
    requireFresh(file);

    var fills = new ArrayList<JournalNativeAccess.Fill>();
    var orders = new ArrayList<JournalNativeAccess.ProtectiveOrder>();
    var capture = JournalAccountCapture.fixture(file,
        () -> new JournalNativeAccess.Snapshot("workspace", List.copyOf(fills), List.copyOf(orders)));
    String run = capture.replayRun("ESZ6", REPLAY);
    String replaySource = "motivewave-replay:" + run + ":simulated";
    var writer = JournalEventWriter.acquire(file);
    var replayWriter = JournalEventWriter.acquire(replayFile);
    var gate = new TickGate<Held>();
    var replayGate = new TickGate<Held>();

    // What MotiveWave already holds when the study starts: two past trades, one with an entry it stored
    // no price for. That fill waits out its grace, so it is written at the second poll, with history's clock.
    long past = LIVE - 3_600_000;
    fills.add(past("past-1", "NQZ6", true, 21000.25, past, "past-position", 20));
    fills.add(past("past-2", "NQZ6", false, 21010.5, past + 60_000, "past-position", 20));
    fills.add(past("unpriced-1", "MNQZ6", true, 0, past + 1_800_000, "unpriced-position", 2));
    fills.add(past("unpriced-2", "MNQZ6", false, 21020, past + 1_860_000, "unpriced-position", 2));
    long started = System.nanoTime();
    // Flat: the first poll writes the heartbeats, and the prints before the entries wait in the pre-roll.
    capture.poll(started);
    capture.poll(started + JournalAccountCapture.UNPRICED_GRACE_NANOS);
    if (!"1 fill with no fill price".equals(capture.warnings)) throw new IllegalStateException(capture.warnings);
    live(gate, writer, capture, 1, 5000.25f);
    replay(replayGate, replayWriter, capture, replaySource, 5, 4800.5f);

    // Live: long 1 at 5000 with a linked stop, one more at 5001, the stop raised, out at 5003.5.
    fills.add(fill("fill-1", false, true, 1, 5000, LIVE + 5_000, "entry-1", true));
    orders.add(order("stop-1", false, false, "entry-1", "stop", "working", 4996, LIVE + 5_000));
    orders.add(order("resting-limit", false, true, "", "target", "working", 4990, LIVE + 5_000)); // not linked: never written
    // Replay: short 1 at 4800 with a linked target, one more at 4802, the target filled at 4797.
    fills.add(fill("replay-1", true, false, 1, 4800, REPLAY + 10_000, "replay-entry-1", true));
    orders.add(order("target-1", true, true, "replay-entry-1", "target", "working", 4797, REPLAY + 10_000));
    capture.poll();
    live(gate, writer, capture, 10, 4998);
    replay(replayGate, replayWriter, capture, replaySource, 20, 4803);

    fills.add(fill("fill-2", false, true, 1, 5001, LIVE + 35_000, "entry-2", true));
    orders.set(0, order("stop-1", false, false, "entry-1", "stop", "working", 4999, LIVE + 60_000));
    fills.add(fill("replay-2", true, false, 1, 4802, REPLAY + 40_000, "replay-entry-2", true));
    capture.poll();
    live(gate, writer, capture, 40, 4999);
    live(gate, writer, capture, 90, 5004);
    live(gate, writer, capture, 124, 5003.25f);
    replay(replayGate, replayWriter, capture, replaySource, 50, 4805);
    replay(replayGate, replayWriter, capture, replaySource, 80, 4796);

    fills.add(fill("fill-3", false, false, 2, 5003.5, LIVE + 125_000, "exit-1", false));
    orders.set(0, order("stop-1", false, false, "entry-1", "stop", "cancelled", 4999, LIVE + 125_000));
    fills.add(fill("replay-3", true, true, 2, 4797, REPLAY + 100_000, "target-1", false));
    orders.set(2, order("target-1", true, true, "replay-entry-1", "target", "filled", 4797, REPLAY + 100_000));
    capture.poll();
    // Flat again, inside the tail a fill leaves: the print right after each exit is still written.
    live(gate, writer, capture, 130, 5003.75f);
    replay(replayGate, replayWriter, capture, replaySource, 105, 4797.25f);
    if (capture.failure != null) throw new IllegalStateException(capture.failure);

    // The past NQ trade was not recorded live: its ticks are asked from the tick history, as the study
    // does on a chart of that contract. The history here answers with a little more than the window.
    var backfill = new JournalBackfill(new JournalBackfill.Ledger(file));
    var loaded = backfill.pass("NQZ6", capture.fills(), LIVE + 600_000, 30, (start, end, each) -> {
      each.accept(print(past - 8_000, 20990));
      each.accept(print(past - 3_000, 21000));
      each.accept(print(past + 5_000, 20995.5f));
      each.accept(print(past + 30_000, 21014));
      each.accept(print(past + 59_000, 21010.75f));
      each.accept(print(past + 63_000, 21011));
      each.accept(print(past + 90_000, 21030));
    }, event -> writer.offerBelow(event, JournalBackfill.HIGH_WATER), () -> false);
    if (loaded.done() != 1 || loaded.ticks() != 5) throw new IllegalStateException("History backfill: " + loaded);

    // The replay trade is flat: its real ticks are asked from the tick history for its own run, up to the
    // replay's present and no further. The history answers with more than that, before and after.
    long clock = REPLAY + 103_000;
    var replayBackfill = new JournalBackfill(new JournalBackfill.Ledger(replayFile, true));
    var replayed = replayBackfill.replayPass("ESZ6", capture.replayFills(), clock, System.currentTimeMillis(), 30,
        (start, end, each) -> {
          each.accept(print(REPLAY + 2_000, 4810));
          each.accept(print(REPLAY + 8_000, 4799));
          each.accept(print(REPLAY + 25_000, 4801.5f));
          each.accept(print(REPLAY + 55_000, 4806));
          each.accept(print(REPLAY + 85_000, 4795.5f));
          each.accept(print(REPLAY + 101_000, 4797.5f));
          each.accept(print(REPLAY + 104_000, 4700)); // after the replay clock: the future, never written
        }, event -> replayWriter.offerBelow(event, JournalBackfill.HIGH_WATER), () -> false);
    if (replayed.done() != 1 || replayed.ticks() != 5) throw new IllegalStateException("Replay backfill: " + replayed);

    // One-minute candles, as the history loader writes them; live only, replay never reads ahead.
    bar(writer, 0, 5000.25f, 5001.5f, 4997.75f, 5001, 1200);
    bar(writer, 1, 5001, 5004.25f, 5000.75f, 5004, 900);
    bar(writer, 2, 5004, 5004.5f, 5003, 5003.75f, 700);
    // A gap line, as written after a full queue; ten minutes before the trade, so it does not touch it.
    writer.offer(JournalEventWriter.gap("motivewave-ticks", 3, Instant.ofEpochMilli(LIVE - 600_000),
        Instant.ofEpochMilli(LIVE - 590_000)));

    writer.release();
    replayWriter.release();
    capture.close();
    if (writer.failure != null) throw writer.failure;
    if (replayWriter.failure != null) throw replayWriter.failure;
    System.out.println("Contract sample written: " + Files.readAllLines(file).size() + " live events to " + file
        + " and " + Files.readAllLines(replayFile).size() + " replay events to " + replayFile.getFileName() + ".");
  }

  /** A sample must never erase existing bridge files or sibling files with a similar name. */
  private static void requireFresh(Path file) throws IOException {
    for (Path candidate : List.of(file, JournalAccountCapture.replayFile(file),
        file.resolveSibling(file.getFileName() + ".capture-state"),
        file.resolveSibling(file.getFileName() + ".replay-clock"), JournalBackfill.Ledger.sidecar(file),
        JournalBackfill.Ledger.sidecar(JournalAccountCapture.replayFile(file))))
      if (Files.exists(candidate)) throw new IOException("Use a fresh temporary sample path: " + candidate);
  }

  private static void live(TickGate<Held> gate, JournalEventWriter target, JournalAccountCapture capture,
                           int second, float price) {
    JournalTicks.record(gate, target, new Held("ESZ6", false, "motivewave-ticks", print(LIVE + second * 1000L, price)),
        "live", JournalTicks.armed(capture, IN_POSITION, false, "ESZ6", "ESZ6", "ESZ6"));
  }

  private static void replay(TickGate<Held> gate, JournalEventWriter target, JournalAccountCapture capture,
                             String source, int second, float price) {
    JournalTicks.record(gate, target, new Held("ESZ6", false, source, print(REPLAY + second * 1000L, price)),
        "replay", JournalTicks.armed(capture, IN_POSITION, true, "ESZ6", "ESZ6", "ESZ6"));
  }

  /** Through the SDK's Tick interface, the way the study receives a print. */
  private static JournalPriceEvents.Print print(long time, float price) {
    Tick tick = fixture(Tick.class, Map.of("getTime", time, "getPrice", price, "getBidPrice", price - 0.25f,
        "getAskPrice", price + 0.25f, "getVolumeAsFloat", 2f));
    return JournalPriceEvents.Print.of(tick);
  }

  private static void bar(JournalEventWriter target, int minute, float open, float high, float low, float close,
                          float volume) {
    long start = LIVE + minute * 60_000L;
    Bar bar = fixture(Bar.class, Map.of("isComplete", true, "getStartTime", start, "getSizeMillis", 60000L,
        "getOpen", open, "getHigh", high, "getLow", low, "getClose", close, "getVolumeAsFloat", volume));
    target.offer(JournalPriceEvents.bar("ESZ6", bar, false), "motivewave-bars:1m", Instant.ofEpochMilli(start).toString());
  }

  private static JournalNativeAccess.Fill fill(String id, boolean replay, boolean buy, double qty, double price,
                                               long time, String order, boolean entry) {
    return new JournalNativeAccess.Fill(id, replay ? "REPLAY-1" : "paper", "ESZ6", buy, qty, price, time,
        1.25 * qty, 50, "USD", order, replay ? "replay-position" : "position", entry, replay);
  }

  /** A fill of a trade that was over before the study started. */
  private static JournalNativeAccess.Fill past(String id, String symbol, boolean buy, double price, long time,
                                               String position, double pointValue) {
    return new JournalNativeAccess.Fill(id, "paper", symbol, buy, 1, price, time, 1.25, pointValue, "USD",
        "order-" + id, position, buy, false);
  }

  private static JournalNativeAccess.ProtectiveOrder order(String id, boolean replay, boolean buy, String parent,
                                                           String role, String state, double price, long time) {
    return new JournalNativeAccess.ProtectiveOrder(id, replay ? "REPLAY-1" : "paper", "ESZ6", buy, parent,
        "", role, state, price, time, replay);
  }

  private static <T> T fixture(Class<T> type, Map<String, Object> values) {
    return type.cast(Proxy.newProxyInstance(type.getClassLoader(), new Class<?>[]{type},
        (proxy, method, arguments) -> values.get(method.getName())));
  }
}
