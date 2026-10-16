"""Stats: the numbers first, one row of controls, and an equity curve that counts trades instead of
drawing nights and weekends as flat lines. Disposable journals only, in a temporary data home."""

import json

import pytest

import common
import runtime
from mwjournal import Execution, Journal
from test_app import open_app, show, widget
from views import edge

NY = 'America/New_York'
# Friday afternoon, Friday evening and Monday morning in New York: a weekend between the last two.
TRADES = [('f1', '2026-10-09T14:00:00Z', '2026-10-09T14:05:00Z', 5000, 5004),
          ('f2', '2026-10-09T19:30:00Z', '2026-10-09T19:40:00Z', 5010, 5008),
          ('m1', '2026-10-12T13:35:00Z', '2026-10-12T13:50:00Z', 5020, 5030)]


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    monkeypatch.setenv('MWJ_HOME', str(tmp_path))
    for dataset in ('live', 'replay'):
        Journal(dataset=dataset).save_preferences(display_timezone=NY)
    yield tmp_path
    runtime.stop_all()


def three_trades():
    journal = Journal()
    for id, opened, closed, entry, exit in TRADES:
        journal.ingest([(Execution('motivewave', 'paper', 'ESZ6', 'buy', 1, entry, opened, execution_id=id + 'a',
                                   commission=2), {}),
                        (Execution('motivewave', 'paper', 'ESZ6', 'sell', 1, exit, closed, execution_id=id + 'b',
                                   commission=2), {})])
    return journal


def walk(node, seen=None):
    """Every element under a block, top to bottom, as (type, label)."""
    seen = [] if seen is None else seen
    for child in getattr(node, 'children', {}).values():
        kind = getattr(child, 'type', type(child).__name__)
        label = getattr(child, 'label', None)
        if kind == 'popover':
            label = child.proto.popover.label
        seen.append((kind, label))
        if kind != 'popover':                       # what a closed menu holds is not on the page
            walk(child, seen)
    return seen


def spec(app, text):
    return next(json.loads(chart.proto.spec) for chart in app.get('plotly_chart') if text in chart.proto.spec)


def test_stats_opens_on_the_headline_numbers_with_one_row_of_controls_above_them():
    three_trades()
    app = open_app('Edge')
    order = walk(app.main)
    first_number = order.index(next(item for item in order if item[0] == 'metric'))
    above = order[:first_number]
    assert [label for kind, label in above if kind == 'popover'] == ['Filters', 'Customize']
    assert not any(kind in ('button', 'expander') for kind, _ in above)            # no stacked buttons first
    assert order.index(next(item for item in order if item[0] == 'tab')) > first_number   # numbers, then charts
    assert [m.label for m in app.metric][:5] == ['Net P&L', 'Win Rate', 'Average Trade', 'Profit Factor',
                                                 'Largest Drawdown']
    assert widget(app.metric, 'Net P&L').value == '+$588.00'
    assert widget(app.metric, 'Average Trade').value == '+$196.00'
    assert widget(app.metric, 'Win Rate').value == '66.7%'
    assert widget(app.metric, 'Profit Factor').value == '6.65'
    assert widget(app.metric, 'Largest Drawdown').value == '$104.00'
    assert not any(b.label.startswith('Refresh') for b in app.button)
    assert not any(s.label == 'Analysis timezone' for s in app.selectbox)


def test_the_equity_curve_counts_trades_and_hovering_shows_when_and_how_much():
    three_trades()
    app = open_app('Edge')
    curve = spec(app, 'Net total')
    net, gross = curve['data']
    assert net['name'] == 'Net total' and gross['name'] == 'Gross total'
    assert net['x'] == [1, 2, 3]                                         # the weekend takes no room
    assert net['y'] == [196, 92, 588] and gross['y'] == [200, 100, 600]
    assert curve['layout']['xaxis']['title']['text'] == 'Trade number'
    assert curve['layout']['xaxis']['range'] == [.5, 3.5]                  # no empty 'trade 0'
    assert curve['layout']['xaxis']['unifiedhovertitle']['text'] == 'Trade %{x}'
    assert curve['layout']['yaxis']['tickformat'].startswith('$')
    when, result, total = net['customdata'][2]
    assert when == common.moment('2026-10-12T13:50:00Z', NY, seconds=False) + ' · ESZ6'     # Oct 12, 9:50 AM
    assert result == '+$496.00' and total == '+$588.00'
    assert net['customdata'][1][1] == '-$104.00'


def test_the_equity_curve_switches_to_the_display_clock_and_remembers_it():
    three_trades()
    app = open_app('Edge')
    toggle = app.segmented_control(key='equity-axis-live')
    assert toggle.value == 'trade' and toggle.options == ['By Trade', 'By Date']
    toggle.set_value('time').run()
    assert not app.exception
    curve = spec(app, 'Net total')
    net = curve['data'][0]
    assert [x[:16] for x in net['x']] == ['2026-10-09T10:05', '2026-10-09T15:40', '2026-10-12T09:50']   # New York
    assert curve['layout']['xaxis']['title']['text'] == f'Exit time · {NY}'
    assert net['customdata'][0][0] == 'Trade 1 · ESZ6'
    show(app, 'Trades')
    show(app, 'Edge')
    assert app.segmented_control(key='equity-axis-live').value == 'time'


def test_a_dashboard_equity_widget_counts_trades_too_and_can_be_put_on_the_clock(home):
    journal = three_trades()
    page = journal.save_dashboard('Clock', [{'kind': 'Equity', 'title': 'On the clock', 'axis': 'time'}])
    app = open_app('Dashboard')
    curve = spec(app, '"Total"')
    assert curve['data'][0]['x'] == [1, 2, 3] and curve['layout']['xaxis']['title']['text'] == 'Trade number'
    widget(app.selectbox, 'Dashboard page').set_value(page).run()
    assert not app.exception
    curve = spec(app, '"Total"')
    assert curve['data'][0]['x'][0].startswith('2026-10-09T10:05')


def test_progress_chart_numbers_trades_from_one_without_clock_gaps():
    rows = [{'closed': closed, 'symbol': 'ESZ6', 'net': net} for (_, _, closed, *_), net in zip(TRADES, (196, -104, 496))]
    figure = edge.progress_chart(rows, [('Net total', [196, 92, 588], '#fff')], NY, currency='USD')
    assert list(figure.data[0].x) == [1, 2, 3] and figure.layout.xaxis.dtick == 1
    by_date = edge.progress_chart(rows, [('Net total', [196, 92, 588], '#fff')], NY, axis='time', currency='USD')
    assert [str(x)[:16] for x in by_date.data[0].x] == ['2026-10-09 10:05', '2026-10-09 15:40', '2026-10-12 09:50']


def test_unconfirmed_fees_are_one_short_line_with_the_explanation_on_hover():
    journal = Journal()
    journal.ingest([(Execution('motivewave', 'paper', 'ESZ6', 'buy', 1, 5000, '2026-10-09T14:00:00Z', execution_id='u1',
                               commission=1.24, commission_known=False), {}),
                    (Execution('motivewave', 'paper', 'ESZ6', 'sell', 1, 5004, '2026-10-09T14:05:00Z', execution_id='u2',
                               commission=1.24, commission_known=False), {})])
    app = open_app('Edge')
    line = [c for c in app.main.caption if 'Fees not confirmed' in c.value]
    assert len(line) == 1 and 'on 1 trade]' in line[0].value and 'estimate' in line[0].proto.help
    assert not app.warning


def test_the_compare_table_writes_average_r_but_still_sorts_it_as_a_number():
    groups = [{'group': 'Breakout', 'count': 2, 'net': 810.02, 'win_rate': 50.0, 'expectancy': 405.01,
               'avg_win': 900.0, 'avg_loss': -89.98, 'expectancy_r': .5, 'risk_recorded': 2},
              {'group': 'Range', 'count': 1, 'net': -152.48, 'win_rate': 0.0, 'expectancy': -152.48,
               'avg_win': None, 'avg_loss': -152.48, 'expectancy_r': None, 'risk_recorded': 0}]
    table = edge.compare_table(groups, 'Tags', 'USD')
    assert table.data['Average R'].dtype.kind == 'f'                       # sorts as a number
    shown = table.to_string()
    assert '+0.50R' in shown and '—' in shown and 'None' not in shown and 'nan' not in shown
    assert '+$810.02' in shown and '-$152.48' in shown
    assert list(table.data.columns)[0] == 'Tag'
