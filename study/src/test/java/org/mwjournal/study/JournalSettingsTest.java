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
 * key unchanged and its default too, apart from Real Tick Data, now on. Those defaults alone capture fills,
 * record every live and replay print as real ticks, and backfill replay trades with real ticks. */
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
    // realReplayTicks, renamed V2 with its default now on, so that saved charts take the new default too.
    var expected = new LinkedHashMap<String, Object>();
    expected.put("journalFile", FILE);
    expected.put("recordTicksV2", "ALWAYS");
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
    assert JournalSettings.FILE.equals("journalFile") && JournalSettings.RECORD.equals("recordTicksV2")
        && JournalSettings.REAL_REPLAY.equals("realReplayTicksV2") && JournalSettings.BACKFILL.equals("backfillDays");

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
        .equals(List.of(JournalTicks.ALWAYS, JournalTicks.IN_POSITION, JournalTicks.NEVER));
    assert byKey.get(JournalSettings.RECORD).choices().getFirst().label().equals("Always While Study Runs");
    // Bounds unchanged.
    assert bounds(byKey, JournalSettings.DAYS).equals(List.of(0, 14)) && bounds(byKey, JournalSettings.TICKS).equals(List.of(0, 120));
    assert bounds(byKey, JournalSettings.BACKFILL).equals(List.of(0, JournalBackfill.MAX_DAYS))
        && bounds(byKey, JournalSettings.POST_EXIT).equals(List.of(0, 120));
    // The constants the study reads its settings with are the dialog's defaults.
    assert JournalSettings.DAYS_DEFAULT == 3 && JournalSettings.TICKS_DEFAULT == 0 && JournalSettings.BACKFILL_DEFAULT == 30
        && JournalSettings.POST_EXIT_DEFAULT == 30 && JournalSettings.ACCOUNTS_DEFAULT && JournalSettings.REPLAY_DEFAULT
        && JournalSettings.REAL_REPLAY_DEFAULT && !JournalSettings.PROXY_DEFAULT
        && JournalSettings.RECORD_DEFAULT.equals(JournalTicks.ALWAYS);

    defaultsAloneRecordEverything(byKey);
    System.out.println("Journal settings passed: nothing to set on the first tab, every 0.3.2 key under Advanced, "
        + "short Title Case labels with tooltips, and the defaults capture fills, record every live print and every "
        + "replay print as real ticks (simulated only with Real Tick Data off), and backfill replay trades.");
  }

  /** A chart nobody has changed: fills are captured, every print is written live and in replay whether or
   * not a position is open, replay prints as real ticks and neither marked proxy, on a continuous chart as
   * on the contract's own, and replay trades are backfilled on a replay chart of the contract. */
  private static void defaultsAloneRecordEverything(Map<String, Setting> byKey) throws Exception {
    assert (Boolean) byKey.get(JournalSettings.ACCOUNTS).fallback() : "Fills captured";
    assert (Boolean) byKey.get(JournalSettings.REPLAY).fallback() : "Replay prices recorded";
    String record = (String) byKey.get(JournalSettings.RECORD).fallback();
    int days = (Integer) byKey.get(JournalSettings.BACKFILL).fallback();
    boolean proxy = (Boolean) byKey.get(JournalSettings.PROXY).fallback();
    String symbol = (String) byKey.get(JournalSettings.SYMBOL).fallback(); // blank: the chart's own contract
    String journalSymbol = symbol.isBlank() ? "NQZ6" : symbol;
    assert JournalBackfill.applies("NQZ6", journalSymbol, proxy, true, true, days) : "Replay trades backfilled";
    assert JournalBackfill.applies("NQZ6", journalSymbol, proxy, false, false, days) : "Live trades backfilled";

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
    var capture = JournalAccountCapture.fixture(file,
        () -> new JournalNativeAccess.Snapshot("workspace", List.of(), List.of()));
    capture.poll(); // the book is known to be flat: a position-gated setting would skip every print
    var writer = JournalEventWriter.acquire(file);
    var replayWriter = JournalEventWriter.acquire(JournalAccountCapture.replayFile(file));
    var gate = new TickGate<Held>();
    var replayGate = new TickGate<Held>();
    long time = 1_609_772_400_000L;
    for (int n = 0; n < 5; n++) {
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
    assert liveTicks.size() == 5 && replayTicks.size() == 5 : "Every print, flat or not: " + liveTicks.size() + " " + replayTicks.size();
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
