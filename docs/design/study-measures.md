# The study measures MAE / MFE: implementation plan

Repo: this repository, main `2669683` (app 0.4.6 plus R-from-stop, study 0.3.4).
This plan merges the two proposals ("minimal" and "clean"). Section 0 says which proposal won on each point and why.
Sections 1–9 are the spec an engineer builds from.

The owner's rules apply throughout:
- ticks only;
- one mechanism per problem;
- no settings a trader does not need;
- works with defaults on any chart of the product, live and replay;
- survives MotiveWave restarts;
- small disk.

---

## 0. Verdict: which proposal wins on each point

Every claim in this table was checked against the code, or against the owner's bridge files, read only.

| Point | Taken from | Why / what was checked |
|---|---|---|
| One measurer inside `JournalAccountCapture`, shared by every chart on a bridge | both | The poller is already one per bridge path (`SHARED` map). Its 500 ms beat is the natural place. |
| Grouping fills into positions | both, **corrected** | It must be matching.py's rule exactly. The "clean" rule ("a book whose first fill is not marked entry is not measured") would drop every prop-account trade. In the owner's live bridge, 287 of 440 distinct fills have a blank position id and `native_entry` false (checked). matching.py consults `native_entry` only when a position id exists. |
| The study nets from its own record of every fill it wrote | new graft | Without it, MotiveWave forgetting old fills can leave a flat-to-flat (blank-id) book offset for good. Every later trade on that account and contract would then disagree with the app. `.capture-state` already stores every written fill's key, so this is the same file with more fields. It also fixes audit 2.7 (all fills rebuilt into JSON twice a second). |
| Trade id derived from the line, plus the fills check | minimal | `digest([[provider, account, symbol, currency], digest([provider, account, entry_execution_id]), side])[:24]` equals `matching.start`. Re-run in scratch for an ordinary trade and a reversal split: both True. |
| 15 s raw tail for late fills; history rebuild beyond that | both | Same constant as `TickGate.PRE_ROLL_MILLIS`. |
| Feed = `onTick` of charts whose active contract is the fill's contract | both | `Instrument.addListener(TickOperation)` exists in the SDK (checked with javap), so the phase 1 probe tests it. Nothing in this plan depends on it. |
| No chart of the contract: measure from history when the stretch is closed off | clean | One catch-up rule instead of minimal's every-minute history jobs. |
| Holes (stretches no price covered) make MAE/MFE unknown | clean | "Never a wrong number". Minimal only flagged `from_history`. |
| One `position` line per position: fills, numbers, path, after-exit | both | "Last line wins" per fill set. |
| Protocol stays v1; `protection` and `heartbeat` events stay | minimal | The app's stop and R code (`risk.py`, `protection_by_trade`) stays unchanged. The clean proposal moved stops into the position line and replaced the heartbeat with `status.json`. That is more churn for no new ability, because per-day files already remove the heartbeat's disk cost. |
| Per-day files under the old base names; sidecars stay where they are | minimal | Clean moved `.capture-state` into a new folder. That risks filing replay fills again under a new run. |
| Delete a file once it is fully read and a newer dated file exists | clean | Minimal's extra one-hour wait is unnecessary. The writer closes a day's file before creating the next, and the lock allows only one writer. |
| Runner window 120 min, with new highs, new lows and per-minute closes | clean | `app/runner_controls.py` `HORIZONS = [5, 15, 30, 60, 120]`; minimal dropped 120. The per-minute closes make `horizon_extra` exact. Lines are written every 5 market minutes, which covers every horizon. |
| 1-minute bars written around positions, from the fill's own instrument | clean, **widened** | Each position's window is entry − 120 min to exit + 120 min, because the app's chart Context offers 15/30/60/120 (`trades.py CONTEXT`). Clean fixed it at 30 min and removed the picker. This also removes all bar writing from chart callbacks: no 3-day reload per load (142,948 bar lines in his live file, checked) and no duplicate writers. It covers replay and contracts no chart shows. |
| Forming minute as a bar line, once a second, while a position is open | both | |
| Tick size comes from the study (`Instrument.getTickSize`) | clean | Removes `units.TICK_SIZES` (4 products) and the Set Tick Size popover. |
| Dedicated `measurements` and `bars` tables with numeric columns | clean | Audit 2.2 measured summary numbers in columns at 8.8 ms, against 1,144 ms through the JSON. |
| Measurement rows keyed by (trade id, fills) | new | No replacement ambiguity. A migrated row can never be overwritten by a study line that grouped the fills differently. |
| Migration: new definition from stored ticks, then ticks deleted | both | From minimal: build missing bars from ticks for old replay trades. From clean: import unread old lines first, report the space freed, upgrade named journals too. |
| Dollar MAE no longer counts banked profit | both | Scratch: long 2 at 100, sell 1 at 104, low 99. Old $MAE 0, new $20, points 1.0 (re-run, confirmed). |
| Stop link by account + contract + closing side, with diagnostics | both | Zero `protection` lines in his 52 MB live file (checked). Prop fills have no position id and no entry flag, so the current parentId/positionId link (`JournalAccountCapture.poll`) can never match. Clean's per-stage counts are grafted in. |
| Phases | minimal's 3 phases, plus clean's "new definition first" | Small and sequential, so each phase can be shipped and checked by itself. |
| CSV-imported trades: no MAE/MFE; price CSV import removed | clean | One source of prices: the study. |

---

## 1. Definitions (exact)

**Position.**
- Built from fills by `mwjournal/matching.py`'s rules:
  - scope = (provider, account, symbol, currency); book = (scope, `native_position_id` or "");
  - sort by time, and at equal times a fill with a position id and `native_entry` true comes first; otherwise first-written order;
  - average cost is updated on entries;
  - a reversal splits the crossing fill;
  - a fill with a position id whose `native_entry` contradicts the book makes it **incomplete** (ENTRY_MISSING);
  - a newer position id in the same scope ends an older open one (EXIT_MISSING).
- Incomplete positions and positions with an unpriced fill (price 0) are not measured and get no line. The app already shows those trades as incomplete.

**Holding states.**
- Between consecutive fills k and k+1 of a position, the state is (from = fill_k time, to = fill_{k+1} time, q = contracts held, c = average cost).
- A print at time t belongs to the state with from ≤ t < to. The last state of an open position has to = now.
- Consequences:
  - a print at the exact time of a fill belongs to the state after that fill;
  - prints at the final exit time are not the position's.
- This is `metrics.holding_states` / `walk` today.

**Per print p in a state** (s = +1 long, −1 short):
- move = (p − c)·s;
- adverse = −move;
- favourable = move.

**Fill boundaries** (`metrics.price_boundaries` today):
- at each fill, while held before it: move = (fill price − c_before)·s, with q_before;
- after an entry fill: move = (fill price − c_after)·s, with q_after.

**Numbers.** One moment for MAE and one for MFE; every unit is that moment rescaled (`metrics.extremes` today).
- Each print in a state, and each fill boundary, is a mark: (held move = move·q, move). A boundary (0, 0) comes
  first, so both numbers are ≥ 0.
- The MAE moment is the mark with the smallest held move; of marks with the same held move, the one with the
  smaller move. The MFE moment is the mark with the largest held move; of the same held move, the larger move.
  The choice needs no point value.
- `mae_points` = max(0, −move at the MAE moment); `mae_held` = max(0, −held move there) = `mae_points` × q then.
- `mfe_points` = max(0, move at the MFE moment); `mfe_held` = max(0, held move there) = `mfe_points` × q then.
- Each is null until at least one print has been counted.
- Banked profit from partial exits never enters. The app converts the units:
  - dollars = held × the trade's point value = points × q then × the point value;
  - ticks = points ÷ `tick_size`;
  - R = dollars ÷ the risk to the stop (`risk.py`, unchanged).
- Example: long 2 NQ at 100, a dip to 97, sell 1 at 102, the runner dips to 96. MAE moment: 97 with 2 held
  (held move −6) beats 96 with 1 held (−4), so `mae_points` 3, `mae_held` 6, $120. MFE moment: the sell at 102
  with 2 held (held move 4), so `mfe_points` 2, $80.
- Carrying on a measurement compares new marks with the stored moment's mark only, so it equals measuring
  from entry. Merged trades are measured the same way, from the combined position's average entry.

**Path.**
- One entry per minute and holding size: [minute start, lowest move, highest move, q], with moves signed (favourable positive).
- Dollars per entry = move × q × point value.

**Runner (after exit).**
- It starts at the final exit (the fill that makes the position flat) and counts prints strictly after the exit time, until exit + 120 min of market time. In replay, market time is the replay clock.
- It records:
  - `highs`: every print that sets a new high since the exit, as [ms after exit, price];
  - `lows`: every print that sets a new low, likewise;
  - `closes[i]`: the last print at or before exit + (i+1) min.
- From these, for any stop S chosen later (saved stop, stop order, breakeven), for a long:
  - the touch is the first low record ≤ S;
  - extra MFE = (best high record before the touch − exit price) × exit qty × point value;
  - extra MAE = (exit price − lowest low record before the touch, or S if touched) × the same;
  - minutes to peak = the time of that best record;
  - horizon extra = from `closes[h−1]`.
- A short mirrors this.
- All of it is exact. It is the same result `post_exit.post_exit` gives on ticks today.

---

## 2. The contract (bridge protocol, `docs/protocol.md`)

### 2.1 Files

| File | What |
|---|---|
| `<home>/bridge/motivewave.YYYY-MM-DD.ndjson` | live events, dated by the study's UTC wall-clock date when the line is written |
| `<home>/bridge/motivewave.replay.YYYY-MM-DD.ndjson` | replay events, same rule; wall clock, not replay time |
| `motivewave.ndjson.lock`, `motivewave.replay.ndjson.lock` | unchanged: one writer each. "Another copy is running" keeps working across versions. |
| `motivewave.ndjson.capture-state` | unchanged name. Each fill entry gains its compact record (section 3.3). |
| `motivewave.ndjson.replay-clock` | unchanged name. Saved when the run changes and at most every 10 s otherwise, not on every poll. |
| `motivewave.ndjson.measure-state` | new: the measurer's checkpoints (section 3.6) |
| `motivewave.ndjson.tick-backfill` | deleted by the study on first start |

- The home folder rule is unchanged: `MWJ_HOME`, otherwise the per-user folder (`JournalPaths`, `mwjournal/paths.py`). The study's File setting goes.
- The writer finishes and closes a day's file before it creates the next one. That makes every file older than the newest complete.

### 2.2 Events

All events have `"version":1`. **Reader rule (new):** an unknown `type` is skipped and counted, never an error. Today `importers.py:191` raises "Unknown event type".

**`execution`:** unchanged.

**`position`** (new, study ≥ 0.4.0). One line describes one position of one account. For one (trade id, fills), the newest line wins.

| Field | Meaning |
|---|---|
| `type`, `mode`, `provider` | provider = the fills' provider (`motivewave-native:<ws>:live` or `…:replay:<run>`) |
| `account`, `symbol`, `currency` | symbol = the exact contract of the fills |
| `side` | side of the position's first fill (`buy`/`sell`) |
| `entry_execution_id` | the first fill. A reversal's second position starts with the crossing fill. |
| `fills` | every execution id of the position in order. The crossing fill of a reversal is listed in both positions. |
| `open` | true while contracts are held |
| `final` | false while the numbers can still change. True once every print up to the exit is counted and no history read is outstanding. |
| `ts` | market time the line is good through: the newest print counted, or the exit time when final |
| `last_price`, `last_ts` | the newest print counted (null until one) |
| `tick_size` | `Instrument.getTickSize()` of the fill's instrument |
| `mae_points`, `mfe_points`, `mae_held`, `mfe_held` | section 1. Null until a print is counted. |
| `path` | [[minute, low move, high move, qty], …]. On non-final lines, only the minutes that changed since this position's previous line. The first line after a (re)start and every final line carry all minutes. |
| `holes` | [[from, to], …]: stretches of the holding time with no prices (section 3.5). Empty normally. |
| `from_history` | true if any part was read from MotiveWave's tick history |
| `why` | present only when there are no numbers. Values: `no-prices`, `rewound`. |
| `after_exit` | final lines only, once flat: {`exit_ts`, `exit_price`, `exit_qty`, `through`, `highs`, `lows`, `closes`} (section 1). `through` is the market time it covers, up to exit + 120 min. |

Example (open):
```
{"version":1,"type":"position","mode":"live","provider":"motivewave-native:ws:live","account":"SIM","symbol":"NQZ6","currency":"USD","side":"buy","entry_execution_id":"E1","fills":["E1","E2"],"open":true,"final":false,"ts":"2026-10-20T14:31:07.250Z","last_price":21234.25,"last_ts":"2026-10-20T14:31:07.250Z","tick_size":0.25,"mae_points":3.25,"mfe_points":8.5,"mae_held":6.5,"mfe_held":17.0,"path":[["2026-10-20T14:31:00Z",-3.25,2.0,2]],"holes":[],"from_history":false}
```

When lines are written:
- while open: at most one per second per position, and only when something changed;
- when final;
- then `after_exit` updates on the same fills every 5 market minutes until exit + 120 min.

Sizes:
- about 0.5 KB per line while open, about 2 MB per hour in a trade;
- a final line is about 0.5 KB + 35 B per minute held + 1–5 KB of after-exit.

**`bar`:** the same fields as today plus `"mode"`. `provider` stays `motivewave-bars:1m` in both modes; `mode` routes it to the right journal, and `scope.event_mode` already honours an explicit mode.
- Written only for position windows: from entry − 120 min to exit + 120 min, capped at entry + 10,000 min like `chart.chart_window`. In replay, never past the replay clock.
- **Forming bar:** `"forming":true`, no volume. Built from the prints the measurer sees, written at most once a second while a position in that contract is open.
- The app replaces a slot with a later bar for that slot, but never replaces a complete bar with a forming one.

**`protection`:** same fields.
- `role` is always `stop`; target orders are no longer written.
- `entry_execution_id` = the linked position's first fill. `store.protection_by_trade` already links on any entry fill id, so the app needs no change.
- Linking rule: section 3.8.

**`heartbeat`:** same fields and the same 10 s cadence, written to today's files. It gains:
- `measuring`: [{"symbol":"NQZ6","how":"chart"|"history"|"waiting"}, …] for each contract with an open position or a running runner window;
- the stop diagnostic, inside `warnings` (section 3.8).

`connector_version` becomes `0.4.x`.

**No longer written:**
- `tick`, `quote`, `metric`;
- `gap` for prints;
- target `protection`;
- the 3-day and Ticks (Minutes) history loads.

### 2.3 Compatibility

- **New app with an old study (≤ 0.3.4), until MotiveWave restarts:**
  - the app reads the undated file as today;
  - tick, quote, metric and gap lines are skipped and counted;
  - fills, bars, stops and heartbeats are imported.
  - Setup says "Restart MotiveWave to finish updating the study" when `connector_version` < 0.4.0.
  - Those trades get MAE/MFE when 0.4 runs its 30-day pass (section 3.5).
- **Old app with the new study:** that app watches only the undated file and shows Stale. This only happens with a jar installed by hand, because the app installs the study it bundles (`paths.STUDY_VERSION` = `JournalPaths.CONNECTOR_VERSION`).
- Two copies of the study in one MotiveWave (jar reloaded without a restart) collide on the same `.lock` files and report the existing `ANOTHER_COPY` message.

---

## 3. The study's work (`study/src/main/java/org/mwjournal/study/`)

### 3.1 Classes after the change

| Class | Role |
|---|---|
| `MotiveWaveJournal` | Chart callbacks only. `onTick` → `measurer.print(...)` and `replayRun(...)`. `calculateValues` → register the chart as a feed and call `replayRun`. No settings reads, no history, no bars. `@StudyHeader` keeps `requiresBarUpdates=true`: without it `onTick` never fires. |
| `JournalAccountCapture` | The poller: fills, its own fill record, stops, heartbeat. It owns one `JournalPositions`, one `JournalMeasurer` and the single `mwj-history` thread for the bridge, moved here from the per-chart study. |
| `JournalPositions` (new, pure) | Fills → positions and holding states (section 1). No MotiveWave types. |
| `JournalMeasurer` (new, pure) | Ring, checkpoints, tail, runner records, forming bar, lines, state. No MotiveWave types. Unit-tested like `TickGate`. |
| `JournalHistory` (replaces `JournalBackfill`) | Runs catch-up and bar jobs on the history thread: `forEachTick(start, end, false, false, …)`, capped at 2,000,000 ticks a job, cancellable, a few jobs per pass. |
| `JournalEventWriter` | Unchanged API. Writes to the dated file of the current UTC day under the base name's lock. |
| `JournalNativeAccess` | `Fill` keeps the SDK `Instrument` from `t.getInstrument()`, which it already calls. Probes are added (section 7). |
| `JournalCaptureState` | Also stores the fill record. `saveAtomic` is reused for `.measure-state`. |
| `JournalSettings` | One note: "Add the study to the chart you trade. Nothing to set." |

**Deleted:**
- `JournalTicks`, `TickGate`;
- `JournalBackfill` (Ledger, Spans, windows, replayWindows; 471 lines);
- in `JournalPriceEvents`: `tick`, `replayProvider` and proxy marking;
- in `JournalAccountCapture`: `Holdings`, `Side`, `Book`, `Tally`, `holding`, `active`, `recording`, `flushBuffered`, `watch(gates)`;
- in `MotiveWaveJournal`: `submitHistory`, `submitBackfill`, `backfillDue`, `replaySource` and `held`.

### 3.2 Which prints measure which position

- A chart **feeds** contract X when its instrument's `getActiveSymbol()` equals X, or its `getSymbol()` does when there is no active symbol. Under that rule:
  - an NQZ6 chart and a continuous NQ chart whose active contract is NQZ6 both feed NQZ6;
  - NQH7 in roll week, a rolled continuous chart, and MNQ never feed NQZ6.
- Comparison is exact string equality. Phase 1 logs both strings to prove they match on his connections.
- The Symbol and Proxy settings go.
- Each chart registers `(chart id, contract, replay?, run)` on every callback and unregisters on `destroy` and `onSettingsUpdated`.
- Two charts feeding the same prints change nothing. Everything kept is an extreme (MAE/MFE, path, bar high/low, runner records) or the newest value (last price, bar close), and prints are never counted or summed.

### 3.3 The fill record (netting from what was written)

- Today `.capture-state` holds `fill.<key>=provider` and `label.<key>`.
- It gains `rec.<key>=account|symbol|currency|side|qty|price|timeMillis|positionId|entry|seq`, written when the fill is first written. `seq` is the write order, which is the app's `ORDER BY rowid` tie order.
- `JournalPositions` nets from the union of these records and the current snapshot. A fill MotiveWave has since forgotten is therefore still netted, as the app does.
- The poller compares each snapshot fill with its record and builds JSON only for new or changed fills (audit 2.7). Fills are no longer re-sent at each start.
- Fills written by 0.3.x have no record. At first start they are filled in from the snapshot; any that MotiveWave no longer keeps stay unknown. The app's fills check (section 4.2) keeps a disagreement from showing a wrong number.

### 3.4 The running measurement

**Tick thread.** `print(contract, replay, run, timeMillis, price)`:
- one synchronized append of (arrival ms, time, price) to the ring for (replay, run, contract);
- an update of that contract's forming-minute OHLC (by print time: high/low = extremes, close = newest time);
- no JSON, no I/O, no settings.

**The ring.** Kept for 15 s by arrival time and capped at 200,000 prints. A replay clock that steps back is a rewind: a new run with a new ring (`replayRun` already detects it).

**Poll thread, after each `poll()`:**
1. Rebuild positions when the fill record or the snapshot changed.
2. **Late fill.** A position whose new fill's time is earlier than the newest print already folded for its contract gets a history rebuild: its checkpoint is reset and [entry, now) becomes a catch-up stretch.
3. **Fold.** Each ring print that arrived more than 15 s ago is applied to the checkpoint of every position on that contract (same mode and run) whose holding state contains its time, and to every runner window that contains it. It is then dropped.
4. **Publish.** For each position: checkpoint ⊕ the prints still in the ring, evaluated against the current fills. If it changed and ≥ 1 s has passed since the last line, write a `position` line. Write the forming bar of each contract with an open position the same way.
5. **Final.** Once a position is flat, every print up to its exit has been folded (≥ 15 s after the exit) and no catch-up is outstanding, write the final line (`final:true`, full path), record `done`, and start its runner.
6. **Runner.** Write `after_exit` every 5 market minutes until exit + 120 min, then mark it finished.

### 3.5 Catch-up from tick history (one rule for every gap)

**Coverage.**
- A position is covered live from the moment a chart feeds its contract, minus the ring's reach, so a chart already open at entry covers it from entry.
- Any stretch of its holding time, or of its runner window, without coverage is a **catch-up stretch**:
  - the study was not running (restart mid-trade, study added mid-trade);
  - no chart of the contract was open;
  - a late fill caused a rebuild.

**When a stretch is read.** As soon as it is closed off: when a feed starts (study start, chart opened), or when the position or runner ends.
- Each stretch is read twice: at once and 5 minutes later, because history can lag. Reading twice is harmless, since only extremes are kept.
- While open with no chart, there are no live numbers. Heartbeat `how` is `waiting`; the app says "Open a chart of NQZ6 with the study to see MAE / MFE live".

**Instrument.**
- The fill's own `Instrument` (`forEachTick(start, end, false, false, …)`; `false` = never manufacture ticks from OHLC).
- If the phase 1 probe shows that this does not work without a chart, the job instead waits for a chart whose instrument symbol is the contract, which is today's `JournalBackfill.applies` rule. `JournalHistory.instrumentFor(contract)` is the one place that decides.

**Holes.** A stretch ≥ 60 s for which both reads return no prints becomes a `hole`, and the app shows MAE/MFE unknown with the times.

**Replay.**
- Catch-up never asks past the current replay clock.
- Only the current run's positions are caught up. Earlier runs keep what they have, with `why:"rewound"` if they never had a print.

**Restart catch-up of finished trades.**
- Closed positions from the last 30 days with no `done` entry are measured once, from history, through the same jobs.
- This replaces today's 30-day backfill. Unlike today, it also covers blank-position-id accounts, which `JournalBackfill.windows` skips.

### 3.6 State (`.measure-state`)

- Properties format, saved with `JournalCaptureState.saveAtomic`.
- Saved at most every 10 s when changed, and at once after each final line.
- Contents:
  - per active position or runner: key, fills fingerprint, the four numbers, last price and time, folded-through time, coverage start, path, holes, runner records and closes, `bars_through`;
  - `done.<trade id>=<fills fingerprint>`, pruned after 400 days.
- **On load.** A position whose fills still begin with the stored ones resumes from its checkpoint. The time from folded-through to now becomes a catch-up stretch. With no state (study added mid-trade), the stretch starts at entry.

### 3.7 One-minute bars

- **Windows.** For every position (open, final, or in its runner window; plus the 30-day pass): bars from `max(bars_through, entry − 120 min)` to the last completed minute, never past `exit + 120 min` or the replay clock.
- **Source.** `instrumentFor(contract).getBars(start, end, BarSize.minute(1), false)`, as `submitHistory` does today, with the same complete-bar checks as `JournalPriceEvents.bar`.
- **Timing.** At each new minute (wall clock live, replay clock in replay), top up from `bars_through`. Repeated slots replace earlier ones in the app.
- **Forming bar.** Section 3.4, published by the poller.

**Removed:**
- the 3-day reload on every chart load and every settings change;
- the 2-minute top-up per chart;
- duplicate writers.

### 3.8 Stops

**Orders read.** `isStopOrTrail` orders with an instrument and a state: working, filled, cancelled or rejected.

**Link** (one rule, no parentId or positionId):
- the order must be on the same mode, account and contract as a position, and on its closing side (a sell stop for a long, a buy stop for a short);
- the first time this study run sees an order id, it goes to the position whose [first fill, last fill] (to now while open) contains the order's `getLastModified()` time;
- a working stop that no position held then (a bracket's stop stamped a moment before its entry fills) goes to the open position;
- the choice is remembered per order id for the run.

**Event.** `protection`, role `stop`, `entry_execution_id` = that position's first fill, provider = that fill's provider. The same order with a changed price or state is a new line, as today.

**Diagnostic**, in `warnings` and once per order in MotiveWave's log:
- counts at each step: orders read, stop orders, skipped as new, no instrument, no state, an account and contract with a position, closing side, linked;
- up to 3 unlinked working stops, with id, parent id, position id, account, symbol, side, state and time, beside the open positions' account and symbol strings.

### 3.9 Settings

- All 12 go: File, Record Ticks, After Exit (Minutes), Symbol, Proxy Prices, Capture Fills, 1m Bars (Days), Ticks (Minutes), End (UTC), Backfill Trade Ticks (Days), Record Prices, Real Tick Data.
- Saved values of the old keys are ignored.
- The dialog shows one line: "Add the study to the chart you trade. Nothing to set."
- Done in study 0.3.10: the settings are gone and their defaults are fixed constants in `JournalSettings` (`BAR_DAYS` 3, `BACKFILL_DAYS` 30, `AFTER_EXIT_MINUTES` 30); the phases below replace the 3-day bars constant with section 3.7's windows.

---

## 4. The app's work

### 4.1 What it stores (schema 4)

**`measurements`**: one row per (trade id, fills).
```
trade_id TEXT, fills_key TEXT,            -- fills_key = digest(fills list)
fills TEXT, final INTEGER, open INTEGER, ts TEXT,
last_price REAL, last_ts TEXT, tick_size REAL,
mae_points REAL, mfe_points REAL, mae_held REAL, mfe_held REAL,
from_history INTEGER, why TEXT, source TEXT,  -- 'study' | 'migrated'
path TEXT, holes TEXT, after_exit TEXT,       -- JSON
PRIMARY KEY (trade_id, fills_key)
```
- `trade_id` is derived from the line as in section 0.
- Precedence for the same key:
  - a non-final row is replaced by any later line, and its `path` minutes are merged (min/max per minute and qty);
  - a final row (study or migrated) never changes its numbers;
  - `after_exit` is replaced only by one with a later `through`.
- When a final row arrives, non-final rows of the same `trade_id` with other `fills_key`s are deleted.

**`bars`**: `symbol, ts, open, high, low, close, volume, forming` with PRIMARY KEY (symbol, ts), WITHOUT ROWID.
- A complete bar replaces a forming one, never the reverse.

**Unchanged:** `executions`, `protection`, reviews, tags, settings.

**Dropped:** `prices`, `metrics`, `capture_events` (after migration).

### 4.2 What it computes

- **Which row.** `measurements[trade.id, digest(execution ids of trade.fills)]`, used only when `trade.complete`. Otherwise one line names the cause:

| Situation | Text |
|---|---|
| open, a newer fill not yet in a line | "Measuring…" (about a second) |
| `waiting` in the heartbeat | "Open a chart of NQZ6 with the study to see MAE / MFE live." |
| closed, no row yet | "Waiting for MotiveWave's tick history for this trade." |
| `why=no-prices` or a hole | "MotiveWave had no prices for 10:02–10:15." |
| provider is not `motivewave-native:` | "Imported trades have no MAE / MFE: the study measures trades while you trade." |
| fills differ from every row | "Fills changed by hand, or grouped differently by MotiveWave: MAE / MFE unknown." |

- **Units:** points; ticks = points ÷ row `tick_size`; dollars = held × trade point value; R = dollars ÷ `risk.plan(...).risk`.
- **Open P&L** = `trade.net_at(row.last_price)`. `common.open_pnl` already does this with the row in place of the metric.
- **Replay "now"** for an open trade = `row.ts`. This replaces `store.trade_clock`'s scan of prices.
- **Chart:** `bars(trade.symbol, start, end)` over `chart_window(trade, context)`, exact contract first, else the product (old continuous labels), never both. The forming bar is drawn as the last candle. Fills and stop segments are drawn as today.
- **Path chart:** from `row.path`, one line per side, "P&L before fees" (UX 21).
- **Runner** (`post_exit`): from `row.after_exit` (section 1).
  - The stop is the existing choice: saved, order, or breakeven.
  - A horizon h is "partial" until `through` ≥ exit + h.
  - No price-source loop, coverage ranks or bar estimates.
- **Merged reviews:** the largest of the legs' numbers. This is exact, because legs never overlap on one account and contract, and nothing carries between them. `merging.mask_overlap_points` goes.
- **Named journals** (until they become views): `mirror` copies `measurements` and `bars` rows for its scopes; `follow` copies the open trade's one row each second. Nothing is measured there.

### 4.3 Sync and the reader

- The default connection reads the bridge folder:
  - the undated `motivewave.ndjson` (legacy) first, then `motivewave.????-??-??.ndjson` in name order; the replay journal reads the replay pair the same way;
  - `bridge_offsets` is keyed `<connection>|<file name>`; the inode and checkpoint rules stay.
- **Deletion** happens after the file's lines are committed:
  - a dated file goes when it is read to its end and a newer dated file exists;
  - the legacy file goes when it is read to its end and any dated file exists;
  - the newest file is never deleted;
  - if the app stops between commit and delete, the next start finds the file fully read and deletes it then;
  - on Windows, a failed delete is retried at the next poll.
- **No measuring:**
  - `sync.recalculate`, `dirty_prices`, `measure_after` and `rebuild` go, along with the trades cache that existed for them;
  - a new fill no longer freezes live numbers (audit 2.1).
- `MODES` = (`bridge`, `executions`).

### 4.4 Setup and status

- The recording panel lists the heartbeat's `measuring` contracts instead of `store.recent_contracts`.
- The status chip stays: Connected / Stale / Needs attention.
- The stop diagnostic shows under Troubleshooting with the skipped-event counts.

---

## 5. What is deleted

**Python (`mwjournal/`):**
- **`metrics.py`, all of it.** For one release, a copy of `holding_states`, `price_boundaries` and the walk lives in `mwjournal/upgrade4.py`, used only by the migration.
- **`store.py`:**
  - prices: `ingest_prices`, `prices`, `_labels`, `price_sources`, `trade_clock`'s price scan, `recent_contracts`;
  - metrics: `record_metric`, `measure`, `measured`, `metric_options`, `best_metric`;
  - gaps: `ingest_gaps`, `capture_gaps`;
  - settings: `set_excursion_policy`, `set_tick_size` and the `excursion_policy` / `tick_sizes` defaults;
  - recalculation: `MEASUREMENT_RULES`, `recalculation_due` / `recalculation_done`;
  - schema: the `prices` / `metrics` / `capture_events` DDL and the `prices_bars` partial index.
- **`sync.py`:** as in 4.3, plus the `prices` mode.
- **`importers.py`:**
  - the tick, quote, metric and gap branches (they become skip-and-count);
  - `preview_prices_csv`.
- **`scope.py`:** everything except `event_mode` and `replay_run`. That means `LIVE_TICKS`, `HISTORY_TICKS`, `REPLAY`, `REPLAY_HISTORY`, `PROVENANCE`, `allows_source`, `is_simulated`, `is_history` and `provenance`.
- **`model.py`:** `PriceSample`, `trade_prices`. A small bars reader replaces it in `store`.
- **`post_exit.py`:** the sample walk and `best_post_exit`'s source loop. It is rewritten over `after_exit`, about 60 lines.
- **`chart.py`:** `minute_candles` (tick aggregation), the `prints` / `prints_source` parameters, provenance notes.
- **`journals.py`:** price, gap and metric copying in `follow` and `mirror`; `measure_own`; `RECENT`.
- **`merging.py`:** re-measuring in `ReviewJournal.metric_options`; `mask_overlap_points`.
- **`units.py`:** `TICK_SIZES`. The description texts become "how far the position you held went against you".

**App (`app/`):**
- **`common.py`:**
  - `policy_selector`, `display_metric`, `best_display_metric`'s ranking, `metric_options`, `recalculate`;
  - `replay_ticks_pending`, `REPLAY_TICKS_LATER`, `PRECISION`;
  - the tick-size popover in `excursion_help`.
- **`views/trades.py`:**
  - `window_prices` and `KEPT`;
  - the Chart price source and Measurement selectboxes;
  - the full recorded path checkbox, re-measure and CSV export;
  - "Recalculate From Stored Prices";
  - accuracy captions.
- **`views/preferences.py`:** MAE / MFE Accuracy.
- **`views/connection.py`:** the "Prices CSV" format and its symbol and bar-duration inputs. "Another bridge file · NDJSON" stays for files read by hand. The default connection no longer asks for a path, since there is no File setting.
- **`views/importing.py`:** the "Import My Own Prices" expander.
- **`views/edge.py`:** the help text naming Accuracy.
- **`runner_controls.py`:**
  - source and coverage codes ("observed-ticks · partial-window", "awaiting-prices");
  - the Upper bound / Lower bound lines.
  - `HORIZONS` stays.

**Tests:**
- `tests/test_carry_on.py`;
- the liquidation, precision, price-source and tick-ingest parts of `test_live.py`, `test_capture.py`, `test_chart.py`, `test_post_exit.py` and `test_journals.py`.

**Settings a trader no longer sees or sets:**
- In MotiveWave: all 12 study inputs.
- In the app:
  - MAE / MFE Accuracy;
  - Measurement;
  - Chart price source;
  - Recalculate;
  - full-path export;
  - Set Tick Size;
  - Prices CSV;
  - import "your own prices";
  - the bridge path form.
- "Context each side" and "Look after exit" stay.

---

## 6. Migration (schema 4, runs once in the watcher thread with progress in Setup)

1. **Drain.** Import whatever is still unread in the undated bridge files (fills, bars, stops). Price lines are still stored in this one step, so trades up to the upgrade have their ticks.
2. **Back up.** `store._backup_before_upgrade` runs as today. Its size is reported, because it is the only place the old ticks survive.
3. **Re-measure.** For each complete trade with no `measurements` row:
   - measure it from its stored ticks with `upgrade4.py` under section 1's definition. That means:
     - the contract's own ticks when there are some, otherwise every tick source its run allows;
     - live and history ticks together, since only extremes are kept;
     - the per-minute path;
     - `after_exit` from the stored ticks after the exit (up to 120 min, `through` = the newest one).
   - Write the row with `source='migrated'`, `final=1`.
   - Trades with only candles, proxies or bid/ask get no row and show "—", as ticks-only shows them today.
4. **Bars.**
   - Copy every stored 1-minute bar into `bars`.
   - For minutes that have ticks but no bar (old replay trades were charted from prints), build the bar from those ticks.
5. **Clean up.**
   - Drop `prices`, `metrics` and `capture_events`.
   - Delete gap rows and the `excursion_policy` / `tick_sizes` settings.
   - VACUUM.
6. **Report**, for example: "1.9 GB of recorded prices removed; MAE / MFE kept for 1,950 trades; backup at …".
7. **Named journals.** Each named journal's database runs the same upgrade when it is opened. It is the same code, because today they are full copies.

**Numbers that change** (say so once in the release notes): dollar and R MAE/MFE on trades with partial exits. Points and ticks are unchanged.

**Old bridge files.** They are deleted by the 4.3 rule. His current pair is 52 MB live and 9 MB replay. The study deletes `.tick-backfill` at its first start. `.capture-state`, `.replay-clock` and the `.lock` files stay.

**Old studies still running** (MotiveWave not restarted): their fills keep arriving with no MAE/MFE. Once 0.4 starts, its 30-day pass measures them. Migrated rows are final and are never replaced.

---

## 7. Phases (each one ships and is tested on its own)

### Phase 1: stops, the new definition, probes. Study 0.3.5 + app 0.4.8

**Study:**
- the stop link rule and diagnostic (3.8); targets no longer written;
- the poller compares fill records before building JSON;
- `.replay-clock` is saved only when the run changes, or at most every 10 s.
- **Probes,** logged once each per session:
  - `getActiveSymbol()` of every chart beside the symbol of every fill on that contract;
  - `Instrument.addListener` on a fill's instrument: count its prints against `onTick` prints for 60 s, live and replay;
  - `forEachTick` and `getBars` on a fill's instrument with no chart of it open, live and replay;
  - `getBars` with end at the replay clock;
  - `getTickSize()`;
  - the largest delay between a fill's time and when it first appears in `byAccount`.

**App:**
- unknown event types are skipped and counted;
- the new dollar definition in `metrics.py`; `MEASUREMENT_RULES = 2`, so every trade is re-measured once from its stored ticks;
- the liquidation measurement is dropped;
- Accuracy is fixed at ticks-only and its selector removed;
- the Measurement picker is hidden;
- a trade with no linked stop says "No stop order was recorded for this trade. Type your stop to get R.".

**Tests prove:**
- `JournalAccountCaptureTest` with a fixture `Reader`:
  - fills shaped like his prop fills (blank position id, `native_entry` false) plus a stop with no parent are linked;
  - a same-side stop is not linked, and its reason is counted;
  - a moved stop and a filled stop link to the same position;
  - after a simulated restart, a cancelled stop never links to a later position.
- The poll with unchanged fills builds no JSON.
- `test_capture`: an unknown event type is skipped, not fatal.
- `pytest`:
  - a scale-out gives $MAE = points × held × point value;
  - liquidation rows are gone;
  - the re-measured numbers of a copy of his journal (under `MWJ_HOME` in /tmp) become the reference for phase 2.

### Phase 2: the study measures (shadow). Study 0.4.0 + app 0.5.0

**Study:**
- `JournalPositions`, `JournalMeasurer`, the fill record, `.measure-state`, catch-up through `JournalHistory`, `position` lines;
- ticks are still written, for comparison; bars as before.

**App:**
- imports `position` lines into `measurements`;
- shows the study's numbers when a row matches the trade's fills, otherwise its own;
- Troubleshooting lists every closed trade where the two differ;
- adds `scripts/compare_measurements.py` (study vs app-from-ticks per trade).

**Tests prove:**
- `JournalPositionsTest`: identical groupings to `matching.py` on shared fixtures. A `ContractSample`-style fixture runs both sides:
  - scale in and out, reversal split, same-millisecond fills;
  - blank-id books;
  - ENTRY_MISSING / EXIT_MISSING;
  - an unpriced fill;
  - forgotten fills recovered from the record.
- `JournalMeasurerTest`:
  - two charts sending the same prints (no change);
  - history prints out of order and overlapping (no change);
  - a fill 10 s late (exact) and 20 s late (rebuild);
  - a rewind;
  - restart from state (fake TickSource);
  - no chart → `waiting`, then history at close;
  - a hole;
  - runner records and closes against `post_exit` on the same ticks.
- `test_contract`: lines written by the Java fixture equal the phase 1 Python measurement of the same prints, and attach to the right trade id.
- `scripts/test.sh` passes.

**Owner:** one week of his live and replay trading with the compare script. Target: zero differences beyond print-time ties at a fill.

### Phase 3: nothing stores prints. Study 0.4.1 + app 0.6.0

**Study:**
- no tick, quote, metric or gap events;
- bars around positions plus the forming bar;
- per-day files;
- `.tick-backfill` deleted;
- settings dialog emptied;
- `JournalTicks`, `TickGate` and `JournalBackfill` deleted;
- `heartbeat.measuring`.

**App:**
- the folder reader with deletion;
- the schema 4 migration;
- charts from `bars`; runner from `after_exit`;
- all of section 5 removed;
- `journals.follow` copies one row.

**Tests prove:**
- migration on a copy of a real journal under `MWJ_HOME` in /tmp (never his folder):
  - every trade with ticks gets a row equal to its phase 2 numbers;
  - the database shrinks by at least 90%;
  - old replay trades still chart;
- the reader deletes only complete, older files, including after a crash between commit and delete; a macOS and a Windows job;
- draining the legacy file;
- UI tests for every "MAE / MFE missing" cause in 4.2.

**Owner:**
- one live day and one replay with a rewind;
- restart MotiveWave mid-trade;
- add the study mid-trade;
- trade a contract with no chart.
- Afterwards the bridge folder holds only today's files.

**Later, a separate job:** named journals become views that read the capture journal's `measurements` and `bars`. Nothing above depends on it.

---

## 8. What only the owner can check in MotiveWave

1. **Stops:**
   - one SIM bracket and one prop-account bracket show the stop line and R;
   - a moved stop and a stop that fills are drawn correctly;
   - the heartbeat has no "not linked" warning. If it does, the log lines say why.
2. **Feed** (phase 1 probe log): a continuous NQ chart's active symbol equals the fill symbol on his connection. Also, does `addListener` on the fill's instrument deliver the same prints, live and in replay?
3. **History without a chart:** do `forEachTick` and `getBars` work on a fill's own instrument with no chart of that contract open, live and in replay? Does history match the live prints for the same trade (the phase 2 compare)?
4. **Late fills:** the largest fill delay the probe logs. Is 15 s enough on his connections and at fast replay?
5. **Replay:**
   - `getBars` bounded at the replay clock returns that day's bars and nothing later;
   - a rewind ends the old run cleanly.
6. **Restarts mid-trade:**
   - a settings change, a workspace switch, and a full MotiveWave restart each resume the numbers from `.measure-state` and fill the gap from history;
   - the study added after the entry measures from the entry.
7. **CPU:** at the NQ open and during fast replay, the poller stays light with the ring.
8. **One-week shadow** (phase 2): the compare script shows no real differences before phase 3 deletes the ticks.

---

## 9. Risks

- **No re-measuring after phase 3.** A measuring bug can no longer be fixed by re-measuring old trades, because the ticks are gone apart from the upgrade backup.
  - Mitigation: the phase 2 shadow week and the Java-vs-Python contract test.
  - A future fix can re-measure only positions MotiveWave still keeps, from history.
- **Grouping disagreement.** Same-millisecond fills listed in another order, fills from 0.3.x that MotiveWave has forgotten, or hand edits.
  - The fills check shows "unknown" with a cause, never a wrong number.
  - Troubleshooting counts such trades.
  - Fallback if they turn out common: the study sends per-interval price extremes and the app does the arithmetic. The scratch check proved this is exact.
- **Clock skew.** Fill times (broker) and print times (exchange) can differ by hundreds of ms, so prints right at a fill may land on the wrong side. Fill prices count as observations, which bounds the error. The app has the same weakness today.
- **History quality.** Trades with no chart, a restart, or a late fill depend on MotiveWave's tick history. When it returns nothing, the trade shows unknown rather than an understated number. A position held only across a market closure while MotiveWave was off can show a false hole.
- **Feature losses** (owner decisions, already implied):
  - MAE/MFE on CSV-imported and hand-edited trades;
  - price CSV import;
  - the every-print export;
  - bid/ask measurement;
  - candle estimates.
- **Migration** takes minutes once on a large journal and needs free disk of about twice the journal, for the backup plus VACUUM.
