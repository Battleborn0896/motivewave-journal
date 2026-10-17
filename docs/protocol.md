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
- Use the exact contract symbol (`ESZ6`, not a continuous alias) and stable execution ids.
- Every event carries `"version": 1`, a `type` and a `provider`. `mode` is `live` or `replay`; an event
  without `mode` counts as live. Live events are rejected by the replay journal and the other way round.

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
`entry_execution_id` or `trade_id`. Orders that are not linked are never guessed from price.

**metric**: excursions reported by the platform instead of computed from prices.

The UI unit preference does not change this bridge contract: platform reports still require
currency position-equity measurements. The local price engine additionally stores `mae_points`,
`mfe_points`, `point_definition: "held-average-entry"`, and `point_precision`. Point-path bounds
are `point_low` / `point_high`; compacting a chart preserves their extrema. Ticks are converted
using a confirmed instrument tick size; R uses the saved initial total risk. Neither is guessed
from an unknown multiplier or missing risk. Older currency-only reports need matching price
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

## Providers written by the study

| Provider | Meaning |
|---|---|
| `motivewave-native:<workspace>:live` | Fills and linked orders from live and paper accounts |
| `motivewave-native:<workspace>:replay:<run>` | Fills from one replay run |
| `motivewave-ticks` | Live trade prints, recorded as they happened |
| `motivewave-ticks:history` | Real trade prints loaded afterwards from the platform's tick history, for the holding window of a past position |
| `motivewave-bars:1m` | One-minute candles |
| `motivewave-replay:<run>:ticks` | Replay prints declared as real tick data |
| `motivewave-replay:<run>:simulated` | Replay prints generated from bars |

A replay run is one pass through the replay clock. Rewinding starts a new run, and a replay trade is
only ever measured against prices from its own run.

## Field notes for the MotiveWave study

What study 0.3.2 writes beyond the required fields. `native_position_id`, `native_entry`, `observed_at`
and `capture_kind` have been written since 0.2. New in 0.3.1: `price_known`, `currency_assumed`, the two
label clauses, the heartbeat's `warnings`, history ticks and a gap's `symbol`. Study 0.3.2 changes the
default tick policy to continuous capture while the study is running; its event fields are unchanged.
All optional fields remain backward compatible, and files written by study 0.1 through 0.3.1 stay valid.

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

Example: `3 fills skipped: instrument unavailable; 2 fills with an assumed currency`. Counts are of what
MotiveWave holds right now, not of new events, and a singular reads `1 fill …`. `detail` on a `connected`
heartbeat reads `Experimental read-only account capture; N fills read`; on an `error` heartbeat it is the
reason, and there is no `warnings`. A heartbeat is written every ten seconds, and at once when what it
says changes.

While any fill is skipped the study cannot be sure which positions are open, so it records every live
print instead of only those around a position.

**History ticks (`motivewave-ticks:history`)**

Ordinary `tick` events in the live file, `mode` `live`, `proxy` `false`, `symbol` the exact contract of
the fills. They are real trades returned by the platform's tick history (never generated from bars) for
a position's holding window: its first fill to its last, five seconds either side. A position is the
fills that share `account` and `native_position_id`.

A window is loaded when the position is flat in the fills MotiveWave retains (so not while it is open,
and not when its entry or exit is missing), was traded on the exact contract of the chart the study is
on (never from a continuous or stand-in chart), is not replay, has a position id, lasted no longer than
24 hours, ended at least five minutes ago and ended within the study's `Backfill Trade Ticks (Days)`
setting (default 30, 0 = off). The part of a window that another window already covered is not loaded
again, so positions that overlap or were copied across accounts share their prints.

Each stretch is loaded once: the study remembers it in `<bridge file>.tick-backfill` beside the bridge
file (symbols and times only). An empty answer is not remembered there, and is asked for again the next
time the study loads. A position recorded live is loaded as well, so its prints can appear under both
`motivewave-ticks` and `motivewave-ticks:history`; the two are separate price sources and are never to
be added together.

A window with more than 2,000,000 ticks is cut there, and a `gap` event with provider
`motivewave-ticks:history`, the `symbol`, the number of ticks left out as `dropped` and their time span
as `start_ts`/`end_ts` says so.

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
