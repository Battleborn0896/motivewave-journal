package org.mwjournal.study;

import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.time.Instant;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.Properties;
import java.util.TreeMap;
import java.util.TreeSet;
import java.util.concurrent.CancellationException;
import java.util.function.BooleanSupplier;
import java.util.function.Consumer;

/** Real ticks for trades that were not recorded as they happened: the fills say when each position was
 * held, and the platform's tick history is asked for exactly those stretches. Nothing is generated from
 * bars, and a stretch with no tick history stays empty.
 *
 * It lives outside the study class so it can be exercised without a MotiveWave session: the tick
 * history and the writer are handed in. It waits and writes files, so it never runs on the tick thread.
 * One instance per study load: what it asked for and got nothing back is only remembered here. */
final class JournalBackfill {
  static final String SOURCE = "motivewave-ticks:history";
  /** Either side of a position: fill times and tick times come from different clocks. */
  static final long PAD_MILLIS = 5_000;
  static final long MAX_WINDOW_MILLIS = 24 * 3_600_000L;
  /** A position that traded this recently may not be over, and its ticks may not be in the history yet. */
  static final long SETTLE_MILLIS = 300_000;
  static final int MAX_DAYS = 365;
  /** Finished windows older than any setting can ask for are forgotten, so the sidecar stays small. */
  static final long KEEP_MILLIS = (MAX_DAYS + 35) * 86_400_000L;
  static final int MAX_TICKS = 2_000_000, WINDOWS_PER_PASS = 4;
  /** Half the writer's queue: history waits above this, so live prints always find room. */
  static final int HIGH_WATER = 32_768;
  private static final double FLAT = 1e-9; // fractional quantities do not sum back to an exact zero

  /** A stretch of time in epoch milliseconds: start included, end not. */
  record Window(long start, long end) {}

  /** The platform's real ticks between two moments, oldest first. Never ticks made up from bars. */
  interface TickSource {
    void forEach(long start, long end, Consumer<JournalPriceEvents.Print> each) throws Exception;
  }

  /** Where the events go. False means no room right now: the event is offered again, never dropped. */
  interface Sink { boolean offer(String event); }

  /** One pass, for the log. done: windows written. empty: windows the history had no ticks for.
   * failed: windows the history could not be asked about. cut: windows that reached the tick cap.
   * remaining: windows left for the next pass. waits: how often the writer's queue was full. */
  record Result(int done, long ticks, int empty, int failed, int cut, int remaining, long waits, String failure) {
    int attempted() { return done + empty + failed; }
  }

  private final Ledger ledger;
  private final int maxTicks, perPass;
  // Windows this study load already asked for without getting anything: asked again only by a later load.
  private final Map<String, Spans> asked = new HashMap<>();

  JournalBackfill(Ledger ledger) { this(ledger, MAX_TICKS, WINDOWS_PER_PASS); }

  /** The bounds are parameters so a test can reach them with a handful of ticks. */
  JournalBackfill(Ledger ledger, int maxTicks, int perPass) {
    this.ledger = ledger;
    this.maxTicks = maxTicks;
    this.perPass = perPass;
  }

  Ledger ledger() { return ledger; }

  /** Whether a chart loads history for its fills at all: live only (replay never reads history, and a
   * chart that has not said which it is counts as neither), and only for the chart's own contract. A
   * continuous chart, or one standing in for the traded contract under another symbol, shows the prices
   * of another instrument than the one the fills are in; those are never written as that contract's ticks. */
  static boolean applies(String instrumentSymbol, String journalSymbol, boolean proxy, Boolean replayMode, int days) {
    return days > 0 && Boolean.FALSE.equals(replayMode) && !proxy && instrumentSymbol != null
        && !instrumentSymbol.isBlank() && instrumentSymbol.equals(journalSymbol);
  }

  private static final class Held { double net; long first = Long.MAX_VALUE, last = Long.MIN_VALUE; }

  /** The holding windows of this instrument's positions, oldest first: first fill to last fill, a few
   * seconds either side. A position is the fills that share an account and MotiveWave's position id;
   * netting fills flat-to-flat instead would turn one missing entry into a window days long.
   *
   * Left out: replay fills, other instruments, fills without a position id, positions that are not flat
   * in the fills MotiveWave retains (still open, or an entry or exit is missing, so there is no complete
   * trade to measure), positions longer than 24 hours, those that ended before notBefore, and those
   * with a fill after notAfter. Positions held at the very same times, as copied accounts are, give
   * one window. */
  static List<Window> windows(List<JournalNativeAccess.Fill> fills, String symbol, long notBefore, long notAfter) {
    var positions = new HashMap<String, Held>();
    for (var fill : fills) {
      if (fill.replay() || symbol == null || !symbol.equals(fill.symbol()) || fill.positionId().isBlank()
          || fill.time() <= 0) continue;
      Held held = positions.computeIfAbsent(fill.account() + "\0" + fill.positionId(), key -> new Held());
      held.net += fill.buy() ? fill.qty() : -fill.qty();
      held.first = Math.min(held.first, fill.time());
      held.last = Math.max(held.last, fill.time());
    }
    var result = new TreeSet<Window>(Comparator.comparingLong(Window::start).thenComparingLong(Window::end));
    for (Held held : positions.values()) {
      // Phrased so that a quantity that is not a number counts as not flat.
      if (!(Math.abs(held.net) <= FLAT) || held.last - held.first + 2 * PAD_MILLIS > MAX_WINDOW_MILLIS
          || held.last + PAD_MILLIS < notBefore || held.last > notAfter) continue;
      result.add(new Window(held.first - PAD_MILLIS, held.last + PAD_MILLIS));
    }
    return List.copyOf(result);
  }

  /** One pass: the oldest few windows that are not in the bridge file yet. The parts of a window another
   * window already covered are not loaded again. Every event waits for room in the writer's queue.
   *
   * A window is remembered as done once its ticks are written. One that came back empty, or that the
   * history could not be asked about, is remembered by this instance only, so it is asked again the next
   * time the study loads and not before. Cancelling leaves the window to be done again.
   *
   * days is the 'Backfill Trade Ticks (Days)' setting; 0 does nothing. */
  synchronized Result pass(String symbol, List<JournalNativeAccess.Fill> fills, long now, int days,
                           TickSource source, Sink sink, BooleanSupplier cancelled) throws IOException {
    int done = 0, empty = 0, failed = 0, cut = 0, remaining = 0;
    long ticks = 0;
    long[] waits = new long[1];
    String failure = null;
    if (days <= 0 || symbol == null || fills == null) return new Result(0, 0, 0, 0, 0, 0, 0, null);
    Spans tried = asked.computeIfAbsent(symbol, key -> new Spans());
    Spans planned = new Spans(); // overlapping windows share their overlap; count and load it once
    long horizon = now - Math.min(days, MAX_DAYS) * 86_400_000L;
    for (Window window : windows(fills, symbol, horizon, now - SETTLE_MILLIS)) {
      for (Window open : ledger.missing(symbol, window, tried)) {
        for (Window piece : planned.missing(open.start(), open.end())) {
          planned.add(piece.start(), piece.end());
          if (done + empty + failed >= perPass) { remaining++; continue; }
          if (cancelled.getAsBoolean()) throw new CancellationException();
          if (!ledger.claim(symbol, piece)) { remaining++; continue; } // another chart took it meanwhile
          boolean written = false;
          try {
            long[] count = load(symbol, piece, source, sink, cancelled, waits);
            if (count[0] == 0 && count[1] == 0) {
              tried.add(piece.start(), piece.end());
              empty++;
            } else {
              written = true;
              done++;
              ticks += count[0];
              if (count[1] > 0) cut++;
            }
          } catch (CancellationException exc) {
            throw exc;
          } catch (Exception exc) {
            // Ticks written before the failure stay in the file; the app keeps one copy of a repeated tick.
            tried.add(piece.start(), piece.end());
            failed++;
            failure = exc.getClass().getSimpleName();
          } finally {
            ledger.finish(symbol, piece, written, now);
          }
        }
      }
    }
    return new Result(done, ticks, empty, failed, cut, remaining, waits[0], failure);
  }

  /** count: ticks written, ticks left out by the cap, and the time of the first and last one left out. */
  private long[] load(String symbol, Window piece, TickSource source, Sink sink, BooleanSupplier cancelled,
                      long[] waits) throws Exception {
    long[] count = {0, 0, Long.MAX_VALUE, Long.MIN_VALUE};
    RuntimeException[] stopped = new RuntimeException[1];
    source.forEach(piece.start(), piece.end(), print -> {
      // Once stopped, stay stopped: whoever iterates the ticks may swallow what a callback throws.
      if (stopped[0] != null) throw stopped[0];
      try {
        if (cancelled.getAsBoolean()) throw new CancellationException();
        if (print == null || print.time() < piece.start() || print.time() >= piece.end()) return;
        if (count[0] >= maxTicks) {
          // Counted, not written: the gap event then says exactly how much is missing, and from when.
          count[1]++;
          count[2] = Math.min(count[2], print.time());
          count[3] = Math.max(count[3], print.time());
          return;
        }
        put(sink, JournalPriceEvents.tick(symbol, print, false, SOURCE, "live"), cancelled, waits);
        count[0]++;
      } catch (RuntimeException exc) {
        stopped[0] = exc;
        throw exc;
      }
    });
    if (stopped[0] != null) throw stopped[0];
    if (count[1] > 0) put(sink, JournalEventWriter.gap(SOURCE, symbol, count[1], Instant.ofEpochMilli(count[2]),
        Instant.ofEpochMilli(count[3])), cancelled, waits);
    return count;
  }

  /** Waits for room rather than dropping: history can always be a little later. */
  private static void put(Sink sink, String event, BooleanSupplier cancelled, long[] waits) {
    while (!sink.offer(event)) {
      if (cancelled.getAsBoolean()) throw new CancellationException();
      waits[0]++;
      try { Thread.sleep(2); }
      catch (InterruptedException exc) {
        Thread.currentThread().interrupt();
        throw new CancellationException();
      }
    }
  }

  /** Stretches of time that do not overlap, joined up as they are added. Not thread-safe. */
  static final class Spans {
    private final TreeMap<Long,Long> spans = new TreeMap<>();

    void add(long start, long end) {
      if (end <= start) return;
      var before = spans.floorEntry(start);
      if (before != null && before.getValue() >= start) {
        start = before.getKey();
        end = Math.max(end, before.getValue());
      }
      var inside = spans.subMap(start, true, end, true);
      for (long value : inside.values()) end = Math.max(end, value);
      inside.clear();
      spans.put(start, end);
    }

    void remove(long start, long end) {
      if (end <= start) return;
      var before = spans.lowerEntry(start);
      if (before != null && before.getValue() > start) {
        spans.put(before.getKey(), start);
        if (before.getValue() > end) spans.put(end, before.getValue());
      }
      var inside = spans.subMap(start, true, end, false);
      long tail = Long.MIN_VALUE;
      for (long value : inside.values()) tail = Math.max(tail, value);
      inside.clear();
      if (tail > end) spans.put(end, tail);
    }

    /** The parts of start..end that are not in here, oldest first. */
    List<Window> missing(long start, long end) {
      var result = new ArrayList<Window>();
      long at = start;
      var before = spans.floorEntry(start);
      if (before != null) at = Math.max(at, before.getValue());
      for (var span : spans.subMap(start, false, end, false).entrySet()) {
        if (span.getKey() > at) result.add(new Window(at, span.getKey()));
        at = Math.max(at, span.getValue());
      }
      if (at < end) result.add(new Window(at, end));
      return result;
    }

    boolean covers(long start, long end) { return missing(start, end).isEmpty(); }

    void dropBefore(long time) { spans.values().removeIf(end -> end < time); }

    boolean isEmpty() { return spans.isEmpty(); }

    /** start-end,start-end in epoch milliseconds. */
    String text() {
      var result = new StringBuilder();
      spans.forEach((start, end) -> result.append(result.isEmpty() ? "" : ",").append(start).append('-').append(end));
      return result.toString();
    }

    static Spans parse(String text) throws IOException {
      Spans result = new Spans();
      try {
        for (String span : text.split(",")) {
          if (span.isBlank()) continue;
          int dash = span.indexOf('-', 1);
          result.add(Long.parseLong(span.substring(0, dash).strip()), Long.parseLong(span.substring(dash + 1).strip()));
        }
      } catch (RuntimeException exc) {
        throw new IOException("Unreadable tick backfill record.", exc);
      }
      return result;
    }
  }

  /** Which stretches of each instrument's tick history are already in the bridge file. One per bridge
   * file, shared by every chart writing to it, and kept in a small file beside it so that a restart
   * does not load them again. The file holds symbols and times only. */
  static final class Ledger {
    private static final Map<Path, Ledger> SHARED = new HashMap<>();
    private final Path path;
    private final Map<String, Spans> done = new HashMap<>(), busy = new HashMap<>();

    /** The one ledger of this bridge file for as long as MotiveWave runs. */
    static Ledger shared(Path bridge) throws IOException {
      Path key = bridge.toAbsolutePath().normalize();
      synchronized (SHARED) {
        Ledger ledger = SHARED.get(key);
        if (ledger == null) SHARED.put(key, ledger = new Ledger(key));
        return ledger;
      }
    }

    static Path sidecar(Path bridge) {
      return bridge.resolveSibling(bridge.getFileName() + ".tick-backfill");
    }

    /** Reads what earlier runs finished. Tests open one directly to stand in for a restart. */
    Ledger(Path bridge) throws IOException {
      path = sidecar(bridge);
      if (!Files.exists(path)) return;
      if (Files.size(path) > 16L * 1024 * 1024) throw new IOException("Tick backfill record exceeds safety limit.");
      Properties data = new Properties();
      try (var input = Files.newInputStream(path)) { data.load(input); }
      if (!"1".equals(data.getProperty("version"))) throw new IOException("Unsupported tick backfill record.");
      for (String key : data.stringPropertyNames()) if (key.startsWith("done."))
        done.put(key.substring(5), Spans.parse(data.getProperty(key)));
    }

    /** The parts of the window that are neither written nor being written, less what the caller rules out. */
    synchronized List<Window> missing(String symbol, Window window, Spans except) {
      var result = new ArrayList<Window>();
      for (Window open : spans(done, symbol).missing(window.start(), window.end()))
        for (Window free : spans(busy, symbol).missing(open.start(), open.end()))
          result.addAll(except.missing(free.start(), free.end()));
      return result;
    }

    /** True when the piece is now the caller's to write; false when any part of it was taken meanwhile. */
    synchronized boolean claim(String symbol, Window piece) {
      if (!spans(done, symbol).missing(piece.start(), piece.end()).equals(List.of(piece))
          || !spans(busy, symbol).missing(piece.start(), piece.end()).equals(List.of(piece))) return false;
      spans(busy, symbol).add(piece.start(), piece.end());
      return true;
    }

    /** Gives a claimed piece back. written: its ticks are in the bridge file, so it is never loaded
     * again. It counts as written from here on even when the file beside the bridge cannot be saved:
     * loading it again every minute would only fill the bridge with copies. */
    synchronized void finish(String symbol, Window piece, boolean written, long now) throws IOException {
      spans(busy, symbol).remove(piece.start(), piece.end());
      if (!written) return;
      spans(done, symbol).add(piece.start(), piece.end());
      Properties data = new Properties();
      data.setProperty("version", "1");
      done.forEach((name, spans) -> {
        spans.dropBefore(now - KEEP_MILLIS);
        if (!spans.isEmpty()) data.setProperty("done." + name, spans.text());
      });
      JournalCaptureState.saveAtomic(path, data, "Tick history already written to the bridge; no credentials");
    }

    synchronized boolean written(String symbol, Window window) {
      return spans(done, symbol).covers(window.start(), window.end());
    }

    private static Spans spans(Map<String, Spans> map, String symbol) {
      return map.computeIfAbsent(symbol, key -> new Spans());
    }
  }
}
