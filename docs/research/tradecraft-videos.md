# Tradecraft videos: what the screen actually shows

Frame-by-frame notes from the official channel, for layout and navigation parity. These are
observations of the picture, written in our own words; nothing is copied from the product. Each note
gives the video id and the second it was read from. Videos are taken in the channel's Popular order,
starting with the ones whose transcripts were not available.

## Main Dashboard (L4wVkZmhTkU, 23 min), watched 2026-10-11

### App chrome (t=1320, t=860)

- A narrow icon rail on the far left: the logo, then four icons only: Dashboard, Journal, Import,
  Settings. No text labels on the rail.
- A top bar holding the section title. On the Dashboard it reads "Dashboard" followed by a
  "Global filters" control with a pencil and the current filters (or "no filters").
- The Journal section adds a second, collapsible sidebar titled "Journal" with two entries: Daily Report
  Card and Daily trades.
- Dark theme: near-black panels, thin borders, gold logo, green and red for results, blue as the default
  series colour.

### Dashboard page (t=45, 150, 270, 420, 560, 700, 1020, 1180)

- Pages are tabs under the title ("General Statistics", "Untitled", a "+" to add). A "Clone Page" button
  sits at the top right; cloning produces "General Statistics (copy)".
- Under the tabs: "Page filters" with a pencil, shown as chips (three stars, "Longs Only").
- Top right of the page: two checkboxes, "Hide serie settings" and "Hide widget settings", which collapse
  the per-widget controls so the page reads as a clean report.
- The page is a split-pane canvas. Every pane has a header with: a drag handle, a bin, the widget title,
  the widget's own dropdowns, and on the right four icons (full screen, split left/right, split
  top/bottom, copy). Splitters between panes can be dragged. An empty pane shows a large "+".
- A new empty widget shows a row of type icons to choose from: line, pie, bar, statistics table, trades
  table, note, calendar, and paste.
- Line widget ("Raw Equity Curve"): dropdowns for the measure (Cumulative P&L), the x axis (Trade
  number), the unit (Currency (total)), and a Markers checkbox. Several series can be drawn on one
  chart, each with its own filter: the "Trade Ratings" example draws one line per star rating, and
  another draws "Up" (longs only) against "Down" (shorts only).
- Each series has its own row: "+" to add a series, a colour swatch, a name, icons for delete, copy,
  hide and rename, then its filter chips ("Longs Only", "P&L > 0", "Mon") or "no filters".
- Pie widgets: Longs vs Shorts, Wins vs Loss, Long Win/Loss, Short Win/Loss. Each slice is a series
  with a filter.
- Bar widget ("Day of week Win/Loss"): a measure dropdown (Number of trades); bars are series such as
  "Monday Win" (filters: Mon, P&L > 0) and "Monday Loss".
- Statistics table widget: rows are series (Total, Wins, Loss); a "Columns" button opens "Widget
  settings" with checkboxes for: Number of Trades, Cumulative P&L, Win %, Max Drawdown, Avg. Trade P&L,
  Avg. Trade MAE, Avg. Trade MFE, Avg. Drawdown, Biggest Win P&L, Biggest Loss P&L, Biggest MAE, Biggest
  MFE, Max Consecutive Winners, Max Consecutive Losers, Number of Drawdowns.
- Trades table widget ("Individual Trades"): Account, Symbol, Size, Side, Entry Pr., Exit Pr., Entry
  Dt., Exit Dt., P&L, MAE, MFE, Rating, Running P&L, Dur.; rows tinted green or red; dropdowns for the
  P&L unit, the MAE/MFE unit, the colouring rule and the columns.
- Note widget: a small rich-text editor (style, bold, italic, underline, strike, link, lists, clear).
- Filter dialog ("<widget> widget filters"): "Select filter type" dropdown (Account, Direction, Tags and
  more) and "Add Filter"; each added filter is a row with its own control ("Trade direction: Long") and
  a bin; the Tags filter row has include and exclude controls.

### Journal > Daily trades (t=860)

- Top: a "Date:" picker. Beneath it the loop timer: a large "05:00", play, pause and stop buttons, and
  a "Duration" box in minutes.
- Left column "Trades" with add, delete and settings icons. Columns: #, Account, Entry Time, Size &
  Symbol, P&L, Duration. Each row has small actions for inserting a trade above or below and deleting.
- Right pane: previous and next arrows around "Trade 1"; two tabs, Journaling and Charts.
- Journaling tab, top to bottom:
  1. "Trade Data" with an edit pencil; Account and Symbol; a table with one row per leg and a Total row
     (Size, Dir., Avg. Entry Pr., Avg. Exit Pr., Entry T., Exit T., MAE (Ticks), MFE (Ticks), P&L).
  2. Three stars.
  3. Buttons: Filters, Calculate Stats, and a help icon.
  4. A row of playbook chips, each in its own colour.
  5. Tag groups as bordered cards laid out in a grid. Each card has a checkbox and the group name in its
     header and the group's tags as outlined buttons stacked vertically; a chosen tag is filled with its
     colour. Groups tied to the timer carry a small stopwatch icon.
  6. The note editor.

### What this means for our layout

- Navigation should be an icon rail with four sections (Dashboard, Journal, Import, Settings) and a
  section sidebar, rather than a long list of pages.
- Daily trades keeps the trade list on the left and puts trade data, stars, playbook chips, tag-group
  cards and the note in one scrolling Journaling tab, with charts on a second tab.
- The dashboard is a split-pane canvas of widgets with per-series filters, not a fixed set of sections.

## Finding edge tutorial (8-okPwuHBNY, 42 min), sampled 2026-10-11

- **Many dashboard pages (t=150).** The author's own journal has thirteen page tabs (a current-day page,
  unfiltered numbers, a main dashboard, one page per tag family, templates and copies). Pages are cheap
  and are cloned constantly, so cloning and renaming must be one click.
- **Edge finding is done with statistics-table widgets (t=150).** A page holds several tables side by
  side. Each table has dozens of rows, and every row is a series: a name, a colour (taken from the tag
  colour) and a filter made of tag combinations. Columns are the chosen statistics (number of trades,
  cumulative P&L, win %, average trade P&L). Rows can also be plain header lines used as separators
  between blocks. One table lists the personal measures: rating 1 to 3, clear or confused, aggressive or
  conservative, calm levels, patience levels, managed versus break-even exits, wins and losses.
- **Series filter dialog (t=1300).** "Select filter type" offers: Direction, Account, Symbol, Tag, P&L,
  MAE, MFE, Date, Time of Day, Day of Week, Month of Year, Rating. Filters are added one at a time and
  combine.
- The first twenty minutes are a whiteboard talk (technical, management and psychological data points
  as three families of tags), not product UI.

Layout consequence: our statistics table needs unlimited named series with per-series filters, header
rows, tag-coloured labels and a column chooser, and pages need fast clone and rename.

## Rating System Explained (u6N5BHa0nrc, 10 min), sampled 2026-10-11

- **Stars (t=125).** Three stars under the trade data; a rating of two shows two filled stars in two
  different colours (the colour follows the level), the rest outlined.
- **Group checkboxes (t=125).** The checkbox in a tag-group header is ticked for every group that has an
  answer on this trade, so unanswered groups stand out.
- **Trades table coloured by rating (t=501).** A trades-table widget has a "Color by" dropdown; with
  Rating chosen, rows are tinted red, orange or green by star level and the selected row is blue.
  Hovering a row opens a "Tags" popover listing that trade's tags as coloured chips.
- **Page filters as chips (t=501).** A page's filters appear as a row of coloured tag chips under the
  page tabs.

## Book of charts Tutorial (7jveeemKQos, 13 min), sampled 2026-10-11

- **Widget controls (t=238).** One row: MAE/MFE unit, "Color by" (Rating), "Columns" (2), and a choice
  between Videos, Screenshots or Both. A second row sets where a video starts: a number of seconds,
  before or after, the entry or the exit.
- **Trade cards (t=238).** Each card has a coloured header strip with the trade's numbers and two
  buttons: "tag" and "journal" (jump to that trade in Daily trades).
- **Assign tags dialog (t=476).** "tag" opens a modal with every tag group as a card in a grid, the
  trade's tags filled with their colours, so a chart can be retagged without leaving the book.
- **Full-screen viewer (t=674).** Prev and Next buttons at the top step through the charts or videos;
  a linked session video opens positioned at the trade.

## Creating Tags (H__DsBsf_ds, 11 min), sampled 2026-10-11

- **Trades list with real rows (t=158).** "Trade 1", account, entry time, size, side and symbol, P&L and
  duration on one compact line, then three tiny row actions.
- **Timer-linked group (t=158).** A group tied to the timer shows a stopwatch icon and its whole card
  turns solid blue when an answer is due.
- **Settings > Tags (t=381).** Settings has its own sidebar (Tags, Accounts, Export, Checklists,
  General). The Tags page has three blocks: ungrouped Tags with a "New Tag" button; Tag Groups with a
  "New Tag Group" button, each group a small card (drag handle, name, add, edit, delete) holding its
  tags as coloured chips that can be dragged between groups; and Playbook Tag Combos with a large "+".

## Checklist 101 (sKCkwyD34IY, 25 min), sampled 2026-10-11

- **Editor (t=529).** A full-screen overlay. Top toolbar: run, record and layout on the left; add-node,
  align and group tools in the middle; copy, paste, undo, redo. Left panel: properties of the selected
  node or connection, Import / Export (JSON) and named Backups. Canvas: question nodes are solid boxes,
  answer nodes are dashed boxes, joined by lines; a selected node shows four connector handles.
- **Settings > Checklists (t=1134).** "Add Checklist"; each checklist is a collapsible row with run,
  list, history, edit, move up, move down, show/hide and delete. Running one shows a question card with
  back and forward arrows and one coloured button per answer.

## Playbook Combos (YvaG59MlsL0, 6 min) and How to tag live (xUmu7qe4WhY, 11 min), sampled 2026-10-11

- **Playbook chips fill tags (t=262, Playbook).** Clicking a playbook chip above the tag grid selects its
  tags in their groups.
- **Calculate Stats result (t=275, tag live).** After pressing Calculate Stats a single line appears under
  the buttons: number of past trades with the selected tag combination, win %, average trade P&L and
  cumulative P&L.
- **Automatic playbook recognition (t=516, tag live).** When the selected tags match a playbook, its name
  is shown as a coloured heading above the tag grid.
- **Pre-created trades (t=516, tag live).** Several empty trades ("Trade 1" to "Trade 6") are added ahead
  of time and tagged before the fill arrives; the import later fills them in.
- **Timer due state (t=516, tag live).** When the loop timer fires, every timer-linked group turns solid
  blue and its answers are cleared so they are answered again.

## Not yet watched

Popular-order items still to do: How to import trades (YO28Js0g7_8), Trade tag ideas for newbies
(3GQB6C8CMeg), My Trade Tags Explained (iRu1PypO-5k), How to connect Rithmic R-Trader Pro (0if7Ai_Vh0Y),
Adding video recordings (U3wDum4CeMg), Sierra Charts linking (bojrL93dyNo), Calculate Stats
(bkPAqJX1_IY), Checklist 102 (XV9I9iC6T5s), and everything below rank 18. Each video above was sampled
at a handful of frames, not watched end to end.
