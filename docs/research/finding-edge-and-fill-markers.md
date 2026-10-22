# Finding edge with tags, and fill markers: what the reference products do

Notes for the Stats tag tables and the chart's fill markers (October 2026). Features and workflow are the
reference; no vendor code, wording or assets.

## Trade Craft: "Finding edge tutorial" (YouTube, Trade Craft channel, video 8-okPwuHBNY, 42 min)

- **Tagging first** (around 4:00): each trade is tagged in groups shown as boxes of chips (an HTF bias group
  with Favor / Mid / Against / None, medium and micro bias groups, entry, price, window, intention, ratings,
  psychology questions). Some groups allow one choice.
- **Dashboard tables** (from 12:00): a table widget whose rows are "series". A series has a name, a colour and
  filters; the usual filter is one tag. Columns are chosen in a settings box: number of trades, cumulative
  P&L, win %, max drawdown, average trade P&L, average MAE, average MFE, average drawdown, biggest win and
  loss, biggest MAE and MFE, longest winning and losing streaks, number of drawdowns.
- **The edge page** (23:00 to 33:00): three such tables side by side, Technicals, Management and Psychological.
  Each lists every tag of its categories as a row, under a header row per category, in the tag's colour.
- **Page filters** (28:00): a row of tag chips above all tables, in the tags' colours ("HTF Mean Favor",
  "Favor Medium Mean", "Against Micro Mean", ...). Every table then shows only the trades with all of those
  tags, so one reads how each other tag does within that context. Each table can add its own filters too.
  The trades that match are listed under the tables.
- **Combos** (from 37:00): playbook rows made by clicking one tag in each of several groups; each combo row
  has its own stats.

The point for us: the context is chosen with a click (chips on the page itself), and every tag of a category
is compared in one table without building anything.

## Sierra Chart: order fills on the chart

From its Chart Trading documentation (sierrachart.com, "Displaying Order Fills on the Chart", image
FilledOrderDrawings.png):

- A small arrowhead points at each fill's exact price and time.
- Beside it a small boxed label with the size and price, e.g. `30.0K@1.22790` for a buy (green box, below the
  bar) and `-30.0K@1.22810` for a sell (red box, above the bar). The text can be switched off
  ("Display Fill Text"), leaving arrows only.
- Optional entry/exit connecting lines join each entry to the exit it was matched with, first in first out;
  they have a primary and a secondary colour.
