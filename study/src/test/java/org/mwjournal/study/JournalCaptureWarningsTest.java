package org.mwjournal.study;

import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import java.util.TreeMap;
import java.util.concurrent.atomic.AtomicReference;
import org.mwjournal.study.JournalNativeAccess.Fill;
import org.mwjournal.study.JournalNativeAccess.Raw;

/** What becomes of a fill MotiveWave describes badly: no account object, no price, no instrument. The
 * fill is written whenever it can be described, labelled with what was assumed, and the heartbeat says
 * 'connected' with a warning; 'error' is kept for a capture that cannot read fills at all.
 * Fixtures only: no MotiveWave, account or order. */
public final class JournalCaptureWarningsTest {
  private JournalCaptureWarningsTest() {}
  private static final long TIME = 1641220200000L, SECOND = 1_000_000_000L;
  private static final long GRACE = JournalAccountCapture.UNPRICED_GRACE_NANOS;
  private static final String PROVIDER = "motivewave-native:workspace:live";
  private static final String PLAIN = "MotiveWave internal capture; fees may be estimated";
  private static final String ASSUMED = PLAIN + "; account currency assumed from the instrument";
  private static final String UNPRICED = PLAIN + "; platform reported no fill price";

  public static void main(String[] args) throws Exception {
    var folder = Files.createTempDirectory("mwj-warnings-fixture-");
    describing();
    wording();
    heartbeats(folder.resolve("motivewave.ndjson"));
    settling(folder.resolve("settling.ndjson"));
    stableLabels(folder);
    // Only a passing run tidies up; a failing one leaves its files to look at.
    try (var left = Files.walk(folder)) {
      for (var path : left.sorted(java.util.Comparator.reverseOrder()).toList()) Files.delete(path);
    }
    System.out.println("Journal capture warnings passed: account fallback and its label, skip reasons and counts, "
        + "fills without a price, the warnings sentence, connected-with-warnings versus error, the grace for an "
        + "unfinished record, and labels that never change once written.");
  }

  /** The adapter's decision for one execution, without MotiveWave. */
  private static void describing() {
    var skipped = new TreeMap<String,Integer>();
    Fill usual = JournalNativeAccess.describe(raw("usual", "paper", "ESZ6", 1, 5000, "USD", "USD"), skipped);
    assert usual != null && !usual.currencyAssumed() && usual.priced() && usual.account().equals("paper")
        && usual.currency().equals("USD") && usual.pointValue() == 50 && usual.commission() == 2;

    // The account object is gone (an account MotiveWave no longer knows): the public SDK still names the
    // account, the instrument says the currency, and the fill says that this was assumed.
    Fill orphan = JournalNativeAccess.describe(raw("orphan", "closed-account", "ESZ6", 1, 5000, null, "USD"), skipped);
    assert orphan != null && orphan.currencyAssumed() && orphan.account().equals("closed-account")
        && orphan.currency().equals("USD") && orphan.priced() && orphan.pointValue() == 50;
    Fill noBase = JournalNativeAccess.describe(raw("no-base", "paper", "FDAXZ6", 1, 20000, " ", "EUR"), skipped);
    assert noBase != null && noBase.currencyAssumed() && noBase.currency().equals("EUR")
        : "An account that names no currency is the same case";

    // No price: the fill is kept so the quantities reconcile, and says its price is not to be used.
    Fill zero = JournalNativeAccess.describe(raw("zero", "paper", "ESZ6", 2, 0, "USD", "USD"), skipped);
    assert zero != null && !zero.priced() && zero.qty() == 2 && !zero.currencyAssumed();
    Fill nan = JournalNativeAccess.describe(raw("nan", "paper", "ESZ6", 1, Double.NaN, "USD", "USD"), skipped);
    assert nan != null && !nan.priced();
    Fill negative = JournalNativeAccess.describe(raw("spread", "paper", "ESZ6", 1, -1.25, "USD", "USD"), skipped);
    assert negative != null && negative.priced() : "A spread can trade below zero; only exactly 0 means no price";
    Fill stock = JournalNativeAccess.describe(new Raw("stock", "paper", "AAPL", true, 10, 200, TIME, Double.NaN,
        Double.NaN, Double.NaN, "USD", "USD", true, "order", "position", true, false), skipped);
    assert stock != null && stock.pointValue() == 1 && stock.commission() == 0
        : "A stock needs no multiplier, and a fee that is not a number is no fee";
    assert skipped.isEmpty() : "None of these is a reason to lose a fill: " + skipped;

    // Left out only when there is no way to describe the fill, and counted by reason.
    assert JournalNativeAccess.describe(raw("a", "", "ESZ6", 1, 5000, null, "USD"), skipped) == null;
    assert JournalNativeAccess.describe(raw("b", null, "ESZ6", 1, 5000, "USD", "USD"), skipped) == null;
    assert JournalNativeAccess.describe(raw("c", "paper", null, 1, 5000, "USD", "USD"), skipped) == null;
    assert JournalNativeAccess.describe(raw("d", "paper", "ESZ6", 1, 5000, "USD", null), skipped) == null;
    assert JournalNativeAccess.describe(raw("e", "paper", " ", 1, 5000, "USD", "USD"), skipped) == null;
    assert JournalNativeAccess.describe(raw("f", "paper", "ESZ6", 1, 5000, "USD", ""), skipped) == null;
    assert JournalNativeAccess.describe(raw("g", "paper", "ESZ6", 1, 5000, null, " "), skipped) == null;
    assert JournalNativeAccess.describe(raw("h", "paper", "FDAXZ6", 1, 20000, "USD", "EUR"), skipped) == null;
    assert JournalNativeAccess.describe(new Raw("i", "paper", "ESZ6", true, 1, 5000, TIME, 2, 12.5, 0, "USD", "USD",
        false, "order", "position", true, false), skipped) == null;
    assert JournalNativeAccess.describe(raw("j", "paper", "ESZ6", 0, 5000, "USD", "USD"), skipped) == null;
    assert JournalNativeAccess.describe(raw("k", "paper", "ESZ6", Double.NaN, 5000, "USD", "USD"), skipped) == null;
    assert JournalNativeAccess.describe(raw(" ", "paper", "ESZ6", 1, 5000, "USD", "USD"), skipped) == null;
    assert JournalNativeAccess.describe(new Raw("l", "paper", "ESZ6", true, 1, 5000, 0, 2, 12.5, .25, "USD", "USD",
        false, "order", "position", true, false), skipped) == null;
    assert skipped.equals(Map.of(JournalNativeAccess.NO_ACCOUNT, 2, JournalNativeAccess.NO_INSTRUMENT, 3,
        JournalNativeAccess.NO_CURRENCY, 2, JournalNativeAccess.CROSS_CURRENCY, 1,
        JournalNativeAccess.NO_MULTIPLIER, 1, JournalNativeAccess.INCOMPLETE, 4)) : skipped;
  }

  /** The exact text on the wire: the app and docs/protocol.md key on it. */
  private static void wording() {
    var skipped = new TreeMap<String,Integer>();
    Fill usual = JournalNativeAccess.describe(raw("usual", "paper", "ESZ6", 1, 5000, "USD", "USD"), skipped);
    String legacy = "{\"version\":1,\"type\":\"execution\",\"mode\":\"live\",\"provider\":\"" + PROVIDER
        + "\",\"account\":\"paper\",\"symbol\":\"ESZ6\",\"side\":\"buy\",\"qty\":1.0,\"price\":5000.0,"
        + "\"ts\":\"2022-01-03T14:30:00Z\",\"execution_id\":\"usual\",\"order_id\":\"order-usual\","
        + "\"native_entry\":true,\"native_position_id\":\"position\",\"commission\":2.0,\"commission_known\":false,"
        + "\"currency\":\"USD\",\"point_value\":50.0,\"quality\":\"" + PLAIN + "\"}";
    assert JournalAccountCapture.execution(usual, PROVIDER).equals(legacy)
        : "An ordinary fill must be written exactly as study 0.2 wrote it";

    Fill orphan = JournalNativeAccess.describe(raw("orphan", "closed-account", "ESZ6", 1, 5000, null, "USD"), skipped);
    String assumed = JournalAccountCapture.execution(orphan, PROVIDER);
    assert assumed.contains("\"account\":\"closed-account\"") && assumed.contains("\"currency\":\"USD\"")
        && assumed.contains("\"currency_assumed\":true") && assumed.endsWith("\"quality\":\"" + ASSUMED + "\"}")
        && !assumed.contains("price_known") : assumed;

    Fill zero = JournalNativeAccess.describe(raw("zero", "paper", "ESZ6", 2, 0, "USD", "USD"), skipped);
    String unpriced = JournalAccountCapture.execution(zero, PROVIDER);
    assert unpriced.contains("\"qty\":2.0,\"price\":0.0,") && unpriced.contains("\"price_known\":false")
        && unpriced.endsWith("\"quality\":\"" + UNPRICED + "\"}") && !unpriced.contains("currency_assumed") : unpriced;
    Fill nan = JournalNativeAccess.describe(raw("nan", "closed-account", "ESZ6", 1, Double.NaN, null, "USD"), skipped);
    String both = JournalAccountCapture.execution(nan, PROVIDER);
    assert both.contains("\"price\":0.0,") && both.contains("\"price_known\":false,\"currency_assumed\":true")
        && both.endsWith("\"quality\":\"" + PLAIN + "; account currency assumed from the instrument; "
            + "platform reported no fill price\"}") : "A price that is not a number is written as the placeholder 0";
    assert !both.contains("NaN");
    // What study 0.2 wrote for a fill at 0, and what such a fill is re-sent as for ever after.
    assert JournalAccountCapture.execution(zero, PROVIDER, "").equals(legacy.replace("\"qty\":1.0,\"price\":5000.0",
        "\"qty\":2.0,\"price\":0.0").replace("usual", "zero"));
    assert JournalAccountCapture.QUALITY.equals(PLAIN);
    assert skipped.isEmpty();

    // The heartbeat's sentence.
    assert JournalAccountCapture.warnings(Map.of(), 0, 0, 0, 0) == null : "Nothing to say is no field at all";
    assert JournalAccountCapture.warnings(Map.of("instrument unavailable", 3), 2, 0, 0, 0)
        .equals("3 fills skipped: instrument unavailable; 2 fills with an assumed currency");
    assert JournalAccountCapture.warnings(Map.of("instrument unavailable", 3, "cross-currency", 2), 0, 0, 0, 0)
        .equals("5 fills skipped: 2 cross-currency, 3 instrument unavailable");
    assert JournalAccountCapture.warnings(Map.of(), 0, 7, 0, 0).equals("7 fills with no fill price");
    assert JournalAccountCapture.warnings(Map.of("account unavailable", 1), 1, 1, 1, 1)
        .equals("1 fill skipped: account unavailable; 1 fill with an assumed currency; 1 fill with no fill price; "
            + "1 replay fill waiting for a replay chart with the study; "
            + "1 fill record changed after capture (not duplicated)");
  }

  /** Through the capture and the real writer: what reaches the bridge file. */
  private static void heartbeats(Path file) throws Exception {
    var raws = new ArrayList<Raw>();
    var broken = new AtomicReference<ReflectiveOperationException>();
    var capture = JournalAccountCapture.fixture(file, reader(raws, broken));
    raws.add(raw("usual", "paper", "ESZ6", 1, 5000, "USD", "USD"));
    raws.add(raw("orphan-in", "closed-account", "ESZ6", 1, 5000, null, "USD"));
    raws.add(raw("orphan-out", "closed-account", "ESZ6", 1, 5004, null, "USD"));
    raws.add(raw("zero", "paper", "ESZ6", 1, 0, "USD", "USD"));
    raws.add(raw("lost-1", "paper", null, 1, 5000, "USD", "USD"));
    raws.add(raw("lost-2", "paper", "ESZ6", 1, 5000, "USD", null));
    raws.add(raw("foreign", "paper", "FDAXZ6", 1, 20000, "USD", "EUR"));
    String warning = "3 fills skipped: 1 cross-currency, 2 instrument unavailable; "
        + "2 fills with an assumed currency; 1 fill with no fill price";
    long clock = System.nanoTime();
    capture.poll(clock);
    assert capture.failure == null && capture.status().equals("connected") : capture.failure;
    assert capture.warnings.equals(warning) : capture.warnings;
    assert capture.fills().size() == 4 : "Skipped fills are in no list";
    capture.poll(clock + GRACE + SECOND);
    assert capture.failure == null && capture.warnings.equals(warning);

    // Fills cannot be read at all: that, and only that, is an error.
    broken.set(new NoSuchMethodException("Fixture: MotiveWave update"));
    capture.poll(clock + GRACE + 2 * SECOND);
    assert capture.failure.contains("NoSuchMethodException") && capture.warnings == null && capture.status().equals("failing");
    broken.set(null);
    capture.poll(clock + GRACE + 3 * SECOND);
    assert capture.failure == null && capture.warnings.equals(warning);
    capture.close();

    var lines = Files.readAllLines(file);
    var fills = lines.stream().filter(s -> s.contains("\"type\":\"execution\"")).toList();
    assert fills.size() == 4 : "Every fill that can be described is written: " + fills.size();
    assert fills.stream().filter(s -> s.contains("\"account\":\"closed-account\"") && s.contains("\"currency_assumed\":true")
        && s.contains("\"quality\":\"" + ASSUMED + "\"")).count() == 2 : "No fill is lost to a missing account object";
    assert fills.stream().filter(s -> s.contains("\"execution_id\":\"zero\"") && s.contains("\"price\":0.0,")
        && s.contains("\"price_known\":false") && s.contains("\"quality\":\"" + UNPRICED + "\"")).count() == 1;
    assert fills.stream().filter(s -> s.contains("\"execution_id\":\"usual\"") && s.contains("\"quality\":\"" + PLAIN + "\"")
        && !s.contains("price_known") && !s.contains("currency_assumed")).count() == 1;
    assert fills.stream().noneMatch(s -> s.contains("lost-") || s.contains("foreign"));
    assert fills.stream().allMatch(s -> s.contains("\"capture_kind\":\"retained\""))
        : "A fill held back for its price is still history, not a new trade to take a screenshot of";

    for (var beats : List.of(beats(lines), beats(Files.readAllLines(JournalAccountCapture.replayFile(file))))) {
      assert beats.size() == 3 : "One when it starts, and one at once each time what it says changes: " + beats.size();
      String working = "\"status\":\"connected\",\"detail\":\"Experimental read-only account capture; 4 fills read\","
          + "\"warnings\":\"" + warning + "\",\"connector\":\"motivewave-journal-study\",\"connector_version\":\"0.3.2\"}";
      assert beats.get(0).endsWith(working) : beats.get(0);
      assert beats.get(1).contains("\"status\":\"error\"")
          && beats.get(1).contains("\"detail\":\"Native MotiveWave adapter unavailable: NoSuchMethodException\"")
          && !beats.get(1).contains("\"warnings\"") : beats.get(1);
      assert beats.get(2).endsWith(working) : "Recovered: connected again, with the same warnings";
      assert beats.stream().allMatch(s -> s.contains("\"provider\":\"motivewave-native\"") && s.contains("\"ts\":\""));
    }

    // Nothing skipped and nothing assumed: connected, and no warnings field at all.
    var clean = JournalAccountCapture.fixture(file.resolveSibling("clean.ndjson"),
        reader(List.of(raw("only", "paper", "ESZ6", 1, 5000, "USD", "USD")), broken));
    clean.poll();
    assert clean.failure == null && clean.warnings == null;
    clean.close();
    var cleanBeats = beats(Files.readAllLines(file.resolveSibling("clean.ndjson")));
    assert cleanBeats.size() == 1 && !cleanBeats.getFirst().contains("warnings")
        && cleanBeats.getFirst().contains("\"status\":\"connected\",\"detail\":\"Experimental read-only account capture; "
            + "1 fill read\",\"connector\"") : cleanBeats;
  }

  /** A fill without a price waits a moment the first time it is seen: once written it cannot be corrected. */
  private static void settling(Path file) throws Exception {
    var raws = new ArrayList<Raw>();
    var capture = JournalAccountCapture.fixture(file, reader(raws, new AtomicReference<>()));
    long clock = System.nanoTime();
    capture.poll(clock); // baseline: nothing held
    raws.add(raw("late-price", "paper", "ESZ6", 1, 0, "USD", "USD"));
    raws.add(raw("no-price", "paper", "ESZ6", 1, 0, "USD", "USD"));
    capture.poll(clock + SECOND);
    capture.poll(clock + 2 * SECOND);
    assert capture.warnings.equals("2 fills with no fill price");
    // MotiveWave finishes one record inside the grace: it is written once, priced, like any other fill.
    raws.set(0, raw("late-price", "paper", "ESZ6", 1, 5001.25, "USD", "USD"));
    capture.poll(clock + 3 * SECOND);
    capture.poll(clock + GRACE);
    assert capture.warnings.equals("1 fill with no fill price") : "Still nothing written for the other one";
    capture.poll(clock + SECOND + GRACE);
    capture.poll(clock + SECOND + GRACE + SECOND);
    assert capture.failure == null && capture.warnings.equals("1 fill with no fill price") : capture.warnings;
    capture.close();
    var fills = Files.readAllLines(file).stream().filter(s -> s.contains("\"type\":\"execution\"")).toList();
    assert fills.size() == 2 : fills.size();
    assert fills.get(0).contains("\"execution_id\":\"late-price\"") && fills.get(0).contains("\"price\":5001.25,")
        && fills.get(0).contains("\"quality\":\"" + PLAIN + "\"") && !fills.get(0).contains("price_known");
    assert fills.get(1).contains("\"execution_id\":\"no-price\"") && fills.get(1).contains("\"price_known\":false")
        && fills.get(1).contains("\"quality\":\"" + UNPRICED + "\"");
    assert fills.stream().allMatch(s -> s.contains("\"capture_kind\":\"new\"")) : "Both appeared after the baseline";
  }

  /** The app treats a fill that comes back different as a conflict, so a fill is always re-sent as it
   * was first written: across restarts, across an upgrade from study 0.2, and when MotiveWave later
   * finds the account after all. */
  private static void stableLabels(Path folder) throws Exception {
    var none = new AtomicReference<ReflectiveOperationException>();
    long clock = System.nanoTime();

    // First written by this version without a price: labelled, and labelled the same after a restart.
    var file = folder.resolve("labels.ndjson");
    var zero = List.of(raw("zero", "paper", "ESZ6", 1, 0, "USD", "USD"));
    var first = JournalAccountCapture.fixture(file, reader(zero, none));
    first.poll(clock);
    first.poll(clock + GRACE);
    first.close();
    var again = JournalAccountCapture.fixture(file, reader(zero, none));
    again.poll(clock); // written before: no second grace
    again.close();
    var sent = events(file);
    assert sent.size() == 2 && sent.get(0).equals(sent.get(1)) && sent.get(0).contains(UNPRICED) : sent;

    // Upgraded from study 0.2, which kept no labels and wrote this fill under the plain one: it stays plain.
    var legacy = folder.resolve("legacy.ndjson");
    var old = JournalAccountCapture.fixture(legacy, reader(zero, none));
    old.poll(clock);
    old.poll(clock + GRACE);
    old.close();
    Path state = legacy.resolveSibling("legacy.ndjson.capture-state");
    assert Files.readAllLines(state).stream().anyMatch(s -> s.startsWith("label."));
    Files.write(state, Files.readAllLines(state).stream().filter(s -> !s.startsWith("label.")).toList()); // as 0.2 left it
    for (int restart = 0; restart < 2; restart++) {
      var upgraded = JournalAccountCapture.fixture(legacy, reader(zero, none));
      upgraded.poll(clock);
      assert upgraded.failure == null && upgraded.warnings.equals("1 fill with no fill price")
          : "The heartbeat still says so, whatever the label";
      upgraded.close();
    }
    var resent = events(legacy);
    assert resent.size() == 3 && resent.get(1).equals(resent.get(2)) : resent;
    assert resent.get(1).contains("\"price\":0.0,") && resent.get(1).endsWith("\"quality\":\"" + PLAIN + "\"}")
        && !resent.get(1).contains("price_known") : "Re-sent exactly as study 0.2 wrote it: " + resent.get(1);

    // The account object turns up later (MotiveWave connected after the chart loaded): the fill was
    // written as assumed and stays that; nothing is written twice and nothing counts as changed.
    var found = folder.resolve("found.ndjson");
    var raws = new ArrayList<>(List.of(raw("orphan", "funded", "ESZ6", 1, 5000, null, "USD")));
    var before = JournalAccountCapture.fixture(found, reader(raws, none));
    before.poll(clock);
    assert before.warnings.equals("1 fill with an assumed currency");
    raws.set(0, raw("orphan", "funded", "ESZ6", 1, 5000, "USD", "USD"));
    before.poll(clock + SECOND);
    assert before.failure == null && before.warnings == null : before.warnings;
    before.close();
    var after = JournalAccountCapture.fixture(found, reader(raws, none));
    after.poll(clock);
    assert after.warnings == null;
    after.close();
    var orphan = events(found);
    assert orphan.size() == 2 && orphan.get(0).equals(orphan.get(1)) && orphan.get(0).contains(ASSUMED)
        && orphan.get(0).contains("\"currency_assumed\":true") : orphan;
  }

  private static JournalNativeAccess.Reader reader(List<Raw> raws, AtomicReference<ReflectiveOperationException> broken) {
    return () -> {
      if (broken.get() != null) throw broken.get();
      var skipped = new TreeMap<String,Integer>();
      var fills = new ArrayList<Fill>();
      for (Raw raw : List.copyOf(raws)) {
        Fill fill = JournalNativeAccess.describe(raw, skipped);
        if (fill != null) fills.add(fill);
      }
      return new JournalNativeAccess.Snapshot("workspace", List.copyOf(fills), List.of(), Map.copyOf(skipped));
    };
  }

  private static List<String> beats(List<String> lines) {
    return lines.stream().filter(s -> s.contains("\"type\":\"heartbeat\"")).toList();
  }

  /** The fills of a bridge file without the two fields that say when this copy was sent. */
  private static List<String> events(Path file) throws Exception {
    return Files.readAllLines(file).stream().filter(s -> s.contains("\"type\":\"execution\""))
        .map(s -> s.replaceAll(",\"observed_at\":\"[^\"]*\",\"capture_kind\":\"[^\"]*\"", "")).toList();
  }

  /** An ES fill as MotiveWave describes it; a null symbol, account currency or instrument currency is
   * the SDK instrument, the account object or the native instrument being missing. */
  private static Raw raw(String id, String accountId, String symbol, double qty, double price,
                         String accountCurrency, String instrumentCurrency) {
    return new Raw(id, accountId, symbol, true, qty, price, TIME, 2, 12.5, .25, accountCurrency, instrumentCurrency,
        false, "order-" + id, "position", true, false);
  }
}
