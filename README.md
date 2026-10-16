# MotiveWave Journal

A free, local-first trade journal: a read-only MotiveWave study records fills and prices; a Python
app automatically reads them and provides trade charts, reviews, tags, statistics, MAE and MFE.
Live/paper and replay have separate databases. No routine CSV export/import is required.

MotiveWave Journal is an independent open-source project. It is not affiliated with, endorsed by or
supported by MotiveWave Software.

## What's new in 0.4.1: simpler screens

0.4.1 changes how the journal looks and where things are, not what it can do. Existing journals,
tags, categories and reviews are kept exactly as they are.

- **Three pages.** The sidebar holds only Live / Replay, **Trades**, **Stats** and **Setup**, and the
  study's state (Connected, Stale, ...). Click the state to open Setup. Nothing in the sidebar folds.
- **Trades** lists trades under day headers with each day's net P&L and trade count. Each row is one
  line: entry time, contract, side, size, net P&L and how long the trade lasted. Click a row, or use
  **← Prev / Next →**, to open a trade. New trades appear by themselves; there is no refresh button.
  **Filters** and **Options** (chart settings, merge and split) are two small menus at the top.
- **Tags start ready to click.** A new journal opens with three starter categories (Setup,
  Execution, Mindset). One click tags the trade. Rename, recolour, retire or delete them under
  **Setup → Tags & Playbooks**; deleted starters never come back. Journals from 0.4.0 keep their own
  library and are never given the starters.
- **Plain times and money.** Every time is shown in one display time zone (this computer's, unless
  you choose another under **Setup → Display**), for example *Oct 15, 11:52 AM*. Money carries its
  currency sign: *-$152.48*, *+$810.02*. Stored fill times stay UTC.
- **Stats** opens on five numbers (Net P&L, Win Rate, Average Trade, Profit Factor, Largest
  Drawdown). The equity curve counts trades along the bottom, so nights and weekends take no room;
  **By Date** puts it on the clock instead.
- **Setup** shows the study's state with one line saying what to do, the three steps to start
  capturing, the display settings (time zone, MAE / MFE units and accuracy, saved filter, hide
  weekends), and one button for every other page: Tags & Playbooks, Rating Systems, Checklists,
  Dashboards, Daily Report Card, Session Tools, Connection, Journals & Accounts, Import Trades,
  Export & Backup. Explanations moved into the small **?** beside each control.

## Using the journal

The sidebar has three pages: **Trades**, **Stats** and **Setup**. Every other page (dashboards,
the Daily Report Card, session tools, checklists, tags, imports, backups) is one click away on Setup.
The trade list stays on the left while the selected trade's chart and tags stay on the right.
Click a row, or use Prev / Next; no separate opening/back-navigation step is needed.
**Chart & Tags** puts the chart and a grouped, clickable tagging panel side by side. Click a tag to
toggle it; changes save automatically. **＋ Tag** creates a reusable tag and applies it to the current
trade; **＋ Category** creates a category. **More Tagging Tools → Copy Tags From Another Trade** adds
another trade's tags without replacing existing tags or notes. Day tags you explicitly select are
inherited by trades opened on that local calendar date; otherwise no trade is automatically tagged.
Setup → Connection also lets you pick a captured instrument instead of retyping its symbol.
**Notes → Use saved setup** applies an existing setup/playbook name in one selection;
new setup names can still be entered normally.

**MAE / MFE units** is under **Setup → Display**: choose account currency, points, ticks or R. The
preference is remembered independently for Live and Replay, and applies to the trade list, detail
cards, excursion plots and runner views. Actual net P&L is unchanged.

**MAE / MFE accuracy** is a separate control. **Recorded ticks only** is the default: OHLC bounds,
proxies, platform-only reports, simulated replay and sources with reported capture gaps do not
qualify. Missing qualifying ticks show unavailable, not zero. **Allow labelled estimates** is an
explicit opt-in. One-minute candles can still be used as chart context without estimating MAE/MFE.
This is a source policy, not proof that an observed feed delivered every tick. JSON retains all raw
measurement alternatives; summary excursions/CSV follow the accuracy policy in account currency.

- Currency: gross realised + unrealised whole-position P&L, before fees.
- Points: directional price move from the **currently held average entry**, regardless of size.
  Scale-ins update that average; partial-exit realised profit is not added to price movement.
- Ticks: those points divided by the contract's confirmed minimum price increment. Outright
  ES/MES/NQ/MNQ are detected automatically; other symbols need a one-time size in MAE/MFE or
  Setup → Connection. Unknown sizes stay unavailable, not guessed. Average entries can produce fractional ticks.
- R: whole-position currency excursion divided by saved **initial total planned risk**. Missing
  risk stays unavailable, not zero.

Price extrema use all available observations and confirmed fill prices, before chart compaction.
Existing stored price measurements can supply points on demand; a platform-only currency report
cannot safely be divided by an arbitrary size to invent a price path. Source quality, OHLC bounds,
replay simulation and capture-gap warnings remain visible. Points and currency can therefore select
different measurement sources. Points remain available without a cash multiplier; currency/R do not.

Stats uses four in-place tabs: **Overview** for cumulative results, **Compare** for tag/setup or
market/time comparisons, **MAE / MFE** for price/position excursions, and **Runners** for the separate
post-exit hypothesis. Tables lead with sample count and average net result per trade. Comparisons
are descriptions of your recorded sample, not proof of an edge. **Customize** saves the
visible tabs and their order per environment. Filters support all/any selected tags, excluded
tags, direction, process rating and playbook combinations. Required historical tag aliases still
resolve after a rename; unknown required names match nothing, not everything.

**Setup → Tags & Playbooks** edits names, colors, group membership, order and availability on
trades/days, and deletes a tag or category that nothing uses. Stable tag IDs and aliases preserve historical associations when renamed. Retirement
hides a tag from new choices without deleting its history; unretire it to restore the choice.
Exclusive groups allow one current choice; enabling exclusivity does not erase historical conflicts.
Day tags use the entry's calendar date in the configured timezone (not an exchange-session roll).
Trade-level choices and unchecked inherited tags override the day. Changing the timezone does not
migrate existing daily reviews or selections.

Playbooks accept alternatives within each participating group (OR), require every participating
group (AND), and ignore other groups. Matching is automatic; applying a combination is explicit.
For exclusive alternatives, application retains a matching choice or selects the first listed tag.
Non-exclusive groups apply the saved alternatives together. **Check This Combination** tests
hypothetical selections without retagging the trade and includes only trades closed before this
entry in the same currency; its sample is descriptive, not a forecast.

The outright index tick defaults follow CME's [Micro equity product overview](https://www.cmegroup.com/education/courses/micro-e-mini-futures/micro-e-mini-futures-products-overview),
[NQ specifications](https://www.cmegroup.com/markets/equities/nasdaq/e-mini-nasdaq-100.contractSpecs.html)
and [MNQ specifications](https://www.cmegroup.com/markets/equities/nasdaq/micro-e-mini-nasdaq-100.contractSpecs.html).
These are not spread/option specifications; contract-specific overrides are stored separately.

Use the in-place **Notes** tab and the **More** tab (MAE / MFE, Executions, Runner, Checklists)
instead of scrolling through a long stacked page. Setup/notes autosave when leaving the field;
planned risk/SL/TP use **Save Plan**. **Prev / Next** reviews adjacent trades while
the list remains visible. A tag edit that removes the current trade from your filters keeps it
open until you change the filter/selection, rather than interrupting the review. Existing flat tags
remain under **Other tags**. **＋ Category** creates first-class groups such as Orderflow or Psychology,
including empty groups; **＋ Tag** creates and immediately applies a reusable tag. A journal from 0.4.0
keeps its untouched Setup, Execution and Mindset suggestions hidden unless **Show starter examples**
(under More Tagging Tools) is on, and shows them when it has no categories of its own. Any starter
tag already used on a trade remains visible. Renames display the current label but keep the original stored review
and aliases. Tag libraries, playbooks and day tags are local and live/replay-specific, included in
JSON/SQLite backup. No destructive schema migration is required.

The charcoal workspace, yellow accents, outcome-tinted trade rows and coloured selected tag groups
follow the same visual direction as Tradecraft. Selection outlines and signed P&L remain alongside
colour cues. This workflow is inspired by its [public daily-trades documentation](https://support.tradecraft.club/kb/daily-trades-section/)
and official tutorials, not its proprietary implementation or brand assets. Study setup, imports
and backups remain in Setup. See [reference notes and remaining gaps](docs/tradecraft-reference.md).

**Markets** automatically groups recognised futures expiries: NQZ6/NQM7 → NQ, ESZ6/ESH7 → ES,
and MNQZ6/MNQM7 → MNQ. Micros remain separate from full-size contracts. Use **Filters → More Filters →
Exact contracts** to narrow to one expiry, or **Stats → Compare → Market / Exact contract**
to compare them. Unknown symbols stay unchanged.

This is an analytics rollup, not a continuous futures price series: fills, round trips, protective
orders, charts and MAE/MFE always retain the exact traded contract. No stored trades are rewritten,
no rollover price gaps are silently adjusted, and one expiry's ticks never substitute for another's.

## Dashboard and session workflows (0.3)

**Setup → Dashboards → Customize** creates/clones/renames pages, chooses 1–3 columns, adds/edits/removes
and reorders widgets. Summary, equity, drawdown, calendar, comparisons, distributions, MAE/MFE,
tag scores, ratings, radar, trades and custom-statistics widgets are independently configurable.
The default uses two independently stacked columns so the calendar doesn't push the equity curve
below an unnecessarily tall row. Global saved filters, page filters and widget filters intersect;
they never modify captured trades. Saved tag rules use stable identities. Calendar buttons open
the exact day's Report Card. **Setup → Display** can hide weekends and sets the display time zone.

Tag scores are signed values configured on each tag. A trade's score is the sum of its selected
tag scores; a widget can restrict the contributing tags. **Setup → Rating Systems** creates
weighted tag criteria, optionally applicable only when a playbook matches. Ratings are satisfied
weight / applicable weight × 100, or unavailable when no criterion applies. Manual stars stay
independent. These are process measures, not P&L, forecasts, or claims of Tradecraft's exact formula.

**Checklists** contains an offline drag-and-connect node canvas: questions, answers, pass-through
and counter nodes; selection, duplication, color/text/tag edits, alignment, start-node choice,
undo/redo, and explicit save. Questions may branch only to answers. Answer loops must reach a
manual question; automatic endless loops are rejected. Definitions have JSON export/import and
non-destructive local versions. Runs in trade review or Session tools retain their definition
snapshot, answers, counters and collected tags. Applying those tags is explicit; no checklist can
place, modify or block an order. This editor is not pixel/shortcut/format compatible with CheckMate.

**Session tools** provides an optional repeating check-in timer. Groups are configured by clicking;
check-ins are logged to the session or an explicitly selected captured position. Timer state is not
persisted or restarted automatically, and there are no background notifications or sounds.
Pre-trade drafts are not fake executions. An armed draft needs a precise provider/account/exact
contract and a market-clock window, at most 24 hours. Only newly captured trades qualify. Ambiguous
matches attach nothing; cancellation preserves the draft record. Existing reviewed groups, notes
and risk take precedence. Reconciliation runs after bridge polling, not during a read-only render.

Custom-statistics scripts require an explicit **Run Local Script** click for each changed code or
trade sample. Code runs in a two-second Web Worker inheriting a restrictive CSP, with no DOM,
account IDs, raw fills, file access, broker access or external libraries. Return a text result or
up to 5,000 finite values in a line/bar series. This is intentionally a smaller surface than
Tradecraft's arbitrary DOM/CDN scripting; do not describe it as full script-widget compatibility.

## Status and limitations

Experimental. Fixture tests exercise the Java writer through the Python reader and app. The native
method-contract check resolves the installed MotiveWave method names, but neither test proves
capture of your actual Rithmic/manual or replay fills inside a running MotiveWave session.
That acceptance check is still required. The study never places, changes or cancels orders.

## SCS-style capture, with MotiveWave

The study records fills, linked order updates and prices into newline-committed local NDJSON;
the app watches it without routine exports. This follows the platform-connector/local-app
pattern documented by [SCS Trading Journal](https://www.scstudies.com/trading-journal), not its
proprietary implementation. Tick prices remain stored at their original resolution. The default
P&L chart is compacted for display; **Show/export full recorded trade path** reconstructs every
available observation with held quantity, average cost and realised P&L. Missing prices are not
invented, and OHLC bounds remain estimates.

### Study-only capture

The desktop app contains no screen, video, audio, microphone, camera, OBS or FFmpeg recorder.
Automatic capture comes only from the MotiveWave study: fills, partials, linked protection, ticks,
bars and replay events are appended to the local bridge file. The app reads that file and never
needs permission to see the desktop.

### Post-exit runner analysis

The study now defaults to **Always While Study Runs**: every trade print MotiveWave delivers to that
exact-contract chart is retained, so 5/15/30-minute post-exit windows do not depend on a separate
recorder. The lower-storage **Trade + After Exit Only** option keeps a bounded pre-roll, the holding
window and **After Exit (Minutes)** (30 by default, 0–120 configurable). Neither mode holds a position
or modifies an order. Both require the chart/study/feed to keep running; `Never` disables ticks.

In Trades, open **More → Runner** and expand **Could I Have Let This Trade Run?**. In Stats, open
**Runners** and expand **Which Tags Have Room for Runners?**. Choose a fixed 5/15/30/60/120-minute horizon. Analysis reports extra peak gross profit,
adverse movement, the observed fixed-horizon result, and approximate time to the peak. The
hypothesis retains only the final exit fill's quantity; earlier partial exits remain unchanged.
Planned total initial risk is required for R statistics. Compare winners or exits near 1R by tag/setup.
Choose an explicit frozen stop: saved original plan/first recorded linked stop, recorded working
stop at exit, or entry-average gross breakeven. Missing/ambiguous stop levels stay unavailable.
Tracking ends at the FIRST observed stop touch or the pre-selected horizon. Peaks after a stop
touch are never credited. If no stop is touched by the cutoff, the result is labelled still running
at cutoff, not the final lifetime peak. Stop and peak in the same OHLC candle are order-ambiguous:
report conservative/possible bounds and exclude that case from default comparisons.

Incomplete/gapped windows are excluded from default comparisons. Observed ticks, OHLC estimates,
proxies and simulated replay are separate groups; bars overlapping exit or the horizon are excluded.
This is a frozen-stop price-path hypothesis, not an execution-accurate strategy backtest or an
achievable-profit claim. No spread/slippage, additional fees, dynamic trailing rules or perfect
peak execution is simulated. Tick completeness and actual stop fills remain unverified.
Do not add hindsight peaks to expectancy; validate a fixed runner rule on unseen trades next.

Prices use the public SDK. Manual-account fill capture uses an allow-listed, read-only adapter to
MotiveWave internals, **not its supported SDK**; a platform update can break it. Connection status
shows errors, missing files and stale heartbeats. The observer only sees retained executions, not
a guaranteed full broker archive. Linked SL/TP updates are polled and very fast changes can be missed.

## Start the app

### macOS desktop window

The local desktop build opens **MotiveWave Journal.app** with a Dock icon, native window/menu,
file dialogs and an embedded WebKit journal interface. No browser tab is opened. It uses the same
live/replay databases and study bridge as the browser version; it has no screen-media recorder.

Build the self-contained tester DMG with a Python environment that contains the app dependencies
and PyInstaller:

```sh
python scripts/build_macos_release.py --python /absolute/path/to/venv/bin/python
```

Output: `build/release/MotiveWave-Journal-<version>.dmg`. It bundles its own Python runtime, journal
source and study JAR; the tester does not need Python or JDK. The default build is ad-hoc signed for
known testers. Public friction-free distribution still needs a Developer ID signature and Apple
notarization; the release builder supports both when credentials are supplied. The older
`scripts/build_desktop.py` command remains available only for quick developer-local builds.

Before sharing an alpha, follow the [tester installation and acceptance guide](docs/ALPHA_TESTING.md).

An identified journal already running on port 8600 is reused; unrelated services are not reused.
Otherwise the app starts its own localhost-only backend (using another free port if needed).
Quit stops only a backend it started itself; a reused browser backend keeps running. Closing an
owned backend also stops its background sync. The MotiveWave study continues writing its
files independently. Diagnostics: data folder `desktop/backend.log`; Help opens that folder/log.

### Windows desktop window

Run `python scripts/build_windows.py` on a 64-bit Windows 10/11 build machine after installing
`desktop/windows/requirements-build.txt`. It produces a complete portable folder and
`build/windows/MotiveWave-Journal-Windows-x64.zip`. The tester extracts the whole ZIP and opens
`MotiveWave Journal.exe`; its native pywebview window and localhost backend do not require Python.
PyInstaller does not cross-compile Windows executables from macOS. GitHub's **Build tester apps**
workflow builds both platforms. See [tester distribution and acceptance](docs/distribution.md).

### Browser/local server

From this repository, with Python 3.11 or newer:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
bash run.sh
```

Windows: use `.venv\Scripts\python.exe` for installation and launch with
`.venv\Scripts\python.exe -m streamlit run app/app.py` from the repository root.
The app is bound to localhost. No hosted service or separate Rithmic API subscription is required
by this app; your MotiveWave/broker data entitlements still apply.

## Build and connect the study

Install JDK 25 and MotiveWave. Then:

```sh
bash study/scripts/build.sh
```

The output is `study/build/libs/motivewave-journal.jar`. A packaged desktop user can click
**Setup → Install MotiveWave Study**. The app atomically installs only this named JAR, preserves its
previous copy and asks for a MotiveWave restart. Manual alternative: use **Study → All Studies →
Import**. Then add **Journal → MotiveWave Journal** to each exact contract
chart you trade. Keep the study's File default. The app starts watching both files by itself.
Check **Setup** in the app: it should change from Waiting For Study to Connected. To install the
study again after an app update, use **Setup → Connection → Install MotiveWave Study**.
See [MotiveWave's import instructions](https://docs.motivewave.com/knowledge-base/study/add-a-custom-study).

Use `MOTIVEWAVE_SDK` for a non-default SDK path and `JAVA_HOME` for the JDK. No SDK/native platform
jars are bundled. `build.sh deploy` explicitly installs the JAR into your extensions folder;
ordinary build/test commands do not install it. Do not run two different capture studies against
the same bridge file.

Default data folders:

| OS | Folder |
|---|---|
| macOS | `~/Library/Application Support/MotiveWave Journal` |
| Windows | `%LOCALAPPDATA%\MotiveWave Journal` |
| Linux | `~/.local/share/motivewave-journal` |

The study writes `bridge/motivewave.ndjson` and `bridge/motivewave.replay.ndjson`; databases are
`trades.sqlite3` and `replay/trades.sqlite3`. Set `MWJ_HOME` to override the folder, but a desktop
MotiveWave will not necessarily inherit your terminal environment. If overriding only the app,
copy the live bridge path shown in Connection into the study's File setting.

## Tick accuracy and replay

- **Always While Study Runs** is the default. It records every trade print delivered to the chart,
  including the time after exit used by runner analytics. It uses more disk, and recording stops if
  the chart, study or feed stops.
- **Trade + After Exit Only** keeps a bounded pre-roll, records while the observed position is active,
  and continues for the configured post-exit window. The account poll also drains observed pre-roll
  after seeing fills, so a paused replay does not need another tick. Unknown/stalled capture records
  rather than guessing flat. **Never** disables streaming ticks; history loading is separate.
- Historical one-minute bars load in live mode. Optional historical real-tick loading defaults off
  and is currently capped at 120 minutes by this implementation, not by a claimed Rithmic limit.
  It requests real ticks, not manufactured OHLC ticks. Availability is up to the data service.
- Replay records only prices already delivered by replay, never a future historical path. Rewinding
  starts a new run; prices from another run cannot measure that trade. Set **Real Tick Data** only
  when replay actually uses tick data; otherwise measurements stay labelled simulated/unverified.
  See [MotiveWave's replay sources](https://docs.motivewave.com/user-guide/replay-mode).
- Tick-derived one-minute candles represent the received prints; position-gated candles can be
  partial. OHLC estimates, proxies, simulated replay and observed ticks stay visibly distinct.
  Missing data means unavailable MAE/MFE, not zero. Observed ticks do not prove feed completeness.

Currency MAE/MFE are non-negative magnitudes of **gross realised plus open-position P&L** across
the holding window, including scale-ins/outs. Fees are excluded from excursions; recorded fees
reduce net trade P&L. Native fees may be platform estimates. Stops/targets require an explicit
order/position link; a saved planned level is labelled separately from a captured order.

## Verify

```sh
.venv/bin/python -m pip install pytest
bash scripts/test.sh
# Optional macOS-only, read-only method-name check:
bash study/scripts/check_contract.sh
```

`scripts/test.sh` builds/tests Java, writes synthetic live/replay bridge files to a fresh build
subfolder, and runs Python tests against those files. Set `MWJ_PYTHON` to choose another interpreter.
Python-only tests: `python -m pytest -q`; the two cross-language tests explicitly skip unless their
Java samples were supplied. The contract check does not launch MotiveWave or initialise account code.

For actual acceptance, install/add the study yourself, check its fresh heartbeat, and verify a trade
you chose to take against MotiveWave's fills (time, price, size, symbol), linked orders and received
price extrema. Repeat in replay, pause immediately after a short round trip, and then rewind.
Never count a fixture chart as proof of that integration.

Stats separates gross/net closed-trade curves and recorded fee drag, including fees in R
when initial planned risk was saved. Fees are not slippage and a closed-trade curve is not marked
account equity. The app also offers optional CSV/BYOD imports, daily reviews and JSON/CSV/SQLite backup.

## Design and future work

[Bridge protocol](docs/protocol.md) · [Carver-inspired analytics direction](docs/analytics-direction.md)

MIT licensed; see [third-party notices](THIRD_PARTY_NOTICES.md). Independent of MotiveWave Software.
