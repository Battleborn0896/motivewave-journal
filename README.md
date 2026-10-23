# MotiveWave Journal

A free, local-first trade journal: a read-only MotiveWave study records fills and prices; a Python
app automatically reads them and provides trade charts, reviews, tags, statistics, MAE and MFE.
Live/paper and replay have separate databases. No routine CSV export/import is required.

MotiveWave Journal is an independent open-source project. It is not affiliated with, endorsed by or
supported by MotiveWave Software.

## What's new in 0.4.15: the study stops recording when you are flat

This release comes with study **0.3.9**.

- **Study 0.3.8 could record ticks all day while you were flat.** MotiveWave keeps only part of an account's
  history, and on a SIM account the oldest fill it keeps is often the exit of a position whose entry it has
  already dropped. 0.3.8 added up every fill, so that exit looked like a position still open and the
  contract recorded every print, whatever you did. 0.3.9 groups fills by MotiveWave's own position ids, the
  way the app groups your trades and links your stops, so it records only while you are really in a trade.

**What to do:** Setup → **Install MotiveWave Study**. MotiveWave switches to 0.3.9 by itself within a few
seconds.

## What's new in 0.4.14: find your edge the way Trade Craft teaches it, and a journal that stays small

This release comes with study **0.3.8**.

- **Find your edge on Stats → Compare, without the Filters menu.** Click a tag once to keep only trades *with*
  it, again for trades *without* it, again to clear it; star ratings are chips too, so leaving out your 1-star
  trades is one click. A line says what is applied ("With HTF Favor, without 1 star · 24 of 150 closed
  trades"). Beside it: a Totals table (all trades, wins, losses), one table per tag category with every tag as
  a row (faded at 0 trades), a star-rating table, grey numbers where a row has under 10 trades, then the equity
  curve of those trades and the trades themselves (click one to open it). **Save as Playbook** turns the kept
  tags into a playbook with a name and a colour.
- **A period above Stats:** This Month, Last Month, Last 3 Months or All. A monthly review starts with Last
  Month.
- **The study records ticks only while you are in a trade,** from a few seconds before entry to just after the
  exit. What comes after the exit (30 minutes, for "could I have let it run") and each trade's exact ticks are
  loaded afterwards from MotiveWave's own tick history, and charts come from MotiveWave's 1-minute bars as
  before. A day with MotiveWave open no longer adds hundreds of megabytes.
- **A one-time clean-up:** the first time 0.4.14 opens your journal it removes the prints recorded away from
  every trade and compacts the file (about 10 to 15 seconds once). Your trades' numbers do not change.

**What to do:** open the app (wait for the clean-up), then Setup → **Install MotiveWave Study**. A study from
0.3.6 on switches to 0.3.8 by itself within a few seconds; an older one needs MotiveWave quit and opened again.
On a continuous chart (NQ rather than NQZ6), "could I have let it run" needs Record Ticks set to Always While
Study Runs.

## What's new in 0.4.13: find your edge by tag, Sierra-style fills, a calendar in colour

- **Find your edge by tag (Stats → Compare).** Click tags under *Only trades with* to keep just the trades that
  have all of them, then read one table per tag category: every tag of it as a row with its trades, Net P&L,
  Win %, Average Trade and Average R, and a row for trades with no tag in that category. Pick a context
  (say, trades with your bias in favour) and see which entries, setups or mindsets work inside it.
- **Fills drawn as Sierra Chart draws them.** A small arrow points at each fill's exact price and time, buys
  green from below and sells red from above, with a box such as *+1 @ 29,665.50*. A dashed line joins each
  entry to the exit that closed it, first in first out: green where that part made money, red where it lost.
  Your stop's label sits at the right edge, clear of the boxes.
- **The trading calendar in colour.** Winning days green, losing days red, deeper for bigger days, with each
  day's trade count, a total for every week and the month's total.
- Study 0.3.7 stays: nothing to install in MotiveWave.

## What's new in 0.4.12: clearer steps for new testers and for updating

- **Each release page says what changed and how to update.** It starts with what is new in that version, then
  how to update from any older one (your journal stays; the study updates itself from 0.3.6 on) and how to
  install for the first time. The tester guide has the same.
- **Setup's last step says what to do if the study is missing.** Until the study reports in, *Add It to Your
  Chart* also says: not in MotiveWave's study list yet? Quit MotiveWave and open it again.
- Study 0.3.7 stays: nothing to install in MotiveWave.

## What's new in 0.4.11: no gaps in a trade's chart

- **Every minute of a trade has its candle.** MotiveWave answers the study from its cache of the day's 1-minute
  history, which can stop several minutes behind the market until it refreshes. A minute the platform has no
  candle for yet is now drawn from the prints the study recorded, as the minute in progress already was; the
  platform's candle replaces it when it arrives. Study 0.3.7 stays: nothing to install in MotiveWave.

## What's new in 0.4.10: replay charts show the whole replayed day

This release comes with study **0.3.7**.

- **A replay chart has the whole day.** While a replay chart has the study, it writes MotiveWave's 1-minute
  bars of the replayed day and the days before it, once a day, and every replay trade on those days is drawn
  from them, whichever replay run it was in (0.3.6 wrote bars only up to the replay's time, for one run). An
  open replay trade's chart still stops at the replay's current time, so nothing ahead shows while you trade.
- **Your stop crosses the whole chart,** as a trading platform draws it, labelled *Stop · R*. Later moves of
  the stop order stay dashed over the minutes each stood.
- MAE / MFE still come from ticks only. Data → Connection no longer reports a working stop without an
  instrument: that was a months-old leftover order MotiveWave keeps, not one of your stops.

**What to do:** install the study from **Setup**. Coming from 0.3.6, it takes over by itself in a few seconds.
Coming from 0.3.5 or older, quit MotiveWave completely first, install, then open MotiveWave.

## What's new in 0.4.9: your stop orders are recorded, replay trades get charts, and no more restarts to update

This release comes with study **0.3.6**.

- **Stop orders are recorded, on every account.** No study before 0.3.6 ever recorded one: it asked
  MotiveWave for each order's instrument in a way that never matched, and left out every stop without a
  word. A working stop now goes to its trade within a second, so the chart draws it and R comes from it.
  Should MotiveWave ever give a stop no instrument again, Data → Connection says so instead of staying silent.
- **Replay trades get 1-minute candles.** The study writes MotiveWave's 1-minute bars of a replay up to the
  replay's own time (never later), and tops them up as the replay plays, so a replay trade's chart is drawn
  like a live one. MAE / MFE still come from ticks only.
- **Updating the study no longer needs a restart.** When MotiveWave loads a new copy of the study, the new
  copy takes over from the old one in a few seconds. This works from 0.3.6 on: going from 0.3.5 to 0.3.6
  still needs MotiveWave quit and opened again, once.
- **Setup knows the new study is installed.** It no longer asks you to click Install again while MotiveWave
  still runs the old one: it says to quit MotiveWave and open it again.

**What to do:**

1. Quit MotiveWave completely.
2. Open MotiveWave Journal, go to **Setup** and click **Install MotiveWave Study**.
3. Open MotiveWave. Setup should read *Connected · study 0.3.6*.
4. Place a bracket order. Its stop should show on the chart within a second, and the trade gets an R.

## What's new in 0.4.8: stops on prop accounts, and MAE that banked profit no longer hides

This release comes with study **0.3.5**.

- **Your stop is found on prop accounts too.** A prop account's fills carry no position id, so earlier
  studies never tied its stop orders to the trade and those trades had no R. Study 0.3.5 ties a stop to
  your position by account, contract and side (a sell stop protects a long, a buy stop a short), on SIM
  and prop alike. A trade with no stop order recorded says so where R would show: *No stop order was
  recorded for this trade*. Trades from before 0.3.5 keep that line (MotiveWave keeps no orders of past
  sessions); type their stop under Notes to give them R.
- **Dollar MAE and MFE measure the contracts you still hold.** Profit you banked with a partial exit no
  longer hides a move against the rest. Long 2 NQ at 100, sell 1 at 104, then price drops to 99: MAE is
  1 point on the 1 contract still held, **$20**. 0.4.7 said $0, because it added the $80 already banked.
  Points and ticks are unchanged; R follows the dollars. Every trade is measured again once, from the ticks
  already stored, the first time the app starts, so trades with a partial exit may show new dollar and R
  values.
- **Only recorded ticks measure MAE and MFE.** The MAE / MFE Accuracy setting and the Measurement choice
  under More → MAE / MFE are gone, and so is the separate bid / ask measurement. Candles still draw the
  chart.
- Take-profit orders are no longer recorded (R never used them), and an event a newer study writes that
  this app does not know is skipped and counted on Data → Connection instead of stopping the import.

**What to do:**

1. Open **Setup** and click **Install MotiveWave Study** in its *Update the Study* step.
2. Quit MotiveWave completely and open it again.
3. Place one bracket order (entry with a stop and a target) on SIM and one on your prop account. Each trade
   should show its stop on the chart and an R. If Data → Connection says *working stop order not linked*,
   MotiveWave's log has one line per stop saying why: send it along with the lines that start
   *MotiveWave Journal probe:*, which the study writes once per session for the next version.

## What's new in 0.4.7: R from your stop, nothing typed is lost, and a faster journal

- **R comes from your stop.** You no longer type a dollar risk: R is your result divided by what your
  stop order risked on the position, wherever you exited.
- **Your stop counts as it stood 10 seconds after your first fill,** so dragging an OCO stop to the
  structure a second or two after entry is still placing it, and moving it later leaves R alone.
- **Notes has one Stop box,** filled in from your stop order, with where the price came from and the risk
  it gives. Type another price to use it; clear the box to go back to the order. A pre-trade draft takes a
  stop price too, and a dollar risk you saved before keeps its R.
- **Take profit is no longer shown.** The chart draws the stop R uses as a solid line, dashed where you
  moved your stop order later.
- **Every review box saves by itself,** on the trade and on the Daily Report Card: there are no Save buttons,
  and moving to another trade or day never loses what you typed.
- **An open trade shows one P&L.** Its row in the list and its header show the same Open P&L, live; a closed
  trade's is its Net P&L.
- **Missing MAE / MFE says why,** in one line: for example "No prices were recorded for MNQ during this
  trade. Keep the study on an MNQ chart while you trade it."
- **Plain words.** Accounts by their names ("SIM"), the orders under the chart as Time, Stop, Status and
  Price, the runner analysis without codes, and no trade or fill ids on screen.
- **Faster, most of all in a big journal.** A new fill measures only its own trade; the trade list is built
  once per change rather than at every click; Stats works out only the tab you open, and compares runners
  when you press Compare Runners; Export & Backup builds a file only when you ask for it.
- **A review stays with its trade.** When an update groups your fills into trades differently, a review
  and its pictures move to the trade that now holds the fill they were saved on (unless that trade already
  has a review of its own). Reviews an earlier update had left out of sight come back the same way.

## What's new in 0.4.6: open trades follow the market everywhere, and a filter saves with a name and Enter

- **Long trades update every second.** An open trade's MAE and MFE are carried on from where they stopped
  instead of measured again from entry each time. A trade with 100,000 prints now updates in about 15 ms
  instead of over a second, with the same result; the tests check that on random tapes.
- **More → MAE / MFE follows an open trade** every second too. The full recorded path is shown and exported
  once the trade is closed.
- **Named journals are live for every open trade**, also one you changed in the named journal alone (a fill
  added by hand, its own multiplier): it is measured there as prices arrive.
- **One set of filters.** The Filters menu on Trades and Stats has one editor, now with Trade Status, Number
  Ranges, Playbook and Process Ratings. To save what is shown, type a name under the filters and press
  Enter or click **Save Filter**: it becomes the saved filter in use, together with the one that was in use.
- **The session bar is one row** on a 1024-pixel-wide window; **Minutes Between Check-Ins** is in the
  **Check-Ins** menu.
- A console error when switching a trade's tabs is gone.

Journals, tags, reviews and settings are unchanged, and the study stays 0.3.4.

## What's new in 0.4.5: live open trades, prices matched by product, replay MAE/MFE by default, and a short Setup

This release comes with study **0.3.4**. Setup asks for it in one line while MotiveWave runs an older
study: click **Install MotiveWave Study**, then quit MotiveWave completely and open it again.

- **Any chart of the product works.** A trade is measured and charted with the prices of its product,
  whatever the contract month or the feed's spelling: `NQZ6`, `NQ` and a continuous `NQ` chart are all
  `NQ`; `MNQ` is another product. When both the contract's own prices and the continuous chart's were
  recorded, the contract's come first. There is nothing to set, and the study's **Symbol** override is
  no longer needed for this: clear it if you typed a contract there.
- **Replay trades get MAE and MFE with the defaults.** The replay's own prints now count as real ticks
  (**Real Tick Data** is on by default; switch it off only for a replay generated from bars). Charts that
  already had the study take the new default too.
- **Prints recorded as they trade are never marked proxy.** Only the history a continuous chart loads
  when it opens is, because it can be back-adjusted after a roll.
- **An open trade is live.** While a trade is open, its chart, **Open P&L** (at the newest price its
  MAE and MFE were measured with; the exit's fees are not in yet; the same number in its row of the list, '—'
  until the first price), MAE and MFE are drawn again every second, in a named journal too. A closed trade's
  result is its **Net P&L**. The minute in progress is drawn from the study's prints until
  MotiveWave's own candle for it arrives. A chart you zoomed or panned keeps your view, and while it
  shows the newest candle it moves along with new ones. The study writes to disk four times a second, the app reads it every second and the
  page redraws every second, so it runs one to three seconds behind MotiveWave. The **More → MAE / MFE**
  tab, with the price path, is drawn once and shows the trade as it was when the page opened.
- **Trades already in your journal are measured again once**, in the background, the first time the new
  app starts, so an open or recent trade that showed no MAE/MFE gets it without pressing anything. Prices
  recorded earlier keep their old labels.

Setup now holds only setup. When the study is recording it is one green line, for example
*Connected · recording NQZ6, ESZ6 · study 0.3.4* (the contracts the study wrote prices or fills for in
the last hour). Only when something is missing does a step appear, and only the one that is needed:
install the study, add it to a chart, update an older study (Install, then restart MotiveWave), or a
study that has gone quiet. Below it are **Time Zone** and **MAE / MFE Units**, then accuracy and hiding
weekends under a small **Advanced** heading, then three pages: **Tags, Playbooks & Checklists**,
**Rating Systems** and **Data** (Import Trades, Export & Backup, Journals & Accounts, Connection). Each of
their tabs has its own link on Setup, so every page is still two clicks from the sidebar. About is one line:
app version, study version and data folder.

- **Daily Report Card**: click a day's header in the Trades list. The card takes the trade's place for that
  date; **← Back to Trades**, or any trade in the list, brings the trade back. A day in a dashboard
  calendar opens it too.
- **Checklists** run in the selected trade's new **Checklist** tab; they are built under Setup → Tags,
  Playbooks & Checklists.
- **Session tools** are a bar at the top of Trades: a large count with play, pause and stop and a duration
  box, and small **Check-Ins**, **Drafts** (pre-trade drafts) and **Checklist** (a session checklist)
  menus. When the timer runs out, one click on a tag is the check-in.
- **Dashboards** are the last tab of Stats, always there; the Stats filters narrow them too.
- The **saved filter** and **Save a Filter** are in the Filters menus of Trades and Stats, and still apply
  on every page.
- Connection's **Capture enabled** switch is now **Read Trades From MotiveWave**, under Advanced at the
  bottom of Data → Connection. It is on unless you switched it off.

Tags, reviews and settings are unchanged.

## What's new in 0.4.4: ticks that actually arrive, and a study with nothing to set

This release comes with study **0.3.3**. Install it from **Setup → Install MotiveWave Study** and
restart MotiveWave; Setup says so in one line while MotiveWave still runs an older study. (Since 0.4.5 that
button is in Setup's **Update the Study** step, and always under Data → Connection.)

- **Live and replay ticks are recorded.** Study 0.3.2 and earlier declared that they *could* take
  MotiveWave's per-tick updates but not that they *needed* them. MotiveWave then never sent a single
  live or replay print unless bar updates were switched on by hand in the study's dialog, so MAE and
  MFE came only from the trade tick backfill. Study 0.3.3 requires bar updates, and every print the
  chart receives reaches the bridge file with the default settings.
- **Replay trades get real ticks.** Once a replay position is flat, the study asks MotiveWave's tick
  history for the real prints of its holding time (first fill to last, five seconds either side) and
  writes them for that replay run only (`motivewave-replay:<run>:history`). It never asks for
  anything later than the replay's current time. Each window is loaded once and remembered beside
  the replay bridge file, so restarts and rewinds do not load it again. The journal counts these as
  real ticks of that run, labelled **Backfilled from MotiveWave history**, never as simulated, and
  never for live trades or another run. A replay trade without them yet says that the study fills
  them in after the trade closes, while the study is on the replay chart.
- **Nothing to set.** The study's first tab only says to add it to the chart of each contract you
  trade. Every setting is under **Advanced**, with the same keys and defaults as before, so a chart
  that was already set up keeps its choices. The defaults capture fills, record every live and replay
  print, and backfill live and replay trades with real ticks. Setup's step 2 now only says to add
  **Journal → MotiveWave Journal** to each chart.

Nothing in your journals changes. A replay trade already captured gets its ticks the next time the
study is on a replay chart of that exact contract with the replay at or past the trade's exit and no
more than **Backfill Trade Ticks (Days)** (30 by default) after it, while MotiveWave still holds its
fills. Whether MotiveWave's tick history answers on a replay chart is up to MotiveWave and the data
service; when it does not, MotiveWave's log says `windows with no tick history`.

## What's new in 0.4.3: one typeface

Every piece of text in the journal is now IBM Plex Sans: pages, buttons, menus, tooltips, tables, the
Stats charts, the trade chart's axes and legend, the checklist editor and custom script widgets. The
monospace numbers are gone; amounts, times, prices and the headline tiles use Plex's tabular figures,
so columns of numbers still line up. A trade chart arrow's fill details now open in the chart's own
tooltip rather than the system's. Plex has no ★ or ⚠, so the process rating's stars and the Stats fee
warning are icons now. The font files ship with the app (`app/static/fonts`, SIL Open
Font License 1.1) and the app serves them itself: nothing is fetched from the internet, and the journal
still works offline. The browser tab's icon is now part of the app too; in 0.4.2 and earlier the browser
fetched it from Google's icon server. Journals, tags, reviews and settings are unchanged. (The Mac
app's menu bar and About box are drawn by macOS in its own font.)

## What's new in 0.4.2: fees come from MotiveWave

Commission rates are set in MotiveWave, and the journal now takes the fee MotiveWave reports for
each fill as the fee, including $0. Trades captured by the study no longer carry a "Data quality"
warning or a "Fees not confirmed" line because of it; real problems (a missing fill price, an
assumed account currency) are still shown. Imported fills without a fee column are still marked.

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

The sidebar has three pages: **Trades**, **Stats** and **Setup**. Trades holds the session tools (the bar
at the top), the Daily Report Card (click a day's header) and each trade's checklist; Stats holds the
dashboards (its last tab); Setup holds tags and checklists, rating systems, and data (imports, backups,
journals, the connection), each two clicks from the sidebar.
The trade list stays on the left while the selected trade's chart and tags stay on the right.
Click a row, or use Prev / Next; no separate opening/back-navigation step is needed.
**Chart & Tags** puts the chart and a grouped, clickable tagging panel side by side. Click a tag to
toggle it; changes save automatically. **+ Tag** creates a reusable tag and applies it to the current
trade; **+ Category** creates a category. **More Tagging Tools → Copy Tags From Another Trade** adds
another trade's tags without replacing existing tags or notes. Day tags you explicitly select are
inherited by trades opened on that local calendar date; otherwise no trade is automatically tagged.
Setup → Data → Connection also lets you pick a captured instrument instead of retyping its symbol.
**Notes → Use saved setup** applies an existing setup/playbook name in one selection;
new setup names can still be entered normally.

**MAE / MFE Units** is on **Setup**: choose account currency, points, ticks or R. The
preference is remembered independently for Live and Replay, and applies to the trade list, detail
cards, excursion plots and runner views. Actual net P&L is unchanged.

Only recorded ticks measure MAE and MFE (since 0.4.8 there is no accuracy to choose): OHLC bounds,
proxies, platform-only reports, simulated replay and sources with reported capture gaps do not
qualify. Missing ticks show unavailable, not zero. One-minute candles are chart context only. This is
a rule about sources, not proof that an observed feed delivered every tick. JSON keeps every stored
measurement; summary excursions and the CSV are in account currency.

MAE and MFE mean the same in every unit: how far price went against (MAE) or for (MFE) the contracts
you held at that moment, from their average entry. Profit banked by a partial exit never counts.

- Currency: that move times the contracts held and the point value, at each moment, before fees.
- Points: directional price move from the **currently held average entry**, regardless of size.
  Scale-ins update that average; partial-exit realised profit is not added to price movement.
- Ticks: those points divided by the contract's confirmed minimum price increment. Outright
  ES/MES/NQ/MNQ are detected automatically; other symbols need a one-time size in MAE/MFE or
  Setup → Data → Connection. Unknown sizes stay unavailable, not guessed. Average entries can produce fractional ticks.
- R: the currency excursion divided by the **risk to the trade's stop**. No stop means
  unavailable, not zero.

Price extrema use all available observations and confirmed fill prices, before chart compaction.
Existing stored price measurements can supply points on demand; a platform-only currency report
cannot safely be divided by an arbitrary size to invent a price path. Source quality, OHLC bounds,
replay simulation and capture-gap warnings remain visible. Points and currency can therefore select
different measurement sources. Points remain available without a cash multiplier; currency/R do not.

Stats uses in-place tabs: **Overview** for cumulative results, **Compare** for tag/setup or
market/time comparisons, **MAE / MFE** for price/position excursions, **Runners** for the separate
post-exit hypothesis, and **Dashboards**, always last. Tables lead with sample count and average net result per trade. Comparisons
are descriptions of your recorded sample, not proof of an edge. **Customize** saves the
visible tabs and their order per environment (Dashboards always stays). Filters support all/any selected tags, excluded
tags, direction, process rating and playbook combinations. Required historical tag aliases still
resolve after a rename; unknown required names match nothing, not everything.

**Setup → Tags, Playbooks & Checklists** edits names, colors, group membership, order and availability on
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

Use the in-place **Notes** and **Checklist** tabs and the **More** tab (MAE / MFE, Executions, Runner)
instead of scrolling through a long stacked page. Every review box (setup, notes, Stop, Followed my plan, and
each box of the Daily Report Card) saves when it changes or you leave it; there is no Save button, and clicking
another trade or day keeps what was typed. **Prev / Next** reviews adjacent trades while
the list remains visible. A tag edit that removes the current trade from your filters keeps it
open until you change the filter/selection, rather than interrupting the review. Existing flat tags
remain under **Other tags**. **+ Category** creates first-class groups such as Orderflow or Psychology,
including empty groups; **+ Tag** creates and immediately applies a reusable tag. A journal from 0.4.0
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
Exact Contracts** to narrow to one expiry, or **Stats → Compare → Market / Exact contract**
to compare them. Unknown symbols stay unchanged.

This is an analytics rollup, not a continuous futures price series: fills, round trips, protective
orders, charts and MAE/MFE always retain the exact traded contract. No stored trades are rewritten,
no rollover price gaps are silently adjusted, and one expiry's ticks never substitute for another's.

## Dashboard and session workflows (0.3)

**Stats → Dashboards → Customize Page** creates/clones/renames pages, chooses 1–3 columns, adds/edits/removes
and reorders widgets. Summary, equity, drawdown, calendar, comparisons, distributions, MAE/MFE,
tag scores, ratings, radar, trades and custom-statistics widgets are independently configurable.
The default uses two independently stacked columns so the calendar doesn't push the equity curve
below an unnecessarily tall row. Global saved filters, page filters and widget filters intersect;
they never modify captured trades. Saved tag rules use stable identities. Calendar buttons open
the exact day's Report Card on Trades. **Setup** sets the time zone, and hides weekends under Advanced.

Tag scores are signed values configured on each tag. A trade's score is the sum of its selected
tag scores; a widget can restrict the contributing tags. **Setup → Rating Systems** creates
weighted tag criteria, optionally applicable only when a playbook matches. Ratings are satisfied
weight / applicable weight × 100, or unavailable when no criterion applies. Manual stars stay
independent. These are process measures, not P&L, forecasts, or claims of Tradecraft's exact formula.

**Setup → Tags, Playbooks & Checklists → Checklists** contains an offline drag-and-connect node canvas: questions, answers, pass-through
and counter nodes; selection, duplication, color/text/tag edits, alignment, start-node choice,
undo/redo, and explicit save. Questions may branch only to answers. Answer loops must reach a
manual question; automatic endless loops are rejected. Definitions have JSON export/import and
non-destructive local versions. Runs retain their definition
snapshot, answers, counters and collected tags: a trade's run in its Checklist tab, a session run from the
Checklist menu above the trade list. Applying those tags is explicit; no checklist can
place, modify or block an order. This editor is not pixel/shortcut/format compatible with CheckMate.

**Session tools**, the bar at the top of Trades, provide an optional repeating check-in timer (play, pause
and stop; the minutes between check-ins and the groups are chosen under **Check-Ins**). When it runs out, one click
on a tag is the check-in, logged to the session or an explicitly selected open position. Timer state is not
persisted or restarted automatically, and there are no background notifications or sounds.
Pre-trade drafts (the **Drafts** menu) are not fake executions. A draft (Waiting until its trade comes) needs an
account and exact contract and a market-clock window, at most 24 hours. Only newly captured trades qualify. When
more than one trade or draft could match, nothing attaches and the draft says Needs review; a draft that attached
says Used, and cancelling (Cancelled) preserves the draft record. Existing reviewed groups, notes
and a stop already saved take precedence. Reconciliation runs after bridge polling, not during a read-only render.

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
P&L chart is compacted for display; once a trade is closed, **Show/export full recorded trade path**
reconstructs every available observation with held quantity and average cost. Missing prices are not
invented.

### Study-only capture

The desktop app contains no screen, video, audio, microphone, camera, OBS or FFmpeg recorder.
Automatic capture comes only from the MotiveWave study: fills, partials, linked protection, ticks,
bars and replay events are appended to the local bridge file. The app reads that file and never
needs permission to see the desktop.

### Post-exit runner analysis

The study now defaults to **Always While Study Runs**: every trade print MotiveWave delivers to the
chart is retained, so 5/15/30-minute post-exit windows do not depend on a separate
recorder. The lower-storage **Trade + After Exit Only** option keeps a bounded pre-roll, the holding
window and **After Exit (Minutes)** (30 by default, 0–120 configurable). Neither mode holds a position
or modifies an order. Both require the chart/study/feed to keep running; `Never` disables ticks.

In Trades, open **More → Runner** and expand **Could I Have Let This Trade Run?**. In Stats, open
**Runners** and expand **Which Tags Have Room for Runners?**. Choose a fixed 5/15/30/60/120-minute horizon. Analysis reports extra peak gross profit,
adverse movement, the observed fixed-horizon result, and approximate time to the peak. The
hypothesis retains only the final exit fill's quantity; earlier partial exits remain unchanged.
R statistics need the trade's stop. Compare winners or exits near 1R by tag/setup.
Choose an explicit frozen stop: your stop (the one R uses), recorded working
stop at exit, or entry-average gross breakeven. Missing/ambiguous stop levels stay unavailable.
Tracking ends at the FIRST observed stop touch or the pre-selected horizon. Peaks after a stop
touch are never credited. If no stop is touched by the cutoff, the result is labelled still running
at cutoff, not the final lifetime peak. Stop and peak in the same OHLC candle are order-ambiguous:
report conservative/possible bounds and exclude that case from default comparisons.

Only recorded ticks are used; incomplete or gapped windows are left out.
This is a frozen-stop price-path hypothesis, not an execution-accurate strategy backtest or an
achievable-profit claim. No spread/slippage, additional fees, dynamic trailing rules or perfect
peak execution is simulated. Tick completeness and actual stop fills remain unverified.
Do not add hindsight peaks to expectancy; validate a fixed runner rule on unseen trades next.

Prices use the public SDK. Manual-account fill capture uses an allow-listed, read-only adapter to
MotiveWave internals, **not its supported SDK**; a platform update can break it. Connection status
shows errors, missing files and stale heartbeats. The observer only sees retained executions, not
a guaranteed full broker archive. Linked stop order updates are polled and very fast changes can be missed.

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
Start it from the repository root on every system: Streamlit then reads `.streamlit/config.toml`,
which serves the bundled fonts (`app/static`) and sets the journal's colours.
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
Import**. Then add **Journal → MotiveWave Journal** to the chart you trade, the contract's own (`NQZ6`)
or a continuous one (`NQ`): a trade is measured with the prices of its product (`NQZ6`, `NQ` and a
continuous `NQ` are all `NQ`; `MNQ` is another product), its exact contract's first when both were
recorded. There is nothing to set: every setting is under the study's **Advanced** tab and the
defaults are what the app expects. The app starts watching both files by itself.
Check **Setup** in the app: it should change from Waiting For Study to one green Connected line. To install the
study again after an app update, use **Setup → Data → Connection → Install MotiveWave Study**.
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
copy the live bridge path shown in Data → Connection into the study's File setting (under Advanced).

## Tick accuracy and replay

- **Always While Study Runs** is the default. It records every trade print delivered to the chart,
  including the time after exit used by runner analytics. It uses more disk, and recording stops if
  the chart, study or feed stops.
- **Trade + After Exit Only** keeps a bounded pre-roll, records while the observed position is active,
  and continues for the configured post-exit window. The account poll also drains observed pre-roll
  after seeing fills, so a paused replay does not need another tick. Unknown/stalled capture records
  rather than guessing flat. **Never** disables streaming ticks; history loading is separate.
- MotiveWave sends a study each print only while the study receives bar updates. Study 0.3.3 requires
  them. Study 0.3.2 and earlier only offered them as a dialog switch that was off, so they recorded no
  live or replay prints; Setup asks for the update.
- Historical one-minute bars load in live mode. Optional historical real-tick loading defaults off
  and is currently capped at 120 minutes by this implementation, not by a claimed Rithmic limit.
  It requests real ticks, not manufactured OHLC ticks. Availability is up to the data service.
- Replay records only prices already delivered by replay, never a future historical path. Rewinding
  starts a new run; prices from another run cannot measure that trade. Once a replay trade is flat, the
  study loads the real ticks of its holding window from MotiveWave's tick history for that run, up to
  the replay's current time and never past it (**Backfill Trade Ticks (Days)** under Advanced, 0 = off,
  counted back from the replay clock). They are asked for as real ticks, never generated from bars, whatever
  the replay plays; a window the history has no ticks for stays empty. The replay's own prints count
  as real ticks too; switch **Real Tick Data** off only when the replay is generated from bars, and
  they are then labelled simulated.
  See [MotiveWave's replay sources](https://docs.motivewave.com/user-guide/replay-mode).
- Tick-derived one-minute candles represent the received prints; position-gated candles can be
  partial. OHLC estimates, proxies, simulated replay and observed ticks stay visibly distinct.
  Missing data means unavailable MAE/MFE, not zero. Observed ticks do not prove feed completeness.

Currency MAE/MFE are non-negative magnitudes of the **moves of the contracts held** across the
holding window, each move times the size held then, including scale-ins/outs; profit banked by a
partial exit is not added. Fees are excluded from excursions; recorded fees
reduce net trade P&L. Fees are whatever MotiveWave reports for each fill: set commission rates in MotiveWave, and a
fill MotiveWave reports with no commission counts as $0. A stop order counts when it is on the same
account and contract as the position and on its closing side (study 0.3.5); a stop you type under
Notes replaces it for R, and the chart shows both.

## Verify

```sh
.venv/bin/python -m pip install pytest
bash scripts/test.sh
# Optional macOS-only, read-only method-name check:
bash study/scripts/check_contract.sh
```

`scripts/test.sh` builds/tests Java, writes synthetic live/replay bridge files, and runs Python tests
against those files, all from a temporary folder it removes afterwards: the checkout, including the committed
study jar, is left as it was. Set `MWJ_PYTHON` to choose another interpreter.
Python-only tests: `python -m pytest -q`; the two cross-language tests explicitly skip unless their
Java samples were supplied. The contract check does not launch MotiveWave or initialise account code.

For actual acceptance, install/add the study yourself, check its fresh heartbeat, and verify a trade
you chose to take against MotiveWave's fills (time, price, size, symbol), linked orders and received
price extrema. Repeat in replay, pause immediately after a short round trip, and then rewind.
Never count a fixture chart as proof of that integration.

Stats separates gross/net closed-trade curves and recorded fee drag, including fees in R
for trades with a stop. Fees are not slippage and a closed-trade curve is not marked
account equity. The app also offers optional CSV/BYOD imports, daily reviews and JSON/CSV/SQLite backup.

## Design and future work

[Bridge protocol](docs/protocol.md) · [Carver-inspired analytics direction](docs/analytics-direction.md)

MIT licensed; see [third-party notices](THIRD_PARTY_NOTICES.md). Independent of MotiveWave Software.
