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
import java.util.concurrent.ConcurrentLinkedQueue;
import java.util.concurrent.Executors;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.ScheduledExecutorService;
import java.util.concurrent.TimeUnit;
import java.util.function.Consumer;
import java.util.function.Function;
import com.motivewave.platform.sdk.common.Instrument;
import static org.mwjournal.study.JournalPriceEvents.quote;

/** One bounded polling observer per bridge path, shared across chart instances. Never trades. */
final class JournalAccountCapture {
  private static final Map<Path, JournalAccountCapture> SHARED = new HashMap<>();
  private final JournalNativeAccess.Reader reader;
  private final JournalEventWriter live, replay;
  private final ScheduledExecutorService timer;
  private final JournalCaptureState state;
  private final Map<String,String> seen = new HashMap<>(), ordersSeen = new HashMap<>();
  /** Each fill as it was the last time its event was built, and whether that event was a conflict. A fill
   * that comes back equal is passed over without building its event again. Poll thread only. */
  private record Sent(JournalNativeAccess.Fill fill, boolean conflict) {}
  private final Map<String, Sent> sent = new HashMap<>();
  /** How many fill events were built from a snapshot fill; the tests read it. Poll thread only. */
  long eventsBuilt;
  // The stop link (link()): the position each order id was linked to in this run of the study, and since when
  // a working stop has gone unlinked. Poll thread only.
  private final Map<String, Position> links = new HashMap<>();
  private final Map<String, Long> unlinkedSince = new HashMap<>();
  private final Set<String> unlinkedLogged = new HashSet<>();
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
  private long clockSaved; // when the replay clock was last saved, on the poll's clock
  /** What one complete snapshot says about positions, live and replay apart. Immutable: the poller
   * publishes a new one and the tick thread only ever reads. */
  private record Holdings(Side live, Side replay, long nanos) {}
  /** Instruments with an open position, and instruments where a fill was first seen moments ago. */
  private record Side(Set<String> open, Set<String> traded) {}
  /** run is the provider a replay fill is filed under, which names its replay run, and empty for a live fill. */
  record Book(boolean replay, String account, String symbol, String run) {}
  private static final class Tally { int fills; long latest; }
  // A snapshot this old means the poller has stalled; it is younger than the tick pre-roll on purpose,
  // so the prints around an entry are still buffered when the gate falls back to recording.
  static final long HOLDINGS_STALE_NANOS = 5_000_000_000L;
  // A new fill keeps its instrument active for as long as the gate's pre-roll reaches back: a trade
  // opened and closed between two polls never shows as a position, but its prints are still buffered.
  static final long TRADED_LINGER_NANOS = TickGate.PRE_ROLL_MILLIS * 1_000_000L;
  // A fill without a price is held back this long the first time it is seen, in case MotiveWave is still
  // writing the record: once sent, a fill cannot be corrected.
  static final long UNPRICED_GRACE_NANOS = 5_000_000_000L;
  // A working stop that no position takes this long is reported. A stop often appears a poll before the fill
  // that opens its position, and is linked then without anything to report.
  static final long UNLINKED_GRACE_NANOS = 5_000_000_000L;
  // The replay clock is saved when the run changes, and otherwise at most this often (and when the study closes).
  static final long CLOCK_EVERY_NANOS = 10_000_000_000L;
  static final String NO_POSITION = "no position on that account and contract",
      OWN_SIDE = "the open position is on the stop's own side", NOT_DURING = "no position at the stop's time";
  private static final int LOG_CAP = 500;
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
  private volatile Map<String, Instrument> instruments = Map.of(); // the last snapshot's, by JournalNativeAccess.contract
  // The replay fills written so far, each with the run it was filed under; null until a snapshot has been read.
  private volatile List<JournalBackfill.ReplayFill> replayFills;
  private String lastBeat = "";
  /** Lines for MotiveWave's log: the stop diagnostic and the probes. A chart's study writes them (drainLog). */
  private final ConcurrentLinkedQueue<String> log = new ConcurrentLinkedQueue<>();
  final JournalProbes probes;
  private boolean closed;
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
    probes = new JournalProbes(this::log, System.nanoTime());
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
    // Before the saved state is read: an older copy of the study saves its own as it retires.
    JournalHandoff.takeOver();
    if (JournalHandoff.retired()) throw new IOException(JournalEventWriter.RETIRED);
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

  /** A chart's callback, for the probes: which instrument it shows, live or replay. Cheap enough for onTick. */
  void chart(Object owner, Instrument instrument, boolean replay) { probes.chart(owner, instrument, replay); }
  /** One print reached that chart's onTick. */
  void ticked(Object owner, boolean replay) { probes.tick(owner, replay); }
  /** The chart is gone or its settings changed. */
  void forget(Object owner) { probes.forget(owner); }

  /** Hands the waiting log lines to one chart's study, each line once, whichever chart asks first. */
  void drainLog(Consumer<String> writer) {
    for (String line; (line = log.poll()) != null; ) writer.accept(line);
  }
  void log(String line) {
    if (log.size() < LOG_CAP) log.add(line); // nobody drains while no chart has the study: never grow for ever
  }

  /** Every fill of the last snapshot that could be read, live and replay; null before the first one. */
  List<JournalNativeAccess.Fill> fills() { return fills; }

  /** The instrument of the fills of each contract in the last snapshot, keyed by JournalNativeAccess.contract:
   * where the backfill reads a contract's tick history (JournalBackfill.history). Empty before the first. Set
   * before fills() and replayFills(), so read it after them: it then holds the instrument of every fill read. */
  Map<String, Instrument> instruments() { return instruments; }

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
   * catches the trade that was opened and closed between two polls, and leaves a short tail after an exit.
   * Nothing longer: the minutes after an exit come from MotiveWave's tick history (JournalBackfill). */
  boolean active(String instrumentSymbol, boolean replay) {
    return active(instrumentSymbol, replay, System.nanoTime());
  }
  boolean active(String instrumentSymbol, boolean replay, long nowNanos) {
    Side side = side(replay, nowNanos);
    return side == null || instrumentSymbol != null
        && (side.open().contains(instrumentSymbol) || side.traded().contains(instrumentSymbol));
  }
  private Side side(boolean replay, long nowNanos) {
    Holdings known = holdings;
    if (known == null || nowNanos - known.nanos() > HOLDINGS_STALE_NANOS) return null;
    return replay ? known.replay() : known.live();
  }

  /** The instruments with an open position, by the positions the stops are linked to (positions(), matching.py's
   * rules), taken from the fills the capture reads anyway. Where MotiveWave gives fills a position id they are
   * netted inside it, so an exit whose entry MotiveWave no longer keeps does not leave its contract open for
   * good. It is only as complete as that list: a position whose entry fill MotiveWave no longer retains
   * is not seen as open.
   *
   * Replay fills are netted run by run. MotiveWave keeps the fills of earlier runs, so a long left open
   * when a replay was abandoned would otherwise cancel the short taken in the next one and that trade
   * would be recorded as flat. The cost is the safe one: an abandoned position keeps its instrument
   * recording until MotiveWave forgets the fills. */
  private Holdings holdings(JournalNativeAccess.Snapshot snapshot, String replayProvider, long nowNanos) {
    // The same rule the fill is filed under further down: the run it was first seen in, else this one.
    Function<JournalNativeAccess.Fill, String> filed = fill -> fill.replay()
        ? fillProviders.getOrDefault(key(snapshot.workspace(), fill), replayProvider) : "";
    var next = new HashMap<Book, Tally>();
    for (var fill : snapshot.fills()) {
      Tally tally = next.computeIfAbsent(new Book(fill.replay(), fill.account(), fill.symbol(), filed.apply(fill)),
          book -> new Tally());
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
    for (var positions : positions(snapshot.fills(), filed).values())
      for (var position : positions) {
        Book book = position.book();
        if (position.open() && book.symbol() != null) (book.replay() ? replayOpen : liveOpen).add(book.symbol());
      }
    var liveTraded = new HashSet<String>();
    var replayTraded = new HashSet<String>();
    for (Book book : traded.keySet()) if (book.symbol() != null) (book.replay() ? replayTraded : liveTraded).add(book.symbol());
    return new Holdings(new Side(Set.copyOf(liveOpen), Set.copyOf(liveTraded)),
        new Side(Set.copyOf(replayOpen), Set.copyOf(replayTraded)), nowNanos);
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
      // The instruments before the fills: whoever reads the fills and then the instruments (the backfill) finds
      // the instrument of every fill it read.
      instruments = snapshot.instruments();
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
        Sent before = sent.get(key);
        if (before != null && before.fill().equals(fill)) { // as it was: nothing to build or write
          if (before.conflict()) conflicts++;
          continue;
        }
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
        eventsBuilt++;
        String old = seen.get(key);
        if (event.equals(old)) { sent.put(key, new Sent(fill, false)); continue; }
        if (old != null) { // Never invent another fill for a correction.
          conflicts++;
          sent.put(key, new Sent(fill, true));
          continue;
        }
        JournalEventWriter output = fill.replay() ? replay : live;
        String wire = event.substring(0, event.length() - 1) + ",\"observed_at\":\"" + Instant.now()
            + "\",\"capture_kind\":\"" + (mediaBaseline ? "new" : "retained") + "\"}";
        if (output.offer(wire, provider, Instant.ofEpochMilli(fill.time()).toString())) {
          seen.put(key, event);
          sent.put(key, new Sent(fill, false));
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
      Stops stops = stops(snapshot, fills, snapshotReplayProvider, nowNanos);
      if (stateDirty) {
        String savedRun = run;
        state.save(savedRun, fillProviders, fillLabels);
        synchronized (replayLock) { stateDirty = !run.equals(savedRun); }
      }
      saveClock(false, nowNanos);
      // Fills were read, so this is not a failure: what was skipped or assumed is said beside a
      // 'connected' status, and the fills that did arrive stay trustworthy.
      warnings = join(warnings(skipped, assumed, unpriced, waitingReplay, conflicts), stops.warning());
      failure = null;
      polled = true;
      status(snapshot.workspace(), null, fills.size());
      long replayClock;
      synchronized (replayLock) { replayClock = replayTimes.values().stream().mapToLong(Long::longValue).max().orElse(0); }
      try { probes.afterPoll(snapshot, replayClock, nowNanos); }
      catch (RuntimeException | LinkageError ignored) { /* a probe never disturbs the capture */ }
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
  private static String join(String first, String second) {
    return first == null ? second : second == null ? first : first + "; " + second;
  }

  /** The replay clock checkpoint: at once when the run changed (a rewind), otherwise at most every
   * CLOCK_EVERY_NANOS, and on close (force) whatever changed since. Never by a capture whose files another
   * copy of the study holds: that copy keeps the clock. */
  private void saveClock(boolean force, long nowNanos) throws IOException {
    ReplayClock clock;
    synchronized (replayLock) { clock = new ReplayClock(run, Map.copyOf(replayTimes)); }
    if (clock.equals(lastClock)) return;
    boolean rewound = lastClock == null || !clock.run().equals(lastClock.run());
    if (!force && !rewound && nowNanos - clockSaved < CLOCK_EVERY_NANOS) return;
    state.saveClock(clock.run(), clock.times());
    lastClock = clock;
    clockSaved = nowNanos;
  }

  // ---------------------------------------------------------------- stops

  /** One position of one account and contract, as the stop link rule sees it: a trade as the app groups the
   * fills (matching.py). first is its first fill and provider the one that fill was written under; from and to
   * are its first and last fills' times; open while contracts are held. */
  record Position(Book book, boolean longSide, JournalNativeAccess.Fill first, String provider, long from, long to,
                  boolean open) {}

  /** The positions of these fills, by account and contract (and replay run), grouped by matching.py's rules:
   * fills with MotiveWave's position id are netted inside that position, the others flat to flat; a reversal
   * starts the next position with the fill that crosses; at one instant a fill MotiveWave calls an entry of its
   * position comes first. A position whose kept fills contradict its entry flags (its entry is not kept) is
   * never open, nor is one that a newer position id of the same account and contract has replaced. Without the
   * position ids, one fill MotiveWave does not hold (a SIM account reset in mid-trade, say) offsets every later
   * position of that account and contract, and their stops go to the wrong one or to none.
   * providerOf names the provider a fill was written under, or null for a fill not written yet, which is left
   * out as the app has not got it. Live books have the run "", replay books their provider. */
  static Map<Book, List<Position>> positions(List<JournalNativeAccess.Fill> fills,
                                             Function<JournalNativeAccess.Fill, String> providerOf) {
    var ordered = new ArrayList<>(fills);
    ordered.sort(Comparator.comparingLong(JournalNativeAccess.Fill::time)
        .thenComparing(f -> f.positionId().isBlank() || !f.entry()));
    final class Trade {
      final Book book; final String id; final boolean longSide; final JournalNativeAccess.Fill first; final String provider;
      double held; long to; boolean entryMissing;
      Trade(Book book, String id, boolean longSide, JournalNativeAccess.Fill first, String provider) {
        this.book = book; this.id = id; this.longSide = longSide; this.first = first; this.provider = provider;
      }
    }
    record Key(Book book, String id) {}
    var trades = new ArrayList<Trade>();
    var open = new HashMap<Key, Trade>();
    var settled = new HashMap<Key, Trade>();
    var newest = new HashMap<Book, Map<String, Integer>>(); // the last fill of each position id, as an index
    int index = 0;
    for (var fill : ordered) {
      String provider = providerOf.apply(fill);
      if (provider == null) continue;
      index++;
      Book book = new Book(fill.replay(), fill.account(), fill.symbol(), fill.replay() ? provider : "");
      String id = fill.positionId();
      var key = new Key(book, id);
      if (!id.isBlank()) newest.computeIfAbsent(book, b -> new HashMap<>()).put(id, index);
      Trade trade = open.get(key);
      if (!id.isBlank() && (trade == null || !trade.entryMissing)) {
        boolean adds = trade != null && trade.longSide == fill.buy();
        double held = trade == null ? 0 : trade.held;
        // MotiveWave says this fill reduced its position but nothing kept was there to reduce, or that it
        // opened or added to one while it reduces: the position began before its kept fills (ENTRY_MISSING).
        if (!fill.entry() && (adds || fill.qty() > held + FLAT)
            || fill.entry() && trade != null && !adds && fill.qty() <= held + FLAT) {
          if (trade == null) {
            trade = settled.get(key);
            if (trade == null) trades.add(trade = new Trade(book, id, !fill.buy(), fill, provider));
            open.put(key, trade);
          }
          trade.entryMissing = true;
        }
      }
      if (trade != null && trade.entryMissing) { // kept together, never netted
        trade.held = 0;
        trade.to = fill.time();
        continue;
      }
      double remaining = fill.qty();
      while (remaining > FLAT) {
        if (trade == null) {
          trades.add(trade = new Trade(book, id, fill.buy(), fill, provider));
          open.put(key, trade);
        }
        boolean entry = trade.longSide == fill.buy();
        double take = entry ? remaining : Math.min(remaining, trade.held);
        trade.held += entry ? take : -take;
        trade.to = fill.time();
        remaining -= take;
        if (trade.held < FLAT) { // flat: what is left of the fill starts the next position
          trade.held = 0;
          settled.put(key, open.remove(key));
          trade = null;
        }
      }
    }
    var result = new HashMap<Book, List<Position>>();
    for (var trade : trades) {
      Map<String, Integer> last = trade.id.isBlank() ? Map.of() : newest.get(trade.book);
      // One account holds one position per contract: once MotiveWave moved on to another position id, this one
      // ended in a fill it did not keep (EXIT_MISSING).
      boolean replaced = !trade.id.isBlank() && last.entrySet().stream()
          .anyMatch(other -> !other.getKey().equals(trade.id) && other.getValue() > last.get(trade.id));
      boolean held = trade.held > FLAT && !trade.entryMissing && !replaced;
      result.computeIfAbsent(trade.book, b -> new ArrayList<>()).add(new Position(trade.book, trade.longSide,
          trade.first, trade.provider, trade.first.time(), trade.to, held));
    }
    return result;
  }

  /** The stop link rule, for a stop on the same mode, account and contract (and replay run) as these positions
   * and on their closing side (a sell stop for a long, a buy stop for a short): the position held at the order's
   * last-modified time, from its first fill to its last (to now while open); a working stop no position held
   * then (a bracket's stop placed a moment before its entry fills) goes to the open position. So a stop left
   * working, and not moved, after a position closed by hand stays with that position across a restart. At the instant one position ends and the next begins, a stop that ended
   * then (filled, cancelled or rejected) ended with the first, and a working one was placed for the next. Null
   * when there is none. The order's parent and position ids play no part: prop accounts fill with no position
   * id and no entry flag. */
  static Position link(List<Position> closingSide, JournalNativeAccess.ProtectiveOrder order) {
    Position held = null;
    for (var position : closingSide)
      if (position.from() <= order.time() && (position.open() || order.time() <= position.to())) {
        held = position;
        if (!order.working()) break; // positions are in time order: the first one held then is the one it ended with
      }
    if (held == null && order.working()) for (var position : closingSide) if (position.open()) return position;
    return held;
  }

  /** What became of one snapshot's stop orders: MotiveWave's counts, then how many were on an account and
   * contract with a position, how many of those on a position's closing side, and how many linked. unlinked
   * counts by reason the working stops no position has taken for UNLINKED_GRACE_NANOS. */
  record Stops(JournalNativeAccess.OrderCounts read, int withPosition, int closingSide, int linked,
               Map<String,Integer> unlinked) {
    /** The heartbeat's part about stops: only while a working stop stays unlinked. A working stop without an
     * instrument is only counted: MotiveWave keeps such leftovers (a client order with no instrument and a
     * negative price, months old), and the reader's own type check (JournalNativeAccess.connect) is what
     * guards against reading every order's instrument the wrong way, as studies before 0.3.6 did. */
    String warning() {
      if (unlinked.isEmpty()) return null;
      int total = 0;
      var reasons = new ArrayList<String>();
      for (var reason : new TreeMap<>(unlinked).entrySet()) {
        total += reason.getValue();
        reasons.add(unlinked.size() == 1 ? reason.getKey() : reason.getValue() + " " + reason.getKey());
      }
      return count(total, "working stop order") + " not linked to a position: " + String.join(", ", reasons)
          + " (orders: " + counts() + ")";
    }
    String counts() {
      return read.read() + " read, " + read.stops() + " stop, " + read.fresh() + " new, " + read.noInstrument()
          + " without instrument, " + read.noState() + " without state, " + withPosition + " with a position, "
          + closingSide + " on the closing side, " + linked + " linked";
    }
  }

  /** Links each stop order and writes the ones linked (section 3.8 of docs/design/study-measures.md). The
   * position an order id is linked to the first time is kept for this run of the study, so a stop that is
   * moved or fills stays with its position. */
  private Stops stops(JournalNativeAccess.Snapshot snapshot, List<JournalNativeAccess.Fill> fills,
                      String replayProvider, long nowNanos) {
    var unlinked = new TreeMap<String,Integer>();
    if (snapshot.orders().isEmpty()) {
      unlinkedSince.clear();
      return new Stops(snapshot.orderCounts(), 0, 0, 0, unlinked);
    }
    var books = positions(fills, fill -> fillProviders.get(key(snapshot.workspace(), fill)));
    int withPosition = 0, closingSide = 0, linked = 0;
    var waiting = new HashSet<String>();
    for (var order : snapshot.orders()) {
      String id = (order.replay() ? "replay" : "live") + "\0" + order.account() + "\0" + order.id();
      Position position = links.get(id);
      String reason = null;
      if (position != null) { withPosition++; closingSide++; }
      else {
        var book = books.getOrDefault(new Book(order.replay(), order.account(), order.symbol(),
            order.replay() ? replayProvider : ""), List.of());
        var closing = book.stream().filter(p -> p.longSide() != order.buy()).toList();
        if (book.isEmpty()) reason = NO_POSITION;
        else {
          withPosition++;
          if (!closing.isEmpty()) { closingSide++; position = link(closing, order); }
          if (position != null) links.put(id, position);
          // The account's history has positions on both sides, so what says why is the position open now.
          else reason = book.stream().anyMatch(p -> p.open() && p.longSide() == order.buy()) ? OWN_SIDE : NOT_DURING;
        }
      }
      if (position == null) {
        if (!order.working()) continue; // an old stop of a position MotiveWave no longer holds: nothing to say
        // Nothing open on this side of MotiveWave (live or replay): an entry stop, or a bracket's stop waiting
        // for its entry to fill. Nothing to say, and the grace starts again once a position is open.
        if (!anyOpen(books, order.replay())) continue;
        waiting.add(id);
        long since = unlinkedSince.computeIfAbsent(id, key -> nowNanos);
        if (nowNanos - since < UNLINKED_GRACE_NANOS) continue;
        unlinked.merge(reason, 1, Integer::sum);
        if (unlinkedLogged.add(id)) log("MotiveWave Journal: stop order " + quote(order.id()) + " is not linked to a "
            + "position (" + reason + "): parent id " + quote(order.parentId()) + ", position id "
            + quote(order.positionId()) + ", account " + quote(order.account()) + ", symbol " + quote(order.symbol())
            + ", " + (order.buy() ? "buy" : "sell") + ", " + order.state() + ", modified "
            + Instant.ofEpochMilli(order.time()) + "; " + (order.replay() ? "replay" : "live") + " positions open: "
            + openPositions(books, order.replay()) + ".");
        continue;
      }
      linked++;
      String event;
      try { event = protection(order, position.first(), position.provider()); }
      catch (IllegalArgumentException exc) { continue; } // no id, time or price: one order must not stop the poll
      String key = position.provider() + "\0" + order.account() + "\0" + order.id();
      if (event.equals(ordersSeen.get(key))) continue;
      if ((order.replay() ? replay : live).offer(event, position.provider(), Instant.ofEpochMilli(order.time()).toString()))
        ordersSeen.put(key, event);
    }
    unlinkedSince.keySet().retainAll(waiting);
    return new Stops(snapshot.orderCounts(), withPosition, closingSide, linked, unlinked);
  }

  private static boolean anyOpen(Map<Book, List<Position>> books, boolean replay) {
    return books.entrySet().stream().anyMatch(book -> book.getKey().replay() == replay
        && book.getValue().stream().anyMatch(Position::open));
  }

  /** The open positions of one mode as account and symbol, word for word, for the log. */
  private static String openPositions(Map<Book, List<Position>> books, boolean replay) {
    var open = new ArrayList<String>();
    books.forEach((book, positions) -> {
      for (var position : positions) if (position.open() && book.replay() == replay)
        open.add(quote(book.account()) + " " + quote(book.symbol()) + (position.longSide() ? " long" : " short"));
    });
    java.util.Collections.sort(open);
    return open.isEmpty() ? "none" : String.join(", ", open.size() > 10 ? open.subList(0, 10) : open);
  }

  private String provider(String workspace, boolean isReplay) {
    return "motivewave-native:" + workspace + (isReplay ? ":replay:" + run : ":live");
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
  /** A stop order's line. Take-profit orders are not written: R needs only the stop (the app's risk.py). */
  static String protection(JournalNativeAccess.ProtectiveOrder o, JournalNativeAccess.Fill first, String provider) {
    if (o.id().isBlank() || o.time() <= 0 || !Double.isFinite(o.price()))
      throw new IllegalArgumentException("Incomplete protective order.");
    return "{\"version\":1,\"type\":\"protection\",\"mode\":" + quote(o.replay() ? "replay" : "live")
        + ",\"provider\":" + quote(provider) + ",\"account\":" + quote(o.account())
        + ",\"symbol\":" + quote(o.symbol()) + ",\"order_id\":" + quote(o.id())
        + ",\"entry_execution_id\":" + quote(first.id()) + ",\"role\":\"stop\""
        + ",\"state\":" + quote(o.state()) + ",\"price\":" + o.price()
        + ",\"ts\":" + quote(Instant.ofEpochMilli(o.time()).toString()) + "}";
  }
  /** Every ten seconds, and at once when what it says has changed. 'error' only when fills cannot be
   * read at all; a capture that works but skipped or assumed something is 'connected' with warnings. */
  private void status(String workspace, String error, int fills) {
    String said = warnings;
    String beat = ",\"workspace\":" + quote(workspace) + ",\"status\":" + quote(error == null ? "connected" : "error")
        + ",\"detail\":" + quote(error == null
            ? "Recording fills read-only; " + count(fills, "fill") + " read" : error)
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
  /** Every capture of this copy, closed for a newer copy (JournalHandoff.retire), whichever charts still hold it. */
  static void closeAll() {
    List<JournalAccountCapture> all;
    synchronized (SHARED) {
      all = new ArrayList<>(SHARED.values());
      SHARED.clear();
    }
    for (var capture : all) capture.close();
  }

  /** Once: a chart that lets go of a capture retired before it (closeAll) finds it closed already, and must not
   * save its replay clock over the newer copy's. */
  void close() {
    synchronized (this) {
      if (closed) return;
      closed = true;
    }
    timer.shutdownNow();
    liveGates.clear();
    replayGates.clear();
    listeners.clear();
    probes.close();
    synchronized (this) {
      // The last replay time seen, so a rewind while the study is unloaded is still noticed.
      if (polled && failure == null && live.failure == null && replay.failure == null) {
        try { saveClock(true, System.nanoTime()); }
        catch (IOException | RuntimeException ignored) { /* the clock saved within the last 10 s stands */ }
      }
      live.release();
      replay.release();
    }
  }
}
