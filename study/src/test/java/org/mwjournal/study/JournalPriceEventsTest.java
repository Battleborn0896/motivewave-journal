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
    String tickEvent = JournalPriceEvents.tick("ES\"Z6", JournalPriceEvents.Print.of(tick), "motivewave-ticks", "live");
    assert tickEvent.contains("ES\\\"Z6") && tickEvent.contains(".250Z");
    assert tickEvent.contains("\"bid\":4999.75") && tickEvent.contains("\"volume\":2.0");
    assert tickEvent.startsWith("{\"version\":1,\"type\":\"tick\",\"mode\":\"live\",\"provider\":\"motivewave-ticks\",")
        && tickEvent.endsWith(",\"proxy\":false}") : tickEvent;
    tickValues.put("getBidPrice", 5001f);
    assert !JournalPriceEvents.tick("ESZ6", JournalPriceEvents.Print.of(tick), "motivewave-ticks", "live").contains("\"bid\"")
        : "Skip crossed quotes";
    tickValues.put("getPrice", Float.NaN);
    assert JournalPriceEvents.Print.of(tick) == null : "No NaN in the bridge";

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
        + "the chart instrument's names for the log, the contract a chart writes under, prints never proxy, a "
        + "continuous chart's loaded history marked, and replay prints as real ticks.");
  }

  /** What a chart writes under, and what is proxy. A chart writes under its active contract, or its own symbol
   * when it names none. Prints, and the minute bars topped up as they close, are the product's own on any chart
   * of it and never marked. History loaded when a continuous chart opens can be back-adjusted, so it is. */
  private static void proxyRule() {
    Bar bar = fixture(Bar.class, Map.of("isComplete", true, "getStartTime", 1791381600000L, "getSizeMillis", 60000L,
        "getOpen", 21000f, "getHigh", 21004f, "getLow", 20999f, "getClose", 21002f, "getVolumeAsFloat", 12f));
    Tick tick = fixture(Tick.class, Map.of("getTime", 1791381623250L, "getPrice", 21001f, "getBidPrice", 21000.75f,
        "getAskPrice", 21001.25f, "getVolumeAsFloat", 1f));
    Instrument continuous = instrument("NQ", "NQZ6"), contract = instrument("NQZ6", "NQZ6");
    Instrument unnamed = instrument("NQ", null), blank = instrument("NQZ6", " ");
    assert JournalPriceEvents.contract(continuous).equals("NQZ6") && JournalPriceEvents.contract(contract).equals("NQZ6")
        && JournalPriceEvents.contract(unnamed).equals("NQ") && JournalPriceEvents.contract(blank).equals("NQZ6");
    assert JournalPriceEvents.proxy(continuous) : "A continuous chart's loaded history is marked";
    assert !JournalPriceEvents.proxy(contract) && !JournalPriceEvents.proxy(unnamed) && !JournalPriceEvents.proxy(blank)
        : "The contract's own history is not proxy";
    // As written: a continuous chart's live and replay prints, its loaded candles, and a minute topped up.
    var print = JournalPriceEvents.Print.of(tick);
    String live = JournalPriceEvents.tick("NQZ6", print, "motivewave-ticks", "live");
    assert live.contains("\"provider\":\"motivewave-ticks\"") && live.endsWith(",\"proxy\":false}") : live;
    String replay = JournalPriceEvents.tick("NQZ6", print, JournalPriceEvents.replayProvider("run-1"), "replay");
    assert replay.contains("\"mode\":\"replay\",\"provider\":\"motivewave-replay:run-1:ticks\"")
        && replay.endsWith(",\"proxy\":false}") : "Replay prints are real ticks: " + replay;
    String candle = JournalPriceEvents.bar("NQZ6", bar, JournalPriceEvents.proxy(continuous));
    assert candle.endsWith(",\"proxy\":true}") : candle;
    String toppedUp = JournalPriceEvents.bar("NQZ6", bar, false);
    assert toppedUp.endsWith(",\"proxy\":false}") : "A minute just traded on the chart: " + toppedUp;
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
