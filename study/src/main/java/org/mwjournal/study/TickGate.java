package org.mwjournal.study;

import java.util.ArrayDeque;
import java.util.ArrayList;
import java.util.List;
import java.util.function.Predicate;

/** Decides which trade prints are worth writing: none while flat, all of them while a position is open.
 *
 * The account poller notices an entry fill up to half a second after it happened, so the prints around
 * the entry have already gone by when the gate is armed. A short pre-roll is therefore kept in memory
 * and written first. It is bounded twice, by age and by count, so a fast market or a fast replay cannot
 * grow it: at most the cap, and never more than the window before the last print that was held back.
 * Pure: no MotiveWave types, no I/O, and the caller supplies the clock. */
final class TickGate<T> {
  static final long PRE_ROLL_MILLIS = 15_000;
  static final int PRE_ROLL_CAP = 20_000;

  private record Slot<T>(T event, long time) {}
  private final ArrayDeque<Slot<T>> buffer = new ArrayDeque<>();
  private final long window;
  private final int cap;

  TickGate() { this(PRE_ROLL_MILLIS, PRE_ROLL_CAP); }

  TickGate(long windowMillis, int cap) {
    if (windowMillis < 0 || cap < 0) throw new IllegalArgumentException("Negative pre-roll.");
    this.window = windowMillis;
    this.cap = cap;
  }

  /** The events to write now, oldest first. Armed: the whole pre-roll, then this event, and the buffer
   * is emptied. Not armed: nothing; the event is kept and those older than the window are dropped.
   *
   * Age is measured only between prints that were not written. The first armed print can come long
   * after the entry (a quiet tape, or a replay stepped bar by bar with a pause for thought), and the
   * prints buffered just before it are then the ones around the entry, however old the clock says they are. */
  synchronized List<T> offer(T event, long timeMillis, boolean armed) {
    // A clock that steps backwards is another timeline (a rewound replay); nothing buffered belongs to it.
    if (!buffer.isEmpty() && timeMillis < buffer.peekLast().time()) buffer.clear();
    if (!armed) {
      while (!buffer.isEmpty() && timeMillis - buffer.peekFirst().time() > window) buffer.pollFirst();
      if (cap == 0) return List.of();
      if (buffer.size() >= cap) buffer.pollFirst();
      buffer.addLast(new Slot<>(event, timeMillis));
      return List.of();
    }
    if (buffer.isEmpty()) return List.of(event); // The usual case while a position is open.
    var due = new ArrayList<T>(buffer.size() + 1);
    for (var slot : buffer) due.add(slot.event());
    due.add(event);
    buffer.clear();
    return due;
  }

  /** An account poll can notice a complete trade after its last print, particularly when replay is
   * paused. Drain then, instead of waiting for a print which may never arrive. A flat book leaves
   * the pre-roll intact. Held events carry their own source/symbol, not current chart settings. */
  synchronized List<T> drainIf(Predicate<T> armed) {
    if (buffer.isEmpty() || !armed.test(buffer.peekLast().event())) return List.of();
    var due = new ArrayList<T>(buffer.size());
    for (var slot : buffer) due.add(slot.event());
    buffer.clear();
    return due;
  }

  /** Forget the pre-roll, for when what it was recorded under (symbol, file, replay run) no longer holds. */
  synchronized void clear() { buffer.clear(); }

  synchronized int size() { return buffer.size(); }
}
