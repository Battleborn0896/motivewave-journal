package org.mwjournal.study;

import com.motivewave.platform.sdk.common.BarSize;
import com.motivewave.platform.sdk.common.Instrument;
import com.motivewave.platform.sdk.common.TickOperation;
import java.time.Instant;
import java.util.ArrayList;
import java.util.HashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.TreeSet;
import java.util.concurrent.ArrayBlockingQueue;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.ThreadPoolExecutor;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicLong;
import java.util.function.Consumer;

/** Questions only MotiveWave itself can answer before the study measures MAE / MFE on its own
 * (docs/design/study-measures.md, section 7). Each is asked once per session and answered in one line of
 * MotiveWave's log:
 * - a chart's getSymbol() and getActiveSymbol() beside the symbols of the fills;
 * - Instrument.addListener on a fill's instrument: its prints against the chart's onTick prints for 60 s;
 * - forEachTick and getBars on a fill's instrument with no chart of it open; in replay, ending at the
 *   replay clock, and whether anything later comes back;
 * - getTickSize() of the fills' instruments;
 * - the largest delay between a fill's time and the poll that first saw it (a new line when it grows).
 *
 * Nothing here writes to the bridge, and nothing it does can stop the capture: every probe catches its
 * own failure and says so in its line. The poller calls afterPoll; the chart callbacks call chart() and
 * tick(), which only touch a map and a counter. */
final class JournalProbes {
  static final long LISTEN_NANOS = 60_000_000_000L;
  // Like the trade-tick backfill's first look: a little after the study loads, so the data connection is up.
  static final long HISTORY_AFTER_NANOS = 30_000_000_000L;
  static final long WINDOW_MILLIS = 5 * 60_000L;
  // How far back the next version reads history for a trade with no chart (design section 3.5). MotiveWave keeps
  // the fills of expired contracts for months, and their history is not what that question is about.
  static final long RECENT_MILLIS = 30 * 86_400_000L;
  static final int TICK_CAP = 200_000;
  static final String PREFIX = "MotiveWave Journal probe: ";

  /** What a chart showed when it last called: its instrument's two symbols and the mode. */
  record Chart(String symbol, String active, boolean replay) {
    /** The contract the chart's prints are of: the active contract, or the symbol when there is none. */
    String contract() { return active == null || active.isBlank() ? symbol : active; }
  }

  /** The addListener probe while it runs. */
  private static final class Listening {
    final Object chart; final Chart shown; final Instrument instrument; final TickOperation operation;
    final long since; final AtomicLong heard = new AtomicLong(), ticks = new AtomicLong();
    Listening(Object chart, Chart shown, Instrument instrument, long since) {
      this.chart = chart; this.shown = shown; this.instrument = instrument; this.since = since;
      operation = tick -> heard.incrementAndGet();
    }
  }

  private final Consumer<String> log;
  private final long started, startedWall;
  private final Map<Object, Chart> charts = new ConcurrentHashMap<>();
  // Poll thread only.
  private final Set<Chart> told = new HashSet<>();
  private final Set<String> sized = new HashSet<>(), known = new HashSet<>();
  private boolean baseline;
  private final long[] largest = {Long.MIN_VALUE, Long.MIN_VALUE}; // live, replay
  private final boolean[] listened = new boolean[2], history = new boolean[2];
  private volatile Listening listening;
  private ExecutorService thread;

  JournalProbes(Consumer<String> log, long startedNanos) {
    this.log = log;
    this.started = startedNanos;
    this.startedWall = System.currentTimeMillis();
  }

  // ---------------------------------------------------------------- chart callbacks (MotiveWave's threads)

  /** A chart's callback: remembered until forget(). One map read when nothing changed. */
  void chart(Object owner, Instrument instrument, boolean replay) {
    try {
      if (instrument == null) return;
      Chart shown = new Chart(instrument.getSymbol(), instrument.getActiveSymbol(), replay);
      if (!shown.equals(charts.get(owner))) charts.put(owner, shown);
    } catch (RuntimeException | LinkageError ignored) { /* a probe never disturbs the chart */ }
  }

  /** One onTick of that chart, counted while the addListener probe listens beside it. */
  void tick(Object owner, boolean replay) {
    Listening now = listening;
    if (now != null && now.chart == owner && now.shown.replay() == replay) now.ticks.incrementAndGet();
  }

  /** The chart was removed or its settings changed. */
  void forget(Object owner) {
    charts.remove(owner);
    Listening now = listening;
    if (now != null && now.chart == owner) finish(now, "stopped early: the chart was closed or its settings changed");
  }

  void close() {
    Listening now = listening;
    if (now != null) finish(now, "stopped early: the study was removed");
    ExecutorService running;
    synchronized (this) { running = thread; thread = null; }
    if (running != null) running.shutdownNow();
  }

  // ---------------------------------------------------------------- the poller

  /** After each complete poll. replayClock is the newest replay time a chart reported, 0 before any. */
  void afterPoll(JournalNativeAccess.Snapshot snapshot, long replayClock, long nowNanos) {
    run("fill delay", () -> delays(snapshot.fills(), replayClock));
    run("chart symbols", () -> symbols(snapshot.fills()));
    run("tick size", () -> tickSizes(snapshot.instruments()));
    run("addListener", () -> listen(snapshot.instruments(), nowNanos));
    run("history", () -> history(snapshot, replayClock, nowNanos));
  }

  private void run(String probe, Runnable body) {
    try { body.run(); }
    catch (RuntimeException | LinkageError exc) { say(probe + " probe failed: " + describe(exc)); }
  }

  /** The largest time between a fill and the poll that first saw it, per mode. The first snapshot is
   * MotiveWave's history, not something that just happened, so only fills after it count, and of live fills
   * only those dated after the study started: an account that connects later brings its earlier fills with
   * it. Replay fills are measured on the replay clock, the time they belong to. */
  private void delays(List<JournalNativeAccess.Fill> fills, long replayClock) {
    long wall = System.currentTimeMillis();
    for (var fill : fills) {
      if (!known.add((fill.replay() ? "replay\0" : "live\0") + fill.account() + "\0" + fill.id()) || !baseline
          || !fill.replay() && fill.time() < startedWall) continue;
      long seen = fill.replay() ? replayClock : wall;
      if (seen <= 0) continue;
      int mode = fill.replay() ? 1 : 0;
      long delay = seen - fill.time();
      if (delay <= largest[mode]) continue;
      largest[mode] = delay;
      say("largest fill delay so far (" + mode(fill.replay()) + "): " + delay + " ms, a " + fill.symbol()
          + " fill at " + Instant.ofEpochMilli(fill.time()) + " first seen at "
          + Instant.ofEpochMilli(seen) + (fill.replay() ? " on the replay clock" : "") + ".");
    }
    baseline = true;
  }

  /** Once per chart: whether the symbols MotiveWave gives the chart are the ones the fills carry. */
  private void symbols(List<JournalNativeAccess.Fill> fills) {
    for (Chart chart : charts.values()) {
      if (told.contains(chart)) continue;
      told.add(chart);
      var symbols = new TreeSet<String>();
      int matching = 0;
      for (var fill : fills) if (fill.replay() == chart.replay()) {
        symbols.add(fill.symbol());
        if (fill.symbol().equals(chart.contract())) matching++;
      }
      say("a " + mode(chart.replay()) + " chart's getSymbol() is " + quoted(chart.symbol()) + " and getActiveSymbol() "
          + quoted(chart.active()) + "; " + (matching > 0 ? matching + " fills have the symbol " + quoted(chart.contract())
              : "no fill has the symbol " + quoted(chart.contract())) + "; the " + mode(chart.replay())
          + " fills' symbols: " + list(symbols) + ".");
    }
  }

  /** getTickSize() of each fill instrument, once per contract. */
  private void tickSizes(Map<String, Instrument> instruments) {
    var said = new ArrayList<String>();
    for (var entry : new java.util.TreeMap<>(instruments).entrySet()) {
      if (!sized.add(entry.getKey())) continue;
      String size;
      try { size = Double.toString(entry.getValue().getTickSize()); }
      catch (RuntimeException | LinkageError exc) { size = "failed (" + describe(exc) + ")"; }
      said.add(entry.getKey() + " " + size);
    }
    if (!said.isEmpty()) say("getTickSize() of the fills' instruments: " + String.join(", ", said) + ".");
  }

  /** Instrument.addListener on a fill's instrument, beside a chart of that contract, for 60 seconds: live
   * first, then replay. One at a time. */
  private void listen(Map<String, Instrument> instruments, long nowNanos) {
    Listening now = listening;
    if (now != null) {
      if (nowNanos - now.since >= LISTEN_NANOS) finish(now, null);
      return;
    }
    for (var chart : charts.entrySet()) {
      Chart shown = chart.getValue();
      int mode = shown.replay() ? 1 : 0;
      Instrument instrument = instruments.get(JournalNativeAccess.contract(shown.replay(), shown.contract()));
      if (listened[mode] || instrument == null) continue;
      listened[mode] = true;
      var probe = new Listening(chart.getKey(), shown, instrument, nowNanos);
      try { instrument.addListener(probe.operation); }
      catch (RuntimeException | LinkageError exc) {
        say("Instrument.addListener on the " + mode(shown.replay()) + " fill instrument " + quoted(shown.contract())
            + " failed: " + describe(exc) + ".");
        return;
      }
      listening = probe;
      return;
    }
  }

  private synchronized void finish(Listening now, String early) {
    if (listening != now) return;
    listening = null;
    String removed = "";
    try { now.instrument.removeListener(now.operation); }
    catch (RuntimeException | LinkageError exc) { removed = "; removeListener failed: " + describe(exc); }
    long seconds = (System.nanoTime() - now.since) / 1_000_000_000L;
    say("Instrument.addListener on the " + mode(now.shown.replay()) + " fill instrument " + quoted(now.shown.contract())
        + " heard " + now.heard.get() + " prints in " + seconds + " s; the chart " + quoted(now.shown.symbol())
        + " (active " + quoted(now.shown.active()) + ") had " + now.ticks.get() + " in onTick over the same time"
        + (early == null ? "" : "; " + early) + removed + ".");
  }

  /** forEachTick and getBars on a fill's instrument, preferring a contract no chart of this study shows, and
   * live a fill of the last 30 days: live, around the newest such fill; replay, the ten minutes up to the replay
   * clock. Once per mode, on its own thread, since MotiveWave may have to load the history. */
  private void history(JournalNativeAccess.Snapshot snapshot, long replayClock, long nowNanos) {
    if (nowNanos - started < HISTORY_AFTER_NANOS) return;
    for (boolean replay : new boolean[]{false, true}) {
      int mode = replay ? 1 : 0;
      if (history[mode] || replay && replayClock <= 0) continue;
      var charted = new HashSet<String>();
      for (Chart chart : charts.values()) if (chart.replay() == replay) charted.add(chart.contract());
      long recent = System.currentTimeMillis() - RECENT_MILLIS;
      // Recent first, then no chart, then the newest: 3 is a recent fill of a contract no chart shows.
      java.util.function.ToIntFunction<JournalNativeAccess.Fill> rank = fill ->
          (replay || fill.time() >= recent ? 2 : 0) + (charted.contains(fill.symbol()) ? 0 : 1);
      JournalNativeAccess.Fill chosen = null;
      for (var fill : snapshot.fills()) {
        if (fill.replay() != replay || !snapshot.instruments().containsKey(JournalNativeAccess.contract(replay, fill.symbol())))
          continue;
        int compared = chosen == null ? 1 : Integer.compare(rank.applyAsInt(fill), rank.applyAsInt(chosen));
        if (compared > 0 || compared == 0 && fill.time() > chosen.time()) chosen = fill;
      }
      if (chosen == null) continue;
      history[mode] = true;
      Instrument instrument = snapshot.instruments().get(JournalNativeAccess.contract(replay, chosen.symbol()));
      String symbol = chosen.symbol(), chart = charted.contains(symbol) ? "a chart of it is open" : "no chart of it open";
      long end = replay ? replayClock : Math.min(chosen.time() + WINDOW_MILLIS, System.currentTimeMillis());
      long start = replay ? replayClock - 2 * WINDOW_MILLIS : chosen.time() - WINDOW_MILLIS;
      submit(() -> history(instrument, replay, symbol, chart, start, end));
    }
  }

  private void history(Instrument instrument, boolean replay, String symbol, String chart, long start, long end) {
    long began = System.nanoTime();
    String ticks, bars;
    long[] seen = {0, Long.MAX_VALUE, Long.MIN_VALUE, 0}; // count, first, last, after the end
    try {
      // false: never manufacture ticks from bars, as the backfill asks.
      instrument.forEachTick(start, end, false, false, tick -> {
        long time = tick.getTime();
        if (++seen[0] > TICK_CAP) throw new IllegalStateException("Enough ticks.");
        seen[1] = Math.min(seen[1], time);
        seen[2] = Math.max(seen[2], time);
        if (time > end) seen[3]++;
      });
      ticks = seen[0] + " ticks" + span(seen[1], seen[2]) + ", " + seen[3] + " after the end";
    } catch (RuntimeException | LinkageError exc) {
      ticks = seen[0] > TICK_CAP ? "more than " + TICK_CAP + " ticks (stopped there)" : "failed (" + describe(exc) + ")";
    }
    try {
      var list = instrument.getBars(start, end, BarSize.minute(1), false);
      long first = Long.MAX_VALUE, last = Long.MIN_VALUE, later = 0;
      int count = 0;
      for (Object item : list == null ? List.of() : list) {
        if (!(item instanceof com.motivewave.platform.sdk.common.Bar bar)) continue;
        count++;
        first = Math.min(first, bar.getStartTime());
        last = Math.max(last, bar.getEndTime());
        if (bar.getEndTime() > end) later++;
      }
      bars = count + " one-minute bars" + span(first, last) + ", " + later + " ending after the end";
    } catch (RuntimeException | LinkageError exc) {
      bars = "failed (" + describe(exc) + ")";
    }
    say("history of the " + mode(replay) + " fill instrument " + quoted(symbol) + " (" + chart + ") from "
        + Instant.ofEpochMilli(start) + " to " + Instant.ofEpochMilli(end) + (replay ? " (the replay clock)" : "")
        + ": forEachTick " + ticks + "; getBars " + bars + "; in " + (System.nanoTime() - began) / 1_000_000 + " ms.");
  }

  private synchronized void submit(Runnable task) {
    if (thread == null) thread = new ThreadPoolExecutor(1, 1, 0, TimeUnit.MILLISECONDS, new ArrayBlockingQueue<>(2),
        work -> { Thread t = new Thread(work, "mwj-probe"); t.setDaemon(true); return t; },
        new ThreadPoolExecutor.DiscardPolicy());
    thread.execute(() -> run("history", task));
  }

  // ---------------------------------------------------------------- wording

  private void say(String line) {
    try { log.accept(PREFIX + line); }
    catch (RuntimeException ignored) { /* the log is the probe's only output */ }
  }
  private static String mode(boolean replay) { return replay ? "replay" : "live"; }
  private static String quoted(String value) { return value == null ? "null" : "'" + value + "'"; }
  private static String list(Set<String> values) {
    if (values.isEmpty()) return "none";
    var shown = new ArrayList<String>();
    for (String value : values) { if (shown.size() == 10) { shown.add("…"); break; } shown.add(quoted(value)); }
    return String.join(", ", shown);
  }
  private static String span(long first, long last) {
    return first > last ? "" : " from " + Instant.ofEpochMilli(first) + " to " + Instant.ofEpochMilli(last);
  }
  private static String describe(Throwable exc) {
    return exc.getClass().getSimpleName() + (exc.getMessage() == null ? "" : ": " + exc.getMessage());
  }
}
