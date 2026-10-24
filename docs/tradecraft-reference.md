# Tradecraft reference notes

The user's target is complete Tradecraft feature/workflow coverage, including UI, layout and
navigation, adapted to the free local MotiveWave app. This is the target, not the current completion
status. Public sources inform independent implementation; no vendor code or brand assets are
included. The collector, tick path and live/replay isolation remain our own.

## Sources reviewed

- [Daily Trades](https://support.tradecraft.club/kb/daily-trades-section/): grouped clickable tags,
  copy-tags, and separate notes, screenshots and video tabs.
- [Tag groups](https://support.tradecraft.club/kb/creating-editing-and-managing-tag-groups/): grouping,
  exclusive choices and organisation.
- [Creating and managing tags](https://support.tradecraft.club/kb/creating-editing-and-managing-tags/):
  tag colours, unique names and retiring tags without discarding historical associations.
- [Official Creating Tags tutorial](https://www.youtube.com/watch?v=H__DsBsf_ds): reviewed the
  auto-generated English transcript. It demonstrates exclusive groups, day-level tags,
  emotional check-ins, reordering, renaming while preserving associations and equity-curve filters.
- [Day vs trade tags](https://support.tradecraft.club/kb/day-vs-trade-tag-setting/),
  [exclusive groups](https://support.tradecraft.club/kb/exclusive-tag-group-setting/),
  [playbook combos](https://support.tradecraft.club/kb/playbook-tag-combos/) and
  [Book of Charts](https://support.tradecraft.club/kb/book-of-charts/).

## Popularity-order video audit — 2026-10-11

The official channel's Videos tab was set to **Popular**. The following 18 videos were visibly
listed in this order. Each transcript export was attempted sequentially. “Transcript reviewed”
means the complete auto-generated English transcript was read, not that the video was played or
its visuals watched end-to-end. Unavailable exports do not establish what the video contains.
The Creating Tags transcript was read in the previous pass; its export was unavailable this time.

| Rank | Official video | Review status |
|---|---|---|
| 1 | [What is Trade Craft?](https://www.youtube.com/watch?v=pcWER3LjN8Q) | Transcript reviewed |
| 2 | [How to import trades](https://www.youtube.com/watch?v=YO28Js0g7_8) | Transcript unavailable |
| 3 | [How to tag live](https://www.youtube.com/watch?v=xUmu7qe4WhY) | Transcript reviewed |
| 4 | [Creating Tags](https://www.youtube.com/watch?v=H__DsBsf_ds) | Previous-pass transcript reviewed |
| 5 | [Trade tag ideas for newbies](https://www.youtube.com/watch?v=3GQB6C8CMeg) | Transcript unavailable |
| 6 | [My Trade Tags Explained](https://www.youtube.com/watch?v=iRu1PypO-5k) | Transcript unavailable |
| 7 | [How to connect Rithmic R-Trader Pro](https://www.youtube.com/watch?v=0if7Ai_Vh0Y) | Transcript unavailable |
| 8 | [Finding edge](https://www.youtube.com/watch?v=8-okPwuHBNY) | Transcript unavailable |
| 9 | [Main Dashboard](https://www.youtube.com/watch?v=L4wVkZmhTkU) | Transcript unavailable |
| 10 | [Adding video recordings](https://www.youtube.com/watch?v=U3wDum4CeMg) | Transcript reviewed |
| 11 | [Book of charts](https://www.youtube.com/watch?v=7jveeemKQos) | Transcript reviewed |
| 12 | [Rating System Explained](https://www.youtube.com/watch?v=u6N5BHa0nrc) | Transcript unavailable |
| 13 | [Checklist 101](https://www.youtube.com/watch?v=sKCkwyD34IY) | Transcript reviewed |
| 14 | [Sierra Charts linking](https://www.youtube.com/watch?v=bojrL93dyNo) | Transcript reviewed |
| 15 | [Playbook Combos](https://www.youtube.com/watch?v=YvaG59MlsL0) | Transcript unavailable; docs reviewed |
| 16 | [Calculate Stats](https://www.youtube.com/watch?v=bkPAqJX1_IY) | Transcript reviewed |
| 17 | [Checklist 102](https://www.youtube.com/watch?v=XV9I9iC6T5s) | Transcript reviewed |
| 18 | [Become an affiliate](https://www.youtube.com/watch?v=oWqThqIDly0) | Transcript reviewed; not a journal feature |

Findings: selected group/tag combinations can be checked against past samples without leaving
review; visual captures are most useful when tagged, sortable and linked to their trade. Recording
alignment needs clock/provenance information. The checklist tutorials describe a branching node
editor and execution history, not just a few checkboxes. These findings shape independent code;
none establish strategy profitability or verified MotiveWave capability.

## Implemented in this pass

- Persistent trade list on the left, focused chart/tag review on the right; compact tag-first panel.
- Grouped click-to-toggle tags, local autosave and reusable custom names/groups.
- Additive copy-tags without replacing notes or risk fields.
- The Rating stars; one page per trade: numbers, chart and tags, note and Stop, checklist, then a Details fold with
  the fills, the MAE / MFE path and After Exit.
- Charcoal panels, yellow navigation/emphasis, green/red results and customizable per-tag colors.
- Legacy flat tags remain available. Catalogue and annotations are local, backed up and separated
  by live/replay environment. Concurrent partial edits preserve unrelated fields.
- Stable tag IDs/aliases; safe rename, retirement/restoration, group rename/order, tag order,
  exclusive groups and calendar-day inheritance with explicit timezone and per-trade overrides.
- Playbook OR/AND rules, explicit one-click application, automatic recognition, filtering and
  comparisons; hypothetical combination checks exclude trades not closed before the selected entry.
- MAE / MFE from recorded ticks only, one moment in every unit, the same on the trade, in Stats, After Exit and
  the export.
- Paged local Book of Charts, sorting, media/type filters, direct tag editing and trade navigation.
- One filter editor for Trades, Stats, dashboard pages and widgets, with all/any/excluded tag filters.

## 0.3 feature and workflow audit — 2026-10-11

The [public demo](https://tradecraft.club/journal/) was opened without logging in. Its dashboard,
Daily Trades and settings navigation were inspected visually and through the rendered controls.
One dummy demo trade was added in its local-only demo to expose the review panels; no real account
or cloud journal was accessed. The demo showed dashboard/page/widget filtering, the journal's
Daily Report Card/Daily Trades split, a loop timer, trade legs, Checklists, Tags & Rating, Video,
Screenshots, Journal Note and Tags Radar Chart. Settings exposes Tags, Accounts, Export,
Checklists and General. These observations complement—not replace—the video/transcript audit.

Additional primary references: [signed tag scores](https://support.tradecraft.club/kb/assigning-a-score-to-tags/),
[stopwatch groups](https://support.tradecraft.club/kb/stopwatch-timer-tag/),
[merging](https://support.tradecraft.club/kb/merging-trades/),
[checklist editor](https://support.tradecraft.club/kb/building-checklists-and-the-node-system/),
[checklist management](https://support.tradecraft.club/kb/creating-checklists/),
[release notes](https://support.tradecraft.club/kb/whats-new-release-notes/).
The radar article and rating video's detailed formula remain unverified; our current calculations
are explicitly defined, not asserted to be the vendor's exact calculations.

| Area | Current local implementation | Remaining to claim full parity |
|---|---|---|
| Navigation / visual language | Vertical page navigation, dark/yellow palette, focused trade workspace, settings categories | Not pixel-identical; no compact icon rail or resizable panel system |
| Dashboard | Named/cloneable pages, 1–3 columns, add/edit/remove/order widgets, multiple metrics, calendar navigation | Drag/resize canvas, series overlays and richer aggregation editor |
| Filters | Stable tag IDs; saved global + page + widget intersections; all/any/excluded tags; accounts/contracts/markets | Full nested Boolean expression editor and every numeric predicate |
| Tags | Groups, colors, exclusivity, rename/retire/order, day/trade inheritance, copy, playbooks, earlier-sample stats | Drag between groups, bulk selection edits, hide individual groups, previous-trade auto-copy preference |
| Process measures | Signed tag scores, subset scores, manual stars, weighted conditional tag ratings, score/rating widgets | Vendor formula compatibility and richer radar/series normalization |
| Session / pre-trade | Explicit repeating category check-ins with pause, resume and stop; Tag My Next Trade on the next new trade of one account and contract | Background alerts; independent live tagging window |
| Checklist list | Numbered yes/no questions, each answer able to branch to another question or Finish, collect tags and count; runs on each trade or before the session (Daily Report Card); stored as a question/answer/counter graph | A drawn node canvas, custom answer wording, more than two answers, attached node images and vendor JSON compatibility |
| Checklist runs | Logged answers, counters, snapshot definitions, branching/manual loops, undo, explicit collected-tag application | Per-answer event timestamps and more advanced automation types |
| Media | Multi-image local upload; arrow/rectangle/pen/text editor; user-click clipboard paste with fallback; local gallery | Native clipboard acceptance test, existing-image editing selection, richer annotation history/full-screen workspace |
| Session video | Explicit existing local/YouTube link; account/contract/clock/duration/speed alignment; execution jumps/pre-roll | Native playback acceptance, variable-speed/paused replay segmentation UI and Book-of-Charts session-video integration |
| Custom scripts | Opt-in, two-second Web Worker; filtered display values; text/line/bar result | Deliberately not arbitrary DOM/CDN execution or vendor script compatibility |
| Trade legs | Raw matching handles scale-ins and partial exits, exact fill roles/time/SL/TP, chart paths | Manual merge/explode of separately imported round trips with reversible provenance-aware excursions |
| Multiple journals/accounts | Separate Live/Replay databases; views of chosen accounts in the Journal menu, sharing one set of reviews and settings; currency/account filters, account names and named dashboard/filter views | Named isolated journals, rename/clone/export/import across journal workspaces |
| Imports / sync | Manual fills, mapped CSV of fills, experimental MotiveWave bridge | Dedicated parsers/connectors for every vendor-supported platform; direct R-Trader/Sierra/other integrations |
| Backups | One zip of both journals (Live and Replay, with their views and account names) with tag library + workspace definitions/runs/check-ins/drafts/videos; restore from it after a copy of the current journal is saved; trades CSV | Media files in the backup and cloud synchronization (not required for the free local target) |

This table is intentionally not all green. It is the acceptance checklist for subsequent work;
shipping a broader 0.3 app is not completion of the user's “all features” request. Current widget
and graph definitions are independently implemented and not drop-in Tradecraft files.

## Platform and runtime acceptance still required

- Actual MotiveWave manual/live/replay capture, historical tick availability and recorder behavior
  still require platform acceptance testing; source/fixture checks alone do not prove completeness.
- Clipboard and linked-video behavior need native WKWebView/OS acceptance, not just source tests.
- Full arbitrary CheckMate/dashboards/vendor import parity remains as enumerated above.

No automatic trade labels, market-data purchases, cloud uploads, trading actions or recording
permission changes are part of this redesign. A prior preview was blocked by the approval service.
This pass's normal browser preview succeeded using isolated synthetic data and recording off;
the compact 1280×720 chart/tag layout and tag autosave were visually checked. Automated tests are
not a substitute for actual live/replay platform acceptance.

## SCS capture reference — reviewed 2026-10-11

[SCS journal documentation](https://www.scstudies.com/trading-journal) distinguishes study-tracked
excursions from historical SCID reconstruction, retains measurement provenance, and describes
optional buffered video, automatic fill screenshots and forward-only post-exit tracking.

Our bridge already retains local fills/prices and distinct measurement sources; After Exit
holds the 15 minutes after each exit to the trade's stop. Verify MotiveWave's available tick history and actual
manual/replay capture before claiming comparable coverage. Media remains opt-in and runtime-
unverified. Prioritize clearer capture diagnostics and review workflow; do not add order blocking
to the read-only journal. These are design references, not vendor code or feature-parity claims.
