package org.mwjournal.study;

import java.nio.channels.FileChannel;
import java.nio.file.Files;
import java.nio.file.StandardOpenOption;
import java.util.HashSet;

/** Exercises the actual asynchronous writer without a live broker or MotiveWave UI. */
public final class JournalWriterTest {
  private JournalWriterTest() {}

  public static void main(String[] args) throws Exception {
    var folder = Files.createTempDirectory("mwj-writer-test-");
    var path = folder.resolve("ticks.ndjson");
    var lockPath = folder.resolve("ticks.ndjson.lock");
    var cut = folder.resolve("cut.ndjson");
    try {
      var first = JournalEventWriter.acquire(path);
      var second = JournalEventWriter.acquire(path);
      assert first == second : "Charts sharing a path must share one writer";
      var expected = new HashSet<String>();
      var producers = new Thread[4];
      for (int i = 0; i < producers.length; i++) {
        final int chart = i;
        for (int tick = 0; tick < 4000; tick++) expected.add(event(chart, tick));
        producers[i] = new Thread(() -> {
          for (int tick = 0; tick < 4000; tick++) first.offer(event(chart, tick));
        });
        producers[i].start();
      }
      for (var producer : producers) producer.join();
      first.release();
      String stillOpen = event(9, 9);
      expected.add(stillOpen);
      second.offer(stillOpen);
      second.release();
      assert !second.thread.isAlive() : "Final release must drain and close";
      assert second.failure == null : second.failure;
      var lines = Files.readAllLines(path);
      assert lines.size() == expected.size() : "Lost or interleaved JSON events";
      assert new HashSet<>(lines).equals(expected) : "Every line must remain intact across chart producers";

      var reopened = JournalEventWriter.acquire(path);
      reopened.offer(event(10, 10));
      reopened.release();
      assert reopened.failure == null;
      assert Files.readAllLines(path).size() == expected.size() + 1 : "Restart must append, not overwrite";

      try (var channel = FileChannel.open(lockPath, StandardOpenOption.WRITE);
           var lock = channel.lock()) {
        assert lock.isValid();
        var blocked = JournalEventWriter.acquire(path);
        blocked.thread.join(3000);
        assert blocked.failure != null : "A competing process must not corrupt the bridge";
        // Held in this Java process: what an old copy of the study that MotiveWave did not unload looks like.
        assert blocked.failure.getMessage().equals("Another copy of the MotiveWave Journal study is still running: "
            + "quit MotiveWave completely and open it again.") : blocked.failure;
        assert JournalEventWriter.anotherCopy(blocked.failure);
        assert !JournalEventWriter.anotherCopy(new java.io.IOException("Disk full"));
        blocked.release();
        assert Files.readAllLines(path).size() == expected.size() + 1;
      }

      // A run killed in the middle of a write leaves half an event. The next run must start on a line of
      // its own, so the half line is the only thing the reader has to discard.
      Files.writeString(cut, event(1, 1) + System.lineSeparator() + "{\"type\":\"tick\",\"id\":\"cut sh");
      var afterCrash = JournalEventWriter.acquire(cut);
      afterCrash.offer(event(2, 2));
      afterCrash.release();
      assert afterCrash.failure == null : afterCrash.failure;
      assert Files.readAllLines(cut).equals(java.util.List.of(event(1, 1), "{\"type\":\"tick\",\"id\":\"cut sh", event(2, 2)))
          : "The first event after a crash must not be glued to the fragment before it";
      var again = JournalEventWriter.acquire(cut);
      again.offer(event(3, 3));
      again.release();
      assert Files.readAllLines(cut).size() == 4 : "A file that ends cleanly gets no blank line";

      String gap = JournalEventWriter.gap("motivewave-ticks", 3, java.time.Instant.parse("2026-10-07T13:50:00Z"),
          java.time.Instant.parse("2026-10-07T13:50:10.250Z"));
      assert gap.contains("\"type\":\"gap\"") && gap.contains("\"provider\":\"motivewave-ticks\"") && gap.contains("\"dropped\":3")
          && gap.contains("\"start_ts\":\"2026-10-07T13:50:00Z\"") && gap.contains("\"end_ts\":\"2026-10-07T13:50:10.250Z\"");
      receipts(folder);
      System.out.println("Journal writer passed: 16,000 concurrent events, shared-chart lifetime, restart, file lock, "
          + "another copy of the study told apart, a clean line after a crash, and a file emptied only once the app's "
          + "receipt says it has read all of it.");
    } finally {
      Files.deleteIfExists(path);
      Files.deleteIfExists(lockPath);
      Files.deleteIfExists(cut);
      Files.deleteIfExists(folder.resolve("cut.ndjson.lock"));
      for (String name : new String[]{"receipt.ndjson", "receipt.ndjson.lock", "receipt.ndjson.read"}) {
        Files.deleteIfExists(folder.resolve(name));
      }
      Files.deleteIfExists(folder);
    }
  }

  /** The app writes `<file>.read` once it has stored what it read (docs/protocol.md, Read receipt): the position, and
   * the 256 bytes before it in base64. The writer empties the file only when that is all of it and it is over 1 MB. */
  private static void receipts(java.nio.file.Path folder) throws Exception {
    JournalEventWriter.receiptMillis = 20;
    var file = folder.resolve("receipt.ndjson");
    var receipt = folder.resolve("receipt.ndjson.read");
    var writer = JournalEventWriter.acquire(file);
    var expected = fill(writer, file, 0, 4500);
    long size = Files.size(file);
    assert size > JournalEventWriter.EMPTY_ABOVE : size;
    byte[] tail = java.util.Arrays.copyOfRange(Files.readAllBytes(file), (int) size - 256, (int) size);
    byte[] other = tail.clone();
    other[100] ^= 1;
    var encoder = java.util.Base64.getEncoder();
    // Not read to its end, read to another file's end, or a receipt that cannot be read: the file stays as it is.
    for (String stale : new String[]{null, "", "\n", (size - 300) + "\n" + encoder.encodeToString(tail) + "\n",
                                     size + "\n" + encoder.encodeToString(other) + "\n", size + "\n!!!\n",
                                     size + "\n" + encoder.encodeToString(java.util.Arrays.copyOf(tail, 100)) + "\n",
                                     "lots\n" + encoder.encodeToString(tail) + "\n"}) {
      if (stale == null) Files.deleteIfExists(receipt); else Files.writeString(receipt, stale);
      Thread.sleep(150);
      assert Files.size(file) == size : "A receipt that does not name every byte must not empty the file: " + stale;
    }
    // A copy that a newer one is taking over from never empties a file, even one read to its end.
    var retired = JournalHandoff.class.getDeclaredField("retired");
    retired.setAccessible(true);
    retired.setBoolean(null, true);
    Files.writeString(receipt, size + "\n" + encoder.encodeToString(tail) + "\n");
    Thread.sleep(150);
    assert Files.size(file) == size : "A retired copy must not empty the file";
    retired.setBoolean(null, false);
    waitFor(() -> size(file) == 0, "a file read to its end is emptied");
    // Writing goes on, from the start of the file, and an event offered as the file is emptied is never lost.
    writer.offer(event(30, 0));
    waitFor(() -> lines(file).equals(java.util.List.of(event(30, 0))), "the next event lands at the start");
    expected = fill(writer, file, 31, 4500);
    Files.writeString(receipt, receiptFor(file));
    writer.offer(event(40, 0));                   // at the moment the receipt is looked at
    waitFor(() -> lines(file).contains(event(40, 0)), "the racing event is written");
    var after = lines(file);
    expected.add(event(40, 0));
    assert after.equals(java.util.List.of(event(40, 0))) || after.equals(expected) : "Lost or reordered: " + after.size();
    if (!after.equals(java.util.List.of(event(40, 0)))) {       // written first: emptied at a later look
      Files.writeString(receipt, receiptFor(file));
      waitFor(() -> size(file) == 0, "a file read to its end again is emptied");
      writer.offer(event(40, 1));
      waitFor(() -> lines(file).equals(java.util.List.of(event(40, 1))), "the next event lands at the start");
    }
    // Under 1 MB a receipt read to the end leaves the file as it is.
    Files.writeString(receipt, receiptFor(file));
    Thread.sleep(150);
    assert size(file) > 0 : "A small file is not emptied";
    // Emptying fails (the file cannot be opened to write): nothing changes and the writer keeps appending to it.
    if (file.getFileSystem().supportedFileAttributeViews().contains("posix")) {
      fill(writer, file, 50, 4500);
      var before = lines(file);
      Files.writeString(receipt, receiptFor(file));
      var permissions = Files.getPosixFilePermissions(file);
      Files.setPosixFilePermissions(file, java.nio.file.attribute.PosixFilePermissions.fromString("r--r--r--"));
      try {
        Thread.sleep(150);
        assert lines(file).equals(before) : "A file that cannot be emptied stays whole";
        writer.offer(event(60, 0));
        waitFor(() -> lines(file).size() == before.size() + 1, "an event after a failed emptying");
        assert lines(file).get(before.size()).equals(event(60, 0));
      } finally {
        Files.setPosixFilePermissions(file, permissions);
      }
    }
    writer.release();
    assert writer.failure == null : writer.failure;
  }

  /** Offers `count` events of chart `chart` and waits until all are on disk; returns the file's lines then. */
  private static java.util.List<String> fill(JournalEventWriter writer, java.nio.file.Path file, int chart, int count)
      throws Exception {
    int already = lines(file).size();
    for (int tick = 0; tick < count; tick++) {
      while (!writer.offer(event(chart, tick))) Thread.sleep(1);
    }
    waitFor(() -> lines(file).size() == already + count, "every event written");
    return new java.util.ArrayList<>(lines(file));
  }

  private static String receiptFor(java.nio.file.Path file) throws java.io.IOException {
    byte[] bytes = Files.readAllBytes(file);
    byte[] tail = java.util.Arrays.copyOfRange(bytes, Math.max(0, bytes.length - 256), bytes.length);
    return bytes.length + "\n" + java.util.Base64.getEncoder().encodeToString(tail) + "\n";
  }

  private static long size(java.nio.file.Path file) {
    try { return Files.size(file); } catch (java.io.IOException exc) { return -1; }
  }

  private static java.util.List<String> lines(java.nio.file.Path file) {
    try { return Files.isRegularFile(file) ? Files.readAllLines(file) : java.util.List.of(); }
    catch (java.io.IOException exc) { return java.util.List.of(); }
  }

  private static void waitFor(java.util.function.BooleanSupplier done, String what) throws InterruptedException {
    long until = System.currentTimeMillis() + 10_000;
    while (!done.getAsBoolean()) {
      if (System.currentTimeMillis() > until) throw new AssertionError("Timed out: " + what);
      Thread.sleep(10);
    }
  }

  private static String event(int chart, int tick) {
    return "{\"type\":\"tick\",\"id\":\"" + chart + ":" + tick + "\",\"padding\":\"" + "x".repeat(256) + "\"}";
  }
}
