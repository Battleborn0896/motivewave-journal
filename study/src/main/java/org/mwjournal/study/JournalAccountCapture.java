package org.mwjournal.study;

import java.io.IOException;
import java.nio.file.Path;
import java.time.Instant;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.HashMap;
import java.util.HashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.TreeMap;
import java.util.UUID;
import java.util.concurrent.Executors;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.ScheduledExecutorService;
import java.util.concurrent.TimeUnit;
import static org.mwjournal.study.JournalPriceEvents.quote;

/** One bounded polling observer per bridge path, shared across chart instances. Never trades. */
final class JournalAccountCapture {
  private static final Map<Path, JournalAccountCapture> SHARED = new HashMap<>();
  private final JournalNativeAccess.Reader reader;
  private final JournalEventWriter live, replay;
  private final ScheduledExecutorService timer;
  private final JournalCaptureState state;
  private final Map<String,String> seen = new HashMap<>(), ordersSeen = new HashMap<>();
  private final Map<String,String> fillProviders = new HashMap<>();
  // What was added to the plain label when a fill was first written: 'c' for an assumed currency,
  // 'p' for no price. No entry means the plain label. A fill is always re-sent as it was first written.
  private final Map<String,String> fillLabels = new HashMap<>();
  private final Map<String,Long> unpricedSince = new HashMap<>(); // poll thread only
  private final Map<String,Long> replayTimes = new HashMap<>();
  private final Object replayLock = new Object();
  private volatile String run = UUID.randomUUID().toString();
  private int references;
  private long lastStatus;
  private volatile boolean stateDirty;
  private ReplayClock lastClock;
  private boolean replayReady; // guarded by replayLock; new replay fills wait for the active chart clock
  private record ReplayClock(String run, Map<String,Long> times) {}
  /** What one complete snapshot says about positions, live and replay apart. Immutable: the poller
   * publishes a new one and the tick thread only ever reads. */
  private record Holdings(Side live, Side replay, long nanos) {}
  /** Instruments with an open position, and instruments where a fill was first seen moments ago. */
  private record Side(Set<String> open, Set<String> traded, Map<String,Long> lastFill) {}
  /** run is the replay run a replay fill belongs to, and empty for a live fill. */
  private record Book(boolean replay, String account, String symbol, String run) {}
  private static final class Tally { double net; int fills; long latest; }
  // A snapshot this old means the poller has stalled; it is younger than the tick pre-roll on purpose,
  // so the prints around an entry are still buffered when the gate falls back to recording.
  static final long HOLDINGS_STALE_NANOS = 5_000_000_000L;
  // A new fill keeps its instrument active for as long as the gate's pre-roll reaches back: a trade
  // opened and closed between two polls never shows as a position, but its prints are still buffered.
  static final long TRADED_LINGER_NANOS = TickGate.PRE_ROLL_MILLIS * 1_000_000L;
  // A fill without a price is held back this long the first time it is seen, in case MotiveWave is still
  // writing the record: once sent, a fill cannot be corrected.
  static final long UNPRICED_GRACE_NANOS = 5_000_000_000L;
  static final String QUALITY = "MotiveWave internal capture; fees may be estimated";
  static final String CURRENCY_ASSUMED = "account currency assumed from the instrument";
  static final String NO_PRICE = "platform reported no fill price";
  private static final double FLAT = 1e-9; // fractional quantities do not sum back to an exact zero
  private volatile Holdings holdings; // null while the positions are not known
  private Map<Book, Tally> tallies; // the last complete snapshot; null until there has been one. Poll thread only.
  private final Map<Book, Long> traded = new HashMap<>(); // when a new fill was last noticed. Poll thread only.
  private volatile boolean polled;
  private boolean mediaBaseline; // retained history on startup is not a new screen-capture trigger
  private final Set<TickGate<JournalTicks.Held>> liveGates = ConcurrentHashMap.newKeySet();
  private final Set<TickGate<JournalTicks.Held>> replayGates = ConcurrentHashMap.newKeySet();
  private final Set<Runnable> listeners = ConcurrentHashMap.newKeySet();
  private volatile List<JournalNativeAccess.Fill> fills; // the last snapshot's; null until one has been read
  // The replay fills written so far, each with the run it was filed under; null until a snapshot has been read.
  private volatile List<JournalBackfill.ReplayFill> replayFills;
  private String lastBeat = "";
  /** Set while fills cannot be read at all. Null means capture is working, whatever warnings says. */
  volatile String failure;
  /** One sentence about what was skipped or assumed while capture is working; null when nothing was. */
  volatile String warnings;

  private JournalAccountCapture(Path file, JournalNativeAccess.Reader reader, boolean schedule) throws IOException {
    this.reader = reader;
    state = new JournalCaptureState(file);
    String savedRun = state.read(fillProviders, fillLabels);
    String clockRun = state.readClock(replayTimes);
    if (clockRun != null) savedRun = clockRun;
    if (savedRun != null) run = UUID.fromString(savedRun).toString();
    stateDirty = savedRun == null;
    live = JournalEventWriter.acquire(file);
    JournalEventWriter other;
    try { other = JournalEventWriter.acquire(replayFile(file)); }
    catch (IOException | RuntimeException exc) { live.release(); throw exc; }
    replay = other;
    timer = Executors.newSingleThreadScheduledExecutor(task -> {
      Thread thread = new Thread(task, "mwj-accounts"); thread.setDaemon(true); return thread;
    });
    if (schedule) timer.scheduleWithFixedDelay(this::beat, 0, 500, TimeUnit.MILLISECONDS);
  }

  /** The timer's beat: one poll, then the charts that asked to be told. They are called outside the
   * poll's lock, so one of them may take a lock of its own without risking a deadlock with close(). */
  private void beat() {
    poll();
    for (Runnable listener : listeners) {
      try { listener.run(); }
      catch (RuntimeException | LinkageError ignored) { /* One chart's trouble must not stop the poller. */ }
    }
  }

  static JournalAccountCapture acquire(Path file) throws IOException, ReflectiveOperationException {
    Path key = file.toAbsolutePath().normalize();
    synchronized (SHARED) {
      var capture = SHARED.get(key);
      if (capture == null) {
        capture = new JournalAccountCapture(key, JournalNativeAccess.connect(MotiveWaveJournal.class.getClassLoader()), true);
        SHARED.put(key, capture);
      }
      capture.references++;
      return capture;
    }
  }
  static JournalAccountCapture fixture(Path file, JournalNativeAccess.Reader reader) throws IOException {
    return new JournalAccountCapture(file, reader, false);
  }
  static Path replayFile(Path liveFile) {
    String name = liveFile.getFileName().toString();
    return liveFile.resolveSibling(name.endsWith(".ndjson")
        ? name.substring(0, name.length() - 7) + ".replay.ndjson" : name + ".replay.ndjson");
  }

  void watch(TickGate<JournalTicks.Held> liveGate, TickGate<JournalTicks.Held> replayGate) {
    liveGates.add(liveGate);
    replayGates.add(replayGate);
  }

  void unwatch(TickGate<JournalTicks.Held> liveGate, TickGate<JournalTicks.Held> replayGate) {
    liveGates.remove(liveGate);
    replayGates.remove(replayGate);
  }

  /** afterPoll runs on the poller's thread after every poll, twice a second, market open or not. */
  void watch(Runnable afterPoll) { listeners.add(afterPoll); }
  void unwatch(Runnable afterPoll) { listeners.remove(afterPoll); }

  /** Every fill of the last snapshot that could be read, live and replay; null before the first one. */
  List<JournalNativeAccess.Fill> fills() { return fills; }

  /** The replay fills of the last snapshot that have been written, each with the replay run it was filed
   * under (the run in its provider's name, which never changes once written). A replay fill still waiting
   * for a replay chart is not in it. Null before the first snapshot. */
  List<JournalBackfill.ReplayFill> replayFills() { return replayFills; }

  /** The run in a replay fill's provider: motivewave-native:<workspace>:replay:<run>. Null for any other. */
  static String runOf(String provider) {
    int at = provider == null ? -1 : provider.lastIndexOf(":replay:");
    return at < 0 || at + 8 >= provider.length() ? null : provider.substring(at + 8);
  }

  private void flushBuffered(long nowNanos) {
    for (var gate : liveGates) JournalTicks.flush(gate, live, this, false, nowNanos);
    for (var gate : replayGates) JournalTicks.flush(gate, replay, this, true, nowNanos);
  }

  String replayRun(String symbol, long timestamp) {
    // Never wait for the account snapshot/file I/O on MotiveWave's chart/tick thread.
    synchronized (replayLock) {
      if (timestamp <= 0) return run;
      Long prior = replayTimes.put(symbol, timestamp);
      if (prior != null && timestamp < prior) {
        run = UUID.randomUUID().toString();
        replayTimes.clear();
        replayTimes.put(symbol, timestamp);
        stateDirty = true;
      }
      replayReady = true;
      return run;
    }
  }

  /** True while any account has an open position in this instrument. Safe on MotiveWave's tick thread:
   * one volatile read, no lock, no I/O. It fails open: while the positions are not known (no poll yet,
   * the adapter failing or returning a partial snapshot, the poller stalled) the answer is true, because
   * a print skipped by mistake is gone for good and one recorded by mistake only costs disk. */
  boolean holding(String instrumentSymbol, boolean replay) {
    return holding(instrumentSymbol, replay, System.nanoTime());
  }
  boolean holding(String instrumentSymbol, boolean replay, long nowNanos) {
    Side side = side(replay, nowNanos);
    return side == null || instrumentSymbol != null && side.open().contains(instrumentSymbol);
  }

  /** What the tick gate asks: holding(), or a fill first seen within the last pre-roll. The second part
   * catches the trade that was opened and closed between two polls, and leaves a short tail after an exit. */
  boolean active(String instrumentSymbol, boolean replay) {
    return active(instrumentSymbol, replay, System.nanoTime());
  }
  boolean active(String instrumentSymbol, boolean replay, long nowNanos) {
    Side side = side(replay, nowNanos);
    return side == null || instrumentSymbol != null
        && (side.open().contains(instrumentSymbol) || side.traded().contains(instrumentSymbol));
  }
  /** Optional post-exit tail runs on MARKET time, not wall time: accelerated and paused replay
   * must retain the same analysis horizon. Prior replay runs never extend the current run's tail. */
  boolean recording(String symbol, boolean replay, long marketTime, int minutes, long nowNanos) {
    Side side = side(replay, nowNanos);
    if (side == null || active(symbol, replay, nowNanos)) return true;
    Long latest = symbol == null ? null : side.lastFill().get(symbol);
    return minutes > 0 && latest != null && marketTime >= latest && marketTime - latest <= minutes * 60000L;
  }
  private Side side(boolean replay, long nowNanos) {
    Holdings known = holdings;
    if (known == null || nowNanos - known.nanos() > HOLDINGS_STALE_NANOS) return null;
    return replay ? known.replay() : known.live();
  }

  /** Net signed quantity per account and instrument, taken from the fills the capture reads anyway.
   * It is only as complete as that list: a position whose entry fill MotiveWave no longer retains
   * is not seen as open.
   *
   * Replay fills are netted run by run. MotiveWave keeps the fills of earlier runs, so a long left open
   * when a replay was abandoned would otherwise cancel the short taken in the next one and that trade
   * would be recorded as flat. The cost is the safe one: an abandoned position keeps its instrument
   * recording until MotiveWave forgets the fills. */
  private Holdings holdings(JournalNativeAccess.Snapshot snapshot, String replayProvider, long nowNanos) {
    var next = new HashMap<Book, Tally>();
    for (var fill : snapshot.fills()) {
      // The same rule the fill is filed under further down: the run it was first seen in, else this one.
      String run = fill.replay() ? fillProviders.getOrDefault(key(snapshot.workspace(), fill), replayProvider) : "";
      Tally tally = next.computeIfAbsent(new Book(fill.replay(), fill.account(), fill.symbol(), run), book -> new Tally());
      tally.net += fill.buy() ? fill.qty() : -fill.qty();
      tally.fills++;
      tally.latest = Math.max(tally.latest, fill.time());
    }
    // The first complete snapshot is the baseline: its fills are history, not something that just happened.
    if (tallies != null) next.forEach((book, tally) -> {
      Tally before = tallies.get(book);
      if (before == null || before.fills != tally.fills || before.latest != tally.latest) traded.put(book, nowNanos);
    });
    tallies = next;
    traded.values().removeIf(noticed -> nowNanos - noticed > TRADED_LINGER_NANOS);
    // A fill without a symbol is rejected further on, with the same visible failure as before; here it
    // is only kept out of the sets, which cannot hold a null.
    var liveOpen = new HashSet<String>();
    var replayOpen = new HashSet<String>();
    var liveLast = new HashMap<String,Long>();
    var replayLast = new HashMap<String,Long>();
    next.forEach((book, tally) -> {
      // Phrased so that a quantity that is not a number counts as open.
      if (book.symbol() != null && !(Math.abs(tally.net) <= FLAT)) (book.replay() ? replayOpen : liveOpen).add(book.symbol());
      if (book.symbol() != null && (!book.replay() || book.run().equals(replayProvider)))
        (book.replay() ? replayLast : liveLast).merge(book.symbol(), tally.latest, Math::max);
    });
    var liveTraded = new HashSet<String>();
    var replayTraded = new HashSet<String>();
    for (Book book : traded.keySet()) if (book.symbol() != null) (book.replay() ? replayTraded : liveTraded).add(book.symbol());
    return new Holdings(new Side(Set.copyOf(liveOpen), Set.copyOf(liveTraded), Map.copyOf(liveLast)),
        new Side(Set.copyOf(replayOpen), Set.copyOf(replayTraded), Map.copyOf(replayLast)), nowNanos);
  }

  /** For the study's log line: whether fills are being read right now. */
  String status() {
    return failure != null ? "failing" : polled ? "connected" : "starting";
  }

  void poll() { poll(System.nanoTime()); }

  /** nowNanos is the clock positions are stamped with; only tests pass anything but the present. */
  synchronized void poll(long nowNanos) {
    try {
      if (live.failure != null) throw live.failure;
      if (replay.failure != null) throw replay.failure;
      var snapshot = reader.read();
      String snapshotReplayProvider;
      boolean replaySnapshotReady;
      synchronized (replayLock) {
        snapshotReplayProvider = provider(snapshot.workspace(), true); replaySnapshotReady = replayReady;
      }
      // Published before any file work, so the tick thread starts recording as soon as an entry fill
      // is visible. A partial snapshot has skipped fills, so its net positions prove nothing.
      holdings = snapshot.skipped().isEmpty() ? holdings(snapshot, snapshotReplayProvider, nowNanos) : null;
      this.fills = snapshot.fills();
      flushBuffered(nowNanos);
      var fills = new ArrayList<>(snapshot.fills());
      // Stable sort: preserve native snapshot order for same-time fills, never invent sequence from ID text.
      fills.sort(Comparator.comparingLong(JournalNativeAccess.Fill::time));
      var skipped = new TreeMap<>(snapshot.skipped()); // sorted, so the sentence reads the same every poll
      int conflicts = 0, waitingReplay = 0, settling = 0, assumed = 0, unpriced = 0;
      for (var fill : fills) {
        String key = key(snapshot.workspace(), fill);
        boolean written = fillProviders.containsKey(key);
        if (fill.currencyAssumed()) assumed++;
        if (!fill.priced()) unpriced++;
        if (fill.replay() && !replaySnapshotReady && !written) {
          waitingReplay++; continue; // Never assign a newly retained replay fill before knowing its session clock.
        }
        if (!written && !fill.priced()) {
          Long first = unpricedSince.putIfAbsent(key, nowNanos);
          if (first == null || nowNanos - first < UNPRICED_GRACE_NANOS) { settling++; continue; }
        }
        String provider = fillProviders.getOrDefault(key,
            fill.replay() ? snapshotReplayProvider : provider(snapshot.workspace(), false));
        String label = written ? fillLabels.getOrDefault(key, "") : label(fill);
        String event;
        try { event = execution(fill, provider, label); }
        catch (IllegalArgumentException exc) {
          // One record that cannot be written must not stop the others from being read.
          skipped.merge(JournalNativeAccess.INCOMPLETE, 1, Integer::sum); continue;
        }
        String old = seen.get(key);
        if (event.equals(old)) continue;
        if (old != null) { conflicts++; continue; } // Never invent another fill for a correction.
        JournalEventWriter output = fill.replay() ? replay : live;
        String wire = event.substring(0, event.length() - 1) + ",\"observed_at\":\"" + Instant.now()
            + "\",\"capture_kind\":\"" + (mediaBaseline ? "new" : "retained") + "\"}";
        if (output.offer(wire, provider, Instant.ofEpochMilli(fill.time()).toString())) {
          seen.put(key, event);
          fillProviders.put(key, provider);
          if (!label.isEmpty()) fillLabels.put(key, label);
          unpricedSince.remove(key);
          stateDirty = true;
        }
      }
      if (snapshot.skipped().isEmpty() && waitingReplay == 0 && settling == 0) mediaBaseline = true;
      var filed = new ArrayList<JournalBackfill.ReplayFill>();
      for (var fill : fills) {
        String run = fill.replay() ? runOf(fillProviders.get(key(snapshot.workspace(), fill))) : null;
        if (run != null) filed.add(new JournalBackfill.ReplayFill(run, fill));
      }
      replayFills = List.copyOf(filed);
      var entryOrders = new HashMap<String, JournalNativeAccess.Fill>();
      var entryPositions = new HashMap<String, JournalNativeAccess.Fill>();
      for (var fill : fills) if (fill.entry()) {
        String scope = linkScope(fill.account(), fill.symbol(), fill.replay(), fill.buy());
        if (!fill.orderId().isBlank()) entryOrders.putIfAbsent(scope + fill.orderId(), fill);
        if (!fill.positionId().isBlank()) entryPositions.putIfAbsent(scope + fill.positionId(), fill);
      }
      for (var order : snapshot.orders()) {
        String scope = linkScope(order.account(), order.symbol(), order.replay(), !order.buy());
        var parent = order.parentId().isBlank() ? null : entryOrders.get(scope + order.parentId());
        var position = order.positionId().isBlank() ? null : entryPositions.get(scope + order.positionId());
        var first = parent == null ? position : position == null || parent.time() <= position.time() ? parent : position;
        if (first == null) continue; // No price/time proximity guessing for unrelated limit orders.
        String provider = fillProviders.get(key(snapshot.workspace(), first));
        if (provider == null) continue;
        String event = protection(order, first, provider);
        String key = provider + "\0" + order.account() + "\0" + order.id();
        if (event.equals(ordersSeen.get(key))) continue;
        if ((order.replay() ? replay : live).offer(event, provider, Instant.ofEpochMilli(order.time()).toString()))
          ordersSeen.put(key, event);
      }
      if (stateDirty) {
        String savedRun = run;
        state.save(savedRun, fillProviders, fillLabels);
        synchronized (replayLock) { stateDirty = !run.equals(savedRun); }
      }
      ReplayClock clock;
      synchronized (replayLock) { clock = new ReplayClock(run, Map.copyOf(replayTimes)); }
      if (!clock.equals(lastClock)) {
        // Small clock checkpoint only; do not rewrite every historical fill on every price tick.
        state.saveClock(clock.run(), clock.times()); lastClock = clock;
      }
      // Fills were read, so this is not a failure: what was skipped or assumed is said beside a
      // 'connected' status, and the fills that did arrive stay trustworthy.
      warnings = warnings(skipped, assumed, unpriced, waitingReplay, conflicts);
      failure = null;
      polled = true;
      status(snapshot.workspace(), null, fills.size());
    } catch (Exception | LinkageError exc) {
      holdings = null; // positions unknown again: record ticks rather than guess
      warnings = null;
      failure = JournalEventWriter.anotherCopy(exc) ? JournalEventWriter.ANOTHER_COPY
          : "Native MotiveWave adapter unavailable: " + exc.getClass().getSimpleName();
      try { flushBuffered(nowNanos); }
      catch (RuntimeException ignored) { /* A failed writer is already reported by the capture. */ }
      status("", failure, 0);
    }
  }

  /** The heartbeat's sentence about a capture that works but not for everything, or null when there is
   * nothing to say. Counts are of what MotiveWave holds now, so the sentence is the same poll after poll. */
  static String warnings(Map<String,Integer> skipped, int assumed, int unpriced, int waitingReplay, int conflicts) {
    var parts = new ArrayList<String>();
    int total = 0;
    var reasons = new ArrayList<String>();
    for (var reason : new TreeMap<>(skipped).entrySet()) {
      total += reason.getValue();
      reasons.add(skipped.size() == 1 ? reason.getKey() : reason.getValue() + " " + reason.getKey());
    }
    if (total > 0) parts.add(count(total, "fill") + " skipped: " + String.join(", ", reasons));
    if (assumed > 0) parts.add(count(assumed, "fill") + " with an assumed currency");
    if (unpriced > 0) parts.add(count(unpriced, "fill") + " with no fill price");
    if (waitingReplay > 0) parts.add(count(waitingReplay, "replay fill") + " waiting for a replay chart with the study");
    if (conflicts > 0) parts.add(count(conflicts, "fill record") + " changed after capture (not duplicated)");
    return parts.isEmpty() ? null : String.join("; ", parts);
  }
  private static String count(int number, String noun) { return number + " " + noun + (number == 1 ? "" : "s"); }

  private String provider(String workspace, boolean isReplay) {
    return "motivewave-native:" + workspace + (isReplay ? ":replay:" + run : ":live");
  }
  private static String linkScope(String account, String symbol, boolean replay, boolean buy) {
    return replay + "\0" + account + "\0" + symbol + "\0" + buy + "\0";
  }
  private static String key(String workspace, JournalNativeAccess.Fill f) {
    return java.util.Base64.getUrlEncoder().withoutPadding().encodeToString(
        (workspace + "\0" + f.replay() + "\0" + f.account() + "\0" + f.id()).getBytes(java.nio.charset.StandardCharsets.UTF_8));
  }
  /** What a fill adds to the plain label the first time it is written: 'c', 'p', both or nothing. */
  static String label(JournalNativeAccess.Fill f) {
    return (f.currencyAssumed() ? "c" : "") + (f.priced() ? "" : "p");
  }
  static String execution(JournalNativeAccess.Fill f, String provider) {
    return execution(f, provider, label(f));
  }
  /** label is what the fill was first written with, which for a fill written before is not always what
   * label(f) says now: the app treats a fill that comes back different as a conflict. A fill study 0.2
   * wrote with a price of 0 therefore keeps the plain label, and the app reads a price of 0 as unknown. */
  static String execution(JournalNativeAccess.Fill f, String provider, String label) {
    if (f.id().isBlank() || f.account().isBlank() || f.symbol().isBlank() || f.currency().isBlank()
        || !Double.isFinite(f.qty()) || f.qty() <= 0
        || !Double.isFinite(f.commission()) || !Double.isFinite(f.pointValue()) || f.pointValue() <= 0 || f.time() <= 0)
      throw new IllegalArgumentException("Incomplete native fill.");
    boolean assumed = label.contains("c"), unpriced = label.contains("p");
    // Do not label commissions as broker-confirmed: MotiveWave may estimate them from instrument settings.
    String multiplier = ",\"point_value\":" + f.pointValue();
    // A price that is not a number cannot be written as JSON; 0 is the placeholder for "none".
    return "{\"version\":1,\"type\":\"execution\",\"mode\":" + quote(f.replay() ? "replay" : "live")
        + ",\"provider\":" + quote(provider) + ",\"account\":" + quote(f.account())
        + ",\"symbol\":" + quote(f.symbol()) + ",\"side\":" + quote(f.buy() ? "buy" : "sell")
        + ",\"qty\":" + f.qty() + ",\"price\":" + (Double.isFinite(f.price()) ? f.price() : 0.0)
        + ",\"ts\":" + quote(Instant.ofEpochMilli(f.time()).toString())
        + ",\"execution_id\":" + quote(f.id()) + ",\"order_id\":" + quote(f.orderId())
        + ",\"native_entry\":" + f.entry()
        + ",\"native_position_id\":" + quote(f.positionId()) + ",\"commission\":" + f.commission()
        + ",\"commission_known\":false,\"currency\":" + quote(f.currency()) + multiplier
        + (unpriced ? ",\"price_known\":false" : "") + (assumed ? ",\"currency_assumed\":true" : "")
        + ",\"quality\":" + quote(QUALITY + (assumed ? "; " + CURRENCY_ASSUMED : "") + (unpriced ? "; " + NO_PRICE : "")) + "}";
  }
  static String protection(JournalNativeAccess.ProtectiveOrder o, JournalNativeAccess.Fill first, String provider) {
    if (o.id().isBlank() || o.time() <= 0 || !Double.isFinite(o.price()))
      throw new IllegalArgumentException("Incomplete protective order.");
    return "{\"version\":1,\"type\":\"protection\",\"mode\":" + quote(o.replay() ? "replay" : "live")
        + ",\"provider\":" + quote(provider) + ",\"account\":" + quote(o.account())
        + ",\"symbol\":" + quote(o.symbol()) + ",\"order_id\":" + quote(o.id())
        + ",\"entry_execution_id\":" + quote(first.id()) + ",\"role\":" + quote(o.role())
        + ",\"state\":" + quote(o.state()) + ",\"price\":" + o.price()
        + ",\"ts\":" + quote(Instant.ofEpochMilli(o.time()).toString()) + "}";
  }
  /** Every ten seconds, and at once when what it says has changed. 'error' only when fills cannot be
   * read at all; a capture that works but skipped or assumed something is 'connected' with warnings. */
  private void status(String workspace, String error, int fills) {
    String said = warnings;
    String beat = ",\"workspace\":" + quote(workspace) + ",\"status\":" + quote(error == null ? "connected" : "error")
        + ",\"detail\":" + quote(error == null
            ? "Experimental read-only account capture; " + count(fills, "fill") + " read" : error)
        + (error == null && said != null ? ",\"warnings\":" + quote(said) : "")
        + ",\"connector\":" + quote(JournalPaths.CONNECTOR)
        + ",\"connector_version\":" + quote(JournalPaths.CONNECTOR_VERSION) + "}";
    if (beat.equals(lastBeat) && System.currentTimeMillis() - lastStatus < 10000) return;
    lastStatus = System.currentTimeMillis();
    lastBeat = beat;
    String now = Instant.now().toString();
    String body = ",\"provider\":\"motivewave-native\",\"ts\":" + quote(now) + beat;
    // Each file on its own: when another copy of the study holds one of them, the other still says why.
    try { live.offer("{\"version\":1,\"type\":\"heartbeat\",\"mode\":\"live\"" + body, provider(workspace, false), now); }
    catch (RuntimeException ignored) { /* Failure remains visible in the study. */ }
    try { replay.offer("{\"version\":1,\"type\":\"heartbeat\",\"mode\":\"replay\"" + body, provider(workspace, true), now); }
    catch (RuntimeException ignored) { /* Failure remains visible in the study. */ }
  }
  void release(Path file) {
    synchronized (SHARED) {
      if (--references > 0) return;
      SHARED.remove(file.toAbsolutePath().normalize(), this);
    }
    close();
  }
  void close() {
    timer.shutdownNow();
    liveGates.clear();
    replayGates.clear();
    listeners.clear();
    synchronized (this) { live.release(); replay.release(); }
  }
}
