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
import com.motivewave.platform.sdk.common.desc.LabelDescriptor;
import com.motivewave.platform.sdk.common.desc.SettingDescriptor;
import com.motivewave.platform.sdk.common.desc.SettingsDescriptor;
import com.motivewave.platform.sdk.common.desc.StringDescriptor;
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
import static org.mwjournal.study.JournalTicks.NEVER;

/** Records what the MotiveWave Journal app needs from a chart: one-minute bars, the trade prints the chart
 * receives while a trade is open, real ticks from MotiveWave's history for the holding time of closed trades
 * and the minutes after their exit, and (through a read-only internal observer) the fills and their linked
 * stop orders, live and replay. Everything goes to a local
 * file; nothing is sent anywhere and no order is ever placed, changed or cancelled.
 *
 * requiresBarUpdates: MotiveWave calls onTick only for a study that receives bar updates. With
 * supportsBarUpdates alone that is a switch in the study's dialog, off unless the user turns it on, and
 * study 0.3.2 and earlier, which declared only that, never recorded a live or replay print. Requiring
 * bar updates makes every print reach onTick with the study's defaults. */
@StudyHeader(namespace="org.mwjournal", id="MOTIVEWAVE_JOURNAL",
    name="MotiveWave Journal", label="MW Journal", menu="Journal",
    desc="Records prices and fills for the MotiveWave Journal app. Fill capture reads MotiveWave internals read-only and is not part of the supported SDK.",
    overlay=true, supportsBarUpdates=true, requiresBarUpdates=true, allowTickAggregate=false, allowTickFilter=false)
public class MotiveWaveJournal extends Study {
  private static final String LIVE_TICKS = "motivewave-ticks";
  // Resolved once: the environment cannot change under a running JVM, and this is read on every tick.
  private static final String DEFAULT_FILE = JournalPaths.defaultBridge();
  private static final long ACCOUNT_RETRY_NANOS = 5_000_000_000L;
  // Trade-tick backfill: a first look a little after the study loads, so the data connection is up; then
  // once a minute, or straight away while a pass left windows over. After a failure, much later.
  private static final long BACKFILL_AFTER_LOAD_NANOS = 30_000_000_000L, BACKFILL_EVERY_NANOS = 60_000_000_000L,
      BACKFILL_MORE_NANOS = 1_000_000_000L, BACKFILL_AFTER_FAILURE_NANOS = 600_000_000_000L;
  // A replay trade is over as soon as it is flat, and the trader is looking at it: ask more often.
  private static final long REPLAY_BACKFILL_EVERY_NANOS = 5_000_000_000L;

  private record ReplaySource(String run, boolean real, String name) {}

  private volatile JournalEventWriter writer;
  private JournalEventWriter replayWriter;
  private volatile JournalAccountCapture accounts; // read by the poller's thread in afterPoll
  private Path accountFile;
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

  /** The settings dialog: a first tab that says there is nothing to set, and every setting under Advanced,
   * with short labels and the explanation in the tooltip. The layout itself is JournalSettings'. */
  static void describe(SettingsDescriptor sd) {
    for (var tab : JournalSettings.layout(DEFAULT_FILE)) {
      var added = sd.addTab(tab.name());
      for (var group : tab.groups()) {
        var rows = added.addGroup(group.name());
        for (var row : group.rows())
          rows.addRow(row.stream().map(MotiveWaveJournal::descriptor).toArray(SettingDescriptor[]::new));
      }
    }
  }

  private static SettingDescriptor descriptor(JournalSettings.Setting setting) {
    return switch (setting.kind()) {
      case NOTE -> new LabelDescriptor(setting.label());
      case TEXT -> new StringDescriptor(setting.key(), setting.label(), (String) setting.fallback(), setting.width())
          .setDescription(setting.tip());
      case WHOLE -> new IntegerDescriptor(setting.key(), setting.label(), (Integer) setting.fallback(),
          setting.min(), setting.max(), 1).setDescription(setting.tip());
      case SWITCH -> new BooleanDescriptor(setting.key(), setting.label(), (Boolean) setting.fallback())
          .setDescription(setting.tip());
      case CHOICE -> new DiscreteDescriptor(setting.key(), setting.label(), (String) setting.fallback(),
          setting.choices().stream().map(choice -> new NVP(choice.label(), choice.value())).toList())
          .setDescription(setting.tip());
    };
  }

  @Override protected void calculateValues(DataContext ctx) {
    // A newer copy of the study took over (JournalHandoff): this one records nothing more.
    if (disposed || JournalHandoff.retired() || ctx == null) return;
    observeMode(ctx.isReplayMode());
    try {
      JournalAccountCapture observer = getAccounts();
      if (observer != null) observer.chart(this, ctx.getInstrument(), ctx.isReplayMode());
      if (ctx.isReplayMode()) {
        Capture capture = capture(ctx);
        if (observer != null) observer.replayRun(capture.symbol(), ctx.getCurrentTime());
        replayNow = ctx.getCurrentTime();
        replayChart = capture;
        // The days the replay plays, as 1-minute bars from MotiveWave's history: the days before it and the day
        // after its current time, whole, so every replay trade of those days has its chart, whichever run it was
        // in. Loaded once a replayed day. An open replay trade's chart still ends at the replay's time (the app
        // draws it to its replay clock), so nothing ahead shows while trading.
        int days = Math.max(0, Math.min(DAYS_MAX, getSettings().getInteger(DAYS, DAYS_DEFAULT)));
        if (days == 0 || replayNow <= 0 || !getSettings().is(REPLAY, REPLAY_DEFAULT)) return;
        String request = "replay:" + capture + ":" + replayNow / 86400000L + ":" + days;
        synchronized (this) {
          if (request.equals(lastRequest)) return;
          lastRequest = request;
        }
        submitHistory(capture, replayNow - days * 86400000L, replayNow + 86400000L, true, 0, capture.historyProxy(),
            true, true);
        return;
      }
      var series = ctx.getDataSeries();
      long end = series != null && series.size() > 0 ? series.getEndTime(series.size() - 1) : ctx.getCurrentTime();
      String configuredEnd = getSettings().getString(END, "").trim();
      if (!configuredEnd.isEmpty()) end = Instant.parse(configuredEnd).toEpochMilli();
      end = Math.min(end, ctx.getCurrentTime());
      int days = Math.max(0, Math.min(DAYS_MAX, getSettings().getInteger(DAYS, DAYS_DEFAULT)));
      int ticks = Math.max(0, Math.min(TICKS_MAX, getSettings().getInteger(TICKS, TICKS_DEFAULT)));
      Capture capture = capture(ctx);
      liveChart = capture;
      String request = capture.toString() + ":" + end / 900000 + ":" + days + ":" + ticks + ":" + configuredEnd;
      synchronized (this) {
        if (request.equals(lastRequest)) return;
        lastRequest = request;
      }
      long start = end - Math.max(days * 86400000L, ticks * 60000L);
      submitHistory(capture, start, end, days > 0, ticks, capture.historyProxy(), true, false);
    } catch (IOException | RuntimeException exc) { report(exc); }
  }

  /** The live bridge file the app looks for when nothing is configured: <data home>/bridge/motivewave.ndjson,
   * where the data home is MWJ_HOME or this OS's per-user folder (JournalPaths has the rule). */
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
      String record = getSettings().getString(RECORD, RECORD_DEFAULT);
      var print = JournalPriceEvents.Print.of(tick);
      if (isReplay) {
        // The run follows the replay clock on every tick, recorded or not: replay fills are labelled from it.
        String run = observer == null ? standaloneReplayRun(ctx.getCurrentTime())
            : observer.replayRun(capture.symbol(), ctx.getCurrentTime());
        replayNow = ctx.getCurrentTime();
        replayChart = capture;
        // The minutes the replay just closed, as the replayed days' bars (calculateValues has the rule): where
        // MotiveWave's history of a replay goes only as far as its time, they arrive as it plays.
        if (print != null && getSettings().getInteger(DAYS, DAYS_DEFAULT) > 0 && getSettings().is(REPLAY, REPLAY_DEFAULT)) {
          long minute = print.time() / 60000;
          if (lastReplayMinute.getAndSet(minute) != minute)
            submitHistory(capture, (minute - 2) * 60000, minute * 60000, true, 0, capture.proxy(), false, true);
        }
        if (print == null || !getSettings().is(REPLAY, REPLAY_DEFAULT) || NEVER.equals(record)) return;
        String source = replaySource(run, getSettings().is(REAL_REPLAY, REAL_REPLAY_DEFAULT));
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
      // The minutes these prints just closed: recorded as they traded, so marked like the prints themselves.
      if (minute > previous && getSettings().getInteger(DAYS, DAYS_DEFAULT) > 0)
        submitHistory(capture, (minute - 2) * 60000, minute * 60000, true, 0, capture.proxy(), false, false);
    } catch (IOException | RuntimeException exc) { report(exc); }
  }

  private static Held held(Capture capture, String source, JournalPriceEvents.Print print) {
    return new Held(capture.symbol(), capture.proxy(), source, print, capture.instrument().getSymbol(),
        capture.instrument().getActiveSymbol());
  }

  private String replaySource(String run, boolean real) {
    ReplaySource cached = replaySource;
    if (cached == null || cached.real() != real || !cached.run().equals(run)) {
      replayGate.clear(); // A rewind or a relabel: the pre-roll belongs to the previous run.
      replaySource = cached = new ReplaySource(run, real, JournalPriceEvents.replayProvider(run, real));
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

  /** Null when fill capture is off or could not start. Prices are then still recorded, ungated: a
   * MotiveWave update that breaks the internal adapter must not also stop the price recording. */
  private synchronized JournalAccountCapture getAccounts() throws IOException {
    Path file = Path.of(getSettings().getString(FILE, defaultFile())).normalize();
    if (!file.isAbsolute()) throw new IOException("Choose an absolute capture path.");
    boolean wanted = getSettings().is(ACCOUNTS, ACCOUNTS_DEFAULT);
    if (accounts != null && (!wanted || !file.equals(accountFile))) closeAccounts();
    if (wanted && accounts == null && System.nanoTime() - accountRetry >= 0) {
      try {
        accounts = JournalAccountCapture.acquire(file);
        accounts.watch(liveGate, replayGate);
        accounts.watch(afterPollBeat);
        accountFile = file;
      } catch (IOException | ReflectiveOperationException | RuntimeException | LinkageError exc) {
        accountRetry = System.nanoTime() + ACCOUNT_RETRY_NANOS; // Not on every tick: a failed lookup is slow.
        report(JournalEventWriter.anotherCopy(exc) ? JournalEventWriter.ANOTHER_COPY
            : "fill capture could not start (" + exc.getClass().getSimpleName() + "). Prices are still recorded; fills are not.");
      }
    }
    if (accounts != null && accounts.failure != null)
      report(accounts.failure.equals(JournalEventWriter.ANOTHER_COPY) ? accounts.failure : "fill capture: " + accounts.failure);
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

  /** proxy: how the prints and minute bars recorded as they trade are marked. historyProxy: how the history
   * loaded when the chart loads is marked. JournalPriceEvents.proxy has the rule. */
  private record Capture(Path file, Instrument instrument, String symbol, boolean proxy, boolean historyProxy) {}

  private Capture capture(DataContext ctx) {
    Instrument instrument = ctx.getInstrument();
    if (instrument == null) throw new IllegalArgumentException("No chart instrument.");
    String active = instrument.getActiveSymbol(), symbol = getSettings().getString(SYMBOL, "").trim();
    if (symbol.isEmpty()) symbol = active == null || active.isBlank() ? instrument.getSymbol() : active;
    Path file = Path.of(getSettings().getString(FILE, defaultFile()));
    if (!file.isAbsolute()) throw new IllegalArgumentException("Choose an absolute export path.");
    boolean marked = getSettings().is(PROXY, PROXY_DEFAULT);
    return new Capture(file, instrument, symbol, JournalPriceEvents.proxy(instrument, marked, false),
        JournalPriceEvents.proxy(instrument, marked, true));
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
   * ones; Record Prices off leaves replay without ticks of either kind. */
  private void backfillDue() {
    Capture live = liveChart, replay = replayChart;
    Capture chart = live != null ? live : replay;
    if (disposed || chart == null || backfilling || System.nanoTime() - nextBackfill < 0) return;
    boolean isReplay = live == null;
    if (isReplay && !getSettings().is(REPLAY, REPLAY_DEFAULT)) return;
    nextBackfill = System.nanoTime() + (isReplay ? REPLAY_BACKFILL_EVERY_NANOS : BACKFILL_EVERY_NANOS);
    int days = Math.max(0, Math.min(JournalBackfill.MAX_DAYS, getSettings().getInteger(BACKFILL, BACKFILL_DEFAULT)));
    int minutes = Math.max(0, Math.min(POST_EXIT_MAX, getSettings().getInteger(POST_EXIT, POST_EXIT_DEFAULT)));
    submitBackfill(chart, days, minutes, isReplay);
  }

  /** minutes: After Exit, the minutes after each exit loaded with the holding window. */
  private synchronized void submitBackfill(Capture chart, int days, int minutes, boolean replay) {
    JournalAccountCapture observer = accounts;
    if (disposed || observer == null || !JournalBackfill.applies(chart.instrument().getSymbol(), chart.symbol(),
        chart.historyProxy(), replayMode, replay, days)) return;
    long token = generation.get();
    history().submit(() -> {
      if (disposed || generation.get() != token || backfilling) return;
      backfilling = true;
      JournalEventWriter output = null;
      long began = System.nanoTime();
      String symbol = chart.instrument().getSymbol(), outcome = "";
      JournalBackfill.Result result = null;
      try {
        // Read once: the pass never asks for anything later than the replay's time when it began.
        long clock = replayNow;
        Path file = replay ? JournalAccountCapture.replayFile(chart.file()) : chart.file();
        var ledger = JournalBackfill.Ledger.shared(file, replay);
        JournalBackfill load = replay ? replayBackfill : backfill;
        if (load == null || load.ledger() != ledger) {
          load = new JournalBackfill(ledger);
          if (replay) replayBackfill = load;
          else backfill = load;
        }
        // false is essential: true would manufacture ticks from OHLC and overstate precision.
        JournalBackfill.TickSource ticks = (start, end, each) -> chart.instrument().forEachTick(start, end, false, false,
            tick -> each.accept(JournalPriceEvents.Print.of(tick)));
        BooleanSupplier cancelled = () -> disposed || generation.get() != token || Thread.currentThread().isInterrupted();
        if (replay) {
          var fills = observer.replayFills();
          if (fills == null || clock <= 0) return; // no snapshot or no replay time yet; the next beat asks again
          // A separate reference keeps the writer alive while settings/destroy close the study's own.
          JournalEventWriter target = output = JournalEventWriter.acquire(file);
          result = load.replayPass(symbol, fills, clock, System.currentTimeMillis(), days, minutes, ticks,
              event -> target.offerBelow(event, JournalBackfill.HIGH_WATER), cancelled);
        } else {
          var fills = observer.fills();
          if (fills == null) return; // no snapshot yet; the next beat asks again
          JournalEventWriter target = output = JournalEventWriter.acquire(file);
          result = load.pass(symbol, fills, System.currentTimeMillis(), days, minutes, ticks,
              event -> target.offerBelow(event, JournalBackfill.HIGH_WATER), cancelled);
        }
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
          info("MotiveWave Journal: " + symbol + (replay ? " replay" : "") + " trade tick backfill: " + counts
              + (counts.isEmpty() || outcome.isEmpty() ? "" : ", ") + outcome
              + " in " + (System.nanoTime() - began) / 1_000_000 + " ms.");
      }
    });
  }

  /** proxy: how the bars and ticks are marked (Capture says which applies). log: true for a load the chart
   * asked for, which leaves one line in MotiveWave's log; false for the two-minute top-up that follows every
   * new minute, which would only bury that line. */
  private synchronized void submitHistory(Capture capture, long start, long end, boolean bars, int tickMinutes,
                                          boolean proxy, boolean log, boolean replay) {
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
        // Replay bars go to the replay bridge (calculateValues has the rule); live bars to the live one.
        output = JournalEventWriter.acquire(replay ? JournalAccountCapture.replayFile(capture.file()) : capture.file());
        String provider = replay ? JournalPriceEvents.REPLAY_BARS : JournalPriceEvents.LIVE_BARS;
        if (bars) {
          for (Object item : capture.instrument().getBars(start, end, BarSize.minute(1), false)) {
            if (disposed || generation.get() != token || Thread.currentThread().isInterrupted()) return;
            if (!(item instanceof Bar bar)) continue;
            if (bar.getStartTime() < start || bar.getEndTime() > end) continue;
            String event = JournalPriceEvents.bar(capture.symbol(), bar, proxy, provider);
            if (event != null)
              wrote[output.offer(event, provider, Instant.ofEpochMilli(bar.getStartTime()).toString()) ? 0 : 2]++;
          }
        }
        if (tickMinutes > 0) {
          JournalEventWriter target = output;
          // false is essential: true would manufacture ticks from OHLC and overstate precision.
          capture.instrument().forEachTick(end - tickMinutes * 60000L, end, false, false, tick -> {
            if (disposed || generation.get() != token || Thread.currentThread().isInterrupted())
              throw new IllegalStateException("Capture settings changed.");
            if (tick.getTime() < end - tickMinutes * 60000L || tick.getTime() >= end) return;
            String event = JournalPriceEvents.tick(capture.symbol(), tick, proxy);
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
          info("MotiveWave Journal: " + capture.symbol() + (replay ? " replay" : "") + " history: " + wrote[0] + " bars and " + wrote[1]
              + " ticks written" + (wrote[2] > 0 ? ", " + wrote[2] + " dropped (queue full)" : "") + outcome
              + " in " + (System.nanoTime() - began) / 1_000_000 + " ms; fill capture " + fillCapture()
              + "; chart instrument " + JournalPriceEvents.names(capture.instrument()) + ".");
      }
    });
  }

  /** connected, starting (no poll has finished yet), failing, or off. */
  private String fillCapture() {
    if (!getSettings().is(ACCOUNTS, ACCOUNTS_DEFAULT)) return "off";
    JournalAccountCapture observer;
    synchronized (this) { observer = accounts; }
    if (observer == null) return "failing";
    String warnings = observer.warnings;
    return observer.status() + (warnings == null ? "" : " with warnings (" + warnings + ")");
  }

  private void report(Exception exc) {
    report(JournalEventWriter.anotherCopy(exc) ? JournalEventWriter.ANOTHER_COPY
        : exc.getClass().getSimpleName() + ". Check the export path, history date, and data connection.");
  }

  private synchronized void report(String problem) {
    if (JournalHandoff.retired()) return; // the newer copy speaks for the journal now
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
    // The pre-roll was gathered under the old settings (symbol, file, mode); it is not carried over.
    liveGate.clear();
    replayGate.clear();
  }

  private void closeAccounts() {
    if (accounts != null) {
      accounts.forget(this);
      accounts.unwatch(liveGate, replayGate);
      accounts.unwatch(afterPollBeat);
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
    lastReplayMinute.set(0);
    liveChart = null; // seen under the old settings or mode; the next callback brings the current one
    replayChart = null;
    if (history != null) history.shutdownNow();
    history = null;
  }
}
