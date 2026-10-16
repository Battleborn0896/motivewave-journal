package org.mwjournal.study;

/** What happens to a streaming trade print between MotiveWave's tick callback and the writer. It lives
 * outside the study class so it can be exercised without a MotiveWave session. Memory only: the caller
 * is the tick thread, and nothing here touches a file or waits for the account poller. */
final class JournalTicks {
  /** The stored values of the 'Record Ticks' setting. */
  static final String IN_POSITION = "POSITION", ALWAYS = "ALWAYS", NEVER = "NEVER";

  /** One print waiting at a gate, with everything it will be written under. */
  record Held(String symbol, boolean proxy, String source, JournalPriceEvents.Print print,
              String instrumentSymbol, String activeSymbol, int postExitMinutes) {
    Held(String symbol, boolean proxy, String source, JournalPriceEvents.Print print) {
      this(symbol, proxy, source, print, symbol, symbol, 0);
    }
    Held(String symbol, boolean proxy, String source, JournalPriceEvents.Print print,
         String instrumentSymbol, String activeSymbol) {
      this(symbol, proxy, source, print, instrumentSymbol, activeSymbol, 0);
    }
  }

  static boolean armed(JournalAccountCapture observer, String record, boolean replay, Held held) {
    if (observer == null || !IN_POSITION.equals(record)) return true;
    long now = System.nanoTime();
    return observer.recording(held.instrumentSymbol(), replay, held.print().time(), held.postExitMinutes(), now)
        || observer.recording(held.activeSymbol(), replay, held.print().time(), held.postExitMinutes(), now)
        || observer.recording(held.symbol(), replay, held.print().time(), held.postExitMinutes(), now);
  }

  private JournalTicks() {}

  /** Whether prints are written right now. Only a book known to be flat skips them: 'Always', a stored
   * value this version does not know, and fill capture switched off or unable to start all record.
   *
   * Fills carry their instrument's own symbol. The chart can show that instrument under a continuous
   * alias, and a proxy chart names the traded contract in the symbol setting, so every name the fills
   * could be under is asked for and a position under any of them records. */
  static boolean armed(JournalAccountCapture observer, String record, boolean replay,
                       String instrumentSymbol, String activeSymbol, String journalSymbol) {
    if (observer == null || !IN_POSITION.equals(record)) return true;
    return observer.active(instrumentSymbol, replay) || observer.active(activeSymbol, replay)
        || observer.active(journalSymbol, replay);
  }

  /** Writes what the gate lets through: nothing while flat, the pre-roll and this print once the book
   * is active. A print that is skipped never reaches the writer, so it can never be counted as a gap. */
  static void record(TickGate<Held> gate, JournalEventWriter target, Held held, String mode, boolean armed) {
    // The gate runs on arrival time, not tick time: the poller's delay is wall-clock time, and a replay
    // can pack minutes of prints into the half second the poller needs to see an entry.
    for (Held due : gate.offer(held, System.nanoTime() / 1_000_000, armed))
      target.offer(JournalPriceEvents.tick(due.symbol(), due.print(), due.proxy(), due.source(), mode),
          due.source(), due.print().time());
  }

  /** Called off the chart thread when a snapshot arms a gate. No future tick is required to release
   * prices already observed. The same alias checks apply as in the chart callback. */
  static void flush(TickGate<Held> gate, JournalEventWriter target, JournalAccountCapture observer,
                    boolean replay, long nowNanos) {
    for (Held due : gate.drainIf(held -> observer.recording(held.instrumentSymbol(), replay, held.print().time(), held.postExitMinutes(), nowNanos)
        || observer.recording(held.activeSymbol(), replay, held.print().time(), held.postExitMinutes(), nowNanos)
        || observer.recording(held.symbol(), replay, held.print().time(), held.postExitMinutes(), nowNanos)))
      target.offer(JournalPriceEvents.tick(due.symbol(), due.print(), due.proxy(), due.source(),
          replay ? "replay" : "live"), due.source(), due.print().time());
  }
}
