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
      System.out.println("Journal writer passed: 16,000 concurrent events, shared-chart lifetime, restart, file lock, "
          + "another copy of the study told apart, and a clean line after a crash.");
    } finally {
      Files.deleteIfExists(path);
      Files.deleteIfExists(lockPath);
      Files.deleteIfExists(cut);
      Files.deleteIfExists(folder.resolve("cut.ndjson.lock"));
      Files.deleteIfExists(folder);
    }
  }

  private static String event(int chart, int tick) {
    return "{\"type\":\"tick\",\"id\":\"" + chart + ":" + tick + "\",\"padding\":\"" + "x".repeat(256) + "\"}";
  }
}
