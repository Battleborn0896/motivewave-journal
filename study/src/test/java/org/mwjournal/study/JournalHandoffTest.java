package org.mwjournal.study;

import java.io.IOException;
import java.lang.reflect.Field;
import java.lang.reflect.InvocationTargetException;
import java.lang.reflect.Method;
import java.lang.reflect.Proxy;
import java.net.URL;
import java.net.URLClassLoader;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.List;
import java.util.Map;

/** MotiveWave loads a changed jar beside the old one in the same Java process: two copies of the study, each from
 * a class loader of its own, as here. The newer one takes the journal over from the older without a restart
 * (JournalHandoff), and the older one opens nothing more. */
public final class JournalHandoffTest {
  private JournalHandoffTest() {}

  public static void main(String[] args) throws Exception {
    URL classes = JournalHandoff.class.getProtectionDomain().getCodeSource().getLocation();
    URL sdk = com.motivewave.platform.sdk.common.Instrument.class.getProtectionDomain().getCodeSource().getLocation();
    var folder = Files.createTempDirectory("mwj-handoff-test-");
    Path file = folder.resolve("motivewave.ndjson");
    try (var oldLoader = new URLClassLoader(new URL[]{classes, sdk}, ClassLoader.getPlatformClassLoader());
         var newLoader = new URLClassLoader(new URL[]{classes, sdk}, ClassLoader.getPlatformClassLoader())) {
      var old = new Copy(oldLoader);
      var fresh = new Copy(newLoader);
      assert old.writerType != fresh.writerType : "Two copies of the study, as after MotiveWave reloads its jar";

      Object chart = old.acquire(file);            // a chart's price writer
      Object capture = old.capture(file);          // the fill reader, with its own writers of both files
      old.offer(chart, "{\"from\":\"old\"}");
      waitFor(() -> lines(file).contains("{\"from\":\"old\"}"), "the old copy writes while it is the only one");

      Object taken = fresh.acquire(file);          // the newer copy opens the journal: the older one retires
      assert old.retired() && !fresh.retired() : "The older copy steps aside, the newer one does not";
      assert old.closed(capture) : "The older copy's fill reader is closed";
      assert !old.alive(chart) : "The older copy's writers let go of the file";
      fresh.offer(taken, "{\"from\":\"new\"}");
      Object replay = fresh.acquire(JournalAccountCapture.replayFile(file));
      fresh.offer(replay, "{\"from\":\"new replay\"}");
      fresh.release(taken);
      fresh.release(replay);
      assert fresh.failure(taken) == null : "The newer copy holds the lock: " + fresh.failure(taken);
      assert fresh.failure(replay) == null : "and the replay file's: " + fresh.failure(replay);
      var written = lines(file);
      assert written.indexOf("{\"from\":\"old\"}") < written.indexOf("{\"from\":\"new\"}") : written;
      assert lines(JournalAccountCapture.replayFile(file)).contains("{\"from\":\"new replay\"}");

      try {
        old.acquire(file);
        throw new AssertionError("A retired copy must open nothing more");
      } catch (IOException expected) {
        assert JournalEventWriter.RETIRED.equals(expected.getMessage()) : expected.getMessage();
      }
      // A chart of the older copy that lets go of what it held changes nothing for the newer one.
      old.release(chart);
      old.releaseCapture(capture, file);

      // A copy that comes after takes over again, from the one that took over before.
      try (var third = new URLClassLoader(new URL[]{classes, sdk}, ClassLoader.getPlatformClassLoader())) {
        var next = new Copy(third);
        Object again = next.acquire(file);
        assert fresh.retired() && !next.retired() : "Each newer copy takes over from the one before";
        next.release(again);
        assert next.failure(again) == null : next.failure(again);
      }
    }
    System.out.println("Journal handoff passed: a newer copy of the study in the same Java process retires the older "
        + "one (its fill reader and writers close and let go of the files), writes on in order, the older one opens "
        + "nothing more, and a third copy takes over from the second.");
  }

  /** One copy of the study: its classes as loaded by its own class loader. */
  private static final class Copy {
    final Class<?> writerType, handoff, captureType;
    final ClassLoader loader;

    Copy(ClassLoader loader) throws ClassNotFoundException {
      this.loader = loader;
      writerType = loader.loadClass("org.mwjournal.study.JournalEventWriter");
      handoff = loader.loadClass("org.mwjournal.study.JournalHandoff");
      captureType = loader.loadClass("org.mwjournal.study.JournalAccountCapture");
    }

    Object acquire(Path file) throws Exception {
      return call(method(writerType, "acquire", Path.class), null, file);
    }
    void offer(Object writer, String event) throws Exception {
      assert (Boolean) call(method(writerType, "offer", String.class), writer, event) : "queued";
    }
    void release(Object writer) throws Exception { call(method(writerType, "release"), writer); }
    Object failure(Object writer) throws Exception { return field(writerType, "failure").get(writer); }
    boolean alive(Object writer) throws Exception { return ((Thread) field(writerType, "thread").get(writer)).isAlive(); }
    boolean retired() throws Exception { return (Boolean) call(method(handoff, "retired"), null); }

    /** A fill reader as the charts share it (acquire), with a reader that is never asked: no MotiveWave here. */
    Object capture(Path file) throws Exception {
      Class<?> readerType = loader.loadClass("org.mwjournal.study.JournalNativeAccess$Reader");
      var snapshot = loader.loadClass("org.mwjournal.study.JournalNativeAccess$Snapshot")
          .getDeclaredConstructor(String.class, List.class, List.class);
      snapshot.setAccessible(true);
      Object empty = snapshot.newInstance("workspace", List.of(), List.of());
      Object reader = Proxy.newProxyInstance(loader, new Class<?>[]{readerType},
          (proxy, called, arguments) -> called.getName().equals("read") ? empty
              : called.getName().equals("hashCode") ? System.identityHashCode(proxy)
              : called.getName().equals("equals") ? proxy == arguments[0] : "reader");
      Object capture = call(method(captureType, "fixture", Path.class, readerType), null, file, reader);
      @SuppressWarnings("unchecked")
      var shared = (Map<Path, Object>) field(captureType, "SHARED").get(null);
      synchronized (shared) { shared.put(file.toAbsolutePath().normalize(), capture); }
      field(captureType, "references").setInt(capture, 1);
      return capture;
    }
    boolean closed(Object capture) throws Exception { return field(captureType, "closed").getBoolean(capture); }
    void releaseCapture(Object capture, Path file) throws Exception {
      call(method(captureType, "release", Path.class), capture, file);
    }
  }

  private static Method method(Class<?> type, String name, Class<?>... parameters) throws NoSuchMethodException {
    Method method = type.getDeclaredMethod(name, parameters);
    method.setAccessible(true);
    return method;
  }
  private static Field field(Class<?> type, String name) throws NoSuchFieldException {
    Field field = type.getDeclaredField(name);
    field.setAccessible(true);
    return field;
  }
  private static Object call(Method method, Object target, Object... arguments) throws Exception {
    try { return method.invoke(target, arguments); }
    catch (InvocationTargetException exc) {
      if (exc.getCause() instanceof Exception cause) throw cause;
      throw exc;
    }
  }

  private static List<String> lines(Path file) {
    try { return Files.isRegularFile(file) ? Files.readAllLines(file) : List.of(); }
    catch (IOException exc) { return List.of(); }
  }
  private static void waitFor(java.util.function.BooleanSupplier done, String what) throws InterruptedException {
    long until = System.currentTimeMillis() + 5000;
    while (!done.getAsBoolean()) {
      if (System.currentTimeMillis() > until) throw new AssertionError("Timed out: " + what);
      Thread.sleep(20);
    }
  }
}
