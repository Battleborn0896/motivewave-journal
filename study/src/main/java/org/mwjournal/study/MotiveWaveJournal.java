package org.mwjournal.study;

import com.motivewave.platform.sdk.common.Bar;
import com.motivewave.platform.sdk.common.BarSize;
import com.motivewave.platform.sdk.common.DataContext;
import com.motivewave.platform.sdk.common.Defaults;
import com.motivewave.platform.sdk.common.Instrument;
import com.motivewave.platform.sdk.common.Tick;
import com.motivewave.platform.sdk.common.desc.LabelDescriptor;
import com.motivewave.platform.sdk.common.desc.SettingsDescriptor;
import com.motivewave.platform.sdk.study.Study;
import com.motivewave.platform.sdk.study.StudyHeader;

import java.io.IOException;
import java.nio.file.Path;
import java.time.Instant;
import java.util.UUID;
import java.util.concurrent.ArrayBlockingQueue;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.ThreadPoolExecutor;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicLong;
import java.util.function.BooleanSupplier;
import org.mwjournal.study.JournalTicks.Held;
import static org.mwjournal.study.JournalSettings.*;

/** Records what the MotiveWave Journal app needs from a chart: one-minute bars, the trade prints the chart
 * receives while a trade is open, real ticks from MotiveWave's history for the holding time of closed trades
 * and the minutes after their exit, and (through a read-only internal observer) the fills and their linked
 * stop orders, live and replay. Everything goes to a local
 * file; nothing is sent anywhere and no order is ever placed, changed or cancelled.
 *
 * There is nothing to set: every value is fixed (JournalSettings), and the study never reads its settings. A
 * chart saved by an earlier study still holds that study's values (Record Ticks on Always, another bridge
 * file, an End date); none of them acts.
 *
 * requiresBarUpdates: MotiveWave calls onTick only for a study that receives bar updates. With
 * supportsBarUpdates alone that is a switch in the study's dialog, off unless the user turns it on, and
 * study 0.3.2 and earlier, which declared only that, never recorded a live or replay print. Requiring
 * bar updates makes every print reach onTick with the study's defaults. */
@StudyHeader(namespace="org.mwjournal", id="MOTIVEWAVE_JOURNAL",
    name="MotiveWave Journal", label="MW Journal", menu="Journal",
    desc="Records prices and fills for the MotiveWave Journal app. Fill recording reads MotiveWave internals read-only and is not part of the supported SDK.",
    overlay=true, supportsBarUpdates=true, requiresBarUpdates=true, allowTickAggregate=false, allowTickFilter=false)
public class MotiveWaveJournal extends Study {
  private static final String LIVE_TICKS = "motivewave-ticks";
  // Resolved once: the environment cannot change under a running JVM, and this is read on every tick.
  private static final String DEFAULT_FILE = JournalPaths.defaultBridge();
  private static final Path BRIDGE = Path.of(DEFAULT_FILE), REPLAY_BRIDGE = JournalAccountCapture.replayFile(BRIDGE);
  private static final long DAY_MILLIS = 86_400_000L;
  private static final long ACCOUNT_RETRY_NANOS = 5_000_000_000L;
  // Trade-tick backfill: a first look a little after the study loads, so the data connection is up; then
  // once a minute, or straight away while a pass left windows over. After a failure, much later.
  private static final long BACKFILL_AFTER_LOAD_NANOS = 30_000_000_000L, BACKFILL_EVERY_NANOS = 60_000_000_000L,
      BACKFILL_MORE_NANOS = 1_000_000_000L, BACKFILL_AFTER_FAILURE_NANOS = 600_000_000_000L;
  // A replay trade is over as soon as it is flat, and the trader is looking at it: ask more often.
  private static final long REPLAY_BACKFILL_EVERY_NANOS = 5_000_000_000L;

  private record ReplaySource(String run, String name) {}

  private volatile JournalEventWriter writer;
  private JournalEventWriter replayWriter;
  private volatile JournalAccountCapture accounts; // read by the poller's thread in afterPoll
  private long accountRetry = System.nanoTime();
  private final TickGate<Held> liveGate = new TickGate<>(), replayGate = new TickGate<>();
  private volatile ReplaySource replaySource;
  private Boolean replayMode;
  private String standaloneRun = UUID.randomUUID().toString();
  private long replayTime;
  private volatile long lastError;
  private final AtomicLong generation = new AtomicLong(), lastMinute = new AtomicLong(), lastReplayMinute = new AtomicLong();
  private ExecutorService history;
  private String lastRequest = "";
  private volatile boolean disposed;
  private final Runnable afterPollBeat = this::afterPoll;
  private volatile Capture liveChart; // the chart as last seen in live mode; null in replay and before the first callback
  private volatile Capture replayChart; // the same in replay mode; null live and before the first callback
  private volatile long replayNow; // the replay's current time as this chart last saw it; 0 before any
  private volatile long nextBackfill = System.nanoTime() + BACKFILL_AFTER_LOAD_NANOS;
  private volatile boolean backfilling;
  // History thread only; one per study load and bridge file, see JournalBackfill.
  private JournalBackfill backfill, replayBackfill;
  private volatile String lastWarnings = "";

  @Override public void initialize(Defaults defaults) {
    describe(createSD());
    createRD();
  }

  /** The settings dialog: one line saying there is nothing to set (JournalSettings has it). */
  static void describe(SettingsDescriptor sd) {
    sd.addTab(JournalSettings.TAB).addGroup(JournalSettings.GROUP).addRow(new LabelDescriptor(JournalSettings.NOTE));
  }

  @Override protected void calculateValues(DataContext ctx) {
    // A newer copy of the study took over (JournalHandoff): this one records nothing more.
    if (disposed || JournalHandoff.retired() || ctx == null) return;
    observeMode(ctx.isReplayMode());
    try {
      JournalAccountCapture observer = getAccounts();
      if (observer != null) observer.chart(this, ctx.getInstrument(), ctx.isReplayMode());
      Capture capture = capture(ctx);
      if (ctx.isReplayMode()) {
        if (observer != null) observer.replayRun(capture.symbol(), ctx.getCurrentTime());
        replayNow = ctx.getCurrentTime();
        replayChart = capture;
        // The days the replay plays, as 1-minute bars from MotiveWave's history: the days before it and the day
        // after its current time, whole, so every replay trade of those days has its chart, whichever run it was
        // in. Loaded once a replayed day. An open replay trade's chart still ends at the replay's time (the app
        // draws it to its replay clock), so nothing ahead shows while trading.
        if (replayNow <= 0) return;
        String request = "replay:" + capture + ":" + replayNow / DAY_MILLIS;
        synchronized (this) {
          if (request.equals(lastRequest)) return;
          lastRequest = request;
        }
        submitHistory(capture, replayNow - BAR_DAYS * DAY_MILLIS, replayNow + DAY_MILLIS, capture.historyProxy(),
            true, true);
        return;
      }
      // The days before the end of the chart, as 1-minute bars, asked again each quarter of an hour it moves on.
      var series = ctx.getDataSeries();
      long end = series != null && series.size() > 0 ? series.getEndTime(series.size() - 1) : ctx.getCurrentTime();
      end = Math.min(end, ctx.getCurrentTime());
      liveChart = capture;
      String request = capture + ":" + end / 900000;
      synchronized (this) {
        if (request.equals(lastRequest)) return;
        lastRequest = request;
      }
      submitHistory(capture, end - BAR_DAYS * DAY_MILLIS, end, capture.historyProxy(), true, false);
    } catch (RuntimeException exc) { report(exc); }
  }

  /** The bridge file the app reads: <data home>/bridge/motivewave.ndjson, where the data home is MWJ_HOME or
   * this OS's per-user folder (JournalPaths has the rule). The replay bridge is beside it. */
  static String defaultFile() {
    return DEFAULT_FILE;
  }

  @Override public void onTick(DataContext ctx, Tick tick) {
    if (disposed || JournalHandoff.retired() || ctx == null || tick == null) return;
    boolean isReplay = ctx.isReplayMode();
    observeMode(isReplay);
    try {
      JournalAccountCapture observer = getAccounts();
      Capture capture = capture(ctx);
      if (observer != null) {
        observer.chart(this, capture.instrument(), isReplay);
        observer.ticked(this, isReplay);
      }
      var print = JournalPriceEvents.Print.of(tick);
      if (isReplay) {
        // The run follows the replay clock on every tick, recorded or not: replay fills are labelled from it.
        String run = observer == null ? standaloneReplayRun(ctx.getCurrentTime())
            : observer.replayRun(capture.symbol(), ctx.getCurrentTime());
        replayNow = ctx.getCurrentTime();
        replayChart = capture;
        if (print == null) return;
        // The minutes the replay just closed, as the replayed days' bars (calculateValues has the rule): where
        // MotiveWave's history of a replay goes only as far as its time, they arrive as it plays.
        long minute = print.time() / 60000;
        if (lastReplayMinute.getAndSet(minute) != minute)
          submitHistory(capture, (minute - 2) * 60000, minute * 60000, false, false, true);
        Held observation = held(capture, replaySource(run), print);
        JournalTicks.record(replayGate, getReplayWriter(), observation, "replay",
            JournalTicks.armed(observer, true, observation));
        return;
      }
      JournalEventWriter target = getWriter();
      liveChart = capture;
      if (print == null) return;
      Held observation = held(capture, LIVE_TICKS, print);
      JournalTicks.record(liveGate, target, observation, "live", JournalTicks.armed(observer, false, observation));
      long minute = print.time() / 60000;
      long previous = lastMinute.getAndSet(minute);
      // The minutes these prints just closed: recorded as they traded, so never marked proxy.
      if (minute > previous) submitHistory(capture, (minute - 2) * 60000, minute * 60000, false, false, false);
    } catch (IOException | RuntimeException exc) { report(exc); }
  }

  private static Held held(Capture capture, String source, JournalPriceEvents.Print print) {
    return new Held(capture.symbol(), source, print, capture.instrument().getSymbol(),
        capture.instrument().getActiveSymbol());
  }

  private String replaySource(String run) {
    ReplaySource cached = replaySource;
    if (cached == null || !cached.run().equals(run)) {
      replayGate.clear(); // A rewind: the pre-roll belongs to the previous run.
      replaySource = cached = new ReplaySource(run, JournalPriceEvents.replayProvider(run));
    }
    return cached.name();
  }

  private synchronized void observeMode(boolean replay) {
    if (replayMode != null && replayMode != replay) stopHistory();
    replayMode = replay;
    // Each mode backfills only its own fills, from a chart seen in that mode.
    if (replay) liveChart = null;
    else replayChart = null;
  }

  private synchronized String standaloneReplayRun(long time) {
    if (time < replayTime) standaloneRun = UUID.randomUUID().toString();
    replayTime = time;
    return standaloneRun;
  }

  /** Null while fill recording could not start. Prices are then still recorded, ungated: a MotiveWave update
   * that breaks the internal adapter must not also stop the price recording. */
  private synchronized JournalAccountCapture getAccounts() {
    if (accounts == null && System.nanoTime() - accountRetry >= 0) {
      try {
        accounts = JournalAccountCapture.acquire(BRIDGE);
        accounts.watch(liveGate, replayGate);
        accounts.watch(afterPollBeat);
      } catch (IOException | ReflectiveOperationException | RuntimeException | LinkageError exc) {
        accountRetry = System.nanoTime() + ACCOUNT_RETRY_NANOS; // Not on every tick: a failed lookup is slow.
        report(JournalEventWriter.anotherCopy(exc) ? JournalEventWriter.ANOTHER_COPY
            : "fill recording could not start (" + exc.getClass().getSimpleName() + "). Prices are still recorded; fills are not.");
      }
    }
    if (accounts != null && accounts.failure != null)
      report(accounts.failure.equals(JournalEventWriter.ANOTHER_COPY) ? accounts.failure : "fill recording: " + accounts.failure);
    // Recording that works but skipped or assumed something is not an error: one line when it changes.
    String warnings = accounts == null ? null : accounts.warnings;
    if (warnings != null && !warnings.equals(lastWarnings)) {
      lastWarnings = warnings;
      info("MotiveWave Journal: fill recording is working, with warnings: " + warnings + ".");
    }
    return accounts;
  }

  private synchronized JournalEventWriter getReplayWriter() throws IOException {
    if (replayWriter == null) replayWriter = JournalEventWriter.acquire(REPLAY_BRIDGE);
    if (replayWriter.failure != null) throw replayWriter.failure;
    return replayWriter;
  }

  /** The chart as the study records it: its instrument, the contract its prices are written under
   * (JournalPriceEvents.contract), and whether the history it loads when it opens is marked proxy
   * (JournalPriceEvents.proxy). */
  private record Capture(Instrument instrument, String symbol, boolean historyProxy) {}

  private static Capture capture(DataContext ctx) {
    Instrument instrument = ctx.getInstrument();
    if (instrument == null) throw new IllegalArgumentException("No chart instrument.");
    return new Capture(instrument, JournalPriceEvents.contract(instrument), JournalPriceEvents.proxy(instrument));
  }

  /** The one background thread for everything that asks MotiveWave for history. Call with the lock held. */
  private ExecutorService history() {
    if (history == null) history = new ThreadPoolExecutor(1, 1, 0, TimeUnit.MILLISECONDS,
      new ArrayBlockingQueue<>(4), task -> {
      Thread thread = new Thread(task, "mwj-history"); thread.setDaemon(true); return thread;
    }, new ThreadPoolExecutor.DiscardOldestPolicy());
    return history;
  }

  /** After each poll of the account capture, on its thread: the lines it has for MotiveWave's log (the stop
   * diagnostic and the probes; whichever chart asks first writes them, each once), then the backfill. */
  private void afterPoll() {
    JournalAccountCapture observer = accounts;
    if (observer != null && !disposed) observer.drainLog(this::info);
    backfillDue();
  }

  /** Whether a backfill pass is due. Called by the account poller after each poll, twice a second and
   * with the market open or closed; never on the tick thread. It only decides and queues: the pass
   * itself runs on the history thread. A chart in replay backfills replay trades, one in live mode live
   * ones. */
  private void backfillDue() {
    Capture live = liveChart, replay = replayChart;
    Capture chart = live != null ? live : replay;
    if (disposed || chart == null || backfilling || System.nanoTime() - nextBackfill < 0) return;
    boolean isReplay = live == null;
    nextBackfill = System.nanoTime() + (isReplay ? REPLAY_BACKFILL_EVERY_NANOS : BACKFILL_EVERY_NANOS);
    submitBackfill(chart, isReplay);
  }

  /** The closed trades of the chart's contract, and the minutes after their exits: their real ticks, read from
   * the fills' own instrument (JournalBackfill has the rule), on a continuous chart as on the contract's. */
  private synchronized void submitBackfill(Capture chart, boolean replay) {
    JournalAccountCapture observer = accounts;
    String contract = chart.symbol();
    if (disposed || observer == null || !JournalBackfill.applies(contract, replayMode, replay)) return;
    long token = generation.get();
    history().submit(() -> {
      if (disposed || generation.get() != token || backfilling) return;
      backfilling = true;
      JournalEventWriter output = null;
      long began = System.nanoTime();
      String outcome = "";
      JournalBackfill.Result result = null;
      try {
        // Read once: the pass never asks for anything later than the replay's time when it began.
        long clock = replayNow;
        Path file = replay ? REPLAY_BRIDGE : BRIDGE;
        var ledger = JournalBackfill.Ledger.shared(file, replay);
        JournalBackfill load = replay ? replayBackfill : backfill;
        if (load == null || load.ledger() != ledger) {
          load = new JournalBackfill(ledger);
          if (replay) replayBackfill = load;
          else backfill = load;
        }
        BooleanSupplier cancelled = () -> disposed || generation.get() != token || Thread.currentThread().isInterrupted();
        // The fills first, then their instruments: JournalAccountCapture.instruments says why.
        if (replay) {
          var fills = observer.replayFills();
          if (fills == null || clock <= 0) return; // no snapshot or no replay time yet; the next beat asks again
          // A separate reference keeps the writer alive while destroy closes the study's own.
          JournalEventWriter target = output = JournalEventWriter.acquire(file);
          result = load.replayPass(contract, fills, clock, System.currentTimeMillis(), BACKFILL_DAYS,
              AFTER_EXIT_MINUTES, JournalBackfill.history(observer.instruments(), true, contract),
              event -> target.offerBelow(event, JournalBackfill.HIGH_WATER), cancelled);
        } else {
          var fills = observer.fills();
          if (fills == null) return; // no snapshot yet; the next beat asks again
          JournalEventWriter target = output = JournalEventWriter.acquire(file);
          result = load.pass(contract, fills, System.currentTimeMillis(), BACKFILL_DAYS, AFTER_EXIT_MINUTES,
              JournalBackfill.history(observer.instruments(), false, contract),
              event -> target.offerBelow(event, JournalBackfill.HIGH_WATER), cancelled);
        }
        if (result.remaining() > 0) nextBackfill = System.nanoTime() + BACKFILL_MORE_NANOS;
      } catch (java.util.concurrent.CancellationException exc) {
        outcome = null; // the mode changed or the study was removed: the window is done again later
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
          info("MotiveWave Journal: " + contract + (replay ? " replay" : "") + " trade tick backfill: " + counts
              + (counts.isEmpty() || outcome.isEmpty() ? "" : ", ") + outcome
              + " in " + (System.nanoTime() - began) / 1_000_000 + " ms.");
      }
    });
  }

  /** One-minute bars from MotiveWave's history, start to end. proxy: how they are marked (Capture says which
   * applies). log: true for a load the chart asked for, which leaves one line in MotiveWave's log; false for
   * the two-minute top-up that follows every new minute, which would only bury that line. */
  private synchronized void submitHistory(Capture capture, long start, long end, boolean proxy, boolean log,
                                          boolean replay) {
    if (disposed) return;
    long token = generation.get();
    history().submit(() -> {
      JournalEventWriter output = null;
      long began = System.nanoTime();
      int[] wrote = new int[2]; // bars, and bars the full queue turned away
      String outcome = null; // stays null for a load that was superseded: nothing to say about it
      try {
        if (disposed || generation.get() != token) return;
        // A separate reference keeps the writer alive while destroy closes the study's own.
        // Replay bars go to the replay bridge (calculateValues has the rule); live bars to the live one.
        output = JournalEventWriter.acquire(replay ? REPLAY_BRIDGE : BRIDGE);
        String provider = replay ? JournalPriceEvents.REPLAY_BARS : JournalPriceEvents.LIVE_BARS;
        for (Object item : capture.instrument().getBars(start, end, BarSize.minute(1), false)) {
          if (disposed || generation.get() != token || Thread.currentThread().isInterrupted()) return;
          if (!(item instanceof Bar bar)) continue;
          if (bar.getStartTime() < start || bar.getEndTime() > end) continue;
          String event = JournalPriceEvents.bar(capture.symbol(), bar, proxy, provider);
          if (event != null)
            wrote[output.offer(event, provider, Instant.ofEpochMilli(bar.getStartTime()).toString()) ? 0 : 1]++;
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
          info("MotiveWave Journal: " + capture.symbol() + (replay ? " replay" : "") + " history: " + wrote[0]
              + " bars written" + (wrote[1] > 0 ? ", " + wrote[1] + " dropped (queue full)" : "") + outcome
              + " in " + (System.nanoTime() - began) / 1_000_000 + " ms; fill recording " + fillRecording()
              + "; chart instrument " + JournalPriceEvents.names(capture.instrument()) + ".");
      }
    });
  }

  /** connected, starting (no poll has finished yet), or failing. */
  private String fillRecording() {
    JournalAccountCapture observer;
    synchronized (this) { observer = accounts; }
    if (observer == null) return "failing";
    String warnings = observer.warnings;
    return observer.status() + (warnings == null ? "" : " with warnings (" + warnings + ")");
  }

  private void report(Exception exc) {
    report(JournalEventWriter.anotherCopy(exc) ? JournalEventWriter.ANOTHER_COPY
        : exc.getClass().getSimpleName() + ". Check the data connection and the journal's data folder.");
  }

  private synchronized void report(String problem) {
    if (JournalHandoff.retired()) return; // the newer copy speaks for the journal now
    if (System.currentTimeMillis() - lastError > 30000) {
      error("MotiveWave Journal: " + problem);
      lastError = System.currentTimeMillis();
    }
  }

  private synchronized JournalEventWriter getWriter() throws IOException {
    if (writer == null) writer = JournalEventWriter.acquire(BRIDGE);
    if (writer.failure != null) throw writer.failure;
    return writer;
  }

  @Override public synchronized void onSettingsUpdated(DataContext ctx) {
    if (accounts != null) accounts.forget(this);
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
    // The pre-roll was gathered for the chart as it was; it is not carried over.
    liveGate.clear();
    replayGate.clear();
  }

  private void closeAccounts() {
    if (accounts != null) {
      accounts.forget(this);
      accounts.unwatch(liveGate, replayGate);
      accounts.unwatch(afterPollBeat);
      accounts.release(BRIDGE);
    }
    accounts = null;
    accountRetry = System.nanoTime();
  }

  private void stopHistory() {
    generation.incrementAndGet();
    lastRequest = "";
    lastMinute.set(0);
    lastReplayMinute.set(0);
    liveChart = null; // seen in the old mode, or before the chart changed; the next callback brings the current one
    replayChart = null;
    if (history != null) history.shutdownNow();
    history = null;
  }
}
