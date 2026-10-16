package org.mwjournal.study;

import java.util.ArrayList;
import java.util.List;

/** The gate is pure: events are plain strings here and the clock is whatever the test says. */
public final class TickGateTest {
  private TickGateTest() {}

  public static void main(String[] args) {
    // Flat: nothing is written, however long it lasts.
    var gate = new TickGate<String>();
    for (int i = 0; i < 50; i++)
      assert gate.offer("flat" + i, 1000 + i * 100, false).isEmpty() : "Nothing may be written while flat";
    assert gate.size() == 50;

    // Armed: the pre-roll first, oldest first, then the print that armed it; then the buffer is empty.
    var flushed = gate.offer("entry", 6000, true);
    assert flushed.size() == 51 && flushed.getFirst().equals("flat0") && flushed.getLast().equals("entry");
    for (int i = 0; i < 50; i++) assert flushed.get(i).equals("flat" + i) : "Pre-roll must keep its order";
    assert gate.size() == 0 : "A flushed pre-roll must not be written twice";
    assert gate.offer("held1", 6100, true).equals(List.of("held1")) : "In a position every print passes straight through";
    assert gate.offer("held2", 6200, true).equals(List.of("held2"));
    assert gate.size() == 0;

    // Re-arming after a flat period: only what was buffered since, nothing from the earlier position.
    assert gate.offer("gap1", 7000, false).isEmpty() && gate.offer("gap2", 8000, false).isEmpty();
    assert gate.offer("entry2", 8400, true).equals(List.of("gap1", "gap2", "entry2"));
    assert gate.offer("after", 8500, false).isEmpty() && gate.size() == 1;

    // Eviction by age: the pre-roll is the last 15 seconds, and it is trimmed on every print held back.
    var aged = new TickGate<String>();
    aged.offer("t0", 0, false);
    aged.offer("t5", 5000, false);
    aged.offer("t10", 10000, false);
    assert aged.size() == 3;
    aged.offer("t20", 20000, false);
    assert aged.size() == 3 : "t0 is 20 s old and must go; t5 is exactly 15 s old and stays";
    aged.offer("t40", 40000, false);
    assert aged.size() == 1 : "A quiet spell between two skipped prints empties the pre-roll";

    // Age is not measured against the print that arms the gate. A replay stepped bar by bar: the entry
    // bar's prints arrive in one burst, the fill is noticed half a second later, and the next step comes
    // after a pause. Everything printed since the entry is still in the buffer and must be written.
    var stepped = new TickGate<String>();
    stepped.offer("earlier-bar", 0, false);
    stepped.offer("before-entry", 30000, false);
    stepped.offer("after-entry-1", 30100, false);
    stepped.offer("after-entry-2", 30200, false);
    assert stepped.offer("next-step", 50200, true)
        .equals(List.of("before-entry", "after-entry-1", "after-entry-2", "next-step"))
        : "Prints since the entry must survive a pause before the next one";
    assert stepped.size() == 0;

    // Eviction by count: a burst cannot grow the buffer past the cap, and the newest prints are the ones kept.
    var burst = new TickGate<String>();
    for (int i = 0; i < TickGate.PRE_ROLL_CAP + 5; i++) burst.offer("b" + i, 1000, false);
    assert burst.size() == TickGate.PRE_ROLL_CAP;
    var kept = burst.offer("last", 1000, true);
    assert kept.size() == TickGate.PRE_ROLL_CAP + 1 && kept.getFirst().equals("b5") && kept.getLast().equals("last");
    var small = new TickGate<String>(1000, 3);
    for (int i = 0; i < 10; i++) small.offer("s" + i, i, false);
    assert small.offer("s10", 10, true).equals(List.of("s7", "s8", "s9", "s10"));
    var none = new TickGate<String>(1000, 0);
    assert none.offer("x", 0, false).isEmpty() && none.size() == 0 && none.offer("y", 1, true).equals(List.of("y"));

    // A clock that steps backwards is another timeline (a rewound replay): its pre-roll is not carried over.
    var rewound = new TickGate<String>();
    rewound.offer("late1", 50000, false);
    rewound.offer("late2", 51000, false);
    assert rewound.offer("early", 20000, false).isEmpty() && rewound.size() == 1;
    assert rewound.offer("entry", 20500, true).equals(List.of("early", "entry"));

    // clear() drops the pre-roll without writing it.
    var cleared = new TickGate<String>();
    cleared.offer("a", 0, false);
    cleared.clear();
    assert cleared.size() == 0 && cleared.offer("b", 1, true).equals(List.of("b"));

    // Every print is either written exactly once or dropped by a bound; none is duplicated or reordered.
    var mixed = new TickGate<Integer>(500, 40);
    var written = new ArrayList<Integer>();
    for (int i = 0; i < 5000; i++) written.addAll(mixed.offer(i, i * 10L, i / 250 % 2 == 1));
    for (int i = 1; i < written.size(); i++) assert written.get(i) > written.get(i - 1) : "Duplicated or reordered";
    assert written.contains(250) && written.contains(499) && !written.contains(100);
    assert written.contains(249) && written.contains(210) && !written.contains(209) : "40 prints of pre-roll, no more";

    try {
      new TickGate<String>(-1, 10);
      throw new AssertionError("Reject a negative window");
    } catch (IllegalArgumentException expected) { }
    System.out.println("Tick gate passed: silent while flat, ordered pre-roll, age and count bounds, a pause after "
        + "the entry, re-arming, rewinds.");
  }
}
