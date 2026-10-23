package org.mwjournal.study;

/** What happens to a streaming trade print between MotiveWave's tick callback and the writer. It lives
 * outside the study class so it can be exercised without a MotiveWave session. Memory only: the caller
 * is the tick thread, and nothing here touches a file or waits for the account poller. */
final class JournalTicks {
  /** One print waiting at a gate, with everything it will be written under, and the names a position in
   * its instrument can be under (see armed). */
  record Held(String symbol, String source, JournalPriceEvents.Print print, String instrumentSymbol,
              String activeSymbol) {
    Held(String symbol, String source, JournalPriceEvents.Print print) {
      this(symbol, source, print, symbol, symbol);
    }
  }

  static boolean armed(JournalAccountCapture observer, boolean replay, Held held) {
    return armed(observer, replay, held.instrumentSymbol(), held.activeSymbol());
  }

  private JournalTicks() {}

  /** Whether prints are written right now: during trades only. Only a book known to be flat skips them; while
   * fills cannot be read every print is recorded. A book is flat a few seconds after the exit
   * (JournalAccountCapture.active); the minutes after it are loaded from MotiveWave's tick history instead
   * (JournalBackfill), never recorded here.
   *
   * Fills carry their instrument's own symbol. The chart can show that instrument under a continuous alias,
   * so both names are asked for: the chart instrument's own symbol and its active contract. */
  static boolean armed(JournalAccountCapture observer, boolean replay, String instrumentSymbol, String activeSymbol) {
    if (observer == null) return true;
    return observer.active(instrumentSymbol, replay) || observer.active(activeSymbol, replay);
  }

  /** Writes what the gate lets through: nothing while flat, the pre-roll and this print once the book
   * is active. A print that is skipped never reaches the writer, so it can never be counted as a gap. */
  static void record(TickGate<Held> gate, JournalEventWriter target, Held held, String mode, boolean armed) {
    // The gate runs on arrival time, not tick time: the poller's delay is wall-clock time, and a replay
    // can pack minutes of prints into the half second the poller needs to see an entry.
    for (Held due : gate.offer(held, System.nanoTime() / 1_000_000, armed))
      target.offer(JournalPriceEvents.tick(due.symbol(), due.print(), due.source(), mode), due.source(),
          due.print().time());
  }

  /** Called off the chart thread when a snapshot arms a gate. No future tick is required to release
   * prices already observed. The same names are asked for as in the chart callback. */
  static void flush(TickGate<Held> gate, JournalEventWriter target, JournalAccountCapture observer,
                    boolean replay, long nowNanos) {
    for (Held due : gate.drainIf(held -> observer.active(held.instrumentSymbol(), replay, nowNanos)
        || observer.active(held.activeSymbol(), replay, nowNanos)))
      target.offer(JournalPriceEvents.tick(due.symbol(), due.print(), due.source(), replay ? "replay" : "live"),
          due.source(), due.print().time());
  }
}
