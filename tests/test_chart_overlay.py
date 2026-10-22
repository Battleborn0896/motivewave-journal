"""Execute the shipped SVG renderer on synthetic fills, without a browser or account."""
import itertools
import json
from pathlib import Path
import shutil
import subprocess

import pytest

from mwjournal import Execution, PriceSample
from mwjournal.chart import trade_chart
from mwjournal.matching import match


COMPONENT = Path(__file__).resolve().parent.parent / 'app' / 'components' / 'tvchart' / 'index.html'
HARNESS = r"""
const fs = require('node:fs');
const vm = require('node:vm');
const fixture = JSON.parse(fs.readFileSync(0, 'utf8'));
const html = fs.readFileSync(process.argv[1], 'utf8');
const script = [...html.matchAll(/<script(?:\s[^>]*)?>([\s\S]*?)<\/script>/g)]
  .map(match => match[1]).find(source => source.includes('function drawOverlay()'));
class Element {
  constructor(tag) { this.tag = tag; this.attrs = {}; this.children = []; this.clientWidth = 1000; }
  setAttribute(key, value) { this.attrs[key] = String(value); }
  appendChild(node) { this.children.push(node); }
  replaceChildren() { this.children = []; }
  addEventListener() {}
  getComputedTextLength() { return String(this.textContent).length * 6; }    // 6px a character, for the test
}
const roots = Object.fromEntries(['wrap', 'chart', 'trade-overlay'].map(id => [id, new Element(id)]));
const scale = {
  timeToCoordinate: time => 40 + time / 60 * 30,
  coordinateToLogical: x => (x - 40) / 30,
  logicalToCoordinate: logical => 40 + logical * 30,
  height: () => 25,
};
const context = vm.createContext({
  window: {LightweightCharts: {LineStyle: {}}, parent: {postMessage() {}}, addEventListener() {}},
  document: {getElementById: id => roots[id], createElementNS: (_, tag) => new Element(tag)},
  ResizeObserver: class { observe() {} },
  requestAnimationFrame: () => 1,
  setTimeout: () => 1,
  fixture,
  fakeChart: {timeScale: () => scale, priceScale: () => ({width: () => 70})},
  fakeEntries: [{spec: fixture.series[0], api: {priceToCoordinate: price => 500 - (price - fixture.base) * 2}}],
});
vm.runInContext(script + '\nspec = fixture; chart = fakeChart; entries = fakeEntries; drawOverlay();', context);
process.stdout.write(JSON.stringify(roots['trade-overlay']));
"""
UP, DOWN, MUTED, SURFACE = '#089981', '#F23645', '#888', '#111'
PLOT_WIDTH, PLOT_HEIGHT = 1000 - 70, 520 - 25       # the harness's chart less its price and time axes


def tip(price, base=0):
    """Where the harness draws `price`: y = 500 - (price - base) * 2."""
    return 500 - (price - base) * 2


def x_at(time):
    return round(40 + time / 60 * 30, 6)


def draw(series, overlays, *, base=0, fmt=None):
    node = shutil.which('node')
    if not node:
        pytest.skip('Node.js is required to execute the shipped JavaScript renderer.')
    fixture = {'height': 520, 'timezone': 'UTC', 'base': base, 'format': fmt,
               'theme': {'muted': MUTED, 'surface': SURFACE, 'candleUp': UP, 'candleDown': DOWN, 'font': 'Plex'},
               'series': [series], 'tradeOverlays': overlays}
    result = subprocess.run([node, '-e', HARNESS, str(COMPONENT)], input=json.dumps(fixture),
                            text=True, encoding='utf-8', capture_output=True, check=True, timeout=10)
    return list(walk(json.loads(result.stdout)))


def render(fills, *, missing_minute=False, levels=(), pairs=(), minutes=(0, 60, 120, 180), base=0):
    candles = [{'time': minute, 'open': 100 + base, 'high': 103 + base, 'low': 97 + base, 'close': 101 + base}
               for minute in minutes]
    if missing_minute:
        candles[2] = {'time': 120}
    return draw({'type': 'candles', 'data': candles},
                {'fills': fills, 'pairs': list(pairs), 'levels': list(levels)}, base=base)


def walk(root):
    yield root
    for child in root['children']:
        yield from walk(child)


def fill(sequence, side, role, time, price, qty=1):
    return {'sequence': sequence, 'side': side, 'role': role, 'time': time, 'price': price, 'qty': qty,
            'ts': '1970-01-01T00:00:23.250Z', 'execution_id': f'paper-{sequence}',
            'label': f"{'Entry' if role == 'entry' else 'Exit'} · {qty} @ {price}"}


def pair(entry, exit, points, qty=1):
    return {'entry': entry, 'exit': exit, 'qty': qty, 'points': points}


def arrows(nodes):
    return [[tuple(map(float, p.split(','))) for p in n['attrs']['points'].split()] for n in nodes
            if n['tag'] == 'polygon']


def boxes(nodes):
    """Each fill's label: its box (x, y, width, height), colour and text."""
    result = []
    for group in (n for n in nodes if 'data-detail' in n['attrs']):
        rect, = [c['attrs'] for c in group['children'] if c['attrs'].get('class') == 'trade-fill-label']
        text, = [c for c in group['children'] if c['tag'] == 'text']
        result.append(((float(rect['x']), float(rect['y']), float(rect['width']), float(rect['height'])),
                       rect['fill'], text))
    return result


def joins(nodes):
    """The entry/exit lines: (x1, y1, x2, y2, colour)."""
    return [tuple(round(float(n['attrs'][k]), 6) for k in ('x1', 'y1', 'x2', 'y2')) + (n['attrs']['stroke'],)
            for n in nodes if n['tag'] == 'line' and n['attrs'].get('stroke-dasharray') == '4 3']


@pytest.mark.parametrize('sides,prices', [(('buy', 'sell'), (100, 102)),
                                         (('sell', 'buy'), (100, 98))])
def test_a_small_arrow_points_at_each_exact_fill_with_its_size_and_price_boxed_beside_it(sides, prices):
    fills = [fill(0, sides[0], 'entry', 23.25, prices[0]),
             fill(1, sides[1], 'exit', 97.75, prices[1])]
    nodes = render(fills, pairs=[pair(0, 1, 2)])
    polygons = [n['attrs'] for n in nodes if n['tag'] == 'polygon']
    assert len(polygons) == 2
    labels = boxes(nodes)
    assert len(labels) == 2
    for points, polygon, (box, colour, text), execution in zip(arrows(nodes), polygons, labels, fills):
        buy = execution['side'] == 'buy'
        # The tip is the fill itself (seconds placed within the minute), and the arrowhead is small and solid: a
        # buy points up from below the price, a sell down from above, in the candles' up and down colours.
        (tip_x, tip_y), *base = points
        assert round(tip_x, 6) == x_at(execution['time']) and tip_y == tip(execution['price'])
        assert len(base) == 2 and all((y > tip_y) == buy and 0 < abs(y - tip_y) <= 8 and abs(x - tip_x) <= 6
                                      for x, y in base)
        assert polygon['fill'] == colour == (UP if buy else DOWN) and polygon['data-role'] == execution['role']
        # The box: signed size and price in the chart's price format, a buy's below the arrow, a sell's above.
        x, y, width, height = box
        assert text['textContent'] == ('+1 @ ' if buy else '−1 @ ') + f"{execution['price']:.2f}"
        assert text['attrs']['fill'] == SURFACE and float(text['attrs']['font-size']) <= 11
        assert width == len(text['textContent']) * 6 + 8 and height <= 16
        assert float(text['attrs']['x']) == pytest.approx(x + width / 2) and x < tip_x < x + width
        assert y > max(y for _, y in points) if buy else y + height < min(y for _, y in points)
        assert y < float(text['attrs']['y']) < y + height
    # The entry joined to its exit: one dashed line from tip to tip, green since that part made money.
    (line,) = joins(nodes)
    assert line == (x_at(23.25), tip(prices[0]), x_at(97.75), tip(prices[1]), UP)
    assert not any(n['tag'] == 'line' and n['attrs'].get('stroke') == MUTED for n in nodes)   # no consecutive joins
    # The details stay in the chart's own IBM Plex Sans tooltip (data-detail) over the arrow and its box, not an
    # SVG <title>, which the browser would show in the system's font.
    assert not any(n['tag'] == 'title' for n in nodes)
    groups = [n for n in nodes if 'data-detail' in n['attrs']]
    assert len(groups) == 2
    assert all({c['tag'] for c in g['children']} == {'polygon', 'rect', 'text'} for g in groups)
    details = [g['attrs']['data-detail'] for g in groups]
    assert 'Entry · 1 @ ' + str(prices[0]) in details[0] and 'paper-0' not in details[0]      # no fill id on screen
    assert 'UTC 1970-01-01T00:00:23.250Z' in details[0]
    assert any(n['attrs'].get('role') == 'img' and n['attrs'].get('aria-label') == details[0] for n in nodes)


def test_a_label_reads_size_and_price_as_the_axis_does():
    nodes = render([fill(0, 'buy', 'entry', 23.25, 29665.5, qty=2), fill(1, 'sell', 'exit', 97.75, 29690.5, qty=2)],
                   base=29600)
    assert [text['textContent'] for _, _, text in boxes(nodes)] == ['+2 @ 29,665.50', '−2 @ 29,690.50']


def test_scale_in_scale_out_lines_join_each_entry_to_its_exit_coloured_by_what_that_part_made():
    # Scaled in: two buys, both closed by one sell, one at a profit, one at a loss.
    nodes = render([fill(0, 'buy', 'entry', 23.25, 100), fill(1, 'buy', 'entry', 70, 104),
                    fill(2, 'sell', 'exit', 130, 102, qty=2)], pairs=[pair(0, 2, 2), pair(1, 2, -2)])
    assert joins(nodes) == [(x_at(23.25), tip(100), x_at(130), tip(102), UP),
                            (x_at(70), tip(104), x_at(130), tip(102), DOWN)]       # never entry to entry
    # Scaled out: one 2-lot buy, a sell at no change and a sell at a profit.
    nodes = render([fill(0, 'buy', 'entry', 23.25, 100, qty=2), fill(1, 'sell', 'exit', 70, 100),
                    fill(2, 'sell', 'exit', 130, 103)], pairs=[pair(0, 1, 0), pair(0, 2, 3)])
    assert joins(nodes) == [(x_at(23.25), tip(100), x_at(70), tip(100), MUTED),
                            (x_at(23.25), tip(100), x_at(130), tip(103), UP)]
    assert len(arrows(nodes)) == 3 and len(boxes(nodes)) == 3


def test_a_reversal_charts_each_trade_with_its_own_part_of_the_reversing_fill():
    """Long 1 from 100, sell 2 at 102 (out of the long, into a short), buy 1 back at 103: the journal's two trades,
    as the Trades page charts them."""
    executions = [Execution('motivewave', 'paper', 'ESZ6', side, qty, price, f'1970-01-01T00:{at}Z',
                            execution_id=id) for side, qty, price, at, id in
                  (('buy', 1, 100, '00:23.250', 'long'), ('sell', 2, 102, '01:37.500', 'reverse'),
                   ('buy', 1, 103, '02:30', 'cover'))]
    candles = [PriceSample('ESZ6', f'1970-01-01T00:0{m}:00Z', 'test', 104, 99, 60, open=100, close=103)
               for m in range(4)]
    long, short = match(executions)
    drawn = []
    for trade in (long, short):
        payload = trade_chart(trade, candles, 'test')
        drawn.append(draw(payload['series'], payload['overlays']))
    long_nodes, short_nodes = drawn
    # The long: a green line to the reversing sell, whose box shows the 1 that closed it.
    assert joins(long_nodes) == [(x_at(23.25), tip(100), x_at(97.5), tip(102), UP)]
    assert [text['textContent'] for _, _, text in boxes(long_nodes)] == ['+1 @ 100.00', '−1 @ 102.00']
    # The short: from the reversing sell to the buy that covered it, red since it lost a point.
    assert joins(short_nodes) == [(x_at(97.5), tip(102), x_at(150), tip(103), DOWN)]
    assert [text['textContent'] for _, _, text in boxes(short_nodes)] == ['−1 @ 102.00', '+1 @ 103.00']


def test_labels_never_overlap_and_stay_in_the_plot_clear_of_the_title_and_legend():
    fills = [fill(i, 'buy', 'entry', 23.25 + i, 100) for i in range(4)]             # a scale-in at one price
    fills += [fill(4, 'sell', 'exit', 60, 246), fill(5, 'sell', 'exit', 61, 245.5),   # at the top of the plot
              fill(6, 'buy', 'exit', 120, 3),                                       # at the bottom
              fill(7, 'buy', 'entry', 1765, 150)]                                   # at the right edge
    nodes = render(fills, minutes=(0, 60, 120, 180, 1740))
    labels = [box for box, _, _ in boxes(nodes)]
    assert len(labels) == len(fills)
    for (x, y, w, h), (x2, y2, w2, h2) in itertools.combinations(labels, 2):
        assert x + w <= x2 or x2 + w2 <= x or y + h <= y2 or y2 + h2 <= y
    assert all(0 <= x and x + w <= PLOT_WIDTH and 30 <= y and y + h <= PLOT_HEIGHT for x, y, w, h in labels)
    # The scale-in's boxes stack downward, away from the price, each still under its arrow.
    assert [y for _, y, _, _ in labels[:4]] == sorted(y for _, y, _, _ in labels[:4])
    assert all(y > tip(100) for _, y, _, _ in labels[:4])


def test_a_fill_out_of_view_leaves_no_label_at_the_edge_but_its_line_still_runs_toward_it():
    nodes = render([fill(0, 'buy', 'entry', 23.25, 100), fill(1, 'sell', 'exit', 1830, 101)],
                   minutes=(0, 60, 1800), pairs=[pair(0, 1, 1)])
    assert len(arrows(nodes)) == len(boxes(nodes)) == 1
    assert joins(nodes) == [(x_at(23.25), tip(100), x_at(1830), tip(101), UP)]


def test_missing_candle_does_not_snap_an_arrow_or_join_a_fill_that_is_not_drawn():
    nodes = render([fill(0, 'buy', 'entry', 23.25, 100, qty=2),
                    fill(1, 'sell', 'exit', 130, 101),
                    fill(2, 'sell', 'exit', 191, 102)], missing_minute=True, pairs=[pair(0, 1, 1), pair(0, 2, 2)])
    assert len(arrows(nodes)) == 2
    assert joins(nodes) == [(x_at(23.25), tip(100), x_at(191), tip(102), UP)]


def test_the_stop_r_uses_crosses_the_whole_chart_and_a_moved_stop_its_own_minutes():
    """A platform draws a stop across the chart; one minute's trade must not leave it a sliver."""
    levels = [{'from': 23.25, 'to': 97.75, 'price': 98, 'label': 'Stop · R', 'color': '#F05B70', 'style': 'solid',
               'full': True},
              {'from': 60, 'to': 97.75, 'price': 99, 'label': 'Stop order', 'color': '#F05B70', 'style': 'dashed'}]
    nodes = render([fill(0, 'buy', 'entry', 23.25, 100), fill(1, 'sell', 'exit', 97.75, 102)], levels=levels)
    stops = [n['attrs'] for n in nodes if n['tag'] == 'line' and n['attrs'].get('stroke') == '#F05B70']
    full, moved = stops
    assert float(full['x1']) == 0 and float(full['x2']) == 930 and 'stroke-dasharray' not in full
    assert float(moved['x1']) == pytest.approx(40 + 60 / 60 * 30) and float(moved['x2']) == pytest.approx(40 + 97.75 / 60 * 30)
    assert moved['stroke-dasharray'] == '7 4'
