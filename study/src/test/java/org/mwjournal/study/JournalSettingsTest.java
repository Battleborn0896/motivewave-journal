package org.mwjournal.study;

import java.lang.reflect.Modifier;
import java.net.URI;
import java.nio.charset.StandardCharsets;
import java.nio.file.FileSystems;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import org.mwjournal.study.JournalTicks.Held;

/** The study has nothing to set. Its dialog is one line, no class of it reads a setting, so the values studies
 * up to 0.3.9 saved in a chart do nothing, and its fixed values are the defaults those studies had. With them
 * alone it records fills, records the prints of every trade live and in replay as real ticks, on a continuous
 * chart as on the contract's, and backfills live and replay trades and the 30 minutes after each exit. */
public final class JournalSettingsTest {
  private JournalSettingsTest() {}
  /** Every key a study up to 0.3.9 stored. MotiveWave keeps them in saved charts: a Record Ticks of Always,
   * another bridge file, a mistyped End. */
  private static final List<String> OLD_KEYS = List.of("journalFile", "recordTicks", "recordTicksV3",
      "postExitMinutes", "journalSymbol", "priceProxy", "nativeAccounts", "historyDays", "historyTickMinutes",
      "historyEnd", "backfillDays", "captureReplay", "realReplayTicks", "realReplayTicksV2");

  public static void main(String[] args) throws Exception {
    // The dialog: one line, and nothing to change.
    assert JournalSettings.NOTE.equals("Add the study to the chart you trade. There is nothing to set.");
    assert JournalSettings.TAB.equals("General") && JournalSettings.GROUP.equals("MotiveWave Journal");
    for (var field : JournalSettings.class.getDeclaredFields()) {
      assert Modifier.isStatic(field.getModifiers()) && Modifier.isFinal(field.getModifiers()) : "Constants only: " + field;
      if (field.getType() == String.class)
        assert List.of("TAB", "GROUP", "NOTE").contains(field.getName()) : "No setting key: " + field.getName();
    }

    // No input: no class of the study asks MotiveWave for its settings or names an old key, so the values saved
    // in a chart by an earlier study never act.
    int read = 0;
    for (var entry : classes().entrySet()) {
      String code = entry.getValue();
      assert !code.contains("getSettings") : entry.getKey() + " reads the study's settings";
      for (String key : OLD_KEYS) assert !code.contains(key) : entry.getKey() + " names the old key " + key;
      read++;
    }
    assert read >= 15 : "Every class of the study was read: " + read;

    // The fixed values: the defaults studies up to 0.3.9 had.
    assert JournalSettings.BAR_DAYS == 3 && JournalSettings.BACKFILL_DAYS == 30 && JournalSettings.AFTER_EXIT_MINUTES == 30;
    assert JournalSettings.BACKFILL_DAYS <= JournalBackfill.MAX_DAYS;

    defaultsRecordTrades();
    System.out.println("Journal settings passed: one line and nothing to set, no class reads a setting or an old key, "
        + "and the fixed values record fills, live and replay prints during trades only as real ticks on a "
        + "continuous chart as on the contract's, and backfill trades and the 30 minutes after each exit.");
  }

  /** A continuous NQ chart whose front contract is NQZ6: fills are recorded; no print is written while flat,
   * live or in replay, and once a trade is open its prints are, with the few seconds before the entry, written
   * under NQZ6 and never marked proxy; replay prints as real ticks; the chart backfills NQZ6's live and replay
   * trades, and the 30 minutes after each exit. */
  private static void defaultsRecordTrades() throws Exception {
    var continuous = JournalPriceEventsTest.instrument("NQ", "NQZ6");
    String contract = JournalPriceEvents.contract(continuous);
    assert contract.equals("NQZ6") : contract;
    assert JournalBackfill.applies(contract, true, true) : "Replay trades backfilled";
    assert JournalBackfill.applies(contract, false, false) : "Live trades backfilled";
    long time = 1_609_772_400_000L;
    var trade = List.of(fill("in", true, time, false), fill("out", false, time + 60_000, false));
    assert JournalBackfill.afterExit(trade, contract, 0, Long.MAX_VALUE, JournalSettings.AFTER_EXIT_MINUTES)
        .equals(List.of(new JournalBackfill.Window(time + 60_000, time + 60_000 + 30 * 60_000))) : "30 minutes after the exit";
    String replaySource = JournalPriceEvents.replayProvider("run-1");
    assert replaySource.equals("motivewave-replay:run-1:ticks") : replaySource;

    var folder = Files.createTempDirectory("mwj-settings-fixture-");
    var file = folder.resolve("motivewave.ndjson");
    var fills = new ArrayList<JournalNativeAccess.Fill>();
    var capture = JournalAccountCapture.fixture(file,
        () -> new JournalNativeAccess.Snapshot("workspace", List.copyOf(fills), List.of()));
    capture.replayRun(contract, time);
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
      var live = new Held(contract, "motivewave-ticks", print, continuous.getSymbol(), continuous.getActiveSymbol());
      var replay = new Held(contract, replaySource, print, continuous.getSymbol(), continuous.getActiveSymbol());
      JournalTicks.record(gate, writer, live, "live", JournalTicks.armed(capture, false, live));
      JournalTicks.record(replayGate, replayWriter, replay, "replay", JournalTicks.armed(capture, true, replay));
    }
    writer.release();
    replayWriter.release();
    capture.close();
    var liveTicks = ticks(Files.readAllLines(file));
    var replayTicks = ticks(Files.readAllLines(JournalAccountCapture.replayFile(file)));
    assert liveTicks.size() == 6 && replayTicks.size() == 6 && gate.size() == 0 && replayGate.size() == 0
        : "The trade's print and the pre-roll before it: " + liveTicks.size() + " " + replayTicks.size();
    assert replayTicks.stream().allMatch(t -> t.contains("\"provider\":\"motivewave-replay:run-1:ticks\"")) : replayTicks;
    assert liveTicks.stream().allMatch(t -> t.contains("\"symbol\":\"NQZ6\"") && t.endsWith(",\"proxy\":false}"))
        && replayTicks.stream().allMatch(t -> t.contains("\"symbol\":\"NQZ6\"") && t.endsWith(",\"proxy\":false}"))
        : "Under the front contract, never proxy";
    try (var left = Files.walk(folder)) {
      for (var path : left.sorted(java.util.Comparator.reverseOrder()).toList()) Files.delete(path);
    }
  }

  /** Every class of the study, by name, as the text of its class file: what it calls and the strings it holds. */
  private static Map<String, String> classes() throws Exception {
    // Located through a class with no MotiveWave superclass: nothing of MotiveWave is loaded or run.
    URI where = JournalSettings.class.getProtectionDomain().getCodeSource().getLocation().toURI();
    Path root = Path.of(where);
    var result = new java.util.TreeMap<String, String>();
    if (Files.isDirectory(root)) {
      read(root.resolve("org").resolve("mwjournal").resolve("study"), result);
    } else {
      try (var jar = FileSystems.newFileSystem(root)) { read(jar.getPath("org", "mwjournal", "study"), result); }
    }
    return result;
  }

  private static void read(Path folder, Map<String, String> into) throws Exception {
    try (var files = Files.list(folder)) {
      for (Path file : files.filter(f -> f.getFileName().toString().endsWith(".class")).toList())
        into.put(file.getFileName().toString(), new String(Files.readAllBytes(file), StandardCharsets.ISO_8859_1));
    }
  }

  private static List<String> ticks(List<String> lines) {
    return lines.stream().filter(line -> line.contains("\"type\":\"tick\"")).toList();
  }

  private static JournalNativeAccess.Fill fill(String id, boolean buy, long time, boolean replay) {
    return new JournalNativeAccess.Fill(id, replay ? "REPLAY-1" : "paper", "NQZ6", buy, 1, 13000, time, 2, 20, "USD",
        "order-" + id, replay ? "replay-position" : "position", buy, replay);
  }
}
