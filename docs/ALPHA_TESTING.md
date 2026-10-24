# Alpha tester guide

This is an early, local-first tester release. Use a MotiveWave **simulation account or Replay**
before considering live-account observation. The study is read-only: it cannot place, modify or
cancel orders. It records account fills, stop orders and prices delivered to the
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

1. Open MotiveWave Journal and select **Settings**.
2. Click **Install MotiveWave Study**.
3. If MotiveWave was open and the study is not in its study list yet, quit MotiveWave and open it again.
4. Add **Journal → MotiveWave Journal** to each chart being traded, the contract's own (`NQZ6`) or a
   continuous one (`NQ`): a trade is measured with the prices of its product (`NQZ6`, `NQ` and a
   continuous `NQ` are all `NQ`; `MNQ` is another product), its exact contract's first when both were
   recorded. There is nothing to set: the study's dialog says so and has nothing else on it. By itself it
   reads every fill, records each trade's prints while the trade is open, and once a trade is over loads its
   holding time and the 30 minutes after its exit from MotiveWave's tick history, for the chart's current
   contract, continuous chart or not.
5. Settings should now read **Recording** in green. **Not recording** (grey) has one line under it saying
   what to do; **Needs attention** (red) means **Settings → Data → Connection** says what went wrong. There is
   nothing to type there: the app reads the study's own file, and **Troubleshooting** holds the details.
6. If Settings says the study is out of date, click **Install MotiveWave Study**: a study from 0.3.6 on hands
   over to the new one by itself; with an older one, Settings tells you to quit MotiveWave and open it again.

### Updating from an older version

Any older version updates straight to the newest; there is no need to install the ones in between.

1. If the version you have now has **Back Up Journal**, click it first (see *Back up and restore* below).
2. Quit MotiveWave Journal and install the new version over the old one (on a Mac, drag it into
   Applications and choose Replace; on Windows, extract the new ZIP and use its `MotiveWave Journal.exe`).
   Trades, reviews, tags and settings stay: they live in the app's data folder, not inside the app, and the
   new version brings them up to date by itself the first time it opens.
3. Open the app. If Settings says the study is out of date, click **Install MotiveWave Study**, as in step 6.
   Anything the update changed in your journal is said once in one line at the top of Settings; **OK** hides it.
4. What changed in each version is under *What's new* at the top of the README and on each release's page.

### Import trades

For trades the study did not record, open **Settings → Data → Import Trades** and drop the file in its one box: a
CSV with one row per fill, or a bridge file the study wrote. The app tells which it is and refuses a bridge file from
the other environment. **Column Mapping** opens only when a column it needs is not found. Never import fills the
study already recorded: they would count twice. **Add One Fill by Hand** takes a date and a time in your Time Zone.

### Back up and restore

Back up before every test session and before every update: open **Settings → Data → Export & Backup** and
click **Back Up Journal**. It downloads one zip, `MotiveWave Journal backup YYYY-MM-DD.zip`, that holds Live and
Replay, with your views and account names. Keep it outside the app's data folder.

To go back to a backup, choose its zip under **Restore From Backup** on the same tab. The app shows the
backup's date and how many live and replay trades it holds; click **Restore This Backup**. The journal as it
was just before is saved first, to `backups/before-restore-<time>.zip` in the app's data folder: restore that
file the same way to undo the restore. A backup made by a newer version is refused; update the app first.

One study instance records only the trade prints MotiveWave delivers to that chart, and only while you are
in a trade. Once a replay trade is flat, the study loads the real ticks of its holding time from MotiveWave's
tick history, never past the replay's current time, for the chart's current contract, continuous chart or
not. The replay's own prints count as real ticks too. In Replay the sidebar reads **No replay trades yet**
until the first replay trade. The journal keeps every recorded tick, packed small an hour or two after it is
recorded, and the study empties its bridge file once the app has read it, so neither grows much. The first
time this version opens a journal it packs every tick already recorded and shrinks the file, in seconds (a
222 MB journal became 17 MB in 13 seconds). Back Up Journal, not the bridge file, is how a journal is
recovered.

## Acceptance exercise

Run this with a simulation account:

1. Enter two contracts, exit one, add one, then flatten the remaining two.
2. Confirm the journal creates one flat-to-flat trade with four fills, and that its **Contracts** reads 2, the
   most held at once.
3. Confirm the chart shows the entry and exit markers, 30 minutes either side, and that the trade's **Details**
   lists the four fills once and draws its MAE / MFE path.
4. Save a stop for the trade, check its **R**, then read the After Exit sentence under **Details** once the 15
   minutes after the exit are over: how much further the price went your way before reaching your stop, in your
   MAE / MFE unit.
5. Create `Orderflow` and `Psychology` tag categories, add tags and apply them to the trade. Pick its Setup
   tag and give it a Rating, 1 to 5 stars.
6. Click the trade's day header in the list: on its Daily Report Card choose Day tags and a Rating, then
   confirm the trade wears those Day tags. Days follow **Settings → Time Zone**, midnight to midnight.
7. Open **Stats → After Exit**, compare by **Tags** and find each tag under its category. On **Stats → Overview**,
   click the trade's day in the calendar under the equity curve and confirm it opens that day's Daily Report Card.
8. Before the next trade, open **Session** above the trade list, choose the contract under **Tag My Next Trade**,
   pick a tag and click **Tag My Next Trade**; after the trade, confirm it wears that tag and the list says **Used**.
9. Restart the journal and confirm the review and tags remain, then click **Back Up Journal** under
   **Settings → Data → Export & Backup**.
10. Under **Settings → Data → Journals & Accounts → New View**, make a view of the simulation account. Choose it
   under **Journal** in the sidebar and confirm it lists only that account's trades, with the same tags, Rating and
   note; then delete it and confirm nothing else changed. A named journal from 0.4.17 or earlier is in that menu as
   a view after the update, its reviews joined into **All accounts**.
11. Repeat a trade in Replay and confirm it appears only in the Replay journal, with MAE / MFE from the
   replay's own ticks, and that it is offered the tags, filters and Units you set in Live: Live and Replay share
   them, and each keeps its own trades and reviews. Leave the study on the replay chart, contract or
   continuous: within about ten seconds of going flat MotiveWave's log should show one
   `replay trade tick backfill` line for the pass;
   when those history ticks saw more of the trade, the line under its time reads **From MotiveWave's tick
   history**.
12. Check that live and replay prints arrive while the chart runs: an open trade's MAE / MFE should move
   with the market, and the line under its time should name no price source.
13. Quit the journal and confirm MotiveWave stays open.

Do not report missing or partial minutes after an exit as zero. The app leaves out of Stats → After Exit any
trade whose 15 minutes after the exit were not all recorded, or had a gap the study reported, and that trade's
own sentence says not every price was recorded. An exit at or past the stop is different: it counts as 0 there,
because nothing was left to hold.

## What to include in a bug report

- macOS or Windows version and CPU architecture
- MotiveWave version
- Journal app version and study version, from the About line at the bottom of Settings
- Live simulation or Replay, plus whether Replay used real tick data
- Exact contract (for example `NQZ6`) and the chart type/timeframe
- Steps to reproduce and what was expected
- A screenshot of the error, if useful, and of **Settings → Data → Connection → Troubleshooting**
- The app's `desktop/backend.log`, after checking it for anything the tester does not want to share

Never include broker passwords, API keys or unrelated account data. A tester may redact account IDs
and order IDs as long as the same redaction is used consistently throughout the report.
