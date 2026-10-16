"""Execute the shipped SVG renderer on synthetic fills, without a browser or account."""
import json
from pathlib import Path
import shutil
import subprocess

import pytest


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
  fixture,
  fakeChart: {timeScale: () => scale, priceScale: () => ({width: () => 70})},
  fakeEntries: [{spec: fixture.series[0], api: {priceToCoordinate: price => 500 - price * 2}}],
});
vm.runInContext(script + '\nspec = fixture; chart = fakeChart; entries = fakeEntries; drawOverlay();', context);
process.stdout.write(JSON.stringify(roots['trade-overlay']));
"""


def render(fills, *, missing_minute=False):
    node = shutil.which('node')
    if not node:
        pytest.skip('Node.js is required to execute the shipped JavaScript renderer.')
    candles = [{'time': minute, 'open': 100, 'high': 103, 'low': 97, 'close': 101}
               for minute in (0, 60, 120, 180)]
    if missing_minute:
        candles[2] = {'time': 120}
    fixture = {'height': 520, 'timezone': 'UTC', 'theme': {'muted': '#888', 'surface': '#111'},
               'series': [{'type': 'candles', 'data': candles}],
               'tradeOverlays': {'fills': fills, 'levels': []}}
    result = subprocess.run([node, '-e', HARNESS, str(COMPONENT)], input=json.dumps(fixture),
                            text=True, capture_output=True, check=True, timeout=10)
    return json.loads(result.stdout)


def walk(root):
    yield root
    for child in root['children']:
        yield from walk(child)


def fill(sequence, side, role, time, price):
    return {'sequence': sequence, 'side': side, 'role': role, 'time': time, 'price': price,
            'qty': 1, 'color': '#58A6FF' if role == 'entry' else '#E8B45A',
            'ts': '1970-01-01T00:00:23.250Z', 'execution_id': f'paper-{sequence}',
            'label': f"{'Entry' if role == 'entry' else 'Exit'} · 1 @ {price}"}


@pytest.mark.parametrize('sides,prices', [(('buy', 'sell'), (100, 102)),
                                         (('sell', 'buy'), (100, 98))])
def test_long_and_short_arrows_are_anchored_to_exact_fills_without_visible_labels(sides, prices):
    fills = [fill(0, sides[0], 'entry', 23.25, prices[0]),
             fill(1, sides[1], 'exit', 97.75, prices[1])]
    nodes = list(walk(render(fills)))
    arrows = [n for n in nodes if n['tag'] == 'polygon']
    assert len(arrows) == 2
    assert not any(n['tag'] in ('circle', 'text') for n in nodes)
    for arrow, execution in zip(arrows, fills):
        points = [tuple(map(float, point.split(','))) for point in arrow['attrs']['points'].split()]
        tip_x, tip_y = points[0]
        assert tip_x == pytest.approx(40 + execution['time'] / 60 * 30)
        assert tip_y == 500 - execution['price'] * 2
        assert all(y > tip_y if execution['side'] == 'buy' else y < tip_y for _, y in points[1:])
        assert arrow['attrs']['fill'] == execution['color']
        assert arrow['attrs']['data-role'] == execution['role']
    connectors = [n for n in nodes if n['tag'] == 'line']
    assert len(connectors) == 1 and connectors[0]['attrs']['stroke-dasharray'] == '3 4'
    assert float(connectors[0]['attrs']['x1']) == pytest.approx(51.625)
    assert float(connectors[0]['attrs']['y2']) == 500 - prices[1] * 2
    titles = [n['textContent'] for n in nodes if n['tag'] == 'title']
    assert 'Entry · 1 @ 100' in titles[0] and 'Execution: paper-0' in titles[0]
    assert 'UTC 1970-01-01T00:00:23.250Z' in titles[0]
    assert any(n['attrs'].get('role') == 'img' and 'aria-label' in n['attrs'] for n in nodes)


def test_partial_exits_keep_each_arrow_and_consecutive_fill_connections():
    nodes = list(walk(render([fill(0, 'buy', 'entry', 23.25, 100),
                              fill(1, 'sell', 'exit', 50.5, 101),
                              fill(2, 'sell', 'exit', 97.75, 102)])))
    assert len([n for n in nodes if n['tag'] == 'polygon']) == 3
    assert len([n for n in nodes if n['tag'] == 'line']) == 2


def test_missing_candle_does_not_snap_arrow_or_connect_across_missing_fill():
    nodes = list(walk(render([fill(0, 'buy', 'entry', 23.25, 100),
                              fill(1, 'sell', 'exit', 130, 101),
                              fill(2, 'sell', 'exit', 191, 102)], missing_minute=True)))
    assert len([n for n in nodes if n['tag'] == 'polygon']) == 2
    assert not any(n['tag'] == 'line' for n in nodes)
