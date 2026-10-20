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
    System.out.println("Journal price events passed: complete 1m OHLCV, invalid bars, exact tick times, quotes, escaping, "
        + "and the chart instrument's names for the log.");
  }

  private static <T> T fixture(Class<T> type, Map<String,Object> values) {
    return type.cast(Proxy.newProxyInstance(type.getClassLoader(), new Class<?>[]{type},
        (proxy, method, args) -> values.get(method.getName())));
  }
}
