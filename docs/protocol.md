# Bridge protocol v1

The study and the app talk through one append-only text file per environment. The study appends one
JSON object per line (NDJSON); the app reads only complete lines and remembers how far it got. A file
is used instead of a socket so that nothing is lost while the app is closed: the next time it starts,
it carries on from where it stopped.

| Environment | File |
|---|---|
| Live and paper | `<data home>/bridge/motivewave.ndjson` |
| Replay | `<data home>/bridge/motivewave.replay.ndjson` |

The data home is `MWJ_HOME` when set, otherwise `~/Library/Application Support/MotiveWave Journal`
(macOS), `%LOCALAPPDATA%\MotiveWave Journal` (Windows) or `~/.local/share/motivewave-journal`.

## Rules for writers

- One complete JSON object per line, newline after each. Never rewrite a line that has been written.
- Every timestamp is ISO-8601 with a full date and a UTC offset, for example `2026-10-07T14:00:00.123Z`.
- Give fills the exact contract symbol (`ESZ6`, not a continuous alias) and stable execution ids. Prices
  may carry whatever the chart is called (`ES`, `ESZ6`, `ESZ6.CME`): see [prices and products](#prices-and-products).
- Every event carries `"version": 1`, a `type` and a `provider`. `mode` is `live` or `replay`; an event
  without `mode` counts as live. Live events are rejected by the replay journal and the other way round.

## Rules for readers

- An event whose `type` the reader does not know is skipped and counted, never an error, so a newer writer
  never stops an older reader: the fills, stops and heartbeats around it still arrive. The app (0.4.8 and
  later) shows the count on Data → Connection. Before 0.4.8 an unknown type rejected the file's batch.

## Events

**execution**: one fill.
`provider`, `account`, `symbol`, `side` (`buy`/`sell`), `qty` (> 0), `price`, `ts`, and optionally
`execution_id`, `order_id`, `commission`, `commission_known`, `point_value` (currency per full price
point per contract), `currency` (default `USD`), `quality` (a label shown with the trade).
A repeated `execution_id` with different values is a conflict, never a second fill.
Optional, and ignored by a reader that does not know them: `native_position_id` and `native_entry` (the
platform's own position id and its entry flag), `price_known`, `currency_assumed`, `observed_at`,
`capture_kind`. The [field notes](#field-notes-for-the-motivewave-study) say what the study puts in them.

**tick**, **quote**: one observed price.
`provider`, `symbol`, `ts`, `price` (or `last`), optionally `bid`, `ask`, `volume`, `proxy`.

**bar**: one candle. `ts` is the bar's open time.
`provider`, `symbol`, `ts`, `open`, `high`, `low`, `close`, `resolution_seconds` (> 0), optionally
`volume`, `proxy`. A revised bar replaces the earlier version of the same slot.

**protection**: a stop or target that is explicitly linked to a trade.
`provider`, `account`, `symbol`, `order_id`, `ts`, `role` (`stop`/`target`), `state`
(`working`/`cancelled`/`filled`/`rejected`), `price` (required while working), and a link:
`entry_execution_id` (any fill that opened or added to the trade) or `trade_id`. Orders that are not
linked are never guessed from price.
The app reads only stops, for R (`mwjournal/risk.py`); it keeps target orders but does not show or use them.
Study 0.3.5 and later write stops only (`role` is always `stop`); the [stop link rule](#stops-protection)
says which position a stop belongs to.

**metric**: excursions reported by the platform instead of computed from prices.

The UI unit preference does not change this bridge contract: platform reports still require
currency position-equity measurements. The local price engine additionally stores `mae_points`,
`mfe_points`, `point_definition: "held-average-entry"`, and `point_precision`. Point-path bounds
are `point_low` / `point_high`; compacting a chart preserves their extrema. Ticks are converted
using a confirmed instrument tick size; R uses the risk to the trade's stop. Neither is guessed
from an unknown multiplier or a missing stop. Older currency-only reports need matching price
history or a separate valid price measurement before points/ticks can be shown.
`provider`, `units: "currency"`, `definition: "position-equity"`, `mae`, `mfe` (both >= 0), `basis`
(`last`/`liquidation`), `observations`, and `trade_id` or `account` + `symbol` + `entry_execution_id`.

**gap**: the writer had to drop events.
`provider`, `dropped` (whole number > 0), `ts`, and optionally `start_ts`/`end_ts` for the affected
price window and `symbol` when only one instrument is affected. Trades overlapping a gap are marked as
possibly understated.

**heartbeat**: the study is alive.
`provider`, `ts`, `status` (`connected`/`error`), `detail`, optionally `workspace`, `connector`,
`connector_version`, `warnings`. The app shows a stale or failing heartbeat, so a dead study is not
mistaken for a quiet day.
`error` means fills cannot be read at all, and `detail` then says why. `connected` means fills are being
read; `warnings`, when present, is one short sentence about what was skipped or assumed on the way. It is
never a reason to stop trusting the fills that did arrive.
When another copy of the study holds a bridge file (MotiveWave loaded a new study jar without a full
restart, or a second MotiveWave is running) the study cannot write to that file, and says in MotiveWave's
log, and in the heartbeat of a bridge file it can still write: `Another copy of the MotiveWave Journal
study is still running: quit MotiveWave completely and open it again.`

## Prices and products

A reader measures a trade with the prices of its product, not only of its exact symbol. The product of
a symbol is the symbol in capitals without a leading `/`, an exchange suffix (`.CME`, or `=F`) and a
trailing month code with its year digits: `NQZ6`, `NQZ26`, `NQZ2026`, `/NQZ6`, `NQZ6.CME` and a
continuous `NQ` chart are all `NQ`; `MNQZ6` is `MNQ`, another product; a CQG `ENQZ6` is `ENQ`. Nothing
is translated: fills and prices that come through the same connection share their letters.

For one source and one time window, when prices exist under the trade's exact symbol only those are
used, so in roll week the next contract's prices do not mix in. Otherwise every symbol of the product
is used, and a print recorded under two labels (two charts of one market) counts once: a bar by its
slot, a tick by everything it says apart from its symbol. Live and replay stay apart as before, and a
replay trade still uses only its own run's prices.

`"proxy": true` on a tick or bar says the price may not be the traded instrument's own; a reader counts
such prices as an estimate, never as observed ticks. The study marks as proxy:

- everything a chart writes while the user has switched on **Proxy Prices** (Advanced);
- otherwise only the history a continuous-contract chart (one whose active contract differs from its own
  symbol) loads when it opens: the one-minute bars of **1m Bars (Days)** and the ticks of **Ticks
  (Minutes)**, which MotiveWave may have back-adjusted across rolls.

Prices recorded as they trade are never marked by the chart they come from: live prints
(`motivewave-ticks`), the one-minute bars topped up as each minute closes, and replay prints are the
product's own prices on a continuous chart as on the contract's. A bar slot written again replaces the
earlier one, so on a continuous chart a topped-up minute is marked proxy again when a later history load
covers it. History-backfill ticks (`motivewave-ticks:history`, `motivewave-replay:<run>:history`) are
loaded only on a chart of the exact contract and are never proxy.

## Providers written by the study

| Provider | Meaning |
|---|---|
| `motivewave-native:<workspace>:live` | Fills and linked orders from live and paper accounts |
| `motivewave-native:<workspace>:replay:<run>` | Fills from one replay run |
| `motivewave-ticks` | Live trade prints, recorded as they happened. With the default **Record Ticks** (study 0.3.8) only while a trade is open, from a few seconds before the entry to a few seconds after the exit |
| `motivewave-ticks:history` | Real trade prints loaded afterwards from the platform's tick history, for the holding window of a past position and (study 0.3.8) the minutes after its exit |
| `motivewave-bars:1m` | One-minute candles |
| `motivewave-replay:<run>:ticks` | Replay prints, as real tick data: the default (**Real Tick Data** on). Recorded as live prints are, by default only while a replay trade is open |
| `motivewave-replay:<run>:simulated` | Replay prints with **Real Tick Data** switched off, for a replay generated from bars |
| `motivewave-replay:<run>:history` | Real trade prints loaded afterwards from the platform's tick history, for the holding window of a replay position of that run (study 0.3.3) and (study 0.3.8) the minutes after its exit |
| `motivewave-replay:bars` | One-minute candles of the days a replay plays, from the platform's history: the days before the replay's time and the whole day after it, loaded once a replayed day and topped up each replay minute (study 0.3.7). They belong to no run: the same history draws the chart of every replay trade they cover, whichever run it was in. They never measure MAE / MFE, move no open trade's replay clock, and are not recording |

A replay run is one pass through the replay clock. Rewinding starts a new run, and a replay trade is
only ever measured against prices from its own run. That holds for `:history` as for the others: its
ticks measure the trades of `<run>` only, never a live trade or another run. A reader treats them as
observed ticks, not as simulated, whatever the replay itself played.

## Field notes for the MotiveWave study

Study 0.3.8 changes no event's shape; it changes which prints are recorded. Its **Record Ticks** setting
(Advanced) has three choices: **During Trades Only**, the default, records live and replay prints only while
a trade is open, from the few seconds before the entry fill is seen (a 15-second pre-roll held in memory) to
a few seconds after the exit (from study 0.3.9 a trade is open by the same position rules that link stops:
fills netted inside MotiveWave's position id where they carry one); **Always While Study Runs** records every print the chart receives, the
default of studies 0.3.2 to 0.3.7; **Never** records none. The setting is stored under a new key,
`recordTicksV3`, so every chart saved by an earlier study takes the new default, whatever it was set to
(MotiveWave saves a default like any other choice). When fills cannot be read, During Trades Only records
every print. One-minute bars are written in every mode. The minutes after each exit, which earlier studies
recorded live (every print, or the **After Exit** minutes in the position-gated mode), are instead loaded
from MotiveWave's tick history with the holding window, as the same history providers (see **History
ticks** below). **After Exit (Minutes)** (Advanced, History; 30 by default, 0–120, the key
`postExitMinutes` as before) says how many. Like the holding window, they are loaded only on a chart of the
exact contract: on a continuous chart, only **Always While Study Runs** keeps them.

Study 0.3.5 changes no event's shape. It writes stop orders only (no take-profit orders), links them by
the [stop link rule](#stops-protection), adds the stop part of the heartbeat's `warnings`, builds a fill's
event only when the fill is new or changed, and saves `<bridge file>.replay-clock` when the replay run changes
and otherwise at most every ten seconds (and when the study closes), instead of at every poll. It also
writes a few lines starting `MotiveWave Journal probe:` to MotiveWave's log, once each per session, which
the next version needs (docs/design/study-measures.md, section 7); they are never written to the bridge.

What study 0.3.3 writes beyond the required fields. `native_position_id`, `native_entry`, `observed_at`
and `capture_kind` have been written since 0.2. New in 0.3.1: `price_known`, `currency_assumed`, the two
label clauses, the heartbeat's `warnings`, history ticks and a gap's `symbol`. Study 0.3.2 changes the
default tick policy to continuous capture while the study is running; its event fields are unchanged.
Study 0.3.3 adds the replay history provider `motivewave-replay:<run>:history`, and it is the first
version whose live (`motivewave-ticks`) and replay (`motivewave-replay:<run>:ticks` / `:simulated`)
prints actually arrive: earlier studies did not require MotiveWave's per-tick updates and so, with
default settings, wrote none. No event or field changes shape. All optional fields remain backward
compatible, and files written by study 0.1 through 0.3.2 stay valid. Study 0.3.4, with its default
settings, labels replay prints `:ticks` (its Real Tick Data setting is stored under a new key,
`realReplayTicksV2`, so charts saved with the old default take the new one) and marks only the prices described under
[prices and products](#prices-and-products) as proxy; earlier studies labelled replay prints `:simulated`
unless **Real Tick Data** was switched on, and marked everything a continuous chart wrote as proxy. Files
they wrote keep those labels. The heartbeat's `connector_version`
says which study wrote a file; the app asks for an update when it is older than the study it bundles.

**Fills (`execution`)**

| Field | Meaning |
|---|---|
| `native_position_id` | MotiveWave's id for the position the fill belongs to. Fills that share `account`, `symbol` and this id are one position. Can be empty. |
| `native_entry` | `true` when MotiveWave marks the fill as opening or adding to the position, `false` when it reduces or closes it. |
| `price_known` | Only present as `false`: MotiveWave stored no fill price (its average price was exactly 0, or not a number). `price` is then `0`, a placeholder. Quantities still reconcile; P&L from this fill is unknown. |
| `currency_assumed` | Only present as `true`: MotiveWave's account object was missing or named no currency, so `currency` is the instrument's. `account` is the SDK's account id, as for every other fill. |
| `observed_at` | Wall-clock time this copy of the fill was written. |
| `capture_kind` | `new` for a fill that appeared while the study was running, `retained` for one that was already in MotiveWave's history when the study started. |

`quality` is the plain label, followed by a clause for each thing that applies, in this order:

| `quality` | When |
|---|---|
| `MotiveWave internal capture; fees may be estimated` | Every fill (unchanged since 0.1). The app treats it as the source name, not a warning, and uses MotiveWave's commission as the fee. |
| `…; account currency assumed from the instrument` | With `currency_assumed`. |
| `…; platform reported no fill price` | With `price_known: false`. |

With both: `MotiveWave internal capture; fees may be estimated; account currency assumed from the instrument; platform reported no fill price`.

A fill's label and these two flags are decided when the fill is first written and never change after
that, so a re-sent fill is identical to its first copy (apart from `observed_at` and `capture_kind`).
Study 0.1 and 0.2 already wrote fills with `price` `0` under the plain label; those keep it, without
`price_known`. A reader must therefore treat `price` `0` from a `motivewave-native:` provider as "no
fill price" whether or not the label or `price_known` says so. For the same reason a fill first written
with an assumed currency stays marked when MotiveWave later finds the account.

A fill that first appears without a price is written five seconds later, in case MotiveWave was still
completing the record; if the price arrives in that time the fill is written once, as an ordinary one.

**Heartbeat `warnings`**

One sentence, parts joined by `; `, each part with its count. The parts, in this order:

| Part | Meaning |
|---|---|
| `N fills skipped: <reason>` | Fills that could not be described and were not written. Reasons: `account unavailable` (the SDK names no account either), `cross-currency` (the account's currency is not the instrument's), `currency unavailable`, `incomplete record` (no id, size or time), `instrument unavailable`, `multiplier unavailable`. With more than one reason each has its count, in alphabetical order: `5 fills skipped: 2 cross-currency, 3 instrument unavailable`. |
| `N fills with an assumed currency` | Fills whose account currency cannot be read right now. |
| `N fills with no fill price` | Fills MotiveWave holds without a price, including ones study 0.2 wrote under the plain label. |
| `N replay fills waiting for a replay chart with the study` | Replay fills that cannot be given a replay run yet. |
| `N fill records changed after capture (not duplicated)` | MotiveWave changed a fill the study had already written; the first version stands. |
| `N working stop orders not linked to a position: <reason> (orders: …)` | Study 0.3.5. Working stops that no position has taken for five seconds (a stop often appears a poll before the fill that opens its position), counted only while a position of the same mode (live or replay) is open: while flat, a working stop is an entry stop or a bracket's stop waiting for its entry, and nothing is said. Reasons: `no position on that account and contract`, `the open position is on the stop's own side` (a buy stop beside a long: an entry or an add, not a stop), `no position at the stop's time` (flat now, and no position on the stop's closing side was held when it was last changed); with more than one, each has its count. The parenthesis counts every order of the snapshot at each step: `R read, S stop, F new, I working without instrument, T without state, P with a position, C on the closing side, L linked`. An order's instrument is its `getTicker()`, the SDK Instrument a fill also has; studies before 0.3.6 read `getInstrument()`, MotiveWave's own type, and so left out every stop order, and from 0.3.6 the reader checks that type when it connects. A working stop with no instrument at all (MotiveWave keeps leftovers like that for months) is only counted. MotiveWave's log has one line per such stop, once: its id, parent id, position id, account, symbol, side, state and last-modified time, beside the open positions' account and symbol, word for word. A cancelled, filled or rejected stop that no position takes is counted but never warned about. |

Example: `3 fills skipped: instrument unavailable; 2 fills with an assumed currency`. Counts are of what
MotiveWave holds right now, not of new events, and a singular reads `1 fill …`. `detail` on a `connected`
heartbeat reads `Experimental read-only account capture; N fills read`; on an `error` heartbeat it is the
reason, and there is no `warnings`. A heartbeat is written every ten seconds, and at once when what it
says changes.

While any fill is skipped the study cannot be sure which positions are open, so it records every live
print instead of only those around a position.

<a id="stops-protection"></a>**Stops (`protection`)**

Study 0.3.5. The orders read are MotiveWave's stop and trailing-stop orders that have an instrument and are
working, filled, cancelled or rejected; take-profit (limit) orders are not read, and orders MotiveWave has not
sent yet (new) are skipped. A stop is linked by one rule, with no parent order or position id of the order's
(prop accounts fill with neither):

- it must be on the same mode, account and contract as a position, and of the same replay run, and on its
  closing side: a sell stop for a long, a buy stop for a short;
- the first time this run of the study links an order id, the stop goes to the position held at the
  order's last-modified time (from its first fill to its last, or to now while open); a working stop that no
  position held then (a bracket's stop placed a moment before its entry fills) goes to the open position. At
  the instant one position ends and the next begins, a stop that ended then (filled, cancelled or rejected)
  goes to the one that ended, and a working stop, stamped then as a bracket's stop is when its entry fills,
  to the one that began;
- that choice is kept for the order id for as long as the study runs, so a stop that is moved, filled or
  cancelled stays with its position. After a restart a stop left working, and not moved, after its position
  was closed by hand stays with that position, and a cancelled stop never goes to a later one. MotiveWave
  stamps a bracket's stop up to about 0.1 s before its entry fill, so a stop of a re-entry on the same side
  within that time of an exit would go to the position that exited.

Positions, for this rule, are the trades the app makes of the fills the study has written (`mwjournal/matching.py`),
per account, contract and replay run: fills with MotiveWave's position id are netted inside that position,
the others flat to flat, in time order (a fill MotiveWave calls an entry of its position first at one
instant), with a reversal starting the next position at the fill that crosses. A position whose kept fills
contradict its entry flags, or that a newer position id has replaced, is never open. The position ids keep
one fill MotiveWave no longer holds (a SIM account reset in mid-trade) from offsetting every later position of
that account and contract. `entry_execution_id` is the position's first fill and `provider` that fill's provider. The same order with
another price or state is another line, as before. Earlier studies linked a stop only through its parent
order or position id to an entry fill, so a prop account's stops were never written.

**History ticks (`motivewave-ticks:history`)**

Ordinary `tick` events in the live file, `mode` `live`, `proxy` `false`, `symbol` the exact contract of
the fills. They are real trades returned by the platform's tick history (never generated from bars) for
two windows of a position: its holding window, its first fill to its last, five seconds either side, for
MAE / MFE; and (study 0.3.8) the minutes after its exit, its last fill to `After Exit (Minutes)` later
(default 30, 0 = none), for the runner analysis. A position is the fills that share `account` and
`native_position_id`.

A position's windows are loaded when it is flat in the fills MotiveWave retains (so not while it is open,
and not when its entry or exit is missing), was traded on the exact contract of the chart the study is
on (never from a continuous or stand-in chart), is not replay, has a position id, lasted no longer than
24 hours, and ended within the study's `Backfill Trade Ticks (Days)` setting (default 30, 0 = off, for
both windows). The holding window is loaded once the position ended at least five minutes ago; the
minutes after the exit once they are over and five more minutes have passed, whole. Every holding window
that is due is loaded before any minutes after an exit, so MAE / MFE never wait for them. The part of a
window that another window already covered is not loaded again, so positions that overlap or were copied
across accounts, and exits a few minutes apart, share their prints.

Each stretch is loaded once: the study remembers it in `<bridge file>.tick-backfill` beside the bridge
file (symbols and times only). An empty answer is not remembered there, and is asked for again the next
time the study loads. A position recorded live is loaded as well, so its prints can appear under both
`motivewave-ticks` and `motivewave-ticks:history`; the two are separate price sources and are never to
be added together.

A window with more than 2,000,000 ticks is cut there, and a `gap` event with provider
`motivewave-ticks:history`, the `symbol`, the number of ticks left out as `dropped` and their time span
as `start_ts`/`end_ts` says so.

**Replay history ticks (`motivewave-replay:<run>:history`)**

Study 0.3.3. Ordinary `tick` events in the replay file, `mode` `replay`, `proxy` `false`, `symbol` the
exact contract of the fills, from the same tick history call as the live backfill (real trades, never
generated from bars). `<run>` is the run of the position's fills, the one in their provider
`motivewave-native:<workspace>:replay:<run>`, so each run has its own copy.

A replay position is the replay fills that share a run, `account` and `native_position_id`. Its window
is loaded when it is flat, its last fill is no later than the replay's current time, it lasted no longer
than 24 hours, and it ended within `Backfill Trade Ticks (Days)` of the replay's current time (0 = off),
while the study is on a replay chart of the exact contract with **Record Prices** on. The window is the
same as live (first fill to last, five seconds either side) except that it never ends later than the
replay's current time: nothing after the replay's present is asked for or written. The rest of a window
cut that way is loaded once the replay has played past it. The minutes after a replay exit (study 0.3.8)
are the same as live, the last fill to `After Exit (Minutes)` later, and are cut the same way: they are
loaded as the replay plays through them, a little at a time, never ahead of it.

Each stretch is loaded once per run: the study remembers it in `<replay bridge file>.tick-backfill`
beside the replay bridge (run ids, symbols and times only; a run not written to for 400 days is
forgotten). A restart does not load it again, and neither does a rewind that plays the same moments
again: the rewind starts a new run, and only that new run's own trades get a copy of their own. An empty
answer is asked for again only the next time the study loads.
The cap and its `gap` event are as live, with provider `motivewave-replay:<run>:history`.

## Example

```jsonl
{"version":1,"type":"execution","mode":"live","provider":"my-platform","account":"paper","symbol":"ESZ6","side":"buy","qty":1,"price":5000,"ts":"2026-10-07T14:00:00Z","execution_id":"fill-1","commission":2,"point_value":50,"currency":"USD"}
{"version":1,"type":"tick","provider":"my-platform-prices","symbol":"ESZ6","ts":"2026-10-07T14:00:01Z","price":5001,"bid":5000.75,"ask":5001.25}
{"version":1,"type":"bar","provider":"my-bars","symbol":"ESZ6","ts":"2026-10-07T14:00:00Z","open":5000,"high":5002,"low":4999,"close":5001,"volume":123,"resolution_seconds":60}
{"version":1,"type":"protection","provider":"my-platform","account":"paper","symbol":"ESZ6","entry_execution_id":"fill-1","order_id":"stop-1","ts":"2026-10-07T14:00:00Z","role":"stop","state":"working","price":4998}
{"version":1,"type":"execution","mode":"live","provider":"my-platform","account":"paper","symbol":"ESZ6","side":"sell","qty":1,"price":5001,"ts":"2026-10-07T14:02:00Z","execution_id":"fill-2","commission":2,"point_value":50,"currency":"USD"}
{"version":1,"type":"heartbeat","mode":"live","provider":"my-platform","ts":"2026-10-07T14:02:10Z","status":"connected","detail":"ok"}
```

Anything that can write these lines can feed the journal; the MotiveWave study is the first writer.
