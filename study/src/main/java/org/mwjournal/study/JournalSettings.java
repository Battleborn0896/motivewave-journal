package org.mwjournal.study;

import java.util.List;

/** The study's settings dialog, as data. A user adds the study to a chart and changes nothing: the first
 * tab only says so, and every setting is under Advanced. Each key and default is the one earlier versions
 * stored, so a chart saved by them keeps its choices.
 *
 * Pure: no MotiveWave types. The SDK's dialog classes need JavaFX, which a test JVM does not have, so the
 * layout is kept here where a test can read it, and MotiveWaveJournal.describe turns it into descriptors. */
final class JournalSettings {
  static final String FILE = "journalFile", SYMBOL = "journalSymbol", PROXY = "priceProxy";
  static final String DAYS = "historyDays", END = "historyEnd", TICKS = "historyTickMinutes", BACKFILL = "backfillDays";
  static final String ACCOUNTS = "nativeAccounts", REPLAY = "captureReplay", REAL_REPLAY = "realReplayTicks";
  // v2 intentionally resets the old position-gated preference: continuous chart capture is the product
  // default, so 5/15/30-minute runner windows exist without another switch.
  static final String RECORD = "recordTicksV2";
  static final String POST_EXIT = "postExitMinutes";

  static final int DAYS_DEFAULT = 3, DAYS_MAX = 14, TICKS_DEFAULT = 0, TICKS_MAX = 120;
  static final int BACKFILL_DEFAULT = 30, POST_EXIT_DEFAULT = 30, POST_EXIT_MAX = 120;
  static final boolean ACCOUNTS_DEFAULT = true, REPLAY_DEFAULT = true, REAL_REPLAY_DEFAULT = false, PROXY_DEFAULT = false;
  static final String RECORD_DEFAULT = JournalTicks.ALWAYS;

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
        List.of(note("Add the study to the chart of each contract you trade. That is all.")),
        List.of(note("The defaults suit nearly everyone; the rest is under Advanced."))))));

    var bridge = new Group("Bridge File", List.of(List.of(
        text(FILE, "File", defaultFile, 440, "The file the MotiveWave Journal app reads. A full path. The default "
            + "is where the app looks, so change it only when the app has been pointed somewhere else as well."))));
    var prices = new Group("Prices", List.of(
        List.of(choice(RECORD, "Record Ticks", RECORD_DEFAULT, List.of(
                new Choice("Always While Study Runs", JournalTicks.ALWAYS),
                new Choice("Trade + After Exit Only", JournalTicks.IN_POSITION),
                new Choice("Never", JournalTicks.NEVER)),
                "Always While Study Runs (default): every trade print delivered to this chart, live and replay, "
                + "for complete post-exit runner windows; the file gets larger. Trade + After Exit Only: a few "
                + "seconds before entry, the whole position, and the After Exit window. Never: no ticks. One-minute "
                + "bars are written in every mode. Trade + After Exit Only relies on Capture Fills: when fills cannot "
                + "be read, every trade is recorded."),
            whole(POST_EXIT, "After Exit (Minutes)", POST_EXIT_DEFAULT, 0, POST_EXIT_MAX,
                "With Trade + After Exit Only: keep recording this instrument after exit for runner analysis. Uses "
                + "market time in live and replay, so replay speed does not shorten the window. 0 = only the short "
                + "fill-detection tail. No orders are kept open or changed; this records prices only.")),
        List.of(text(SYMBOL, "Symbol", "", 110, "The symbol written with each price. Blank uses the chart's active "
                + "contract. Set it only when this chart stands in for the contract that is traded, and name that "
                + "contract. Trade tick backfill is then off for this chart."),
            toggle(PROXY, "Proxy Prices", PROXY_DEFAULT, "Marks the prices as coming from another instrument than "
                + "the one traded. A continuous-contract chart is marked automatically."))));
    var fills = new Group("Fills", List.of(List.of(
        toggle(ACCOUNTS, "Capture Fills", ACCOUNTS_DEFAULT, "Reads fills and their linked stop and target orders, "
            + "live and replay. Read-only: it never places, changes or cancels an order. It reads MotiveWave "
            + "internals that are not part of the supported SDK, so a MotiveWave update can stop it working."))));
    var history = new Group("History", List.of(
        List.of(whole(DAYS, "1m Bars (Days)", DAYS_DEFAULT, 0, DAYS_MAX,
                "Days of one-minute bars written when a live chart loads and kept up to date after. 0 = off."),
            whole(TICKS, "Ticks (Minutes)", TICKS_DEFAULT, 0, TICKS_MAX,
                "Also writes the real trades of the last N minutes when a live chart loads. 0 = off.")),
        List.of(text(END, "End (UTC)", "", 190, "Loads history up to this moment instead of the end of the chart, "
            + "for example 2026-01-05T21:00:00Z. Blank = the end of the loaded chart.")),
        List.of(whole(BACKFILL, "Backfill Trade Ticks (Days)", BACKFILL_DEFAULT, 0, JournalBackfill.MAX_DAYS,
            "Loads the real trades of this chart's contract for the holding time of every closed position, so "
            + "trades get tick-based MAE and MFE. Live: positions closed in the last N days, each loaded once, in "
            + "the background. Replay: each replay trade once it is flat, closed in the N days before the replay's "
            + "current time, and never past that time. "
            + "0 = off. Needs Capture Fills, a chart of the exact contract, and tick history from the data service."))));
    var replay = new Group("Replay", List.of(List.of(
        toggle(REPLAY, "Record Prices", REPLAY_DEFAULT, "Records replay prices, and backfills replay trades with "
            + "real ticks, in a file of their own beside the live one."),
        toggle(REAL_REPLAY, "Real Tick Data", REAL_REPLAY_DEFAULT, "Switch on only when the replay runs on real "
            + "tick data. Off, the replay's own prices are labelled simulated: generated from bars, so less exact. "
            + "Ticks backfilled from history are real either way."))));
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
