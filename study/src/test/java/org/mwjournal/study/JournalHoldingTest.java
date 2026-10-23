package org.mwjournal.study;

import java.nio.file.Files;
import java.util.ArrayList;
import java.util.List;
import java.util.concurrent.atomic.AtomicReference;

/** Which instruments have an open position, as the tick gate sees it. Fixtures only: no MotiveWave. */
public final class JournalHoldingTest {
  private JournalHoldingTest() {}
  private static final long TIME = 1641220200000L;
  private static int next;

  public static void main(String[] args) throws Exception {
    var folder = Files.createTempDirectory("mwj-holding-fixture-");
    var fills = new ArrayList<JournalNativeAccess.Fill>();
    var skipped = new AtomicReference<java.util.Map<String,Integer>>(java.util.Map.of());
    var broken = new AtomicReference<ReflectiveOperationException>();
    var capture = JournalAccountCapture.fixture(folder.resolve("motivewave.ndjson"), () -> {
      if (broken.get() != null) throw broken.get();
      return new JournalNativeAccess.Snapshot("workspace", List.copyOf(fills), List.of(), skipped.get());
    });
    capture.replayRun("ESZ6", TIME); // a replay chart is open, so replay fills are not left waiting

    // Before the first poll nothing is known, and unknown must record: fail open.
    assert capture.holding("ESZ6", false) && capture.holding("ESZ6", true) && capture.holding("anything", false);
    assert capture.status().equals("starting");

    // Flat: a successful poll with no fills is knowledge, so ticks may be skipped.
    capture.poll();
    assert capture.failure == null && capture.status().equals("connected");
    assert !capture.holding("ESZ6", false) && !capture.holding("ESZ6", true);
    assert !capture.holding(null, false) : "A chart without a symbol holds nothing";

    // Long, then scaled out to flat.
    fills.add(fill("paper", "ESZ6", true, 2, false));
    capture.poll();
    assert capture.holding("ESZ6", false);
    assert !capture.holding("NQZ6", false) : "Another instrument stays flat";
    assert !capture.holding("ESZ6", true) : "A live position is not a replay position";
    fills.add(fill("paper", "ESZ6", false, 1, false));
    capture.poll();
    assert capture.holding("ESZ6", false) : "Half out is still in";
    fills.add(fill("paper", "ESZ6", false, 1, false));
    capture.poll();
    assert !capture.holding("ESZ6", false) : "Scaled out to flat";

    // Reversal: through flat to short, then covered.
    fills.add(fill("paper", "ESZ6", true, 1, false));
    fills.add(fill("paper", "ESZ6", false, 2, false));
    capture.poll();
    assert capture.holding("ESZ6", false) : "Long 1 then sold 2 is short 1";
    fills.add(fill("paper", "ESZ6", true, 1, false));
    capture.poll();
    assert !capture.holding("ESZ6", false);

    // Two accounts are two books: opposite positions do not cancel, and either one holds.
    fills.add(fill("paper", "NQZ6", true, 1, false));
    fills.add(fill("funded", "NQZ6", false, 1, false));
    capture.poll();
    assert capture.holding("NQZ6", false) : "Long in one account and short in another is not flat";
    fills.add(fill("paper", "NQZ6", false, 1, false));
    capture.poll();
    assert capture.holding("NQZ6", false) : "The other account is still short";
    fills.add(fill("funded", "NQZ6", true, 1, false));
    capture.poll();
    assert !capture.holding("NQZ6", false);

    // Live and replay are separate books, even for the same account name and instrument.
    fills.add(fill("paper", "CLZ6", true, 1, true));
    capture.poll();
    assert capture.holding("CLZ6", true) && !capture.holding("CLZ6", false);
    fills.add(fill("paper", "CLZ6", false, 1, false));
    capture.poll();
    assert capture.holding("CLZ6", true) && capture.holding("CLZ6", false) : "A live sell does not close a replay long";
    fills.add(fill("paper", "CLZ6", false, 1, true));
    fills.add(fill("paper", "CLZ6", true, 1, false));
    capture.poll();
    assert !capture.holding("CLZ6", true) && !capture.holding("CLZ6", false);

    // Fractional quantities do not sum back to an exact zero; that is still flat.
    fills.add(fill("paper", "BTCUSD", true, 0.1, false));
    fills.add(fill("paper", "BTCUSD", true, 0.2, false));
    capture.poll();
    assert capture.holding("BTCUSD", false);
    fills.add(fill("paper", "BTCUSD", false, 0.3, false));
    capture.poll();
    assert !capture.holding("BTCUSD", false);
    assert capture.failure == null : capture.failure;

    // MotiveWave's position ids, as on a SIM account. Its oldest kept fill is the exit of a position whose
    // entry it no longer keeps: netting every fill would leave the contract short for good, and the gate
    // would record it all day while flat. Inside its own position that exit opens nothing.
    fills.add(fill("sim", "MNQZ6", false, 1, false, "SIM-1", false));
    capture.poll();
    assert !capture.holding("MNQZ6", false) : "An exit whose entry is not kept is not a position";
    fills.add(fill("sim", "MNQZ6", true, 1, false, "SIM-2", true));
    capture.poll();
    assert capture.holding("MNQZ6", false) : "A new position is held";
    fills.add(fill("sim", "MNQZ6", false, 1, false, "SIM-2", false));
    capture.poll();
    assert !capture.holding("MNQZ6", false) : "Flat within its own position, whatever the older one left";
    // A position whose exit MotiveWave did not keep is over once a newer position id appears.
    fills.add(fill("sim", "MNQZ6", true, 1, false, "SIM-3", true));
    fills.add(fill("sim", "MNQZ6", false, 1, false, "SIM-4", true));
    capture.poll();
    assert capture.holding("MNQZ6", false) : "The newest position is short";
    fills.add(fill("sim", "MNQZ6", true, 1, false, "SIM-4", false));
    capture.poll();
    assert !capture.holding("MNQZ6", false) : "Covered; the replaced long is not held";
    assert capture.failure == null : capture.failure;

    // The reader throws: positions are unknown again, so everything records until a poll succeeds.
    broken.set(new NoSuchMethodException("Fixture: MotiveWave update"));
    capture.poll();
    assert capture.failure.contains("NoSuchMethodException") && capture.status().equals("failing");
    assert capture.holding("ESZ6", false) && capture.holding("ESZ6", true) && capture.holding("NQZ6", false);
    broken.set(null);
    capture.poll();
    assert capture.failure == null && !capture.holding("ESZ6", false) : "A recovered adapter is trusted again";

    // A partial snapshot skipped some fills, so a net of zero proves nothing. The fills that were read
    // are still good, so the capture is not failing: it says what it skipped and the gate records.
    skipped.set(java.util.Map.of(JournalNativeAccess.NO_INSTRUMENT, 2));
    capture.poll();
    assert capture.failure == null && capture.status().equals("connected");
    assert capture.warnings.equals("2 fills skipped: instrument unavailable") : capture.warnings;
    assert capture.holding("ESZ6", false) && capture.holding("NQZ6", true);
    skipped.set(java.util.Map.of());
    capture.poll();
    assert capture.warnings == null && !capture.holding("ESZ6", false);

    // A stalled poller: what it last saw goes stale well inside the tick pre-roll, and stale is unknown.
    long now = System.nanoTime();
    assert !capture.holding("ESZ6", false, now);
    assert capture.holding("ESZ6", false, now + JournalAccountCapture.HOLDINGS_STALE_NANOS + 1_000_000_000L);
    assert JournalAccountCapture.HOLDINGS_STALE_NANOS < TickGate.PRE_ROLL_MILLIS * 1_000_000L
        : "A stalled poller must be noticed while the entry is still in the pre-roll";
    capture.close();

    // active() is what the tick gate asks: holding(), or a fill first seen within the last pre-roll.
    // The clock is the test's, so the tail can lapse without waiting for it.
    var book = new ArrayList<JournalNativeAccess.Fill>();
    book.add(fill("paper", "ESZ6", true, 1, false));
    book.add(fill("paper", "ESZ6", false, 1, false));
    var brokenToo = new AtomicReference<ReflectiveOperationException>();
    var scalper = JournalAccountCapture.fixture(folder.resolve("active.ndjson"), () -> {
      if (brokenToo.get() != null) throw brokenToo.get();
      return new JournalNativeAccess.Snapshot("workspace", List.copyOf(book), List.of());
    });
    long clock = System.nanoTime(), second = 1_000_000_000L;
    assert scalper.active("ESZ6", false, clock) && scalper.active("ESZ6", true, clock) : "Unknown is active as well";
    scalper.poll(clock);
    assert !scalper.active("ESZ6", false, clock) : "Fills that were already there are history, not activity";
    // Opened and closed between two polls: never a position, still a reason to write the pre-roll.
    book.add(fill("paper", "ESZ6", true, 1, false));
    book.add(fill("paper", "ESZ6", false, 1, false));
    scalper.poll(clock += second / 2);
    assert !scalper.holding("ESZ6", false, clock) && scalper.active("ESZ6", false, clock)
        : "A round trip between two polls must still arm the gate";
    assert !scalper.active("ESZ6", true, clock) && !scalper.active("NQZ6", false, clock) && !scalper.active(null, false, clock);
    scalper.poll(clock += JournalAccountCapture.TRADED_LINGER_NANOS - second);
    assert scalper.active("ESZ6", false, clock) : "Active for as long as the pre-roll reaches back";
    scalper.poll(clock += 2 * second);
    assert !scalper.active("ESZ6", false, clock) : "And no longer";
    // An ordinary trade: active while held, for a short tail after the exit, then quiet.
    book.add(fill("paper", "ESZ6", false, 2, false));
    scalper.poll(clock += second);
    assert scalper.holding("ESZ6", false, clock) && scalper.active("ESZ6", false, clock);
    scalper.poll(clock += 60 * second);
    assert scalper.active("ESZ6", false, clock) : "A held position stays active however long it lasts";
    book.add(fill("paper", "ESZ6", true, 2, false));
    scalper.poll(clock += second);
    assert !scalper.holding("ESZ6", false, clock) && scalper.active("ESZ6", false, clock) : "The tail after an exit";
    scalper.poll(clock += JournalAccountCapture.TRADED_LINGER_NANOS + second);
    assert !scalper.active("ESZ6", false, clock)
        : "Quiet after the short tail: the minutes after an exit come from the tick history, not from here";
    // A fill that arrives while the reader is down is noticed by the first poll that works again.
    brokenToo.set(new NoSuchMethodException("Fixture: outage"));
    scalper.poll(clock += second);
    assert scalper.active("ESZ6", false, clock) && scalper.active("NQZ6", true, clock) : "Unknown again";
    book.add(fill("paper", "NQZ6", true, 1, false));
    book.add(fill("paper", "NQZ6", false, 1, false));
    brokenToo.set(null);
    scalper.poll(clock += second);
    assert scalper.active("NQZ6", false, clock) && !scalper.active("ESZ6", false, clock);
    assert scalper.failure == null : scalper.failure;
    scalper.close();

    // A problem that is not about reading fills does not blind the gate: replay fills waiting for a
    // replay chart are still counted as a position.
    var waitingFills = List.of(fill("REPLAY-1", "ESZ6", true, 1, true));
    var waiting = JournalAccountCapture.fixture(folder.resolve("waiting.ndjson"),
        () -> new JournalNativeAccess.Snapshot("workspace", waitingFills, List.of()));
    waiting.poll();
    assert waiting.failure == null && waiting.warnings.contains("waiting for a replay chart") : waiting.warnings;
    assert waiting.holding("ESZ6", true) && !waiting.holding("ESZ6", false) && !waiting.holding("NQZ6", true);
    waiting.close();

    // Replay runs are separate books. MotiveWave keeps the fills of earlier runs, so a long abandoned in
    // one run must not cancel the short taken in the next, or that trade would be recorded as flat.
    var runs = new ArrayList<JournalNativeAccess.Fill>();
    var rerun = JournalAccountCapture.fixture(folder.resolve("runs.ndjson"),
        () -> new JournalNativeAccess.Snapshot("workspace", List.copyOf(runs), List.of()));
    String firstRun = rerun.replayRun("ESZ6", TIME + 60000);
    rerun.poll();
    runs.add(fill("REPLAY-1", "ESZ6", true, 1, true));
    rerun.poll();
    assert rerun.holding("ESZ6", true);
    assert !rerun.replayRun("ESZ6", TIME).equals(firstRun) : "The fixture must rewind into a new run";
    runs.add(fill("REPLAY-1", "ESZ6", false, 1, true));
    rerun.poll();
    assert rerun.holding("ESZ6", true) : "Short in this run is a position, whatever an earlier run left open";
    runs.add(fill("REPLAY-1", "ESZ6", true, 1, true));
    rerun.poll();
    assert rerun.holding("ESZ6", true) : "Covered here, but the abandoned long keeps recording: the safe side";
    assert !rerun.holding("NQZ6", true) && !rerun.holding("ESZ6", false) && rerun.failure == null : rerun.failure;
    // Within one run a round trip is flat as before.
    var oneRun = new ArrayList<JournalNativeAccess.Fill>();
    var single = JournalAccountCapture.fixture(folder.resolve("one-run.ndjson"),
        () -> new JournalNativeAccess.Snapshot("workspace", List.copyOf(oneRun), List.of()));
    single.replayRun("ESZ6", TIME);
    oneRun.add(fill("REPLAY-1", "ESZ6", false, 2, true));
    single.poll();
    assert single.holding("ESZ6", true);
    oneRun.add(fill("REPLAY-1", "ESZ6", true, 2, true));
    single.poll();
    assert !single.holding("ESZ6", true) : "Flat within its own run";
    rerun.close();
    single.close();
    try (var left = Files.walk(folder)) {
      for (var path : left.sorted(java.util.Comparator.reverseOrder()).toList()) Files.delete(path);
    }
    System.out.println("Journal holding passed: flat, long, scale-out, reversal, two accounts, live versus replay, "
        + "fractions, MotiveWave position ids, replay runs apart, a trade between two polls, the tail after an exit, and failing open "
        + "before the first poll, on a broken, partial or stalled reader.");
  }

  private static JournalNativeAccess.Fill fill(String account, String symbol, boolean buy, double qty, boolean replay) {
    return fill(account, symbol, buy, qty, replay, "", buy);
  }
  /** position is MotiveWave's position id ("" for none) and entry whether it calls the fill an entry of it. */
  private static JournalNativeAccess.Fill fill(String account, String symbol, boolean buy, double qty, boolean replay,
                                               String position, boolean entry) {
    String id = "fill-" + next++;
    return new JournalNativeAccess.Fill(id, account, symbol, buy, qty, 5000, TIME + next * 1000L, 2, 50, "USD",
        "order-" + id, position, entry, replay);
  }
}
