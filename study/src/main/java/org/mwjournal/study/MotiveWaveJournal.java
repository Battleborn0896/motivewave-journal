package org.mwjournal.study;

import com.motivewave.platform.sdk.common.Bar;
import com.motivewave.platform.sdk.common.BarSize;
import com.motivewave.platform.sdk.common.DataContext;
import com.motivewave.platform.sdk.common.Defaults;
import com.motivewave.platform.sdk.common.Instrument;
import com.motivewave.platform.sdk.common.NVP;
import com.motivewave.platform.sdk.common.Tick;
import com.motivewave.platform.sdk.common.desc.BooleanDescriptor;
import com.motivewave.platform.sdk.common.desc.DiscreteDescriptor;
import com.motivewave.platform.sdk.common.desc.IntegerDescriptor;
import com.motivewave.platform.sdk.common.desc.SettingsDescriptor;
import com.motivewave.platform.sdk.common.desc.StringDescriptor;
import com.motivewave.platform.sdk.study.Study;
import com.motivewave.platform.sdk.study.StudyHeader;

import java.io.IOException;
import java.nio.file.Path;
import java.time.Instant;
import java.util.List;
import java.util.UUID;
import java.util.concurrent.ArrayBlockingQueue;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.ThreadPoolExecutor;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicLong;
import org.mwjournal.study.JournalTicks.Held;
import static org.mwjournal.study.JournalTicks.ALWAYS;
import static org.mwjournal.study.JournalTicks.IN_POSITION;
import static org.mwjournal.study.JournalTicks.NEVER;

/** Records what the MotiveWave Journal app needs from a chart: one-minute bars, trade prints around
 * the user's own positions, and (optionally, through a read-only internal observer) the fills and their
 * linked stop and target orders, live and replay. Everything goes to a local file; nothing is sent
 * anywhere and no order is ever placed, changed or cancelled. */
@StudyHeader(namespace="org.mwjournal", id="MOTIVEWAVE_JOURNAL",
    name="MotiveWave Journal", label="MW Journal", menu="Journal",
    desc="Records prices and fills for the MotiveWave Journal app. Fill capture reads MotiveWave internals read-only and is not part of the supported SDK.",
    overlay=true, supportsBarUpdates=true, allowTickAggregate=false, allowTickFilter=false)
public class MotiveWaveJournal extends Study {
  private static final String FILE = "journalFile";
  private static final String SYMBOL = "journalSymbol";
  private static final String DAYS = "historyDays", END = "historyEnd", TICKS = "historyTickMinutes";
  private static final String BACKFILL = "backfillDays";
  private static final String PROXY = "priceProxy";
  private static final String ACCOUNTS = "nativeAccounts", REPLAY = "captureReplay", REAL_REPLAY = "realReplayTicks";
  // v2 intentionally resets the old position-gated preference: continuous chart capture is now the
  // product default so 5/15/30-minute runner windows exist without another switch.
  private static final String RECORD = "recordTicksV2";
  private static final String POST_EXIT = "postExitMinutes";
  private static final String LIVE_TICKS = "motivewave-ticks";
  // Resolved once: the environment cannot change under a running JVM, and this is read on every tick.
  private static final String DEFAULT_FILE = JournalPaths.defaultBridge();
  private static final long ACCOUNT_RETRY_NANOS = 5_000_000_000L;
  // Trade-tick backfill: a first look a little after the study loads, so the data connection is up; then
  // once a minute, or straight away while a pass left windows over. After a failure, much later.
  private static final long BACKFILL_AFTER_LOAD_NANOS = 30_000_000_000L, BACKFILL_EVERY_NANOS = 60_000_000_000L,
      BACKFILL_MORE_NANOS = 1_000_000_000L, BACKFILL_AFTER_FAILURE_NANOS = 600_000_000_000L;

  private record ReplaySource(String run, boolean real, String name) {}

  private volatile JournalEventWriter writer;
  private JournalEventWriter replayWriter;
  private JournalAccountCapture accounts;
  private Path accountFile;
  private long accountRetry = System.nanoTime();
  private final TickGate<Held> liveGate = new TickGate<>(), replayGate = new TickGate<>();
  private volatile ReplaySource replaySource;
  private Boolean replayMode;
  private String standaloneRun = UUID.randomUUID().toString();
  private long replayTime;
  private volatile long lastError;
  private final AtomicLong generation = new AtomicLong(), lastMinute = new AtomicLong();
  private ExecutorService history;
  private String lastRequest = "";
  private volatile boolean disposed;
  private final Runnable backfillBeat = this::backfillDue;
  private volatile Capture liveChart; // the chart as last seen in live mode; null in replay and before the first callback
  private volatile long nextBackfill = System.nanoTime() + BACKFILL_AFTER_LOAD_NANOS;
  private volatile boolean backfilling;
  private JournalBackfill backfill; // history thread only; one per study load, see JournalBackfill
  private volatile String lastWarnings = "";

  @Override public void initialize(Defaults defaults) {
    describe(createSD());
    createRD();
  }

  /** The settings dialog: one tab per family, short labels, the explanation in the tooltip. */
  static void describe(SettingsDescriptor sd) {
    var capture = sd.addTab("Capture");
    capture.addGroup("Bridge File").addRow(new StringDescriptor(FILE, "File", DEFAULT_FILE, 440)
        .setDescription("The file the MotiveWave Journal app reads. A full path. The default is where the app "
            + "looks, so change it only when the app has been pointed somewhere else as well."));
    var prices = capture.addGroup("Prices");
    prices.addRow(new DiscreteDescriptor(RECORD, "Record Ticks", ALWAYS, List.of(
        new NVP("Always While Study Runs", ALWAYS), new NVP("Trade + After Exit Only", IN_POSITION), new NVP("Never", NEVER)))
        .setDescription("Always While Study Runs (default): every trade print delivered to this chart, for complete post-exit runner windows; the file gets larger. "
            + "Trade + After Exit Only: a few seconds before entry, the whole position, and the After Exit window below. Never: no ticks. "
            + "One-minute bars are written in every mode. Trade + After Exit Only relies on Capture Fills: "
            + "when fills cannot be read, every trade is recorded."));
    prices.addRow(new IntegerDescriptor(POST_EXIT, "After Exit (Minutes)", 30, 0, 120, 1)
        .setDescription("Keep recording this instrument after exit for runner analysis. Uses market time in "
            + "live and replay, so replay speed does not shorten the window. 0 = only the short fill-detection tail. "
            + "No orders are kept open or changed; this records prices only."));
    prices.addRow(
        new StringDescriptor(SYMBOL, "Symbol", "", 110)
            .setDescription("The symbol written with each price. Blank uses the chart's active contract. Set it "
                + "when this chart stands in for the contract that is traded, and name that contract."),
        new BooleanDescriptor(PROXY, "Proxy Prices", false)
            .setDescription("Marks the prices as coming from another instrument than the one traded. "
                + "A continuous-contract chart is marked automatically."));
    capture.addGroup("Fills").addRow(new BooleanDescriptor(ACCOUNTS, "Capture Fills", true)
        .setDescription("Reads fills and their linked stop and target orders, live and replay. Read-only: it "
            + "never places, changes or cancels an order. It reads MotiveWave internals that are not part of "
            + "the supported SDK, so a MotiveWave update can stop it working."));

    var history = sd.addTab("History");
    var loaded = history.addGroup("Loaded With The Chart");
    loaded.addRow(
        new IntegerDescriptor(DAYS, "1m Bars (Days)", 3, 0, 14, 1)
            .setDescription("Days of one-minute bars written when the chart loads and kept up to date after. 0 = off."),
        new IntegerDescriptor(TICKS, "Ticks (Minutes)", 0, 0, 120, 1)
            .setDescription("Also writes the real trades of the last N minutes when the chart loads. 0 = off."));
    loaded.addRow(new StringDescriptor(END, "End (UTC)", "", 190)
        .setDescription("Loads history up to this moment instead of the end of the chart, for example "
            + "2026-01-05T21:00:00Z. Blank = the end of the loaded chart."));
    history.addGroup("Past Trades").addRow(
        new IntegerDescriptor(BACKFILL, "Backfill Trade Ticks (Days)", 30, 0, JournalBackfill.MAX_DAYS, 1)
            .setDescription("Loads the real trades of this chart's contract around every position closed in the last "
                + "N days, so trades that were not recorded live still get tick-based MAE and MFE. Each position "
                + "is loaded once, in the background, in live mode only. 0 = off. Needs Capture Fills, a chart of "
                + "the exact contract, and tick history from the data service for those days."));

    sd.addTab("Replay").addGroup("Replay Prices").addRow(
        new BooleanDescriptor(REPLAY, "Record Prices", true)
            .setDescription("Records replay prices, in a file of their own beside the live one."),
        new BooleanDescriptor(REAL_REPLAY, "Real Tick Data", false)
            .setDescription("Switch on only when the replay runs on real tick data. Off, replay prices are "
                + "labelled as simulated: generated from bars, so less exact."));
  }

  @Override protected void calculateValues(DataContext ctx) {
    if (disposed || ctx == null) return;
    observeMode(ctx.isReplayMode());
    try {
      JournalAccountCapture observer = getAccounts();
      if (ctx.isReplayMode()) {
        if (observer != null) observer.replayRun(capture(ctx).symbol(), ctx.getCurrentTime());
        return; // Never prefetch the future price path during replay.
      }
      var series = ctx.getDataSeries();
      long end = series != null && series.size() > 0 ? series.getEndTime(series.size() - 1) : ctx.getCurrentTime();
      String configuredEnd = getSettings().getString(END, "").trim();
      if (!configuredEnd.isEmpty()) end = Instant.parse(configuredEnd).toEpochMilli();
      end = Math.min(end, ctx.getCurrentTime());
      int days = Math.max(0, Math.min(14, getSettings().getInteger(DAYS, 3)));
      int ticks = Math.max(0, Math.min(120, getSettings().getInteger(TICKS, 0)));
      Capture capture = capture(ctx);
      liveChart = capture;
      String request = capture.toString() + ":" + end / 900000 + ":" + days + ":" + ticks + ":" + configuredEnd;
      synchronized (this) {
        if (request.equals(lastRequest)) return;
        lastRequest = request;
      }
      long start = end - Math.max(days * 86400000L, ticks * 60000L);
      submitHistory(capture, start, end, days > 0, ticks, true);
    } catch (IOException | RuntimeException exc) { report(exc); }
  }

  /** The live bridge file the app looks for when nothing is configured: <data home>/bridge/motivewave.ndjson,
   * where the data home is MWJ_HOME or this OS's per-user folder (JournalPaths has the rule). */
  static String defaultFile() {
    return DEFAULT_FILE;
  }

  @Override public void onTick(DataContext ctx, Tick tick) {
    if (disposed || ctx == null || tick == null) return;
    boolean isReplay = ctx.isReplayMode();
    observeMode(isReplay);
    try {
      JournalAccountCapture observer = getAccounts();
      Capture capture = capture(ctx);
      String record = getSettings().getString(RECORD, ALWAYS);
      var print = JournalPriceEvents.Print.of(tick);
      if (isReplay) {
        if (!getSettings().is(REPLAY, true)) return;
        // The run follows the replay clock on every tick, recorded or not: replay fills are labelled from it.
        String run = observer == null ? standaloneReplayRun(ctx.getCurrentTime())
            : observer.replayRun(capture.symbol(), ctx.getCurrentTime());
        if (print == null || NEVER.equals(record)) return;
        String source = replaySource(run, getSettings().is(REAL_REPLAY, false));
        Held observation = held(capture, source, print);
        JournalTicks.record(replayGate, getReplayWriter(capture.file()),
            observation, "replay", JournalTicks.armed(observer, record, true, observation));
        return;
      }
      JournalEventWriter target = getWriter();
      liveChart = capture;
      if (print == null) return;
      if (!NEVER.equals(record)) {
        Held observation = held(capture, LIVE_TICKS, print);
        JournalTicks.record(liveGate, target, observation,
            "live", JournalTicks.armed(observer, record, false, observation));
      }
      long minute = print.time() / 60000;
      long previous = lastMinute.getAndSet(minute);
      if (minute > previous && getSettings().getInteger(DAYS, 3) > 0)
        submitHistory(capture, (minute - 2) * 60000, minute * 60000, true, 0, false);
    } catch (IOException | RuntimeException exc) { report(exc); }
  }

  private Held held(Capture capture, String source, JournalPriceEvents.Print print) {
    return new Held(capture.symbol(), capture.proxy(), source, print, capture.instrument().getSymbol(),
        capture.instrument().getActiveSymbol(), getSettings().getInteger(POST_EXIT, 30));
  }

  private String replaySource(String run, boolean real) {
    ReplaySource cached = replaySource;
    if (cached == null || cached.real() != real || !cached.run().equals(run)) {
      replayGate.clear(); // A rewind or a relabel: the pre-roll belongs to the previous run.
      replaySource = cached = new ReplaySource(run, real, "motivewave-replay:" + run + (real ? ":ticks" : ":simulated"));
    }
    return cached.name();
  }

  private synchronized void observeMode(boolean replay) {
    if (replayMode != null && replayMode != replay) stopHistory();
    replayMode = replay;
    if (replay) liveChart = null; // No history is ever asked for in replay, and none for replay fills.
  }

  private synchronized String standaloneReplayRun(long time) {
    if (time < replayTime) standaloneRun = UUID.randomUUID().toString();
    replayTime = time;
    return standaloneRun;
  }

  /** Null when fill capture is off or could not start. Prices are then still recorded, ungated: a
   * MotiveWave update that breaks the internal adapter must not also stop the price recording. */
  private synchronized JournalAccountCapture getAccounts() throws IOException {
    Path file = Path.of(getSettings().getString(FILE, defaultFile())).normalize();
    if (!file.isAbsolute()) throw new IOException("Choose an absolute capture path.");
    boolean wanted = getSettings().is(ACCOUNTS, true);
    if (accounts != null && (!wanted || !file.equals(accountFile))) closeAccounts();
    if (wanted && accounts == null && System.nanoTime() - accountRetry >= 0) {
      try {
        accounts = JournalAccountCapture.acquire(file);
        accounts.watch(liveGate, replayGate);
        accounts.watch(backfillBeat);
        accountFile = file;
      } catch (IOException | ReflectiveOperationException | RuntimeException | LinkageError exc) {
        accountRetry = System.nanoTime() + ACCOUNT_RETRY_NANOS; // Not on every tick: a failed lookup is slow.
        report("fill capture could not start (" + exc.getClass().getSimpleName()
            + "). Prices are still recorded; fills are not.");
      }
    }
    if (accounts != null && accounts.failure != null) report("fill capture: " + accounts.failure);
    // Capture that works but skipped or assumed something is not an error: one line when it changes.
    String warnings = accounts == null ? null : accounts.warnings;
    if (warnings != null && !warnings.equals(lastWarnings)) {
      lastWarnings = warnings;
      info("MotiveWave Journal: fill capture is working, with warnings: " + warnings + ".");
    }
    return accounts;
  }

  private synchronized JournalEventWriter getReplayWriter(Path file) throws IOException {
    if (replayWriter == null) replayWriter = JournalEventWriter.acquire(JournalAccountCapture.replayFile(file));
    if (replayWriter.failure != null) throw replayWriter.failure;
    return replayWriter;
  }

  private record Capture(Path file, Instrument instrument, String symbol, boolean proxy) {}

  private Capture capture(DataContext ctx) {
    Instrument instrument = ctx.getInstrument();
    if (instrument == null) throw new IllegalArgumentException("No chart instrument.");
    String active = instrument.getActiveSymbol(), symbol = getSettings().getString(SYMBOL, "").trim();
    boolean continuous = active != null && !active.isBlank() && !active.equals(instrument.getSymbol());
    if (symbol.isEmpty()) symbol = active == null || active.isBlank() ? instrument.getSymbol() : active;
    Path file = Path.of(getSettings().getString(FILE, defaultFile()));
    if (!file.isAbsolute()) throw new IllegalArgumentException("Choose an absolute export path.");
    return new Capture(file, instrument, symbol, continuous || getSettings().is(PROXY, false));
  }

  /** The one background thread for everything that asks MotiveWave for history. Call with the lock held. */
  private ExecutorService history() {
    if (history == null) history = new ThreadPoolExecutor(1, 1, 0, TimeUnit.MILLISECONDS,
      new ArrayBlockingQueue<>(4), task -> {
      Thread thread = new Thread(task, "mwj-history"); thread.setDaemon(true); return thread;
    }, new ThreadPoolExecutor.DiscardOldestPolicy());
    return history;
  }

  /** Whether a backfill pass is due. Called by the account poller after each poll, twice a second and
   * with the market open or closed; never on the tick thread. It only decides and queues: the pass
   * itself runs on the history thread. */
  private void backfillDue() {
    Capture chart = liveChart;
    if (disposed || chart == null || backfilling || System.nanoTime() - nextBackfill < 0) return;
    nextBackfill = System.nanoTime() + BACKFILL_EVERY_NANOS;
    submitBackfill(chart, Math.max(0, Math.min(JournalBackfill.MAX_DAYS, getSettings().getInteger(BACKFILL, 30))));
  }

  private synchronized void submitBackfill(Capture chart, int days) {
    JournalAccountCapture observer = accounts;
    if (disposed || observer == null || !JournalBackfill.applies(chart.instrument().getSymbol(), chart.symbol(),
        chart.proxy(), replayMode, days)) return;
    long token = generation.get();
    history().submit(() -> {
      if (disposed || generation.get() != token || backfilling) return;
      backfilling = true;
      JournalEventWriter output = null;
      long began = System.nanoTime();
      String symbol = chart.instrument().getSymbol(), outcome = "";
      JournalBackfill.Result result = null;
      try {
        var fills = observer.fills();
        if (fills == null) return; // no snapshot yet; the next beat asks again
        var ledger = JournalBackfill.Ledger.shared(chart.file());
        if (backfill == null || backfill.ledger() != ledger) backfill = new JournalBackfill(ledger);
        // A separate reference keeps the writer alive while settings/destroy close the live one.
        JournalEventWriter target = output = JournalEventWriter.acquire(chart.file());
        result = backfill.pass(symbol, fills, System.currentTimeMillis(), days,
            // false is essential: true would manufacture ticks from OHLC and overstate precision.
            (start, end, each) -> chart.instrument().forEachTick(start, end, false, false,
                tick -> each.accept(JournalPriceEvents.Print.of(tick))),
            event -> target.offerBelow(event, JournalBackfill.HIGH_WATER),
            () -> disposed || generation.get() != token || Thread.currentThread().isInterrupted());
        if (result.remaining() > 0) nextBackfill = System.nanoTime() + BACKFILL_MORE_NANOS;
      } catch (java.util.concurrent.CancellationException exc) {
        outcome = null; // settings changed or the study was removed: the window is done again later
      } catch (IOException | RuntimeException exc) {
        nextBackfill = System.nanoTime() + BACKFILL_AFTER_FAILURE_NANOS;
        if (!disposed && generation.get() == token) report(exc);
        outcome = "stopped early by " + exc.getClass().getSimpleName();
      } finally {
        if (output != null) output.release();
        backfilling = false;
        // One line per pass that had something to do; a pass that found nothing new stays silent.
        String counts = result == null || result.attempted() == 0 ? "" : result.done() + " windows done, "
            + result.ticks() + " ticks written, " + result.empty() + " windows with no tick history"
            + (result.failed() > 0 ? ", " + result.failed() + " failed (" + result.failure() + ")" : "")
            + (result.cut() > 0 ? ", " + result.cut() + " cut at " + JournalBackfill.MAX_TICKS + " ticks" : "")
            + (result.remaining() > 0 ? ", " + result.remaining() + " still to do" : "");
        if (!disposed && outcome != null && !(counts + outcome).isEmpty())
          info("MotiveWave Journal: " + symbol + " trade tick backfill: " + counts
              + (counts.isEmpty() || outcome.isEmpty() ? "" : ", ") + outcome
              + " in " + (System.nanoTime() - began) / 1_000_000 + " ms.");
      }
    });
  }

  /** log: true for a load the chart asked for, which leaves one line in MotiveWave's log; false for the
   * two-minute top-up that follows every new minute, which would only bury that line. */
  private synchronized void submitHistory(Capture capture, long start, long end, boolean bars, int tickMinutes,
                                          boolean log) {
    if (disposed || (!bars && tickMinutes == 0)) return;
    long token = generation.get();
    history().submit(() -> {
      JournalEventWriter output = null;
      long began = System.nanoTime();
      int[] wrote = new int[3]; // bars, ticks, and events the full queue turned away
      String outcome = null; // stays null for a load that was superseded: nothing to say about it
      try {
        if (disposed || generation.get() != token) return;
        // A separate reference keeps the writer alive while settings/destroy close the live one.
        output = JournalEventWriter.acquire(capture.file());
        if (bars) {
          for (Object item : capture.instrument().getBars(start, end, BarSize.minute(1), false)) {
            if (disposed || generation.get() != token || Thread.currentThread().isInterrupted()) return;
            if (!(item instanceof Bar bar)) continue;
            if (bar.getStartTime() < start || bar.getEndTime() > end) continue;
            String event = JournalPriceEvents.bar(capture.symbol(), bar, capture.proxy());
            if (event != null)
              wrote[output.offer(event, "motivewave-bars:1m", Instant.ofEpochMilli(bar.getStartTime()).toString()) ? 0 : 2]++;
          }
        }
        if (tickMinutes > 0) {
          JournalEventWriter target = output;
          // false is essential: true would manufacture ticks from OHLC and overstate precision.
          capture.instrument().forEachTick(end - tickMinutes * 60000L, end, false, false, tick -> {
            if (disposed || generation.get() != token || Thread.currentThread().isInterrupted())
              throw new IllegalStateException("Capture settings changed.");
            if (tick.getTime() < end - tickMinutes * 60000L || tick.getTime() >= end) return;
            String event = JournalPriceEvents.tick(capture.symbol(), tick, capture.proxy());
            if (event != null) wrote[target.offer(event, LIVE_TICKS, tick.getTime()) ? 1 : 2]++;
          });
        }
        outcome = "";
      } catch (Exception exc) {
        if (!disposed && generation.get() == token) {
          synchronized (this) { lastRequest = ""; }
          report(exc);
          outcome = ", stopped early by " + exc.getClass().getSimpleName();
        }
      } finally {
        if (output != null) output.release();
        // The one sign in MotiveWave's log that the study ran, and with what result.
        if (log && outcome != null && !disposed)
          info("MotiveWave Journal: " + capture.symbol() + " history: " + wrote[0] + " bars and " + wrote[1]
              + " ticks written" + (wrote[2] > 0 ? ", " + wrote[2] + " dropped (queue full)" : "") + outcome
              + " in " + (System.nanoTime() - began) / 1_000_000 + " ms; fill capture " + fillCapture() + ".");
      }
    });
  }

  /** connected, starting (no poll has finished yet), failing, or off. */
  private String fillCapture() {
    if (!getSettings().is(ACCOUNTS, true)) return "off";
    JournalAccountCapture observer;
    synchronized (this) { observer = accounts; }
    if (observer == null) return "failing";
    String warnings = observer.warnings;
    return observer.status() + (warnings == null ? "" : " with warnings (" + warnings + ")");
  }

  private void report(Exception exc) {
    report(exc.getClass().getSimpleName() + ". Check the export path, history date, and data connection.");
  }

  private synchronized void report(String problem) {
    if (System.currentTimeMillis() - lastError > 30000) {
      error("MotiveWave Journal: " + problem);
      lastError = System.currentTimeMillis();
    }
  }

  private synchronized JournalEventWriter getWriter() throws IOException {
    if (writer == null) {
      Path file = Path.of(getSettings().getString(FILE, defaultFile()));
      if (!file.isAbsolute()) throw new IOException("Choose an absolute export path in study settings.");
      writer = JournalEventWriter.acquire(file);
    }
    if (writer.failure != null) throw writer.failure;
    return writer;
  }

  @Override public synchronized void onSettingsUpdated(DataContext ctx) {
    stopHistory();
    closeWriter();
    super.onSettingsUpdated(ctx);
  }

  @Override public synchronized void destroy() {
    disposed = true;
    stopHistory();
    closeWriter();
    closeAccounts();
    super.destroy();
  }

  private void closeWriter() {
    if (writer != null) writer.release();
    writer = null;
    if (replayWriter != null) replayWriter.release();
    replayWriter = null;
    // The pre-roll was gathered under the old settings (symbol, file, mode); it is not carried over.
    liveGate.clear();
    replayGate.clear();
  }

  private void closeAccounts() {
    if (accounts != null) {
      accounts.unwatch(liveGate, replayGate);
      accounts.unwatch(backfillBeat);
      accounts.release(accountFile);
    }
    accounts = null;
    accountFile = null;
    accountRetry = System.nanoTime();
  }

  private void stopHistory() {
    generation.incrementAndGet();
    lastRequest = "";
    lastMinute.set(0);
    liveChart = null; // seen under the old settings or mode; the next callback brings the current one
    if (history != null) history.shutdownNow();
    history = null;
  }
}
