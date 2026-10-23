package org.mwjournal.study;

import java.nio.file.Files;
import java.util.ArrayList;
import java.util.HashSet;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;
import org.mwjournal.study.JournalSettings.Kind;
import org.mwjournal.study.JournalSettings.Setting;
import org.mwjournal.study.JournalTicks.Held;

/** The settings dialog: nothing to set on the first tab, every setting of 0.3.2 under Advanced with its
 * key unchanged and its default too, apart from Real Tick Data, now on, and Record Ticks, now During Trades
 * Only, each under a renamed key. Those defaults alone capture fills, record the prints of every trade live
 * and in replay as real ticks, and backfill live and replay trades, and the minutes after each exit, with
 * real ticks from MotiveWave's history. */
public final class JournalSettingsTest {
  private JournalSettingsTest() {}
  private static final String FILE = "/fixture/bridge/motivewave.ndjson";
  private static final Set<String> SMALL = Set.of("a", "an", "and", "as", "at", "by", "for", "from", "in", "of",
      "on", "or", "the", "to", "with");

  public static void main(String[] args) throws Exception {
    var layout = JournalSettings.layout(FILE);
    assert layout.size() == 2 && layout.get(0).name().equals("General") && layout.get(1).name().equals("Advanced")
        : layout.stream().map(JournalSettings.Tab::name).toList();

    // The first tab: one short line or two, and nothing to change.
    var first = settings(layout.get(0));
    assert !first.isEmpty() && first.size() <= 2 && first.stream().allMatch(s -> s.kind() == Kind.NOTE && s.key() == null)
        : "The first tab only says there is nothing to set: " + first;
    assert first.stream().allMatch(s -> s.label().length() <= 80) : "Short lines";
    assert first.getFirst().label().contains("Add the study to the chart you trade, contract or continuous");

    // Every key of study 0.3.2, once each and all under Advanced, so a saved chart keeps its choices, except
    // realReplayTicks, renamed V2 with its default now on, and recordTicks, renamed V3 (study 0.3.8) with its
    // default now During Trades Only, so that saved charts take the new defaults too. After Exit keeps its
    // key and its 30 minutes: they are the same minutes, now loaded from history instead of recorded.
    var expected = new LinkedHashMap<String, Object>();
    expected.put("journalFile", FILE);
    expected.put("recordTicksV3", "POSITION");
    expected.put("postExitMinutes", 30);
    expected.put("journalSymbol", "");
    expected.put("priceProxy", false);
    expected.put("nativeAccounts", true);
    expected.put("historyDays", 3);
    expected.put("historyTickMinutes", 0);
    expected.put("historyEnd", "");
    expected.put("backfillDays", 30);
    expected.put("captureReplay", true);
    expected.put("realReplayTicksV2", true);
    var inputs = JournalSettings.inputs(layout);
    var stored = new LinkedHashMap<String, Object>();
    for (Setting input : inputs) assert stored.put(input.key(), input.fallback()) == null : "Twice: " + input.key();
    assert stored.equals(expected) : stored;
    assert settings(layout.get(1)).equals(inputs) : "Everything is under Advanced";
    assert JournalSettings.FILE.equals("journalFile") && JournalSettings.RECORD.equals("recordTicksV3")
        && JournalSettings.REAL_REPLAY.equals("realReplayTicksV2") && JournalSettings.BACKFILL.equals("backfillDays")
        && JournalSettings.POST_EXIT.equals("postExitMinutes");

    var byKey = new LinkedHashMap<String, Setting>();
    inputs.forEach(input -> byKey.put(input.key(), input));
    // Labels: short and Title Case. Explanations: in the tooltip, never in the label.
    var labels = new HashSet<String>();
    for (Setting input : inputs) {
      assert input.label().length() <= 28 && titleCase(input.label()) : "Short Title Case label: " + input.label();
      assert labels.add(input.label()) : "One label, one setting: " + input.label();
      assert input.tip() != null && input.tip().length() > 40 : "Explained in the tooltip: " + input.key();
    }
    assert byKey.get(JournalSettings.REAL_REPLAY).label().equals("Real Tick Data");
    assert byKey.get(JournalSettings.RECORD).choices().stream().map(JournalSettings.Choice::value).toList()
        .equals(List.of(JournalTicks.IN_POSITION, JournalTicks.ALWAYS, JournalTicks.NEVER)) : "The default first";
    assert byKey.get(JournalSettings.RECORD).choices().stream().map(JournalSettings.Choice::label).toList()
        .equals(List.of("During Trades Only", "Always While Study Runs", "Never"));
    // After Exit is part of the backfill now, so it sits beside it, and both tooltips say so.
    assert inputs.indexOf(byKey.get(JournalSettings.POST_EXIT)) == inputs.indexOf(byKey.get(JournalSettings.BACKFILL)) + 1;
    assert byKey.get(JournalSettings.POST_EXIT).label().equals("After Exit (Minutes)")
        && byKey.get(JournalSettings.POST_EXIT).tip().contains("tick history, not recorded live")
        && byKey.get(JournalSettings.BACKFILL).tip().contains("After Exit")
        && byKey.get(JournalSettings.RECORD).tip().contains("The minutes after the exit come from MotiveWave's tick history");
    // A continuous chart loads no tick history, so both say where those minutes come from there.
    assert byKey.get(JournalSettings.POST_EXIT).tip().contains("on a chart of the exact contract only")
        && byKey.get(JournalSettings.RECORD).tip().contains("on a continuous chart, choose Always While Study Runs");
    // Bounds unchanged.
    assert bounds(byKey, JournalSettings.DAYS).equals(List.of(0, 14)) && bounds(byKey, JournalSettings.TICKS).equals(List.of(0, 120));
    assert bounds(byKey, JournalSettings.BACKFILL).equals(List.of(0, JournalBackfill.MAX_DAYS))
        && bounds(byKey, JournalSettings.POST_EXIT).equals(List.of(0, 120));
    // The constants the study reads its settings with are the dialog's defaults.
    assert JournalSettings.DAYS_DEFAULT == 3 && JournalSettings.TICKS_DEFAULT == 0 && JournalSettings.BACKFILL_DEFAULT == 30
        && JournalSettings.POST_EXIT_DEFAULT == 30 && JournalSettings.ACCOUNTS_DEFAULT && JournalSettings.REPLAY_DEFAULT
        && JournalSettings.REAL_REPLAY_DEFAULT && !JournalSettings.PROXY_DEFAULT
        && JournalSettings.RECORD_DEFAULT.equals(JournalTicks.IN_POSITION);

    defaultsRecordTrades(byKey);
    System.out.println("Journal settings passed: nothing to set on the first tab, every 0.3.2 key under Advanced, "
        + "short Title Case labels with tooltips, and the defaults capture fills, record live and replay prints "
        + "during trades only, replay prints as real ticks (simulated only with Real Tick Data off), and backfill "
        + "live and replay trades and the 30 minutes after each exit from MotiveWave's history.");
  }

  /** A chart nobody has changed: fills are captured; no print is written while flat, live or in replay, and
   * once a trade is open its prints are, with the few seconds before the entry; replay prints as real ticks
   * and neither marked proxy, on a continuous chart as on the contract's own; live and replay trades are
   * backfilled on a chart of the contract, and so are the 30 minutes after each exit. */
  private static void defaultsRecordTrades(Map<String, Setting> byKey) throws Exception {
    assert (Boolean) byKey.get(JournalSettings.ACCOUNTS).fallback() : "Fills captured";
    assert (Boolean) byKey.get(JournalSettings.REPLAY).fallback() : "Replay prices recorded";
    String record = (String) byKey.get(JournalSettings.RECORD).fallback();
    int days = (Integer) byKey.get(JournalSettings.BACKFILL).fallback();
    boolean proxy = (Boolean) byKey.get(JournalSettings.PROXY).fallback();
    String symbol = (String) byKey.get(JournalSettings.SYMBOL).fallback(); // blank: the chart's own contract
    String journalSymbol = symbol.isBlank() ? "NQZ6" : symbol;
    assert JournalBackfill.applies("NQZ6", journalSymbol, proxy, true, true, days) : "Replay trades backfilled";
    assert JournalBackfill.applies("NQZ6", journalSymbol, proxy, false, false, days) : "Live trades backfilled";
    // And the minutes after each exit with them, from the same tick history.
    int minutes = (Integer) byKey.get(JournalSettings.POST_EXIT).fallback();
    long time = 1_609_772_400_000L;
    var trade = List.of(fill("in", true, time, false), fill("out", false, time + 60_000, false));
    assert JournalBackfill.afterExit(trade, journalSymbol, 0, Long.MAX_VALUE, minutes)
        .equals(List.of(new JournalBackfill.Window(time + 60_000, time + 60_000 + 30 * 60_000))) : "30 minutes after the exit";

    // Replay prints are real ticks unless the user says the replay is generated from bars.
    boolean realReplay = (Boolean) byKey.get(JournalSettings.REAL_REPLAY).fallback();
    String replaySource = JournalPriceEvents.replayProvider("run-1", realReplay);
    assert replaySource.equals("motivewave-replay:run-1:ticks") : replaySource;
    assert JournalPriceEvents.replayProvider("run-1", false).equals("motivewave-replay:run-1:simulated")
        : "Real Tick Data off: the replay's prints are labelled simulated";
    // Prints recorded as they trade are never marked proxy by the chart they come from.
    var continuous = JournalPriceEventsTest.instrument("NQ", "NQZ6");
    assert !JournalPriceEvents.proxy(continuous, proxy, false) : "A continuous chart's prints are the product's own";

    var folder = Files.createTempDirectory("mwj-settings-fixture-");
    var file = folder.resolve("motivewave.ndjson");
    var fills = new ArrayList<JournalNativeAccess.Fill>();
    var capture = JournalAccountCapture.fixture(file,
        () -> new JournalNativeAccess.Snapshot("workspace", List.copyOf(fills), List.of()));
    capture.replayRun("NQZ6", time);
    capture.poll(); // the book is known to be flat
    var writer = JournalEventWriter.acquire(file);
    var replayWriter = JournalEventWriter.acquire(JournalAccountCapture.replayFile(file));
    var gate = new TickGate<Held>();
    var replayGate = new TickGate<Held>();
    for (int n = 0; n < 6; n++) {
      if (n == 5) {
        assert gate.size() == 5 && replayGate.size() == 5 : "Flat: held back as the pre-roll, not written";
        fills.add(fill("live-in", true, time + 1_000, false)); // a trade opens, live and in replay
        fills.add(fill("replay-in", true, time + 1_000, true));
        capture.poll();
      }
      var print = new JournalPriceEvents.Print(time + n * 250L, 13000 + n, 12999.75f + n, 13000.25f + n, 1);
      boolean marked = JournalPriceEvents.proxy(continuous, proxy, false);
      var live = new Held("NQZ6", marked, "motivewave-ticks", print, "NQ", "NQZ6");
      var replay = new Held("NQZ6", marked, replaySource, print, "NQ", "NQZ6");
      JournalTicks.record(gate, writer, live, "live", JournalTicks.armed(capture, record, false, live));
      JournalTicks.record(replayGate, replayWriter, replay, "replay", JournalTicks.armed(capture, record, true, replay));
    }
    writer.release();
    replayWriter.release();
    capture.close();
    var liveTicks = ticks(Files.readAllLines(file));
    var replayTicks = ticks(Files.readAllLines(JournalAccountCapture.replayFile(file)));
    assert liveTicks.size() == 6 && replayTicks.size() == 6 && gate.size() == 0 && replayGate.size() == 0
        : "The trade's print and the pre-roll before it: " + liveTicks.size() + " " + replayTicks.size();
    assert replayTicks.stream().allMatch(t -> t.contains("\"provider\":\"motivewave-replay:run-1:ticks\"")) : replayTicks;
    assert liveTicks.stream().allMatch(t -> t.endsWith(",\"proxy\":false}"))
        && replayTicks.stream().allMatch(t -> t.endsWith(",\"proxy\":false}")) : "Never proxy by default";
    try (var left = Files.walk(folder)) {
      for (var path : left.sorted(java.util.Comparator.reverseOrder()).toList()) Files.delete(path);
    }
  }

  private static List<String> ticks(List<String> lines) {
    return lines.stream().filter(line -> line.contains("\"type\":\"tick\"")).toList();
  }

  private static JournalNativeAccess.Fill fill(String id, boolean buy, long time, boolean replay) {
    return new JournalNativeAccess.Fill(id, replay ? "REPLAY-1" : "paper", "NQZ6", buy, 1, 13000, time, 2, 20, "USD",
        "order-" + id, replay ? "replay-position" : "position", buy, replay);
  }

  private static List<Setting> settings(JournalSettings.Tab tab) {
    var result = new ArrayList<Setting>();
    for (var group : tab.groups()) {
      assert titleCase(group.name()) : "Title Case group: " + group.name();
      for (var row : group.rows()) result.addAll(row);
    }
    return result;
  }

  private static List<Integer> bounds(Map<String, Setting> byKey, String key) {
    return List.of(byKey.get(key).min(), byKey.get(key).max());
  }

  private static boolean titleCase(String label) {
    for (String word : label.split("[\\s()]+"))
      if (!word.isEmpty() && Character.isLetter(word.charAt(0)) && !Character.isUpperCase(word.charAt(0))
          && !SMALL.contains(word)) return false;
    return true;
  }
}
