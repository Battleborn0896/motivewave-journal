package org.mwjournal.study;

import com.motivewave.platform.sdk.common.Bar;
import com.motivewave.platform.sdk.common.Instrument;
import com.motivewave.platform.sdk.common.Tick;
import java.lang.reflect.Proxy;
import java.util.HashMap;
import java.util.Map;

/** Pure SDK interface fixtures; no MotiveWave session or brokerage account required. */
public final class JournalPriceEventsTest {
  private JournalPriceEventsTest() {}

  public static void main(String[] args) {
    var values = new HashMap<String, Object>(Map.of("isComplete", true, "getStartTime", 1791381600000L,
        "getSizeMillis", 60000L, "getOpen", 5000f, "getHigh", 5004f, "getLow", 4999f,
        "getClose", 5002f, "getVolumeAsFloat", 123f));
    Bar bar = fixture(Bar.class, values);
    String event = JournalPriceEvents.bar("ESZ6", bar, false);
    assert event.contains("\"open\":5000.0") && event.contains("\"close\":5002.0");
    assert event.contains("\"volume\":123.0") && event.contains("\"resolution_seconds\":60");
    assert event.contains("motivewave-bars:1m") && event.contains("\"proxy\":false");
    assert JournalPriceEvents.bar("ESZ6", bar, true).contains("\"proxy\":true");
    // A replay's bars are the platform's history of the days it plays, the same in every run: one provider.
    String replayBar = JournalPriceEvents.bar("ESZ6", bar, false, JournalPriceEvents.REPLAY_BARS);
    assert replayBar.contains("\"provider\":\"motivewave-replay:bars\"") && replayBar.contains("\"resolution_seconds\":60")
        : replayBar;
    assert JournalPriceEvents.bar("ESZ6", bar, false).contains("\"provider\":\"motivewave-bars:1m\"");
    values.put("isComplete", false);
    assert JournalPriceEvents.bar("ESZ6", bar, false) == null : "Do not freeze an incomplete candle";
    values.put("isComplete", true);
    values.put("getSizeMillis", 300000L);
    assert JournalPriceEvents.bar("ESZ6", bar, false) == null : "Never label a 5m bar as 1m";
    values.put("getSizeMillis", 60000L);
    values.put("getOpen", 5005f);
    assert JournalPriceEvents.bar("ESZ6", bar, false) == null : "O/C must lie within range";

    var tickValues = new HashMap<String, Object>(Map.of("getTime", 1791381623250L, "getPrice", 5000f,
        "getBidPrice", 4999.75f, "getAskPrice", 5000.25f, "getVolumeAsFloat", 2f));
    Tick tick = fixture(Tick.class, tickValues);
    String tickEvent = JournalPriceEvents.tick("ES\"Z6", tick, false);
    assert tickEvent.contains("ES\\\"Z6") && tickEvent.contains(".250Z");
    assert tickEvent.contains("\"bid\":4999.75") && tickEvent.contains("\"volume\":2.0");
    tickValues.put("getBidPrice", 5001f);
    assert !JournalPriceEvents.tick("ESZ6", tick, false).contains("\"bid\"") : "Skip crossed quotes";
    tickValues.put("getPrice", Float.NaN);
    assert JournalPriceEvents.tick("ESZ6", tick, false) == null : "No NaN in the bridge";

    // The log's diagnostic: each name exactly as MotiveWave returns it, and why when it cannot.
    Instrument chart = Instrument.class.cast(Proxy.newProxyInstance(Instrument.class.getClassLoader(),
        new Class<?>[]{Instrument.class}, (proxy, method, arguments) -> switch (method.getName()) {
          case "getSymbol" -> "NQ";
          case "getActiveSymbol" -> "NQZ6";
          case "getUnderlying" -> throw new UnsupportedOperationException();
          default -> null;
        }));
    assert JournalPriceEvents.names(chart).equals("getSymbol()=\"NQ\", getActiveSymbol()=\"NQZ6\", "
        + "getExchangeSymbol()=null, getUnderlying() failed: UnsupportedOperationException") : JournalPriceEvents.names(chart);

    proxyRule();
    System.out.println("Journal price events passed: complete 1m OHLCV, invalid bars, exact tick times, quotes, escaping, "
        + "the chart instrument's names for the log, prints recorded as they trade never marked proxy by their "
        + "chart, a continuous chart's loaded history marked, and the replay labels.");
  }

  /** What is proxy. Prints recorded as they trade, and the minute bars topped up as they close, are the
   * product's own on any chart of it: only the user's Proxy Prices setting marks them. History loaded when
   * a continuous chart opens can be back-adjusted, so it stays marked as before. */
  private static void proxyRule() {
    Bar bar = fixture(Bar.class, Map.of("isComplete", true, "getStartTime", 1791381600000L, "getSizeMillis", 60000L,
        "getOpen", 21000f, "getHigh", 21004f, "getLow", 20999f, "getClose", 21002f, "getVolumeAsFloat", 12f));
    Tick tick = fixture(Tick.class, Map.of("getTime", 1791381623250L, "getPrice", 21001f, "getBidPrice", 21000.75f,
        "getAskPrice", 21001.25f, "getVolumeAsFloat", 1f));
    Instrument continuous = instrument("NQ", "NQZ6"), contract = instrument("NQZ6", "NQZ6");
    Instrument unnamed = instrument("NQ", null), blank = instrument("NQZ6", " ");
    for (Instrument chart : java.util.List.of(continuous, contract, unnamed, blank)) {
      assert !JournalPriceEvents.proxy(chart, false, false) : "Recorded as they trade: never proxy by default";
      assert JournalPriceEvents.proxy(chart, true, false) && JournalPriceEvents.proxy(chart, true, true)
          : "Proxy Prices marks everything the chart writes";
    }
    assert JournalPriceEvents.proxy(continuous, false, true) : "A continuous chart's loaded history keeps its flag";
    assert !JournalPriceEvents.proxy(contract, false, true) && !JournalPriceEvents.proxy(unnamed, false, true)
        && !JournalPriceEvents.proxy(blank, false, true) : "The contract's own history is not proxy";
    // As written: a continuous chart's live and replay prints, and its loaded candles.
    String live = JournalPriceEvents.tick("NQZ6", tick, JournalPriceEvents.proxy(continuous, false, false));
    assert live.contains("\"provider\":\"motivewave-ticks\"") && live.endsWith(",\"proxy\":false}") : live;
    String replay = JournalPriceEvents.tick("NQZ6", JournalPriceEvents.Print.of(tick),
        JournalPriceEvents.proxy(continuous, false, false), JournalPriceEvents.replayProvider("run-1", true), "replay");
    assert replay.contains("\"provider\":\"motivewave-replay:run-1:ticks\"") && replay.endsWith(",\"proxy\":false}") : replay;
    String candle = JournalPriceEvents.bar("NQZ6", bar, JournalPriceEvents.proxy(continuous, false, true));
    assert candle.endsWith(",\"proxy\":true}") : candle;
    String toppedUp = JournalPriceEvents.bar("NQZ6", bar, JournalPriceEvents.proxy(continuous, false, false));
    assert toppedUp.endsWith(",\"proxy\":false}") : "A minute just traded on the chart: " + toppedUp;
    assert JournalPriceEvents.replayProvider("run-1", false).equals("motivewave-replay:run-1:simulated");
  }

  /** A chart instrument: its own symbol and the contract MotiveWave says is active (a continuous chart's differ). */
  static Instrument instrument(String symbol, String active) {
    return Instrument.class.cast(Proxy.newProxyInstance(Instrument.class.getClassLoader(),
        new Class<?>[]{Instrument.class}, (proxy, method, arguments) -> switch (method.getName()) {
          case "getSymbol" -> symbol;
          case "getActiveSymbol" -> active;
          default -> null;
        }));
  }

  private static <T> T fixture(Class<T> type, Map<String,Object> values) {
    return type.cast(Proxy.newProxyInstance(type.getClassLoader(), new Class<?>[]{type},
        (proxy, method, args) -> values.get(method.getName())));
  }
}
