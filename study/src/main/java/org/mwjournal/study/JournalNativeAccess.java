package org.mwjournal.study;

import com.motivewave.platform.sdk.common.Instrument;
import com.motivewave.platform.sdk.order_mgmt.Trade;
import java.io.File;
import java.lang.reflect.Method;
import java.lang.reflect.Modifier;
import java.util.ArrayList;
import java.util.List;
import java.util.Map;

/** Experimental interoperability adapter. Only allow-listed PUBLIC read methods; no private access,
 * broker login, order entry, cancel, persistence, or modification methods are used. */
final class JournalNativeAccess {
  /** currencyAssumed: the account's own currency could not be read, so currency is the instrument's. */
  record Fill(String id, String account, String symbol, boolean buy, double qty, double price,
              long time, double commission, double pointValue, String currency, String orderId,
              String positionId, boolean entry, boolean replay, boolean currencyAssumed) {
    Fill(String id, String account, String symbol, boolean buy, double qty, double price,
         long time, double commission, double pointValue, String currency, String orderId,
         String positionId, boolean entry, boolean replay) {
      this(id, account, symbol, buy, qty, price, time, commission, pointValue, currency, orderId, positionId,
          entry, replay, false);
    }
    /** False when MotiveWave stored no fill price: an average price of exactly 0, or one that is not a number. */
    boolean priced() { return Double.isFinite(price) && price != 0; }
  }
  /** One execution as MotiveWave describes it, before anything is decided about it. symbol is null when
   * the SDK instrument is missing, instrumentCurrency when the native instrument is, and accountCurrency
   * when the account object is. accountId is the public SDK's, and does not need the account object. */
  record Raw(String id, String accountId, String symbol, boolean buy, double qty, double price, long time,
             double commission, double pointValue, double pointSize, String accountCurrency,
             String instrumentCurrency, boolean stock, String orderId, String positionId, boolean entry,
             boolean replay) {}
  record ProtectiveOrder(String id, String account, String symbol, boolean buy, String parentId,
                         String positionId, String role, String state, double price, long time,
                         boolean replay) {}
  /** skipped: how many executions could not be described, by reason. They are in none of the lists. */
  record Snapshot(String workspace, List<Fill> fills, List<ProtectiveOrder> orders, Map<String,Integer> skipped) {
    Snapshot(String workspace, List<Fill> fills, List<ProtectiveOrder> orders) {
      this(workspace, fills, orders, Map.of());
    }
  }
  /** Why a fill is left out, in the words the heartbeat uses. */
  static final String NO_INSTRUMENT = "instrument unavailable", NO_ACCOUNT = "account unavailable",
      NO_CURRENCY = "currency unavailable", CROSS_CURRENCY = "cross-currency",
      NO_MULTIPLIER = "multiplier unavailable", INCOMPLETE = "incomplete record";
  interface Reader { Snapshot read() throws ReflectiveOperationException; }

  private JournalNativeAccess() {}

  static Reader connect(ClassLoader loader) throws ReflectiveOperationException {
    Class<?> executions = Class.forName("com.motivewave.platform.databean.Executions", false, loader);
    Class<?> execution = Class.forName("com.motivewave.platform.databean.Execution", false, loader);
    Class<?> orders = Class.forName("com.motivewave.platform.databean.OrderDirectory", false, loader);
    Class<?> order = Class.forName("com.motivewave.platform.databean.MWOrder", false, loader);
    Class<?> account = Class.forName("com.motivewave.platform.databean.Account", false, loader);
    Class<?> workspace = Class.forName("com.motivewave.platform.databean.Workspace", false, loader);
    Class<?> nativeInstrument = Class.forName("com.motivewave.platform.databean.MWInstrument", false, loader);
    Class<?> position = Class.forName("com.motivewave.platform.databean.Position", false, loader);
    Method positionKey = readMethod(position, "getId", false);
    if (!Trade.class.isAssignableFrom(execution)) throw new NoSuchMethodException("Native execution is not an SDK Trade.");
    // getAll() is a UI-filtered/limited "active" list. byAccount(null) returns a snapshot of
    // every execution retained in this workspace, including non-persistent replay accounts.
    Method allFills = executions.getMethod("byAccount", account);
    if (!Modifier.isPublic(allFills.getModifiers()) || !Modifier.isStatic(allFills.getModifiers()))
      throw new NoSuchMethodException("Incompatible execution snapshot method.");
    Method allOrders = readMethod(orders, "getAllOrders", true);
    Method directory = readMethod(workspace, "getWorkspaceDir", true);
    Method replay = readMethod(execution, "isReplay", false);
    Method execAccount = readMethod(execution, "getAccount", false);
    Method orderId = readMethod(execution, "getOrderId", false);
    Method positionId = readMethod(execution, "getPositionId", false);
    Method entry = readMethod(execution, "isEntry", false);
    Method currency = readMethod(account, "getBaseCurrency", false);
    Method mwInstrument = readMethod(execution, "getMWInstrument", false);
    Method instrumentCurrency = readMethod(nativeInstrument, "getCurrency", false);
    Method stock = readMethod(nativeInstrument, "isStock", false);
    String[] getters = {"getOrderId", "getAccountId", "getInstrument", "isBuy", "getParentId",
        "getPosition", "isStopOrTrail", "isLimit", "isFilled", "isCancelled", "isRejected",
        "isActive", "isNew", "isTrail", "getStopPrice", "getTrailStopPrice", "getLimitPrice",
        "getLastModified", "isReplay"};
    var methods = new java.util.HashMap<String, Method>();
    for (String name : getters) methods.put(name, readMethod(order, name, false));
    return () -> {
      Object folder = directory.invoke(null);
      if (!(folder instanceof File file)) throw new IllegalStateException("Native workspace directory unavailable.");
      var fills = new ArrayList<Fill>();
      var skipped = new java.util.TreeMap<String,Integer>();
      Object fillRows = allFills.invoke(null, new Object[]{null});
      if (!(fillRows instanceof List<?> rows)) throw new IllegalStateException("Unexpected native execution snapshot.");
      if (rows.size() > 100000) throw new IllegalStateException("Native execution snapshot exceeds safety limit.");
      for (Object item : rows) {
        if (!execution.isInstance(item) || !(item instanceof Trade t))
          throw new IllegalStateException("Unexpected native execution type.");
        Instrument instrument = t.getInstrument();
        Object a = execAccount.invoke(item), nativeI = mwInstrument.invoke(item);
        boolean known = nativeInstrument.isInstance(nativeI);
        Fill fill = describe(new Raw(t.getId(), t.getAccountId(), instrument == null ? null : instrument.getSymbol(),
            t.isBuy(), t.getQuantity(), t.getAvgPrice(), t.getTime(), t.getCommission(),
            instrument == null ? Double.NaN : instrument.getPointValue(),
            instrument == null ? Double.NaN : instrument.getPointSize(),
            account.isInstance(a) ? string(currency.invoke(a)) : null,
            known ? string(instrumentCurrency.invoke(nativeI)) : null, known && bool(stock.invoke(nativeI)),
            string(orderId.invoke(item)), string(positionId.invoke(item)),
            bool(entry.invoke(item)), bool(replay.invoke(item))), skipped);
        if (fill != null) fills.add(fill);
      }
      var protection = new ArrayList<ProtectiveOrder>();
      Object orderRows = allOrders.invoke(null);
      if (!(orderRows instanceof List<?> list)) throw new IllegalStateException("Unexpected native order snapshot.");
      if (list.size() > 100000) throw new IllegalStateException("Native order snapshot exceeds safety limit.");
      for (Object item : new ArrayList<>(list)) {
        if (!order.isInstance(item)) throw new IllegalStateException("Unexpected native order type.");
        boolean stop = bool(invoke(methods, "isStopOrTrail", item));
        boolean target = bool(invoke(methods, "isLimit", item));
        if ((!stop && !target) || bool(invoke(methods, "isNew", item))) continue;
        Object i = invoke(methods, "getInstrument", item);
        if (!(i instanceof Instrument instrument)) continue;
        String state = bool(invoke(methods, "isFilled", item)) ? "filled"
            : bool(invoke(methods, "isCancelled", item)) ? "cancelled"
            : bool(invoke(methods, "isRejected", item)) ? "rejected"
            : bool(invoke(methods, "isActive", item)) ? "working" : "";
        if (state.isEmpty()) continue;
        Object linkedPosition = invoke(methods, "getPosition", item);
        String linkedId = position.isInstance(linkedPosition) ? string(positionKey.invoke(linkedPosition)) : "";
        String priceGetter = stop ? bool(invoke(methods, "isTrail", item)) ? "getTrailStopPrice" : "getStopPrice"
            : "getLimitPrice";
        protection.add(new ProtectiveOrder(string(invoke(methods, "getOrderId", item)),
            string(invoke(methods, "getAccountId", item)), instrument.getSymbol(),
            bool(invoke(methods, "isBuy", item)), string(invoke(methods, "getParentId", item)),
            linkedId, stop ? "stop" : "target", state,
            ((Number)invoke(methods, priceGetter, item)).doubleValue(),
            ((Number)invoke(methods, "getLastModified", item)).longValue(),
            bool(invoke(methods, "isReplay", item))));
      }
      return new Snapshot(file.getName(), List.copyOf(fills), List.copyOf(protection), Map.copyOf(skipped));
    };
  }

  /** The fill to write, or null with its reason counted in skipped. A fill is left out only when it
   * cannot be described: no instrument, no account id of any kind, no currency, another currency than
   * the account's (converting would need a historical FX rate), no multiplier, or no id, size or time.
   *
   * A missing account object is not one of those. The public SDK still names the account, and the
   * instrument says which currency it trades in; the fill is then marked, because the account being in
   * that same currency is an assumption. A missing price is not one either: the fill is written so the
   * quantities reconcile, and Fill.priced() says the price is not to be used. */
  static Fill describe(Raw raw, Map<String,Integer> skipped) {
    String currency = raw.accountCurrency(), quote = raw.instrumentCurrency(), reason = null;
    boolean assumed = currency == null || currency.isBlank();
    // Native raw moves are scaled by pointValue / pointSize, not by a tick/pip value alone.
    double pv = raw.stock() ? 1 : multiplier(raw.pointValue(), raw.pointSize());
    if (blank(raw.symbol()) || quote == null) reason = NO_INSTRUMENT;
    else if (blank(raw.accountId())) reason = NO_ACCOUNT;
    else if (quote.isBlank()) reason = NO_CURRENCY;
    else if (!assumed && !currency.equals(quote)) reason = CROSS_CURRENCY;
    else if (!Double.isFinite(pv) || pv <= 0) reason = NO_MULTIPLIER;
    else if (blank(raw.id()) || !Double.isFinite(raw.qty()) || raw.qty() <= 0 || raw.time() <= 0) reason = INCOMPLETE;
    if (reason != null) { skipped.merge(reason, 1, Integer::sum); return null; }
    // Fees are never presented as known (commission_known is false), so one that is not a number is none.
    return new Fill(raw.id(), raw.accountId(), raw.symbol(), raw.buy(), raw.qty(), raw.price(), raw.time(),
        Double.isFinite(raw.commission()) ? raw.commission() : 0, pv, quote, string(raw.orderId()),
        string(raw.positionId()), raw.entry(), raw.replay(), assumed);
  }
  private static boolean blank(String value) { return value == null || value.isBlank(); }
  static double multiplier(double pointValue, double pointSize) {
    return Double.isFinite(pointValue) && pointValue > 0 && Double.isFinite(pointSize) && pointSize > 0
        ? pointValue / pointSize : Double.NaN;
  }

  private static Method readMethod(Class<?> type, String name, boolean statik) throws NoSuchMethodException {
    Method result = type.getMethod(name);
    if (!Modifier.isPublic(result.getModifiers()) || Modifier.isStatic(result.getModifiers()) != statik)
      throw new NoSuchMethodException("Incompatible read method " + name);
    return result;
  }
  private static Object invoke(java.util.Map<String,Method> methods, String name, Object item)
      throws ReflectiveOperationException { return methods.get(name).invoke(item); }
  private static String string(Object value) { return value == null ? "" : value.toString(); }
  private static boolean bool(Object value) {
    if (!(value instanceof Boolean b)) throw new IllegalStateException("Unexpected native boolean.");
    return b;
  }
}
