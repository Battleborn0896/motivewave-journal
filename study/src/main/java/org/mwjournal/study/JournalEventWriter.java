package org.mwjournal.study;

import java.io.IOException;
import java.nio.ByteBuffer;
import java.nio.channels.FileChannel;
import java.nio.channels.FileLock;
import java.nio.channels.OverlappingFileLockException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.StandardOpenOption;
import java.time.Instant;
import java.util.HashMap;
import java.util.Map;
import java.util.concurrent.ArrayBlockingQueue;
import java.util.concurrent.TimeUnit;

/** One append writer per path across chart instances. All filesystem I/O runs off the tick callback. */
final class JournalEventWriter {
  /** Why a writer failed when another copy of the study holds its file, word for word in MotiveWave's log
   * and in the heartbeat. Usually MotiveWave loaded a new jar without a full restart and the old copy is
   * still running in the same Java process. */
  static final String ANOTHER_COPY = "Another copy of the MotiveWave Journal study is still running: "
      + "quit MotiveWave completely and open it again.";
  private static final Map<Path, JournalEventWriter> WRITERS = new HashMap<>();
  private final Path path;
  private final ArrayBlockingQueue<String> queue = new ArrayBlockingQueue<>(65536);
  private final Map<String, DropWindow> dropped = new HashMap<>();
  final Thread thread;
  volatile IOException failure;
  private volatile boolean running = true;
  private int references;

  private JournalEventWriter(Path path) {
    this.path = path;
    thread = new Thread(this::run, "mwj-writer");
    thread.setDaemon(true);
  }

  static JournalEventWriter acquire(Path requested) throws IOException {
    if (!requested.isAbsolute()) throw new IOException("Choose an absolute journal export path.");
    Path path = requested.normalize();
    synchronized (WRITERS) {
      JournalEventWriter writer = WRITERS.get(path);
      if (writer == null) {
        writer = new JournalEventWriter(path);
        WRITERS.put(path, writer);
        writer.thread.start();
      }
      if (!writer.running) throw new IOException("Previous journal writer is still closing; retry shortly.");
      if (writer.failure != null) throw writer.failure;
      writer.references++;
      return writer;
    }
  }

  boolean offer(String event) {
    return offer(event, "motivewave-ticks", Instant.now().toString());
  }

  boolean offer(String event, String provider, String timestamp) {
    if (accept(event)) return true;
    drop(provider, Instant.parse(timestamp));
    return false;
  }

  /** The same for the tick path, which has the time as a number: no text is built unless a print is dropped. */
  boolean offer(String event, String provider, long occurredMillis) {
    if (accept(event)) return true;
    drop(provider, Instant.ofEpochMilli(occurredMillis));
    return false;
  }

  private boolean accept(String event) {
    if (!running || failure != null) throw new IllegalStateException("Journal capture is not running.");
    return queue.offer(event);
  }

  /** For bulk work that can wait, such as loading history: the event is taken only while fewer than
   * limit are waiting to be written. False is not a drop and no gap is recorded; the caller holds on to
   * the event and offers it again. Keeping limit below the queue's size leaves room for live prints,
   * which cannot wait. */
  boolean offerBelow(String event, int limit) {
    if (!running || failure != null) throw new IllegalStateException("Journal capture is not running.");
    return queue.size() < limit && queue.offer(event);
  }

  /** A gap is an event the queue had no room for. A print the study chose not to record never gets here. */
  private void drop(String provider, Instant occurred) {
    synchronized (dropped) {
      var gap = dropped.computeIfAbsent(provider, key -> new DropWindow());
      gap.count++;
      if (gap.first == null || occurred.isBefore(gap.first)) gap.first = occurred;
      if (gap.last == null || occurred.isAfter(gap.last)) gap.last = occurred;
    }
  }

  private static final class DropWindow { long count; Instant first, last; }

  /** The line that tells the app events were lost: how many, from which source, over which stretch of time. */
  static String gap(String provider, long count, Instant first, Instant last) {
    return gap(provider, null, count, first, last);
  }

  /** The same for a loss that concerns one instrument only; symbol is null when it concerns them all. */
  static String gap(String provider, String symbol, long count, Instant first, Instant last) {
    return "{\"version\":1,\"type\":\"gap\",\"provider\":" + JournalPriceEvents.quote(provider)
        + (symbol == null ? "" : ",\"symbol\":" + JournalPriceEvents.quote(symbol))
        + ",\"ts\":\"" + Instant.now() + "\",\"dropped\":" + count
        + ",\"start_ts\":" + JournalPriceEvents.quote(first.toString())
        + ",\"end_ts\":" + JournalPriceEvents.quote(last.toString()) + "}";
  }

  private static boolean endsMidLine(Path path) throws IOException {
    if (!Files.isRegularFile(path)) return false;
    try (FileChannel channel = FileChannel.open(path, StandardOpenOption.READ)) {
      if (channel.size() == 0) return false;
      ByteBuffer last = ByteBuffer.allocate(1);
      return channel.read(last, channel.size() - 1) == 1 && last.get(0) != '\n';
    }
  }

  private boolean hasDrops() { synchronized (dropped) { return !dropped.isEmpty(); } }

  /** The bridge's lock, or null while someone else holds it. Only this study takes that lock, so that is
   * another copy of the study: in another process tryLock returns null, in this one (an old copy that
   * MotiveWave did not unload) it throws. */
  private static FileLock tryLock(FileChannel channel) throws IOException {
    try { return channel.tryLock(); }
    catch (OverlappingFileLockException exc) { return null; }
  }

  /** True for the failure of a writer whose file another copy of the study holds. */
  static boolean anotherCopy(Throwable exc) {
    return exc instanceof IOException && ANOTHER_COPY.equals(exc.getMessage());
  }

  private void run() {
    try {
      Files.createDirectories(path.getParent());
      // Also reject a second writer of the same bridge. The harmless .lock file stays on disk.
      try (FileChannel channel = FileChannel.open(path.resolveSibling(path.getFileName() + ".lock"),
                                                  StandardOpenOption.CREATE, StandardOpenOption.WRITE)) {
        try (FileLock lock = tryLock(channel)) {
          if (lock == null) throw new IOException(ANOTHER_COPY);
          boolean midLine = endsMidLine(path);
          try (var file = Files.newBufferedWriter(path, StandardCharsets.UTF_8,
                                                  StandardOpenOption.CREATE, StandardOpenOption.APPEND)) {
            // A run that was killed in the middle of a write left half an event. Finish its line, or this
            // run's first event is glued to it and the reader has to throw both away.
            if (midLine) file.newLine();
            long lastFlush = 0;
            while (running || !queue.isEmpty() || hasDrops()) {
              String event = queue.poll(100, TimeUnit.MILLISECONDS);
              if (event != null) { file.write(event); file.newLine(); }
              Map<String, DropWindow> gaps;
              synchronized (dropped) { gaps = new HashMap<>(dropped); dropped.clear(); }
              for (var entry : gaps.entrySet()) {
                var gap = entry.getValue();
                file.write(gap(entry.getKey(), gap.count, gap.first, gap.last));
                file.newLine();
              }
              if (System.currentTimeMillis() - lastFlush >= 1000) {
                file.flush();
                lastFlush = System.currentTimeMillis();
              }
            }
            file.flush();
          }
        }
      }
    } catch (IOException | RuntimeException exc) {
      failure = exc instanceof IOException io ? io : new IOException("Journal capture failed.", exc);
    } catch (InterruptedException exc) {
      Thread.currentThread().interrupt();
      failure = new IOException("Writer interrupted; check capture completeness.", exc);
    } finally {
      synchronized (WRITERS) {
        if (references == 0) WRITERS.remove(path, this);
      }
    }
  }

  void release() {
    synchronized (WRITERS) {
      if (references <= 0) return;
      if (--references > 0) return;
      running = false;
    }
    try { thread.join(2000); }
    catch (InterruptedException exc) { Thread.currentThread().interrupt(); }
    synchronized (WRITERS) {
      if (!thread.isAlive()) WRITERS.remove(path, this);
    }
  }
}
