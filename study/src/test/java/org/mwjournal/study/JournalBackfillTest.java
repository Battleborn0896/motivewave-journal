package org.mwjournal.study;

import com.motivewave.platform.sdk.common.Instrument;
import com.motivewave.platform.sdk.common.Tick;
import com.motivewave.platform.sdk.common.TickOperation;
import java.io.IOException;
import java.lang.reflect.Proxy;
import java.nio.file.Files;
import java.nio.file.Path;
import java.time.Instant;
import java.util.ArrayList;
import java.util.List;
import java.util.Map;
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
    // Which charts load history at all: in the mode of the fills (a live chart loads live trades, a replay
    // chart replay trades), for the trades of the chart's active contract.
    assert JournalBackfill.applies("ESZ6", false, false) : "A live chart, live trades";
    assert JournalBackfill.applies("ESZ6", true, true) : "A replay chart, replay trades";
    assert !JournalBackfill.applies("ESZ6", true, false) : "A replay chart never loads live trades";
    assert !JournalBackfill.applies("ESZ6", false, true) : "A live chart never loads replay trades";
    for (boolean replay : new boolean[]{false, true}) {
      assert !JournalBackfill.applies("ESZ6", null, replay) : "Not before the chart has said live or replay";
      assert !JournalBackfill.applies(null, replay, replay) && !JournalBackfill.applies(" ", replay, replay);
    }
    fillInstruments(folder);
    windows();
    afterExitWindows();
    spans();
    bookkeeping(folder);
    overlapAndPasses(folder);
    cap(folder);
    backpressure(folder);
    cancelling(folder);
    afterExitPasses(folder);
    replayWindows();
    replayPasses(folder);
    replayLedger(folder);
    replayAfterExit(folder);
    try (var left = Files.walk(folder)) {
      for (var path : left.sorted(java.util.Comparator.reverseOrder()).toList()) Files.delete(path);
    }
    System.out.println("Journal backfill passed: a chart backfills its active contract's trades, continuous or not, "
        + "from the fills' own instrument (never the chart's; none is no history), live and replay; "
        + "windows from fills (scale-in, partial exits, back to back, a missing "
        + "entry, replay and other symbols left out), done once across restarts, an empty answer asked again only "
        + "by a later load, the tick cap and its gap event, waiting for the writer's queue, cancelling, two charts; "
        + "the minutes after each exit, whole once settled, after every holding window, once, under the same provider; "
        + "replay: windows per run and position, clamped to the replay clock, nothing past it written, the rest "
        + "loaded once the replay plays on, its own provider and sidecar, rewinds and restarts, no live leakage, and "
        + "the minutes after a replay exit loaded as the replay plays through them.");
  }

  /** Where the ticks come from: the instrument of the fills of the chart's active contract, from the account
   * snapshot, never the chart's own instrument. A continuous NQ chart whose front contract is NQZ6 backfills
   * NQZ6's trades and the minutes after their exits, as an NQZ6 chart does; ESZ6's trades are left to an ES
   * chart; a contract the snapshot has no instrument for has no history, so its windows come back empty. */
  private static void fillInstruments(Path folder) throws Exception {
    var asked = new java.util.LinkedHashMap<String, List<Window>>();
    Instrument chart = instrument("NQ", "NQZ6", asked); // what the chart shows: maybe back-adjusted
    var instruments = Map.of(
        JournalNativeAccess.contract(false, "NQZ6"), instrument("NQZ6", "NQZ6", asked),
        JournalNativeAccess.contract(false, "ESZ6"), instrument("ESZ6", "ESZ6", asked),
        JournalNativeAccess.contract(true, "NQZ6"), instrument("replay NQZ6", "NQZ6", asked));
    String contract = JournalPriceEvents.contract(chart);
    assert contract.equals("NQZ6") && JournalBackfill.applies(contract, false, false)
        : "A continuous chart backfills its front contract";

    var fills = List.of(fill("paper", "NQZ6", "nq", true, 1, 0, false), fill("paper", "NQZ6", "nq", false, 1, 60, false),
        fill("paper", "ESZ6", "es", true, 1, 120, false), fill("paper", "ESZ6", "es", false, 1, 180, false));
    var events = new ArrayList<String>();
    var load = new JournalBackfill(new Ledger(folder.resolve("instruments.ndjson")));
    var result = load.pass(contract, fills, NOW, 30, 30, JournalBackfill.history(instruments, false, contract),
        events::add, NEVER);
    assert result.done() == 2 && result.empty() == 0 : result;
    assert asked.keySet().equals(java.util.Set.of("NQZ6")) : "Only the fills' own instrument is asked: " + asked;
    assert asked.get("NQZ6").equals(List.of(new Window(T - PAD, T + 60_000 + PAD),
        new Window(T + 60_000 + PAD, T + 60_000 + 30 * 60_000))) : "The holding window, then the 30 minutes after the exit";
    assert events.size() == (60_000 + 30 * 60_000 + PAD) / 250 && events.stream().allMatch(e -> e.startsWith(
        "{\"version\":1,\"type\":\"tick\",\"mode\":\"live\",\"provider\":\"motivewave-ticks:history\",\"symbol\":\"NQZ6\",")
        && e.endsWith(",\"proxy\":false}")) : events.size();
    assert new Ledger(folder.resolve("instruments.ndjson")).written("NQZ6", new Window(T - PAD, T + 1_860_000))
        && !new Ledger(folder.resolve("instruments.ndjson")).written("ESZ6", new Window(T + 120_000, T + 180_000))
        : "ESZ6's trade is left alone";

    // Replay: the replay instrument of the contract, never the live one.
    asked.clear();
    var replayFills = List.of(replay("run-a", "NQZ6", "RPLY-1", true, 0), replay("run-a", "NQZ6", "RPLY-1", false, 60));
    var replayLoad = new JournalBackfill(new Ledger(folder.resolve("instruments.replay.ndjson"), true));
    result = replayLoad.replayPass(contract, replayFills, R + 120_000, NOW, 30, 0,
        JournalBackfill.history(instruments, true, contract), events::add, NEVER);
    assert result.done() == 1 && asked.keySet().equals(java.util.Set.of("replay NQZ6")) : asked;

    // MNQZ6: no instrument in the snapshot, so no tick history. Empty, and asked again only by a later load.
    var micro = List.of(fill("paper", "MNQZ6", "m", true, 1, 0, false), fill("paper", "MNQZ6", "m", false, 1, 60, false));
    asked.clear();
    events.clear();
    var none = JournalBackfill.history(instruments, false, "MNQZ6");
    result = load.pass("MNQZ6", micro, NOW, 30, 0, none, events::add, NEVER);
    assert result.empty() == 1 && result.done() == 0 && result.failed() == 0 && events.isEmpty() && asked.isEmpty() : result;
    assert load.pass("MNQZ6", micro, NOW, 30, 0, none, events::add, NEVER).attempted() == 0 : "Not again in this load";
    assert new JournalBackfill(new Ledger(folder.resolve("instruments.ndjson")))
        .pass("MNQZ6", micro, NOW, 30, 0, none, events::add, NEVER).empty() == 1 : "A later load asks again";
    // Before the first snapshot there are no instruments at all.
    assert new JournalBackfill(new Ledger(folder.resolve("no-snapshot.ndjson"))).pass(contract, fills, NOW, 30, 0,
        JournalBackfill.history(Map.of(), false, contract), events::add, NEVER).empty() == 1 && asked.isEmpty();

    // As the study wires it: the fills and their instruments both come from the account capture's last snapshot.
    var capture = JournalAccountCapture.fixture(folder.resolve("snapshot.ndjson"), () -> new JournalNativeAccess.Snapshot(
        "workspace", fills, List.of(), Map.of(), JournalNativeAccess.OrderCounts.of(List.of()), instruments));
    assert capture.fills() == null && capture.instruments().isEmpty() : "Nothing before the first snapshot";
    capture.poll();
    assert capture.failure == null && capture.fills().equals(fills) && capture.instruments().equals(instruments)
        : capture.failure;
    asked.clear();
    events.clear();
    result = new JournalBackfill(new Ledger(folder.resolve("from-capture.ndjson"))).pass(contract, capture.fills(), NOW,
        30, 30, JournalBackfill.history(capture.instruments(), false, contract), events::add, NEVER);
    assert result.done() == 2 && asked.keySet().equals(java.util.Set.of("NQZ6")) : result + " " + asked;
    capture.close();
  }

  /** An instrument whose tick history has a print every 250 ms; it notes each stretch asked under its symbol,
   * and checks that no ticks are ever made from bars. */
  private static Instrument instrument(String symbol, String active, java.util.Map<String, List<Window>> asked) {
    return Instrument.class.cast(Proxy.newProxyInstance(Instrument.class.getClassLoader(),
        new Class<?>[]{Instrument.class}, (proxy, method, arguments) -> switch (method.getName()) {
          case "getSymbol" -> symbol;
          case "getActiveSymbol" -> active;
          case "forEachTick" -> {
            if (arguments.length != 5 || (Boolean) arguments[2] || (Boolean) arguments[3])
              throw new AssertionError("Only real ticks: forEachTick(start, end, false, false, each)");
            long start = (Long) arguments[0], end = (Long) arguments[1];
            asked.computeIfAbsent(symbol, key -> new ArrayList<>()).add(new Window(start, end));
            for (long time = start - start % 250; time < end; time += 250) {
              long at = time;
              ((TickOperation) arguments[4]).onTick(Tick.class.cast(Proxy.newProxyInstance(Tick.class.getClassLoader(),
                  new Class<?>[]{Tick.class}, (tick, getter, none) -> switch (getter.getName()) {
                    case "getTime" -> at;
                    case "getPrice" -> 21000f;
                    case "getBidPrice" -> 20999.75f;
                    case "getAskPrice" -> 21000.25f;
                    case "getVolumeAsFloat" -> 1f;
                    default -> null;
                  })));
            }
            yield null;
          }
          default -> null;
        }));
  }

  /** The minutes after each exit: the same positions as the holding windows, from the last fill on. */
  private static void afterExitWindows() {
    var fills = new ArrayList<Fill>();
    fills.add(fill("paper", "ESZ6", "first", true, 1, 0, false));
    fills.add(fill("paper", "ESZ6", "first", false, 1, 60, false));
    fills.add(fill("paper", "ESZ6", "second", false, 2, 600, false));
    fills.add(fill("paper", "ESZ6", "second", true, 1, 630, false));
    fills.add(fill("paper", "ESZ6", "second", true, 1, 660, false));
    // Never a window, as for the holding time: still open, replay, another instrument, no position id.
    fills.add(fill("paper", "ESZ6", "still-open", true, 1, 700, false));
    fills.add(fill("REPLAY-1", "ESZ6", "replayed", true, 1, 400, true));
    fills.add(fill("REPLAY-1", "ESZ6", "replayed", false, 1, 430, true));
    fills.add(fill("paper", "NQZ6", "other", true, 1, 500, false));
    fills.add(fill("paper", "NQZ6", "other", false, 1, 530, false));
    fills.add(fill("paper", "ESZ6", "", true, 1, 800, false));
    fills.add(fill("paper", "ESZ6", "", false, 1, 830, false));
    long minute = 60_000;
    Window first = new Window(T + 60_000, T + 60_000 + 30 * minute), second = new Window(T + 660_000, T + 660_000 + 30 * minute);
    assert JournalBackfill.afterExit(fills, "ESZ6", NOW - 30 * DAY, NOW, 30).equals(List.of(first, second))
        : JournalBackfill.afterExit(fills, "ESZ6", NOW - 30 * DAY, NOW, 30);
    assert JournalBackfill.afterExit(fills, "ESZ6", NOW - 30 * DAY, second.end() - 1, 30).equals(List.of(first))
        : "Asked for whole, once all of it is over";
    assert JournalBackfill.afterExit(fills, "ESZ6", NOW - 30 * DAY, NOW, 0).isEmpty() : "0 minutes: none";
    assert JournalBackfill.afterExit(fills, "ESZ6", T + 61_000 + PAD, NOW, 30).equals(List.of(second))
        : "Positions that ended before the days asked for reach back are left out, as their holding windows are";
    assert JournalBackfill.afterExit(fills, "NQZ6", NOW - 30 * DAY, Long.MAX_VALUE, 120)
        .equals(List.of(new Window(T + 530_000, T + 530_000 + 120 * minute)));
    assert JournalBackfill.afterExit(fills, null, NOW - 30 * DAY, NOW, 30).isEmpty();
  }

  /** Loading the minutes after an exit: whole once they and the settling time have passed, after every
   * holding window, never twice, under the same provider, and longer when more minutes are asked for. */
  private static void afterExitPasses(Path folder) throws Exception {
    var file = folder.resolve("after-exit.ndjson");
    var fills = List.of(fill("paper", "ESZ6", "trade", true, 1, 0, false), fill("paper", "ESZ6", "trade", false, 1, 60, false));
    Window holding = new Window(T - PAD, T + 60_000 + PAD);
    var history = new History();
    var events = new ArrayList<String>();
    var load = new JournalBackfill(new Ledger(file));
    // Ten minutes after the exit: the holding window is due, the 30 minutes after the exit are not.
    var result = load.pass("ESZ6", fills, T + 600_000, 30, 30, history, events::add, NEVER);
    assert result.done() == 1 && result.remaining() == 0 && history.asked.equals(List.of(holding)) : history.asked;
    // 30 minutes and the settling time after the exit: the rest of them, where the holding window ended.
    long due = T + 60_000 + 1_800_000 + JournalBackfill.SETTLE_MILLIS;
    assert load.pass("ESZ6", fills, due - 1, 30, 30, history, events::add, NEVER).attempted() == 0 : "Not a moment early";
    result = load.pass("ESZ6", fills, due, 30, 30, history, events::add, NEVER);
    assert result.done() == 1 && history.asked.equals(List.of(holding, new Window(T + 60_000 + PAD, T + 1_860_000)))
        : history.asked;
    assert result.ticks() == 1_795_000 / 250 && events.size() == 280 + 7_180 : result;
    assert events.stream().allMatch(s -> s.startsWith("{\"version\":1,\"type\":\"tick\",\"mode\":\"live\","
        + "\"provider\":\"motivewave-ticks:history\",\"symbol\":\"ESZ6\",\"ts\":\"")) : "The same provider as the holding window";
    assert events.getLast().contains("\"ts\":\"2026-10-07T14:30:59.750Z\"") : events.getLast();
    // Done once: not again in this load, nor after a restart.
    assert load.pass("ESZ6", fills, due + DAY, 30, 30, history, events::add, NEVER).attempted() == 0;
    assert new Ledger(file).written("ESZ6", new Window(T - PAD, T + 1_860_000));
    var restarted = new JournalBackfill(new Ledger(file));
    assert restarted.pass("ESZ6", fills, due + DAY, 30, 30, history, events::add, NEVER).attempted() == 0 && history.asked.size() == 2;
    // More minutes load only the minutes they add.
    result = restarted.pass("ESZ6", fills, due + DAY, 30, 60, history, events::add, NEVER);
    assert result.done() == 1 && history.asked.getLast().equals(new Window(T + 1_860_000, T + 3_660_000)) : history.asked;
    assert events.size() == events.stream().distinct().count() : "No print written twice";

    // A backlog, one window a pass: both holding windows come before the minutes after either exit, although
    // the first exit's minutes start before the second trade does.
    var backlog = List.of(fill("paper", "ESZ6", "a", true, 1, 0, false), fill("paper", "ESZ6", "a", false, 1, 60, false),
        fill("paper", "ESZ6", "b", false, 1, 600, false), fill("paper", "ESZ6", "b", true, 1, 660, false));
    var slow = new JournalBackfill(new Ledger(folder.resolve("after-exit-backlog.ndjson")), JournalBackfill.MAX_TICKS, 1);
    var asked = new History();
    var written = new ArrayList<String>();
    result = slow.pass("ESZ6", backlog, NOW, 30, 30, asked, written::add, NEVER);
    assert result.done() == 1 && result.remaining() == 4 : result;
    slow.pass("ESZ6", backlog, NOW, 30, 30, asked, written::add, NEVER);
    assert asked.asked.equals(List.of(holding, new Window(T + 600_000 - PAD, T + 660_000 + PAD))) : asked.asked;
    for (int n = 0; n < 3; n++) assert slow.pass("ESZ6", backlog, NOW, 30, 30, asked, written::add, NEVER).done() == 1;
    assert slow.pass("ESZ6", backlog, NOW, 30, 30, asked, written::add, NEVER).attempted() == 0 && asked.asked.size() == 5;
    assert written.size() == written.stream().distinct().count() && written.size() == (2_460_000 + PAD) / 250
        : "From the first entry to 30 minutes after the second exit, once: " + written.size();

    // No tick history for those minutes: counted as empty, asked again only by a later load.
    var quiet = new History();
    quiet.empty.add(new Window(T + 600_000, T + 700_000));
    var none = new JournalBackfill(new Ledger(folder.resolve("after-exit-quiet.ndjson")));
    result = none.pass("ESZ6", fills, due, 30, 30, quiet, events::add, NEVER);
    assert result.done() == 1 && result.empty() == 1 : result;
    assert none.pass("ESZ6", fills, due, 30, 30, quiet, events::add, NEVER).attempted() == 0 && quiet.asked.size() == 2;
  }

  /** Replay: the minutes after a replay exit are loaded as the replay plays through them, never past its
   * present, for the run's own provider, and each stretch once. */
  private static void replayAfterExit(Path folder) throws Exception {
    var fills = List.of(replay("run-a", "ESZ6", "RPLY-1", true, 0), replay("run-a", "ESZ6", "RPLY-1", false, 60));
    long clock = R + 120_000;
    assert JournalBackfill.replayAfterExit(fills, "ESZ6", clock - DAY, clock, 30)
        .equals(List.of(new JournalBackfill.RunWindow("run-a", new Window(R + 60_000, clock)))) : "Cut at the replay clock";
    assert JournalBackfill.replayAfterExit(fills, "ESZ6", R + 60_000 - DAY, R + 60_000, 30).isEmpty()
        : "Nothing after the exit has played yet";
    assert JournalBackfill.replayAfterExit(fills, "ESZ6", clock - DAY, clock, 0).isEmpty();
    assert JournalBackfill.replayAfterExit(fills, "ESZ6", R + 7_200_000 - DAY, R + 7_200_000, 30)
        .equals(List.of(new JournalBackfill.RunWindow("run-a", new Window(R + 60_000, R + 1_860_000))));
    assert JournalBackfill.afterExit(fills.stream().map(JournalBackfill.ReplayFill::fill).toList(), "ESZ6", 0, Long.MAX_VALUE, 30)
        .isEmpty() : "Never a live window";

    var history = new History();
    var events = new ArrayList<String>();
    var load = new JournalBackfill(new Ledger(folder.resolve("after-exit.replay.ndjson"), true));
    var result = load.replayPass("ESZ6", fills, clock, NOW, 30, 30, history, events::add, NEVER);
    assert result.done() == 2 && history.asked.equals(List.of(new Window(R - PAD, R + 60_000 + PAD),
        new Window(R + 60_000 + PAD, clock))) : "The holding window, then what has played since: " + history.asked;
    assert events.size() == 280 + 220 && events.stream().allMatch(e -> stamp(e) < clock) : "Nothing past the replay clock";
    assert events.stream().allMatch(e -> e.contains("\"mode\":\"replay\",\"provider\":\"motivewave-replay:run-a:history\""));
    // The replay plays on: only what it played since, each time, up to 30 minutes after the exit and no further.
    history.asked.clear();
    load.replayPass("ESZ6", fills, R + 600_000, NOW, 30, 30, history, events::add, NEVER);
    load.replayPass("ESZ6", fills, R + 3_600_000, NOW, 30, 30, history, events::add, NEVER);
    assert load.replayPass("ESZ6", fills, R + 7_200_000, NOW, 30, 30, history, events::add, NEVER).attempted() == 0;
    assert history.asked.equals(List.of(new Window(clock, R + 600_000), new Window(R + 600_000, R + 1_860_000)))
        : history.asked;
    assert events.size() == (1_860_000 + PAD) / 250 && events.size() == events.stream().distinct().count() : events.size();
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

    // Bounds: 24 hours, the days asked for, and a position that traded moments ago.
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
    assert JournalBackfill.windows(bounds, "ESZ6", NOW - 60 * DAY, settled).size() == 2 : "More days reach the old one";
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

    assert load.pass("ESZ6", fills, NOW, 0, 0, history, events::add, NEVER).attempted() == 0 && history.asked.isEmpty()
        : "0 days switches it off";
    assert load.pass("ESZ6", null, NOW, 30, 0, history, events::add, NEVER).attempted() == 0 : "No snapshot yet";
    var result = load.pass("ESZ6", fills, NOW, 30, 0, history, events::add, NEVER);
    assert result.done() == 1 && result.empty() == 1 && result.failed() == 0 && result.remaining() == 0 : result;
    assert history.asked.equals(List.of(first, quiet)) : "Oldest first, and never the replay position: " + history.asked;
    assert result.ticks() == 280 && events.size() == 280 : "70 seconds at four prints a second: " + result.ticks();
    assert events.stream().allMatch(s -> s.startsWith("{\"version\":1,\"type\":\"tick\",\"mode\":\"live\","
        + "\"provider\":\"motivewave-ticks:history\",\"symbol\":\"ESZ6\",\"ts\":\"") && s.endsWith(",\"proxy\":false}"));
    assert events.getFirst().contains("\"ts\":\"2026-10-07T13:59:55Z\"") && events.getLast().contains("\"ts\":\"2026-10-07T14:01:04.750Z\"")
        : "Only prints inside the window, although the history answered with more";

    // The same study load: neither is asked for again, whatever came back.
    result = load.pass("ESZ6", fills, NOW + 60_000, 30, 0, history, events::add, NEVER);
    assert result.attempted() == 0 && result.remaining() == 0 && history.asked.size() == 2 && events.size() == 280;
    String sidecar = Files.readString(Ledger.sidecar(file));
    assert Ledger.sidecar(file).getFileName().toString().equals("motivewave.ndjson.tick-backfill");
    assert sidecar.contains("version=1") && sidecar.contains("done.ESZ6=" + first.start() + "-" + first.end()) : sidecar;
    assert !sidecar.contains("paper") && !sidecar.contains(String.valueOf(quiet.start()))
        : "Symbols and finished times only; an empty answer is not remembered on disk";

    // A restart: the finished window is not loaded again; the empty one is asked for once more.
    history.asked.clear();
    var restarted = new JournalBackfill(new Ledger(file));
    result = restarted.pass("ESZ6", fills, NOW, 30, 0, history, events::add, NEVER);
    assert result.done() == 0 && result.empty() == 1 && history.asked.equals(List.of(quiet)) && events.size() == 280 : result;
    assert restarted.pass("ESZ6", fills, NOW, 30, 0, history, events::add, NEVER).attempted() == 0 && history.asked.size() == 1;
    // By the next load the history has it.
    history.empty.clear();
    history.asked.clear();
    var later = new JournalBackfill(new Ledger(file));
    result = later.pass("ESZ6", fills, NOW, 30, 0, history, events::add, NEVER);
    assert result.done() == 1 && result.empty() == 0 && history.asked.equals(List.of(quiet)) && events.size() == 560 : result;
    assert new Ledger(file).written("ESZ6", first) && new Ledger(file).written("ESZ6", quiet);
    assert !new Ledger(file).written("NQZ6", first) : "Another instrument's history is its own";
    history.asked.clear();
    assert new JournalBackfill(new Ledger(file)).pass("ESZ6", fills, NOW, 30, 0, history, events::add, NEVER).attempted() == 0
        && history.asked.isEmpty() : "Nothing left to do after another restart";

    // The history cannot be asked (no connection): counted, not retried by this load, asked again by the next.
    fills.add(fill("paper", "ESZ6", "offline", true, 1, 1800, false));
    fills.add(fill("paper", "ESZ6", "offline", false, 1, 1830, false));
    history.failure = new IOException("Fixture: not connected");
    result = later.pass("ESZ6", fills, NOW, 30, 0, history, events::add, NEVER);
    assert result.failed() == 1 && result.done() == 0 && "IOException".equals(result.failure()) && history.asked.size() == 1 : result;
    assert later.pass("ESZ6", fills, NOW, 30, 0, history, events::add, NEVER).attempted() == 0 && history.asked.size() == 1;
    history.failure = null;
    result = new JournalBackfill(new Ledger(file)).pass("ESZ6", fills, NOW, 30, 0, history, events::add, NEVER);
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
    var result = load.pass("ESZ6", fills, NOW, 30, 0, history, events::add, NEVER);
    assert result.done() == 4 && result.remaining() == 3 : "A few at a time, and how many are left: " + result;
    assert history.asked.subList(0, 2).equals(List.of(new Window(T + 200_000 - PAD, T + 230_000 + PAD),
        new Window(T + 230_000 + PAD, T + 260_000 + PAD))) : "The second window starts where the first ended: " + history.asked;
    result = load.pass("ESZ6", fills, NOW, 30, 0, history, events::add, NEVER);
    assert result.done() == 3 && result.remaining() == 0 && history.asked.size() == 7 : result;
    assert load.pass("ESZ6", fills, NOW, 30, 0, history, events::add, NEVER).attempted() == 0 && history.asked.size() == 7;
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
    var result = load.pass("ESZ6", fills, NOW, 30, 0, history, events::add, NEVER);
    assert result.done() == 1 && result.cut() == 1 && result.ticks() == 30 && result.empty() == 0 : result;
    assert events.size() == 31 && events.subList(0, 30).stream().allMatch(s -> s.contains("\"type\":\"tick\""));
    String gap = events.getLast();
    assert gap.startsWith("{\"version\":1,\"type\":\"gap\",\"provider\":\"motivewave-ticks:history\",\"symbol\":\"ESZ6\",\"ts\":\"")
        && gap.endsWith(",\"dropped\":50,\"start_ts\":\"2026-10-07T14:00:02.500Z\",\"end_ts\":\"2026-10-07T14:00:14.750Z\"}")
        : "How many were left out, and from when to when: " + gap;
    assert events.get(29).contains("\"ts\":\"2026-10-07T14:00:02.250Z\"") : "The last print written is the one before the gap";
    // Cut is still done: asking again would only reach the same cap.
    assert load.pass("ESZ6", fills, NOW, 30, 0, history, events::add, NEVER).attempted() == 0;
    assert new JournalBackfill(new Ledger(file), 30, 4).pass("ESZ6", fills, NOW, 30, 0, history, events::add, NEVER).attempted() == 0;
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
    var result = new JournalBackfill(new Ledger(folder.resolve("slow.ndjson"))).pass("ESZ6", fills, NOW, 30, 0, new History(),
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
    result = new JournalBackfill(new Ledger(file)).pass("ESZ6", day, NOW + DAY, 30, 0, new History(), event -> {
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

  /** The chart changed mode or the study was removed half way: the window is not done, and is loaded later. */
  private static void cancelling(Path folder) throws Exception {
    var file = folder.resolve("cancel.ndjson");
    var fills = List.of(fill("paper", "ESZ6", "cut-short", true, 1, 0, false), fill("paper", "ESZ6", "cut-short", false, 1, 60, false));
    Window window = new Window(T - PAD, T + 60_000 + PAD);
    var ledger = new Ledger(file);
    var load = new JournalBackfill(ledger);
    var events = new ArrayList<String>();
    try {
      load.pass("ESZ6", fills, NOW, 30, 0, new History(), events::add, () -> events.size() >= 100);
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
      load.pass("ESZ6", fills, NOW, 30, 0, stubborn, events::add, () -> events.size() >= 100);
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
      try { seen.add(otherChart.pass("ESZ6", fills, NOW, 30, 0, otherHistory, events::add, NEVER)); }
      catch (IOException exc) { throw new IllegalStateException(exc); }
    };
    var result = load.pass("ESZ6", fills, NOW, 30, 0, history, events::add, NEVER);
    assert result.done() == 1 && result.ticks() == 280 && events.size() == 280 && ledger.written("ESZ6", window) : result;
    assert seen.size() == 1 && seen.getFirst().attempted() == 0 && seen.getFirst().remaining() == 0
        && otherHistory.asked.isEmpty() : "Two charts on one instrument must not both load the window: " + seen;
    assert otherChart.pass("ESZ6", fills, NOW, 30, 0, otherHistory, events::add, NEVER).attempted() == 0 && events.size() == 280;
  }

  // ---------------------------------------------------------------- replay

  /** Replay time: a session in 2021, years before the wall clock. */
  private static final long R = Instant.parse("2021-01-04T15:00:00Z").toEpochMilli();

  /** Replay positions: per run, account and position id, flat, closed no later than the replay clock,
   * and the window cut at that clock. */
  private static void replayWindows() {
    var fills = new ArrayList<JournalBackfill.ReplayFill>();
    fills.add(replay("run-a", "ESZ6", "RPLY-1", true, 0));
    fills.add(replay("run-a", "ESZ6", "RPLY-1", false, 60));
    fills.add(replay("run-a", "ESZ6", "RPLY-2", false, 120));
    fills.add(replay("run-a", "ESZ6", "RPLY-2", true, 150));
    fills.add(replay("run-a", "ESZ6", "RPLY-3", true, 200));           // still open
    // After a rewind MotiveWave can number positions from the start again: the same id in another run
    // is another position, with a window of its own.
    fills.add(replay("run-b", "ESZ6", "RPLY-1", true, 30));
    fills.add(replay("run-b", "ESZ6", "RPLY-1", false, 90));
    // Never a window: a live fill, another instrument, no run, no position id.
    fills.add(new JournalBackfill.ReplayFill("run-a", new Fill("live-in", "paper", "ESZ6", true, 1, 5000, R + 10_000, 2, 50,
        "USD", "o", "live", true, false)));
    fills.add(new JournalBackfill.ReplayFill("run-a", new Fill("live-out", "paper", "ESZ6", false, 1, 5000, R + 20_000, 2, 50,
        "USD", "o", "live", false, false)));
    fills.add(replay("run-a", "NQZ6", "RPLY-9", true, 0));
    fills.add(replay("run-a", "NQZ6", "RPLY-9", false, 60));
    fills.add(replay(" ", "ESZ6", "RPLY-7", true, 0));
    fills.add(replay(" ", "ESZ6", "RPLY-7", false, 60));
    fills.add(replay("run-a", "ESZ6", "", true, 0));
    fills.add(replay("run-a", "ESZ6", "", false, 60));
    long clock = R + 152_000;
    var windows = JournalBackfill.replayWindows(fills, "ESZ6", clock - DAY, clock);
    assert windows.equals(List.of(
        new JournalBackfill.RunWindow("run-a", new Window(R - PAD, R + 60_000 + PAD)),
        new JournalBackfill.RunWindow("run-b", new Window(R + 30_000 - PAD, R + 90_000 + PAD)),
        new JournalBackfill.RunWindow("run-a", new Window(R + 120_000 - PAD, clock))))
        : "Oldest first, one per run and position, the last cut at the clock: " + windows;
    assert JournalBackfill.replayWindows(fills, "ESZ6", R + 140_000 - DAY, R + 140_000).size() == 2
        : "A position that closes after the replay's present is not over yet";
    assert JournalBackfill.replayWindows(fills, "ESZ6", R + 10_000 - DAY, R + 10_000).isEmpty()
        : "Rewound to before every exit: nothing is over";
    assert JournalBackfill.replayWindows(fills, "NQZ6", clock - DAY, clock).size() == 1;
    assert JournalBackfill.replayWindows(fills, "ESZ6", R + 3 * DAY - DAY, R + 3 * DAY).isEmpty()
        : "Days count back from the replay clock";
    // Live windows never include replay positions, and the replay ones never include live positions.
    assert JournalBackfill.windows(fills.stream().map(JournalBackfill.ReplayFill::fill).toList(), "ESZ6", 0, Long.MAX_VALUE)
        .equals(List.of(new Window(R + 10_000 - PAD, R + 20_000 + PAD))) : "Only the live position";
    assert JournalBackfill.replaySource("run-a").equals("motivewave-replay:run-a:history");
  }

  /** Loading replay windows: the run's own provider, replay mode, nothing past the clock however much
   * the history answers with, the rest once the replay has played past it, and a cut window's gap. */
  private static void replayPasses(Path folder) throws Exception {
    var replayFile = folder.resolve("replay-passes.replay.ndjson");
    var liveFile = folder.resolve("replay-passes.ndjson");
    var fills = List.of(replay("run-a", "ESZ6", "RPLY-1", true, 0), replay("run-a", "ESZ6", "RPLY-1", false, 60),
        replay("run-b", "ESZ6", "RPLY-1", true, 30), replay("run-b", "ESZ6", "RPLY-1", false, 90),
        replay("run-a", "ESZ6", "RPLY-2", false, 120), replay("run-a", "ESZ6", "RPLY-2", true, 150));
    var history = new History(); // answers with prints up to a second past whatever it is asked for
    var events = new ArrayList<String>();
    var load = new JournalBackfill(new Ledger(replayFile, true));
    long clock = R + 152_000, wall = NOW;
    assert load.replayPass("ESZ6", fills, clock, wall, 0, 0, history, events::add, NEVER).attempted() == 0 : "0 days is off";
    assert load.replayPass("ESZ6", fills, 0, wall, 30, 0, history, events::add, NEVER).attempted() == 0 : "No replay time yet";
    assert load.replayPass("ESZ6", null, clock, wall, 30, 0, history, events::add, NEVER).attempted() == 0 : "No snapshot yet";
    assert history.asked.isEmpty();
    var result = load.replayPass("ESZ6", fills, clock, wall, 30, 0, history, events::add, NEVER);
    assert result.done() == 3 && result.empty() == 0 && result.remaining() == 0 : result;
    // The two runs overlap in time, and each gets its own copy: the app measures a trade with its own run's prices.
    assert history.asked.equals(List.of(new Window(R - PAD, R + 65_000), new Window(R + 25_000, R + 95_000),
        new Window(R + 115_000, clock))) : history.asked;
    assert result.ticks() == 280 + 280 + 148 && events.size() == 708 : result;
    var runA = events.stream().filter(e -> e.contains("\"provider\":\"motivewave-replay:run-a:history\"")).toList();
    var runB = events.stream().filter(e -> e.contains("\"provider\":\"motivewave-replay:run-b:history\"")).toList();
    assert runA.size() == 280 + 148 && runB.size() == 280 : runA.size() + " " + runB.size();
    assert events.stream().allMatch(e -> e.startsWith("{\"version\":1,\"type\":\"tick\",\"mode\":\"replay\",\"provider\":"
        + "\"motivewave-replay:run-") && e.contains("\"symbol\":\"ESZ6\"") && e.endsWith(",\"proxy\":false}"));
    assert events.stream().noneMatch(e -> e.contains("motivewave-ticks")) : "Never the live provider";
    // No look-ahead: the history answered with prints past the replay's present; none of them is written.
    assert events.stream().allMatch(e -> stamp(e) < clock) : "Nothing at or after the replay clock";
    assert runA.getLast().contains("\"ts\":\"2021-01-04T15:02:31.750Z\"") : runA.getLast();
    assert !Files.exists(Ledger.sidecar(liveFile)) : "Replay bookkeeping stays beside the replay bridge";

    // The same load, the clock unchanged: nothing to do. The replay plays on: only the rest of the cut window.
    assert load.replayPass("ESZ6", fills, clock, wall, 30, 0, history, events::add, NEVER).attempted() == 0;
    history.asked.clear();
    long later = R + 600_000;
    result = load.replayPass("ESZ6", fills, later, wall, 30, 0, history, events::add, NEVER);
    assert result.done() == 1 && history.asked.equals(List.of(new Window(clock, R + 150_000 + PAD))) : history.asked;
    assert result.ticks() == 12 && events.size() == 720 : result;

    // A window with more ticks than the cap: cut, and a gap event under the run's own provider.
    var capped = new ArrayList<String>();
    var small = new JournalBackfill(new Ledger(folder.resolve("replay-cap.replay.ndjson"), true), 30, 4);
    result = small.replayPass("ESZ6", fills.subList(0, 2), later, wall, 30, 0, new History(), capped::add, NEVER);
    assert result.done() == 1 && result.cut() == 1 && capped.size() == 31 : result;
    assert capped.getLast().startsWith("{\"version\":1,\"type\":\"gap\",\"provider\":\"motivewave-replay:run-a:history\","
        + "\"symbol\":\"ESZ6\"") && capped.getLast().contains("\"dropped\":250,") : capped.getLast();
  }

  /** The replay bridge's own sidecar: restarts and rewinds never load a run's window twice, a new run
   * gets its own copy, an empty answer is asked again only by a later load, and old runs are forgotten. */
  private static void replayLedger(Path folder) throws Exception {
    var replayFile = folder.resolve("ledger.replay.ndjson");
    var fills = new ArrayList<JournalBackfill.ReplayFill>(List.of(
        replay("run-a", "ESZ6", "RPLY-1", true, 0), replay("run-a", "ESZ6", "RPLY-1", false, 60)));
    Window window = new Window(R - PAD, R + 60_000 + PAD);
    var history = new History();
    var events = new ArrayList<String>();
    long clock = R + 600_000;
    assert new JournalBackfill(new Ledger(replayFile, true)).replayPass("ESZ6", fills, clock, NOW, 30, 0, history, events::add,
        NEVER).done() == 1 && events.size() == 280;
    String sidecar = Files.readString(Ledger.sidecar(replayFile));
    assert Ledger.sidecar(replayFile).getFileName().toString().equals("ledger.replay.ndjson.tick-backfill");
    assert sidecar.contains("done.run-a\\:ESZ6=" + window.start() + "-" + window.end()) && sidecar.contains("used.run-a\\:ESZ6=" + NOW)
        : "Filed by run and contract, with when it was written: " + sidecar;
    assert !sidecar.contains("REPLAY-1") && !sidecar.contains("RPLY-1") : "Run ids, symbols and times only";
    assert new Ledger(replayFile, true).written(JournalBackfill.replayKey("run-a", "ESZ6"), window);
    assert !new Ledger(replayFile, true).written(JournalBackfill.replayKey("run-b", "ESZ6"), window);
    assert !new Ledger(replayFile, true).written("ESZ6", window) : "Not a live contract's history";

    // A restart, and the replay rewound to before the trade and played past it again in the same run: not again.
    history.asked.clear();
    var restarted = new JournalBackfill(new Ledger(replayFile, true));
    assert restarted.replayPass("ESZ6", fills, clock, NOW, 30, 0, history, events::add, NEVER).attempted() == 0;
    assert restarted.replayPass("ESZ6", fills, R + 10_000, NOW, 30, 0, history, events::add, NEVER).attempted() == 0;
    assert history.asked.isEmpty() && events.size() == 280;
    // A rewind starts a new run, and its trade at the same times is loaded again, for that run.
    fills.add(replay("run-c", "ESZ6", "RPLY-1", true, 0));
    fills.add(replay("run-c", "ESZ6", "RPLY-1", false, 60));
    var result = restarted.replayPass("ESZ6", fills, clock, NOW, 30, 0, history, events::add, NEVER);
    assert result.done() == 1 && history.asked.equals(List.of(window)) && events.size() == 560 : result;
    assert events.subList(280, 560).stream().allMatch(e -> e.contains("motivewave-replay:run-c:history"));

    // No ticks in the history: asked again only by a later load.
    fills.add(replay("run-d", "ESZ6", "RPLY-1", true, 300));
    fills.add(replay("run-d", "ESZ6", "RPLY-1", false, 330));
    Window quiet = new Window(R + 300_000 - PAD, R + 330_000 + PAD);
    history.empty.add(quiet);
    history.asked.clear();
    result = restarted.replayPass("ESZ6", fills, clock, NOW, 30, 0, history, events::add, NEVER);
    assert result.empty() == 1 && result.done() == 0 && history.asked.equals(List.of(quiet)) : result;
    assert restarted.replayPass("ESZ6", fills, clock, NOW, 30, 0, history, events::add, NEVER).attempted() == 0;
    history.empty.clear();
    result = new JournalBackfill(new Ledger(replayFile, true)).replayPass("ESZ6", fills, clock, NOW, 30, 0, history,
        events::add, NEVER);
    assert result.done() == 1 && history.asked.size() == 2 : result;

    // One bridge file is one kind: the replay sidecar is not opened as a live one.
    assert Ledger.shared(replayFile, true) == Ledger.shared(replayFile, true);
    try {
      Ledger.shared(replayFile, false);
      throw new AssertionError("A replay ledger must not be shared as a live one");
    } catch (IOException expected) { }

    // A run nothing has been written for in longer than any pass reaches is forgotten; a live ledger
    // would instead drop these 2021 stretches at once, by their times.
    var ledger = new Ledger(replayFile, true);
    long much = NOW + JournalBackfill.KEEP_MILLIS + DAY;
    assert ledger.claim(JournalBackfill.replayKey("run-z", "ESZ6"), window);
    ledger.finish(JournalBackfill.replayKey("run-z", "ESZ6"), window, true, much);
    var reread = new Ledger(replayFile, true);
    assert reread.written(JournalBackfill.replayKey("run-z", "ESZ6"), window)
        && !reread.written(JournalBackfill.replayKey("run-a", "ESZ6"), window) : Files.readString(Ledger.sidecar(replayFile));
  }

  /** The epoch milliseconds of an event's "ts". */
  private static long stamp(String event) {
    int at = event.indexOf("\"ts\":\"") + 6;
    return Instant.parse(event.substring(at, event.indexOf('"', at))).toEpochMilli();
  }

  /** One replay fill of a position, at R plus so many seconds, filed under run. */
  private static JournalBackfill.ReplayFill replay(String run, String symbol, String position, boolean buy, long second) {
    return new JournalBackfill.ReplayFill(run, new Fill("replay-" + next++, "REPLAY-1", symbol, buy, 1, 5000,
        R + second * 1000, 2, 50, "USD", "order", position, buy, true));
  }

  /** One fill of a position, at T plus so many seconds. */
  private static Fill fill(String account, String symbol, String position, boolean buy, double qty, long second, boolean replay) {
    return new Fill("fill-" + next++, account, symbol, buy, qty, 5000, T + second * 1000, 2, 50, "USD", "order", position,
        buy, replay);
  }
}
