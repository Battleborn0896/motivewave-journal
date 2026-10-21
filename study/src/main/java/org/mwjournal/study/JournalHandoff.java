package org.mwjournal.study;

/** One copy of the study writes the journal at a time, and the newest takes over.
 *
 * When its jar changes, MotiveWave loads the new copy beside the old one in the same Java process, and the old
 * copy keeps running: Java cannot unload it. So every copy keeps a guard thread named NAME. The first time a copy
 * opens the journal it interrupts the guard of every other copy and waits for it to end, and a guard that is
 * interrupted retires its own copy first: its fill reader stops, its writers write what they hold and let go of
 * their files, and its charts record nothing more. Threads are what every copy in the process can see, so nothing
 * else is shared. A copy from before 0.3.6 has no guard and keeps the journal until MotiveWave restarts. */
final class JournalHandoff {
  static final String NAME = "mwj-study-copy";
  /** Longer than a retiring copy needs: its last poll, then each writer's last flush (two seconds at most). */
  static final long WAIT_MILLIS = 10_000;
  private static Thread guard;
  private static volatile boolean retired;

  private JournalHandoff() {}

  /** True once a newer copy of the study has taken over from this one. */
  static boolean retired() { return retired; }

  /** Retires every other copy of the study in this process and stands guard for this one. Once per copy; the
   * calls after the first return at once. */
  static synchronized void takeOver() {
    if (guard != null || retired) return;
    for (Thread other : Thread.getAllStackTraces().keySet()) {
      if (!NAME.equals(other.getName())) continue;
      other.interrupt();
      try { other.join(WAIT_MILLIS); }
      catch (InterruptedException exc) { Thread.currentThread().interrupt(); return; }
    }
    guard = new Thread(JournalHandoff::stand, NAME);
    guard.setDaemon(true);
    guard.start();
  }

  private static void stand() {
    try { Thread.sleep(Long.MAX_VALUE); }
    catch (InterruptedException exc) { retire(); }
  }

  /** This copy steps aside for a newer one: nothing more is written, and every file is let go. */
  static void retire() {
    retired = true;
    JournalAccountCapture.closeAll();
    JournalEventWriter.closeAll();
  }
}
