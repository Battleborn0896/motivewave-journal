package org.mwjournal.study;

import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.time.Instant;
import java.util.ArrayList;
import java.util.List;
import java.util.concurrent.ArrayBlockingQueue;
import java.util.concurrent.CancellationException;
import java.util.concurrent.atomic.AtomicBoolean;
import java.util.function.BooleanSupplier;
import org.mwjournal.study.JournalBackfill.Ledger;
import org.mwjournal.study.JournalBackfill.Spans;
import org.mwjournal.study.JournalBackfill.Window;
import org.mwjournal.study.JournalNativeAccess.Fill;

/** Loading real ticks for past trades: which windows the fills ask for, and the bookkeeping around
 * loading them. The tick history is a fixture and the writer is the real one or a small stand-in;
 * no MotiveWave, no data service. */
public final class JournalBackfillTest {
  private JournalBackfillTest() {}
  private static final long T = Instant.parse("2026-10-07T14:00:00Z").toEpochMilli();
  private static final long NOW = T + 3_600_000L, DAY = 86_400_000L, PAD = JournalBackfill.PAD_MILLIS;
  private static final BooleanSupplier NEVER = () -> false;
  private static int next;

  /** A tick history that has a print every 250 ms, and remembers what it was asked for. */
  private static final class History implements JournalBackfill.TickSource {
    final List<Window> asked = new ArrayList<>();
    final List<Window> empty = new ArrayList<>(); // stretches it has nothing for
    Exception failure; // what asking fails with, if anything
    Runnable during;   // called once, in the middle of answering
    @Override public void forEach(long start, long end, java.util.function.Consumer<JournalPriceEvents.Print> each)
        throws Exception {
      asked.add(new Window(start, end));
      if (failure != null) throw failure;
      for (Window none : empty) if (start < none.end() && end > none.start()) return;
      for (long time = start - start % 250; time < end + 1000; time += 250) {
        if (during != null && time >= (start + end) / 2) { Runnable once = during; during = null; once.run(); }
        float price = 5000 + (time - T) % 40_000 / 1000f;
        each.accept(new JournalPriceEvents.Print(time, price, price - .25f, price + .25f, 1));
      }
    }
  }

  public static void main(String[] args) throws Exception {
    var folder = Files.createTempDirectory("mwj-backfill-fixture-");
    // Which charts load history at all: live, switched on, and showing the very contract of the fills.
    assert JournalBackfill.applies("ESZ6", "ESZ6", false, false, 30);
    assert !JournalBackfill.applies("ESZ6", "ESZ6", false, false, 0) : "0 days is off";
    assert !JournalBackfill.applies("ESZ6", "ESZ6", false, true, 30) : "Never in replay";
    assert !JournalBackfill.applies("ESZ6", "ESZ6", false, null, 30) : "Not before the chart has said live or replay";
    assert !JournalBackfill.applies("@ES", "ESZ6", true, false, 30) : "A continuous chart is not the contract";
    assert !JournalBackfill.applies("ESZ6", "MESZ6", false, false, 30) : "Nor is a chart standing in for another symbol";
    assert !JournalBackfill.applies("ESZ6", "ESZ6", true, false, 30) : "Nor one marked as proxy prices";
    assert !JournalBackfill.applies(null, null, false, false, 30) && !JournalBackfill.applies(" ", " ", false, false, 30);
    windows();
    spans();
    bookkeeping(folder);
    overlapAndPasses(folder);
    cap(folder);
    backpressure(folder);
    cancelling(folder);
    try (var left = Files.walk(folder)) {
      for (var path : left.sorted(java.util.Comparator.reverseOrder()).toList()) Files.delete(path);
    }
    System.out.println("Journal backfill passed: windows from fills (scale-in, partial exits, back to back, a missing "
        + "entry, replay and other symbols left out), done once across restarts, an empty answer asked again only "
        + "by a later load, the tick cap and its gap event, waiting for the writer's queue, cancelling, two charts.");
  }

  private static void windows() {
    var fills = new ArrayList<Fill>();
    // Scale-in: one position, three fills.
    fills.add(fill("paper", "ESZ6", "scale", true, 1, 0, false));
    fills.add(fill("paper", "ESZ6", "scale", true, 1, 10, false));
    fills.add(fill("paper", "ESZ6", "scale", false, 2, 60, false));
    // Partial exits.
    fills.add(fill("paper", "ESZ6", "partial", true, 3, 100, false));
    fills.add(fill("paper", "ESZ6", "partial", false, 1, 130, false));
    fills.add(fill("paper", "ESZ6", "partial", false, 2, 160, false));
    // Two positions back to back: out of a long, into a short two seconds later.
    fills.add(fill("paper", "ESZ6", "long", true, 1, 200, false));
    fills.add(fill("paper", "ESZ6", "long", false, 1, 230, false));
    fills.add(fill("paper", "ESZ6", "short", false, 1, 232, false));
    fills.add(fill("paper", "ESZ6", "short", true, 1, 260, false));
    var expected = List.of(new Window(T - PAD, T + 60_000 + PAD), new Window(T + 100_000 - PAD, T + 160_000 + PAD),
        new Window(T + 200_000 - PAD, T + 230_000 + PAD), new Window(T + 232_000 - PAD, T + 260_000 + PAD));
    assert JournalBackfill.windows(fills, "ESZ6", NOW - 30 * DAY, NOW).equals(expected)
        : JournalBackfill.windows(fills, "ESZ6", NOW - 30 * DAY, NOW);

    // An exit whose entry MotiveWave no longer has, and an entry still open: not flat, so there is no
    // complete trade to measure and no window. Netting them flat-to-flat would join them into one
    // window from the first to the second, hours long.
    fills.add(fill("paper", "ESZ6", "entry-lost", false, 1, 300, false));
    fills.add(fill("paper", "ESZ6", "still-open", true, 1, 2000, false));
    // Replay fills, another instrument, and fills MotiveWave gave no position id.
    fills.add(fill("REPLAY-1", "ESZ6", "replayed", true, 1, 400, true));
    fills.add(fill("REPLAY-1", "ESZ6", "replayed", false, 1, 430, true));
    fills.add(fill("paper", "NQZ6", "other", true, 1, 500, false));
    fills.add(fill("paper", "NQZ6", "other", false, 1, 530, false));
    fills.add(fill("paper", "ESZ6", "", true, 1, 600, false));
    fills.add(fill("paper", "ESZ6", "", false, 1, 630, false));
    assert JournalBackfill.windows(fills, "ESZ6", NOW - 30 * DAY, NOW).equals(expected) : "None of these is a window";
    assert JournalBackfill.windows(fills, "NQZ6", NOW - 30 * DAY, NOW)
        .equals(List.of(new Window(T + 500_000 - PAD, T + 530_000 + PAD)));
    assert JournalBackfill.windows(fills, null, NOW - 30 * DAY, NOW).isEmpty();
    assert JournalBackfill.windows(fills, "CLZ6", NOW - 30 * DAY, NOW).isEmpty();

    // Bounds: 24 hours, the days setting, and a position that traded moments ago.
    var bounds = new ArrayList<Fill>();
    bounds.add(fill("paper", "ESZ6", "too-long", true, 1, -3 * 86_400, false));
    bounds.add(fill("paper", "ESZ6", "too-long", false, 1, -2 * 86_400, false));
    bounds.add(fill("paper", "ESZ6", "all-day", true, 1, -5 * 86_400, false));
    bounds.add(fill("paper", "ESZ6", "all-day", false, 1, -4 * 86_400 - 60, false));
    bounds.add(fill("paper", "ESZ6", "old", true, 1, -31 * 86_400, false));
    bounds.add(fill("paper", "ESZ6", "old", false, 1, -31 * 86_400 + 60, false));
    bounds.add(fill("paper", "ESZ6", "fresh", true, 1, 3540, false));
    bounds.add(fill("paper", "ESZ6", "fresh", false, 1, 3570, false));
    long settled = NOW - JournalBackfill.SETTLE_MILLIS;
    assert JournalBackfill.windows(bounds, "ESZ6", NOW - 30 * DAY, settled)
        .equals(List.of(new Window(T - 5 * DAY - PAD, T - 4 * DAY - 60_000 + PAD)))
        : "Only the position just under 24 hours: " + JournalBackfill.windows(bounds, "ESZ6", NOW - 30 * DAY, settled);
    assert JournalBackfill.windows(bounds, "ESZ6", NOW - 60 * DAY, settled).size() == 2 : "A longer setting reaches the old one";
    assert JournalBackfill.windows(bounds, "ESZ6", NOW - 30 * DAY, NOW).size() == 2 : "Later, the fresh one has settled";

    // Copied accounts hold the same position at the same times: one window, not one each. The same
    // position id in two accounts at different times is two positions.
    var copies = new ArrayList<Fill>();
    for (String account : List.of("one", "two", "three")) {
      copies.add(new Fill("in-" + account, account, "ESZ6", true, 1, 5000, T, 2, 50, "USD", "o", "copied", true, false));
      copies.add(new Fill("out-" + account, account, "ESZ6", false, 1, 5001, T + 30_000, 2, 50, "USD", "o", "copied", false, false));
    }
    assert JournalBackfill.windows(copies, "ESZ6", NOW - DAY, NOW).equals(List.of(new Window(T - PAD, T + 30_000 + PAD)));
    copies.add(new Fill("in-four", "four", "ESZ6", true, 1, 5000, T + 900_000, 2, 50, "USD", "o", "copied", true, false));
    copies.add(new Fill("out-four", "four", "ESZ6", false, 1, 5001, T + 930_000, 2, 50, "USD", "o", "copied", false, false));
    assert JournalBackfill.windows(copies, "ESZ6", NOW - DAY, NOW).size() == 2 : "Grouped by account as well as by id";
  }

  private static void spans() throws IOException {
    var spans = new Spans();
    spans.add(100, 200);
    spans.add(300, 400);
    assert spans.text().equals("100-200,300-400");
    assert spans.missing(50, 450).equals(List.of(new Window(50, 100), new Window(200, 300), new Window(400, 450)));
    assert spans.missing(100, 200).isEmpty() && spans.covers(120, 180) && !spans.covers(150, 250);
    assert spans.missing(150, 350).equals(List.of(new Window(200, 300)));
    spans.add(200, 300); // touching on both sides joins all three
    assert spans.text().equals("100-400") && spans.covers(100, 400);
    spans.add(50, 120);
    spans.add(390, 500);
    assert spans.text().equals("50-500");
    spans.remove(200, 300);
    assert spans.text().equals("50-200,300-500") : spans.text();
    spans.remove(0, 60);
    spans.remove(450, 600);
    assert spans.text().equals("60-200,300-450") : spans.text();
    spans.remove(100, 400);
    assert spans.text().equals("60-100,400-450") : spans.text();
    spans.add(7, 7);
    assert Spans.parse(spans.text()).text().equals("60-100,400-450") && Spans.parse("").isEmpty();
    spans.dropBefore(101);
    assert spans.text().equals("400-450");
    try {
      Spans.parse("100-two hundred");
      throw new AssertionError("A damaged record must not read as nothing done");
    } catch (IOException expected) { }
  }

  /** Done once, across restarts; an empty or failed answer asked again only by a later study load. */
  private static void bookkeeping(Path folder) throws Exception {
    var file = folder.resolve("motivewave.ndjson");
    var fills = new ArrayList<Fill>();
    fills.add(fill("paper", "ESZ6", "first", true, 1, 0, false));
    fills.add(fill("paper", "ESZ6", "first", false, 1, 60, false));
    fills.add(fill("paper", "ESZ6", "quiet", true, 1, 600, false));
    fills.add(fill("paper", "ESZ6", "quiet", false, 1, 660, false));
    fills.add(fill("REPLAY-1", "ESZ6", "replayed", true, 1, 1200, true));
    fills.add(fill("REPLAY-1", "ESZ6", "replayed", false, 1, 1260, true));
    Window first = new Window(T - PAD, T + 60_000 + PAD), quiet = new Window(T + 600_000 - PAD, T + 660_000 + PAD);
    var history = new History();
    history.empty.add(quiet); // the data service has no ticks for the second position
    var events = new ArrayList<String>();
    var load = new JournalBackfill(new Ledger(file));

    assert load.pass("ESZ6", fills, NOW, 0, history, events::add, NEVER).attempted() == 0 && history.asked.isEmpty()
        : "0 days switches it off";
    assert load.pass("ESZ6", null, NOW, 30, history, events::add, NEVER).attempted() == 0 : "No snapshot yet";
    var result = load.pass("ESZ6", fills, NOW, 30, history, events::add, NEVER);
    assert result.done() == 1 && result.empty() == 1 && result.failed() == 0 && result.remaining() == 0 : result;
    assert history.asked.equals(List.of(first, quiet)) : "Oldest first, and never the replay position: " + history.asked;
    assert result.ticks() == 280 && events.size() == 280 : "70 seconds at four prints a second: " + result.ticks();
    assert events.stream().allMatch(s -> s.startsWith("{\"version\":1,\"type\":\"tick\",\"mode\":\"live\","
        + "\"provider\":\"motivewave-ticks:history\",\"symbol\":\"ESZ6\",\"ts\":\"") && s.endsWith(",\"proxy\":false}"));
    assert events.getFirst().contains("\"ts\":\"2026-10-07T13:59:55Z\"") && events.getLast().contains("\"ts\":\"2026-10-07T14:01:04.750Z\"")
        : "Only prints inside the window, although the history answered with more";

    // The same study load: neither is asked for again, whatever came back.
    result = load.pass("ESZ6", fills, NOW + 60_000, 30, history, events::add, NEVER);
    assert result.attempted() == 0 && result.remaining() == 0 && history.asked.size() == 2 && events.size() == 280;
    String sidecar = Files.readString(Ledger.sidecar(file));
    assert Ledger.sidecar(file).getFileName().toString().equals("motivewave.ndjson.tick-backfill");
    assert sidecar.contains("version=1") && sidecar.contains("done.ESZ6=" + first.start() + "-" + first.end()) : sidecar;
    assert !sidecar.contains("paper") && !sidecar.contains(String.valueOf(quiet.start()))
        : "Symbols and finished times only; an empty answer is not remembered on disk";

    // A restart: the finished window is not loaded again; the empty one is asked for once more.
    history.asked.clear();
    var restarted = new JournalBackfill(new Ledger(file));
    result = restarted.pass("ESZ6", fills, NOW, 30, history, events::add, NEVER);
    assert result.done() == 0 && result.empty() == 1 && history.asked.equals(List.of(quiet)) && events.size() == 280 : result;
    assert restarted.pass("ESZ6", fills, NOW, 30, history, events::add, NEVER).attempted() == 0 && history.asked.size() == 1;
    // By the next load the history has it.
    history.empty.clear();
    history.asked.clear();
    var later = new JournalBackfill(new Ledger(file));
    result = later.pass("ESZ6", fills, NOW, 30, history, events::add, NEVER);
    assert result.done() == 1 && result.empty() == 0 && history.asked.equals(List.of(quiet)) && events.size() == 560 : result;
    assert new Ledger(file).written("ESZ6", first) && new Ledger(file).written("ESZ6", quiet);
    assert !new Ledger(file).written("NQZ6", first) : "Another instrument's history is its own";
    history.asked.clear();
    assert new JournalBackfill(new Ledger(file)).pass("ESZ6", fills, NOW, 30, history, events::add, NEVER).attempted() == 0
        && history.asked.isEmpty() : "Nothing left to do after another restart";

    // The history cannot be asked (no connection): counted, not retried by this load, asked again by the next.
    fills.add(fill("paper", "ESZ6", "offline", true, 1, 1800, false));
    fills.add(fill("paper", "ESZ6", "offline", false, 1, 1830, false));
    history.failure = new IOException("Fixture: not connected");
    result = later.pass("ESZ6", fills, NOW, 30, history, events::add, NEVER);
    assert result.failed() == 1 && result.done() == 0 && "IOException".equals(result.failure()) && history.asked.size() == 1 : result;
    assert later.pass("ESZ6", fills, NOW, 30, history, events::add, NEVER).attempted() == 0 && history.asked.size() == 1;
    history.failure = null;
    result = new JournalBackfill(new Ledger(file)).pass("ESZ6", fills, NOW, 30, history, events::add, NEVER);
    assert result.done() == 1 && result.failed() == 0 && history.asked.size() == 2 : result;

    assert Ledger.shared(file) == Ledger.shared(folder.resolve("other").resolveSibling("motivewave.ndjson"))
        : "Charts writing to one bridge file share one ledger";
    assert Ledger.shared(file).written("ESZ6", first);
    Files.writeString(Ledger.sidecar(folder.resolve("newer.ndjson")), "version=2\n");
    try {
      new Ledger(folder.resolve("newer.ndjson"));
      throw new AssertionError("A record from a later version must not be overwritten");
    } catch (IOException expected) { }
  }

  /** Overlapping windows load their overlap once, a few windows at a time. */
  private static void overlapAndPasses(Path folder) throws Exception {
    var file = folder.resolve("passes.ndjson");
    var fills = new ArrayList<Fill>();
    fills.add(fill("paper", "ESZ6", "long", true, 1, 200, false));
    fills.add(fill("paper", "ESZ6", "long", false, 1, 230, false));
    fills.add(fill("paper", "ESZ6", "short", false, 1, 232, false));
    fills.add(fill("paper", "ESZ6", "short", true, 1, 260, false));
    for (int n = 0; n < 5; n++) {
      fills.add(fill("paper", "ESZ6", "later-" + n, true, 1, 600 + n * 100, false));
      fills.add(fill("paper", "ESZ6", "later-" + n, false, 1, 620 + n * 100, false));
    }
    var history = new History();
    var events = new ArrayList<String>();
    var load = new JournalBackfill(new Ledger(file));
    assert JournalBackfill.WINDOWS_PER_PASS == 4;
    var result = load.pass("ESZ6", fills, NOW, 30, history, events::add, NEVER);
    assert result.done() == 4 && result.remaining() == 3 : "A few at a time, and how many are left: " + result;
    assert history.asked.subList(0, 2).equals(List.of(new Window(T + 200_000 - PAD, T + 230_000 + PAD),
        new Window(T + 230_000 + PAD, T + 260_000 + PAD))) : "The second window starts where the first ended: " + history.asked;
    result = load.pass("ESZ6", fills, NOW, 30, history, events::add, NEVER);
    assert result.done() == 3 && result.remaining() == 0 && history.asked.size() == 7 : result;
    assert load.pass("ESZ6", fills, NOW, 30, history, events::add, NEVER).attempted() == 0 && history.asked.size() == 7;
    assert events.size() == events.stream().distinct().count() : "No print written twice";
    assert events.size() == (70 + 5 * 30) * 4 : events.size();
  }

  /** A window with more ticks than the cap is cut there, and a gap event says exactly what is missing. */
  private static void cap(Path folder) throws Exception {
    var file = folder.resolve("cap.ndjson");
    var fills = List.of(fill("paper", "ESZ6", "busy", true, 1, 0, false), fill("paper", "ESZ6", "busy", false, 1, 10, false));
    var history = new History();
    var events = new ArrayList<String>();
    var load = new JournalBackfill(new Ledger(file), 30, 4); // 80 prints in the window, room for 30
    var result = load.pass("ESZ6", fills, NOW, 30, history, events::add, NEVER);
    assert result.done() == 1 && result.cut() == 1 && result.ticks() == 30 && result.empty() == 0 : result;
    assert events.size() == 31 && events.subList(0, 30).stream().allMatch(s -> s.contains("\"type\":\"tick\""));
    String gap = events.getLast();
    assert gap.startsWith("{\"version\":1,\"type\":\"gap\",\"provider\":\"motivewave-ticks:history\",\"symbol\":\"ESZ6\",\"ts\":\"")
        && gap.endsWith(",\"dropped\":50,\"start_ts\":\"2026-10-07T14:00:02.500Z\",\"end_ts\":\"2026-10-07T14:00:14.750Z\"}")
        : "How many were left out, and from when to when: " + gap;
    assert events.get(29).contains("\"ts\":\"2026-10-07T14:00:02.250Z\"") : "The last print written is the one before the gap";
    // Cut is still done: asking again would only reach the same cap.
    assert load.pass("ESZ6", fills, NOW, 30, history, events::add, NEVER).attempted() == 0;
    assert new JournalBackfill(new Ledger(file), 30, 4).pass("ESZ6", fills, NOW, 30, history, events::add, NEVER).attempted() == 0;
    assert history.asked.size() == 1 && events.size() == 31;
    assert JournalBackfill.MAX_TICKS == 2_000_000 && JournalBackfill.MAX_WINDOW_MILLIS == DAY;
  }

  /** History waits for the writer rather than flooding its queue: nothing dropped, no gap, live prints keep room. */
  private static void backpressure(Path folder) throws Exception {
    // A stand-in writer with room for 8 events that takes a millisecond over each.
    var fills = List.of(fill("paper", "ESZ6", "slow", true, 1, 0, false), fill("paper", "ESZ6", "slow", false, 1, 65, false));
    var queue = new ArrayBlockingQueue<String>(8);
    var written = new ArrayList<String>();
    var running = new AtomicBoolean(true);
    var drain = new Thread(() -> {
      try {
        while (running.get() || !queue.isEmpty()) {
          String event = queue.poll(20, java.util.concurrent.TimeUnit.MILLISECONDS);
          if (event != null) { written.add(event); Thread.sleep(1); }
        }
      } catch (InterruptedException exc) { Thread.currentThread().interrupt(); }
    });
    drain.start();
    var sent = new ArrayList<String>();
    var result = new JournalBackfill(new Ledger(folder.resolve("slow.ndjson"))).pass("ESZ6", fills, NOW, 30, new History(),
        event -> { boolean taken = queue.offer(event); if (taken) sent.add(event); return taken; }, NEVER);
    running.set(false);
    drain.join();
    assert result.done() == 1 && result.ticks() == 300 && result.waits() > 0 : "It must have had to wait: " + result;
    assert written.equals(sent) && written.size() == 300 : "Every print, once, in order: " + written.size();

    // The real writer and its real queue of 65,536: 100,000 history prints, more than it holds at once.
    var file = folder.resolve("flood.ndjson");
    var day = List.of(fill("paper", "ESZ6", "day", true, 1, 0, false), fill("paper", "ESZ6", "day", false, 1, 24_990, false));
    var writer = JournalEventWriter.acquire(file);
    int[] deepest = {0};
    result = new JournalBackfill(new Ledger(file)).pass("ESZ6", day, NOW + DAY, 30, new History(), event -> {
      boolean taken = writer.offerBelow(event, JournalBackfill.HIGH_WATER);
      if (taken && writer.offer(LIVE_PRINT)) deepest[0]++; // a live print beside every history print
      return taken;
    }, NEVER);
    assert !writer.offerBelow("never", 0) : "No room is an answer, not a drop";
    writer.release();
    assert writer.failure == null : writer.failure;
    assert result.done() == 1 && result.ticks() == 100_000 : result;
    var lines = Files.readAllLines(file);
    assert lines.stream().filter(s -> s.contains("motivewave-ticks:history")).count() == 100_000 : "No history print lost";
    assert lines.stream().filter(s -> s.equals(LIVE_PRINT)).count() == 100_000 && deepest[0] == 100_000
        : "Live prints always found room beside the history";
    assert lines.stream().noneMatch(s -> s.contains("\"type\":\"gap\"") || s.contains("never"))
        : "Waiting is not dropping: no gap event";
    assert JournalBackfill.HIGH_WATER == 32_768 : "Half the queue is left for live prints";
  }
  private static final String LIVE_PRINT = "{\"type\":\"tick\",\"provider\":\"motivewave-ticks\",\"fixture\":true}";

  /** Settings changed or the study was removed half way: the window is not done, and is loaded later. */
  private static void cancelling(Path folder) throws Exception {
    var file = folder.resolve("cancel.ndjson");
    var fills = List.of(fill("paper", "ESZ6", "cut-short", true, 1, 0, false), fill("paper", "ESZ6", "cut-short", false, 1, 60, false));
    Window window = new Window(T - PAD, T + 60_000 + PAD);
    var ledger = new Ledger(file);
    var load = new JournalBackfill(ledger);
    var events = new ArrayList<String>();
    try {
      load.pass("ESZ6", fills, NOW, 30, new History(), events::add, () -> events.size() >= 100);
      throw new AssertionError("A cancelled pass must say so");
    } catch (CancellationException expected) { }
    assert events.size() == 100 && !ledger.written("ESZ6", window) && !Files.exists(Ledger.sidecar(file));

    // Whoever iterates the ticks may swallow what a callback throws; the pass is cancelled all the same.
    var swallowing = new History();
    JournalBackfill.TickSource stubborn = (start, end, each) -> swallowing.forEach(start, end, print -> {
      try { each.accept(print); } catch (RuntimeException ignored) { }
    });
    events.clear();
    try {
      load.pass("ESZ6", fills, NOW, 30, stubborn, events::add, () -> events.size() >= 100);
      throw new AssertionError("Still cancelled");
    } catch (CancellationException expected) { }
    assert events.size() == 100 && !ledger.written("ESZ6", window) : "Nothing written after the cancel: " + events.size();

    // Not cancelled the next time: the same load does the whole window. Meanwhile a second chart on the
    // same instrument asks too, in the middle of it, and finds the window taken.
    events.clear();
    var history = new History();
    var otherChart = new JournalBackfill(ledger);
    var otherHistory = new History();
    var seen = new ArrayList<JournalBackfill.Result>();
    history.during = () -> {
      try { seen.add(otherChart.pass("ESZ6", fills, NOW, 30, otherHistory, events::add, NEVER)); }
      catch (IOException exc) { throw new IllegalStateException(exc); }
    };
    var result = load.pass("ESZ6", fills, NOW, 30, history, events::add, NEVER);
    assert result.done() == 1 && result.ticks() == 280 && events.size() == 280 && ledger.written("ESZ6", window) : result;
    assert seen.size() == 1 && seen.getFirst().attempted() == 0 && seen.getFirst().remaining() == 0
        && otherHistory.asked.isEmpty() : "Two charts on one instrument must not both load the window: " + seen;
    assert otherChart.pass("ESZ6", fills, NOW, 30, otherHistory, events::add, NEVER).attempted() == 0 && events.size() == 280;
  }

  /** One fill of a position, at T plus so many seconds. */
  private static Fill fill(String account, String symbol, String position, boolean buy, double qty, long second, boolean replay) {
    return new Fill("fill-" + next++, account, symbol, buy, qty, 5000, T + second * 1000, 2, 50, "USD", "order", position,
        buy, replay);
  }
}
