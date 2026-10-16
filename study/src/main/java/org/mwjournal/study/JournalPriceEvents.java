package org.mwjournal.study;

import com.motivewave.platform.sdk.common.Bar;
import com.motivewave.platform.sdk.common.Tick;
import java.time.Instant;

/** Strict price-only serialization. Never generates synthetic ticks from bars. */
final class JournalPriceEvents {
  private JournalPriceEvents() {}

  static String bar(String symbol, Bar bar, boolean proxy) {
    if (bar == null || !bar.isComplete() || bar.getStartTime() <= 0 || bar.getSizeMillis() != 60000
        || bar.getStartTime() % 60000 != 0) return null;
    float open = bar.getOpen(), high = bar.getHigh(), low = bar.getLow(), close = bar.getClose();
    if (!Float.isFinite(open) || !Float.isFinite(high) || !Float.isFinite(low) || !Float.isFinite(close)
        || low > high || open < low || open > high || close < low || close > high) return null;
    float volume = bar.getVolumeAsFloat();
    String vol = Float.isFinite(volume) && volume >= 0 ? ",\"volume\":" + volume : "";
    return "{\"version\":1,\"type\":\"bar\",\"provider\":\"motivewave-bars:1m\",\"symbol\":"
        + quote(symbol) + ",\"ts\":" + quote(Instant.ofEpochMilli(bar.getStartTime()).toString())
        + ",\"resolution_seconds\":60,\"open\":" + open + ",\"high\":" + high + ",\"low\":" + low
        + ",\"close\":" + close + vol + ",\"proxy\":" + proxy + "}";
  }

  static String tick(String symbol, Tick tick, boolean proxy) {
    return tick(symbol, tick, proxy, "motivewave-ticks", "live");
  }

  static String tick(String symbol, Tick tick, boolean proxy, String source, String mode) {
    Print print = Print.of(tick);
    return print == null ? null : tick(symbol, print, proxy, source, mode);
  }

  /** One trade print's own numbers, copied out of the SDK object. A print waiting in the pre-roll is
   * kept in this form: it holds no MotiveWave object and costs no text unless it is written. */
  record Print(long time, float price, float bid, float ask, float volume) {
    /** Null for a print the bridge must not carry: no price or no time. */
    static Print of(Tick tick) {
      if (tick == null || !Float.isFinite(tick.getPrice()) || tick.getTime() <= 0) return null;
      return new Print(tick.getTime(), tick.getPrice(), tick.getBidPrice(), tick.getAskPrice(),
          tick.getVolumeAsFloat());
    }
  }

  static String tick(String symbol, Print print, boolean proxy, String source, String mode) {
    String bidAsk = "";
    float bid = print.bid(), ask = print.ask(), volume = print.volume();
    if (Float.isFinite(bid) && Float.isFinite(ask) && bid != 0 && ask != 0 && bid <= ask)
      bidAsk = ",\"bid\":" + bid + ",\"ask\":" + ask;
    String vol = Float.isFinite(volume) && volume >= 0 ? ",\"volume\":" + volume : "";
    return "{\"version\":1,\"type\":\"tick\",\"mode\":" + quote(mode)
        + ",\"provider\":" + quote(source) + ",\"symbol\":"
        + quote(symbol) + ",\"ts\":" + quote(Instant.ofEpochMilli(print.time()).toString())
        + ",\"price\":" + print.price() + bidAsk + vol + ",\"proxy\":" + proxy + "}";
  }

  static String quote(String value) {
    StringBuilder result = new StringBuilder("\"");
    for (char c : value.toCharArray()) {
      if (c == '"' || c == '\\') result.append('\\').append(c);
      else if (c < 32) result.append(String.format("\\u%04x", (int)c));
      else result.append(c);
    }
    return result.append('"').toString();
  }
}
