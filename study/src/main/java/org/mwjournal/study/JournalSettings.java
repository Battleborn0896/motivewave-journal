package org.mwjournal.study;

import java.util.List;

/** The study's settings dialog, as data. A user adds the study to a chart and changes nothing: the first
 * tab only says so, and every setting is under Advanced. Each key is the one earlier versions stored, so a
 * chart saved by them keeps its choices, except where a key ends in V2 or V3: there the default changed, and
 * a chart saved with the old default would otherwise keep it.
 *
 * Pure: no MotiveWave types. The SDK's dialog classes need JavaFX, which a test JVM does not have, so the
 * layout is kept here where a test can read it, and MotiveWaveJournal.describe turns it into descriptors. */
final class JournalSettings {
  static final String FILE = "journalFile", SYMBOL = "journalSymbol", PROXY = "priceProxy";
  static final String DAYS = "historyDays", END = "historyEnd", TICKS = "historyTickMinutes", BACKFILL = "backfillDays";
  static final String ACCOUNTS = "nativeAccounts", REPLAY = "captureReplay";
  // v3 (study 0.3.8): prints are recorded during trades only by default, and the minutes after an exit are
  // loaded from MotiveWave's tick history (After Exit). Under v2, recording every print while the study ran,
  // a chart open all day wrote about 540,000 NQ prints a day; the new key gives every saved chart the new default.
  static final String RECORD = "recordTicksV3";
  // v2 (study 0.3.4): replay prints count as real ticks by default. Under the old key, every chart that
  // had the study kept the old default, off, and its replay trades showed no MAE / MFE.
  static final String REAL_REPLAY = "realReplayTicksV2";
  // The same key as before 0.3.8, when it was a live recording tail: the minutes are the same minutes, now
  // loaded from history, and the default is unchanged.
  static final String POST_EXIT = "postExitMinutes";

  static final int DAYS_DEFAULT = 3, DAYS_MAX = 14, TICKS_DEFAULT = 0, TICKS_MAX = 120;
  static final int BACKFILL_DEFAULT = 30, POST_EXIT_DEFAULT = 30, POST_EXIT_MAX = 120;
  static final boolean ACCOUNTS_DEFAULT = true, REPLAY_DEFAULT = true, REAL_REPLAY_DEFAULT = true, PROXY_DEFAULT = false;
  static final String RECORD_DEFAULT = JournalTicks.IN_POSITION;

  /** NOTE is a line of text with no value; the others are inputs stored under their key. */
  enum Kind { NOTE, TEXT, WHOLE, SWITCH, CHOICE }
  record Choice(String label, String value) {}
  /** fallback: what MotiveWave stores for a chart that has never been changed. min/max: WHOLE only.
   * width: TEXT only, in the SDK's units. tip: the tooltip, where the explanation lives. */
  record Setting(Kind kind, String key, String label, Object fallback, int min, int max, int width,
                 List<Choice> choices, String tip) {}
  record Group(String name, List<List<Setting>> rows) {}
  record Tab(String name, List<Group> groups) {}

  private JournalSettings() {}

  static List<Tab> layout(String defaultFile) {
    var general = new Tab("General", List.of(new Group("Nothing To Set", List.of(
        List.of(note("Add the study to the chart you trade, contract or continuous. That is all.")),
        List.of(note("The defaults suit nearly everyone; the rest is under Advanced."))))));

    var bridge = new Group("Bridge File", List.of(List.of(
        text(FILE, "File", defaultFile, 440, "The file the MotiveWave Journal app reads. A full path. The default "
            + "is where the app looks, so change it only when the app has been pointed somewhere else as well."))));
    var prices = new Group("Prices", List.of(
        List.of(choice(RECORD, "Record Ticks", RECORD_DEFAULT, List.of(
                new Choice("During Trades Only", JournalTicks.IN_POSITION),
                new Choice("Always While Study Runs", JournalTicks.ALWAYS),
                new Choice("Never", JournalTicks.NEVER)),
                "During Trades Only (default): every trade print from a few seconds before the entry to a few "
                + "seconds after the exit, live and replay. The minutes after the exit come from MotiveWave's tick "
                + "history instead (After Exit, under History), which needs a chart of the exact contract: on a "
                + "continuous chart, choose Always While Study Runs to keep them. Always While Study Runs: every "
                + "print delivered to this chart, all day; the file gets far larger. Never: no prints, so in replay "
                + "the journal has no replay time and an open replay trade's chart stops at its entry. One-minute "
                + "bars are written in every mode. During Trades Only relies on Capture Fills: when fills cannot be "
                + "read, every print is recorded.")),
        List.of(text(SYMBOL, "Symbol", "", 110, "The symbol written with each price. Blank uses the chart's active "
                + "contract. Set it only when this chart stands in for the contract that is traded, and name that "
                + "contract. Trade tick backfill is then off for this chart."),
            toggle(PROXY, "Proxy Prices", PROXY_DEFAULT, "Marks this chart's prices as coming from another "
                + "instrument than the one traded. Prints recorded as they trade, live or replay, are never marked "
                + "otherwise; the history a continuous-contract chart loads is, as it can be back-adjusted."))));
    var fills = new Group("Fills", List.of(List.of(
        toggle(ACCOUNTS, "Capture Fills", ACCOUNTS_DEFAULT, "Reads fills and the stop orders that protect them, "
            + "live and replay. Read-only: it never places, changes or cancels an order. It reads MotiveWave "
            + "internals that are not part of the supported SDK, so a MotiveWave update can stop it working."))));
    var history = new Group("History", List.of(
        List.of(whole(DAYS, "1m Bars (Days)", DAYS_DEFAULT, 0, DAYS_MAX,
                "Days of one-minute bars written when a chart loads (in replay, those days and the replayed day "
                    + "whole) and kept up to date after. 0 = off."),
            whole(TICKS, "Ticks (Minutes)", TICKS_DEFAULT, 0, TICKS_MAX,
                "Also writes the real trades of the last N minutes when a live chart loads. 0 = off.")),
        List.of(text(END, "End (UTC)", "", 190, "Loads history up to this moment instead of the end of the chart, "
            + "for example 2026-01-05T21:00:00Z. Blank = the end of the loaded chart.")),
        List.of(whole(BACKFILL, "Backfill Trade Ticks (Days)", BACKFILL_DEFAULT, 0, JournalBackfill.MAX_DAYS,
                "Loads the real trades of this chart's contract for the holding time of every closed position, so "
                + "trades get tick-based MAE and MFE, and for the After Exit minutes that follow. Live: positions "
                + "closed in the last N days, each loaded once, in the background. Replay: each replay trade once it "
                + "is flat, closed in the N days before the replay's current time, and never past that time. "
                + "0 = off. Needs Capture Fills, a chart of the exact contract, and tick history from the data service."),
            whole(POST_EXIT, "After Exit (Minutes)", POST_EXIT_DEFAULT, 0, POST_EXIT_MAX,
                "Minutes of real trades after each exit, for the runner analysis, loaded from the same tick history, "
                + "not recorded live, so on a chart of the exact contract only. A longer runner window in the app "
                + "needs more. Live: once those minutes and five more have passed. Replay: as the replay plays "
                + "through them, never past its current time. 0 = none. Prices only: no order is kept open or changed."))));
    var replay = new Group("Replay", List.of(List.of(
        toggle(REPLAY, "Record Prices", REPLAY_DEFAULT, "Records replay prices, and backfills replay trades with "
            + "real ticks, in a file of their own beside the live one."),
        toggle(REAL_REPLAY, "Real Tick Data", REAL_REPLAY_DEFAULT, "On (default), the replay's own prints are "
            + "recorded as real ticks and measure replay trades like live ones. Switch off if your replay is "
            + "generated from bars: its prints are then labelled simulated, so less exact. Ticks backfilled from "
            + "history are real either way."))));
    return List.of(general, new Tab("Advanced", List.of(bridge, prices, fills, history, replay)));
  }

  /** Every input of the layout, in dialog order. */
  static List<Setting> inputs(List<Tab> layout) {
    return layout.stream().flatMap(tab -> tab.groups().stream()).flatMap(group -> group.rows().stream())
        .flatMap(List::stream).filter(setting -> setting.kind() != Kind.NOTE).toList();
  }

  private static Setting note(String text) {
    return new Setting(Kind.NOTE, null, text, null, 0, 0, 0, List.of(), null);
  }
  private static Setting text(String key, String label, String fallback, int width, String tip) {
    return new Setting(Kind.TEXT, key, label, fallback, 0, 0, width, List.of(), tip);
  }
  private static Setting whole(String key, String label, int fallback, int min, int max, String tip) {
    return new Setting(Kind.WHOLE, key, label, fallback, min, max, 0, List.of(), tip);
  }
  private static Setting toggle(String key, String label, boolean fallback, String tip) {
    return new Setting(Kind.SWITCH, key, label, fallback, 0, 0, 0, List.of(), tip);
  }
  private static Setting choice(String key, String label, String fallback, List<Choice> choices, String tip) {
    return new Setting(Kind.CHOICE, key, label, fallback, 0, 0, 0, choices, tip);
  }
}
