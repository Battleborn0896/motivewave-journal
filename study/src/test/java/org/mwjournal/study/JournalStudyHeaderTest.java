package org.mwjournal.study;

import com.motivewave.platform.sdk.common.DataContext;
import com.motivewave.platform.sdk.common.Tick;
import com.motivewave.platform.sdk.study.StudyHeader;

/** What the study declares to MotiveWave, read from the compiled class the way MotiveWave reads it.
 *
 * MotiveWave calls onTick only for a study that receives bar updates. supportsBarUpdates alone leaves that
 * to a switch in the study's dialog which is off until the user turns it on, and study 0.3.2, which
 * declared only that, recorded no live or replay print at all. requiresBarUpdates must stay true. */
public final class JournalStudyHeaderTest {
  private JournalStudyHeaderTest() {}

  public static void main(String[] args) throws Exception {
    // Loaded, not initialised: no study is created and no MotiveWave code runs.
    Class<?> study = Class.forName("org.mwjournal.study.MotiveWaveJournal", false, JournalStudyHeaderTest.class.getClassLoader());
    StudyHeader header = study.getAnnotation(StudyHeader.class);
    if (header == null) throw new AssertionError("MotiveWaveJournal has no @StudyHeader");
    if (!header.requiresBarUpdates())
      throw new AssertionError("requiresBarUpdates must be true: without it MotiveWave never calls onTick, "
          + "and no live or replay tick is recorded");
    assert header.supportsBarUpdates();
    assert !header.allowTickAggregate() && !header.allowTickFilter() : "Every print on its own, never merged or filtered";
    // Setup tells users to add Journal > MotiveWave Journal; the study namespace and id keep saved charts working.
    assert header.menu().equals("Journal") && header.name().equals("MotiveWave Journal");
    assert header.namespace().equals("org.mwjournal") && header.id().equals("MOTIVEWAVE_JOURNAL");
    assert header.overlay() : "Drawn over the price chart, with nothing of its own to draw";
    // Read-only: a study that is not a strategy is given no order context, so it cannot trade.
    assert !header.strategy() : "Never a strategy";
    assert study.getDeclaredMethod("onTick", DataContext.class, Tick.class).getDeclaringClass() == study
        : "The study handles every print itself";
    System.out.println("Study header passed: requiresBarUpdates and supportsBarUpdates, every print unaggregated, "
        + "Journal > MotiveWave Journal, read-only.");
  }
}
