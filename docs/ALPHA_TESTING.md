# Alpha tester guide

This is an early, local-first tester release. Use a MotiveWave **simulation account or Replay**
before considering live-account observation. The study is read-only: it cannot place, modify or
cancel orders. It records account fills, linked stop/target observations and prices delivered to the
chart. Journal data stays on the tester's computer.

## Install

### macOS

The current tester DMG is for Apple Silicon Macs running macOS 13 or newer. Drag **MotiveWave
Journal** to Applications. The build is ad-hoc signed rather than Apple-notarized, so the first launch
may require Control-clicking the app and choosing **Open**.

### Windows

Extract the entire `MotiveWave-Journal-Windows-x64.zip`, then open `MotiveWave Journal.exe`. Do not
move the EXE away from its `_internal` folder. Current Windows 10/11 systems normally include the
Edge WebView2 runtime the native window uses. Windows may show a SmartScreen warning because the
alpha is not Authenticode-signed.

### Connect MotiveWave

1. Open MotiveWave Journal and select **Setup**.
2. Click **Install MotiveWave study**.
3. Restart MotiveWave.
4. Add **Journal → MotiveWave Journal** to every exact-contract chart being tested. There is nothing
   to set: the study's first tab says so, and every setting is under **Advanced** with defaults that
   record fills, every trade print, and real ticks for closed live and replay trades.
5. If Setup says the study records no live or replay ticks, click **Install MotiveWave Study** and restart MotiveWave:
   studies before 0.3.3 never received live or replay prints.

One study instance records only the trade prints MotiveWave delivers to that chart. Once a replay
trade is flat, the study loads the real ticks of its holding time from MotiveWave's tick history, never
past the replay's current time; the replay's own prints count as real ticks only when the replay source
itself emits them. Continuous capture can use significant disk space on busy contracts.

## Acceptance exercise

Run this with a simulation account:

1. Enter two contracts, exit one, add one, then flatten the remaining two.
2. Confirm the journal creates one flat-to-flat trade with four fills and the correct maximum size.
3. Confirm the chart shows the entry and exit markers and that an observed tick path exists.
4. Save a planned stop, then inspect the 5-, 15- and 30-minute Runner views.
5. Create `Orderflow` and `Psychology` tag categories, add tags and apply them to the trade.
6. Open **Stats → Runners** and compare by **Tag categories**.
7. Restart the journal and confirm the review and tags remain.
8. Repeat a trade in Replay and confirm it appears only in the Replay journal. Leave the study on the
   replay chart: within about ten seconds of going flat the trade's MAE / MFE should read from
   **Backfilled from MotiveWave history**, and MotiveWave's log should show one
   `replay trade tick backfill` line for the pass.
9. Check that live and replay prints arrive while the chart runs: the Trades chart of a new trade should
   offer **Recorded live** (or the replay's own prints) as a price source.
10. Quit the journal and confirm MotiveWave stays open.

Do not report a missing or partial runner window as zero. The app intentionally excludes windows
with missing prices, recorded capture gaps or ambiguous OHLC stop ordering.

## What to include in a bug report

- macOS or Windows version and CPU architecture
- MotiveWave version
- Journal app version and study version, from the About line at the bottom of Setup
- Live simulation or Replay, plus whether Replay used real tick data
- Exact contract (for example `NQZ6`) and the chart type/timeframe
- Steps to reproduce and what was expected
- A screenshot of the error, if useful
- The app's `desktop/backend.log`, after checking it for anything the tester does not want to share

Never include broker passwords, API keys or unrelated account data. A tester may redact account IDs
and order IDs as long as the same redaction is used consistently throughout the report.
