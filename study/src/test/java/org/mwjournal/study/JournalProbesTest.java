package org.mwjournal.study;

import com.motivewave.platform.sdk.common.Bar;
import com.motivewave.platform.sdk.common.Instrument;
import com.motivewave.platform.sdk.common.Tick;
import com.motivewave.platform.sdk.common.TickOperation;
import java.lang.reflect.Proxy;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import java.util.concurrent.CopyOnWriteArrayList;
import org.mwjournal.study.JournalNativeAccess.Fill;
import org.mwjournal.study.JournalNativeAccess.Snapshot;

/** The probes of docs/design/study-measures.md section 7, against instruments made of proxies: each answers
 * once per session in one log line, and a probe that fails says so without disturbing the capture.
 * Fixtures only: no MotiveWave session, account or order. */
public final class JournalProbesTest {
  private JournalProbesTest() {}
  private static final long TIME = 1641220200000L, SECOND = 1_000_000_000L;

  public static void main(String[] args) throws Exception {
    var folder = Files.createTempDirectory("mwj-probes-fixture-");
    answers(folder.resolve("probes.ndjson"));
    failures(folder.resolve("failing.ndjson"));
    try (var left = Files.walk(folder)) {
      for (var path : left.sorted(java.util.Comparator.reverseOrder()).toList()) Files.delete(path);
    }
    System.out.println("Journal probes passed: chart and fill symbols, tick sizes, the largest fill delay, "
        + "addListener against onTick for 60 s, history on a fill's instrument with no chart (live, and replay up to "
        + "its clock), each said once; a failing probe says so and the capture goes on.");
  }

  private static void answers(Path file) throws Exception {
    var listeners = new CopyOnWriteArrayList<TickOperation>();
    var asked = new CopyOnWriteArrayList<String>();
    Instrument nq = instrument("NQZ6", "NQZ6", .25, listeners, asked, false);
    Instrument mnq = instrument("MNQZ6", "MNQZ6", .25, listeners, asked, false);
    Instrument replayNq = instrument("NQZ6", "NQZ6", .25, listeners, asked, false);
    Instrument chart = instrument("NQ", "NQZ6", .25, listeners, asked, false);
    long fillTime = System.currentTimeMillis() - 600_000;
    var fills = new ArrayList<Fill>(List.of(fill("n1", "NQZ6", fillTime, false), fill("m1", "MNQZ6", fillTime + 1000, false),
        fill("r1", "NQZ6", TIME, true)));
    var instruments = Map.of(JournalNativeAccess.contract(false, "NQZ6"), nq, JournalNativeAccess.contract(false, "MNQZ6"), mnq,
        JournalNativeAccess.contract(true, "NQZ6"), replayNq);
    var capture = JournalAccountCapture.fixture(file, () -> new Snapshot("workspace", List.copyOf(fills), List.of(), Map.of(),
        JournalNativeAccess.OrderCounts.of(List.of()), instruments));
    Object liveChart = new Object(), replayChart = new Object();
    capture.chart(liveChart, chart, false);
    capture.replayRun("NQZ6", TIME + 30_000);
    long now = System.nanoTime();
    capture.poll(now);
    var lines = drain(capture);
    assert count(lines, "getSymbol() is 'NQ' and getActiveSymbol() 'NQZ6'; 1 fills have the symbol 'NQZ6'; "
        + "the live fills' symbols: 'MNQZ6', 'NQZ6'.") == 1 : lines;
    assert count(lines, "getTickSize() of the fills' instruments: live MNQZ6 0.25, live NQZ6 0.25, replay NQZ6 0.25.") == 1 : lines;
    assert count(lines, "largest fill delay") == 0 : "The first snapshot is history: " + lines;
    assert listeners.size() == 1 : "addListener on the live fill instrument beside the NQ chart";
    for (int i = 0; i < 7; i++) capture.ticked(liveChart, false);
    capture.ticked(replayChart, false);                        // another chart's prints are not counted
    for (int i = 0; i < 5; i++) listeners.getFirst().onTick(tick(fillTime));
    // An account that connects after the study started brings its earlier fills: history, not a delay.
    fills.add(new Fill("p1", "prop", "ESZ6", true, 1, 100, fillTime + 2000, 1, 50, "USD", "o-p1", "", false, false));
    capture.poll(now + SECOND / 2);
    lines = drain(capture);
    assert count(lines, "largest fill delay") == 0 : "A fill from before the study started: " + lines;
    // A new fill, dated now: the largest delay so far.
    Thread.sleep(5);
    fills.add(fill("n2", "NQZ6", System.currentTimeMillis(), false));
    capture.poll(now + SECOND);
    lines = drain(capture);
    assert count(lines, "largest fill delay so far (live): ") == 1 && count(lines, "a NQZ6 fill at") == 1 : lines;
    assert count(lines, "getSymbol()") == 0 && count(lines, "getTickSize()") == 0 : "Each is said once: " + lines;
    capture.poll(now + JournalProbes.LISTEN_NANOS + SECOND);
    lines = drain(capture);
    assert listeners.isEmpty() : "removeListener after 60 s";
    assert count(lines, "Instrument.addListener on the live fill instrument 'NQZ6' heard 5 prints in ") == 1
        && count(lines, "the chart 'NQ' (active 'NQZ6') had 7 in onTick over the same time.") == 1 : lines;
    // The replay chart: its listener probe, ended early when the chart goes.
    capture.chart(replayChart, replayNq, true);
    capture.poll(now + JournalProbes.LISTEN_NANOS + 2 * SECOND);
    assert listeners.size() == 1;
    capture.forget(replayChart);
    lines = drain(capture);
    assert listeners.isEmpty() && count(lines, "Instrument.addListener on the replay fill instrument 'NQZ6' heard 0 prints")
        == 1 && count(lines, "stopped early: the chart was closed or its settings changed") == 1 : lines;
    // History, once the data connection has had its 30 s: MNQZ6 has no chart, so it is chosen over the newer NQ fill.
    capture.poll(now + JournalProbes.HISTORY_AFTER_NANOS + 3 * SECOND);
    lines = waitFor(capture, "history of the replay fill instrument", 2);
    // BarSize.minute needs MotiveWave's own classes, which a test outside MotiveWave does not have: there the
    // probe's line says getBars failed, which is also what a failing getBars inside MotiveWave would say.
    String bars = barSizes() ? "getBars 2 one-minute bars from " : "getBars failed (NoClassDefFoundError";
    String live = lines.stream().filter(line -> line.contains("history of the live fill instrument")).findFirst().orElseThrow();
    assert live.contains("'MNQZ6' (no chart of it open)") && live.contains("forEachTick 3 ticks from ")
        && live.contains(", 0 after the end; " + bars) : live;
    String replay = lines.stream().filter(line -> line.contains("history of the replay fill instrument")).findFirst().orElseThrow();
    assert replay.contains(" to " + java.time.Instant.ofEpochMilli(TIME + 30_000) + " (the replay clock)")
        && replay.contains("(no chart of it open)") && replay.contains(bars)
        && (!barSizes() || replay.contains(", 1 ending after the end;")) : replay;
    assert asked.contains("forEachTick " + (fillTime + 1000 - JournalProbes.WINDOW_MILLIS)) : asked;
    capture.poll(now + JournalProbes.HISTORY_AFTER_NANOS + 4 * SECOND);
    Thread.sleep(100);
    assert count(drain(capture), "history of the") == 0 : "Once per mode";
    capture.close();
    assert capture.failure == null;
  }

  /** Instruments whose every answer throws: each probe says it failed, and the fills are still written. */
  private static void failures(Path file) throws Exception {
    var listeners = new CopyOnWriteArrayList<TickOperation>();
    Instrument broken = instrument("ESZ6", "ESZ6", .25, listeners, new CopyOnWriteArrayList<>(), true);
    var fills = List.of(fill("e1", "ESZ6", TIME, false));
    var capture = JournalAccountCapture.fixture(file, () -> new Snapshot("workspace", fills, List.of(), Map.of(),
        JournalNativeAccess.OrderCounts.of(List.of()), Map.of(JournalNativeAccess.contract(false, "ESZ6"), broken)));
    capture.chart(new Object(), broken, false);
    long now = System.nanoTime();
    capture.poll(now);
    capture.poll(now + JournalProbes.HISTORY_AFTER_NANOS + SECOND);
    var lines = waitFor(capture, "history of the live fill instrument", 1);
    assert count(lines, "getTickSize() of the fills' instruments: live ESZ6 failed (IllegalStateException: broken)") == 1
        : lines;
    assert count(lines, "Instrument.addListener on the live fill instrument 'ESZ6' failed: IllegalStateException: broken") == 1
        : lines;
    assert count(lines, "forEachTick failed (IllegalStateException: broken); getBars failed ("
        + (barSizes() ? "IllegalStateException: broken)" : "NoClassDefFoundError")) == 1 : lines;
    assert capture.failure == null && capture.status().equals("connected");
    capture.close();
    assert Files.readAllLines(file).stream().filter(line -> line.contains("\"type\":\"execution\"")).count() == 1;
  }

  private static boolean barSizes() {
    try { return com.motivewave.platform.sdk.common.BarSize.minute(1) != null; }
    catch (LinkageError exc) { return false; }
  }

  private static List<String> drain(JournalAccountCapture capture) {
    var lines = new ArrayList<String>();
    capture.drainLog(lines::add);
    assert lines.stream().allMatch(line -> line.startsWith(JournalProbes.PREFIX)) : lines;
    return lines;
  }

  /** The log until `wanted` lines contain `text`, for the probe that runs on its own thread. */
  private static List<String> waitFor(JournalAccountCapture capture, String text, int wanted) throws Exception {
    var lines = new ArrayList<String>();
    for (int i = 0; i < 200 && count(lines, "history of the") < wanted; i++) {
      lines.addAll(drain(capture));
      if (count(lines, text) >= 1 && count(lines, "history of the") >= wanted) break;
      Thread.sleep(20);
    }
    assert count(lines, text) == 1 : lines;
    return lines;
  }

  private static long count(List<String> lines, String text) {
    return lines.stream().filter(line -> line.contains(text)).count();
  }

  private static Fill fill(String id, String symbol, long time, boolean replay) {
    return new Fill(id, replay ? "REPLAY-1" : "paper", symbol, true, 1, 100, time, 1, 20, "USD", "o-" + id, "", false, replay);
  }

  private static Tick tick(long time) {
    return (Tick) Proxy.newProxyInstance(Tick.class.getClassLoader(), new Class<?>[]{Tick.class},
        (proxy, method, arguments) -> method.getName().equals("getTime") ? (Object) time : null);
  }

  private static Bar bar(long start) {
    return (Bar) Proxy.newProxyInstance(Bar.class.getClassLoader(), new Class<?>[]{Bar.class},
        (proxy, method, arguments) -> switch (method.getName()) {
          case "getStartTime" -> start;
          case "getEndTime" -> start + 60_000;
          default -> null;
        });
  }

  /** An instrument that answers the probes: three ticks and two one-minute bars from the start of any window
   * (one bar ending a little after it), or an IllegalStateException for every question when broken. */
  private static Instrument instrument(String symbol, String active, double tickSize, List<TickOperation> listeners,
                                       List<String> asked, boolean broken) {
    return (Instrument) Proxy.newProxyInstance(Instrument.class.getClassLoader(), new Class<?>[]{Instrument.class},
        (proxy, method, arguments) -> {
          String name = method.getName();
          if (name.equals("getSymbol")) return symbol;
          if (name.equals("getActiveSymbol")) return active;
          if (name.equals("hashCode")) return System.identityHashCode(proxy);
          if (name.equals("equals")) return proxy == arguments[0];
          if (name.equals("toString")) return symbol;
          if (broken) throw new IllegalStateException("broken");
          switch (name) {
            case "getTickSize": return tickSize;
            case "addListener": if (arguments[0] instanceof TickOperation operation) listeners.add(operation); return null;
            case "removeListener": listeners.remove(arguments[0]); return null;
            case "forEachTick": {
              long start = (Long) arguments[0];
              asked.add("forEachTick " + start);
              var each = (TickOperation) arguments[arguments.length - 1];
              for (int i = 0; i < 3; i++) each.onTick(tick(start + i * 1000));
              return null;
            }
            case "getBars": {
              long start = (Long) arguments[0], end = (Long) arguments[1];
              return List.of(bar(start), bar(end - 30_000));
            }
            default: return null;
          }
        });
  }
}
