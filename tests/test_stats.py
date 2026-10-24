"""Stats: the numbers first, one row of controls, and an equity curve that counts trades instead of
drawing nights and weekends as flat lines. Disposable journals only, in a temporary data home."""

import html
import json
import re

import pytest

import common
import runtime
import theme
from mwjournal import Execution, Journal, tagging
from mwjournal.workspace import GROUPS
from test_app import open_app, open_tab, press, said, show, widget
from test_workspace import calendar_cells
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
    assert [label for kind, label in above if kind == 'popover'] == ['Filters']                # one menu, no Customize
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
    journal.save_dashboard('Trades', [{'kind': 'Equity', 'title': 'By trade'}])
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


def tagged_trades():
    """three_trades() tagged from the starter categories: f1 +$196 and f2 -$104 are Breakouts, m1 +$496 a Pullback;
    f1 and m1 Followed Plan, f2 Chased Entry; f1 Calm, f2 FOMO, m1 no Mindset tag. f1 has 5 stars, f2 1 star, m1
    none. Only f1 has a stop: 1.96R."""
    journal = three_trades()
    trades = traded(journal)
    for id, setup, execution, mindset, rating in (('f1', 'Breakout', 'Followed Plan', 'Calm', 5),
                                                  ('f2', 'Breakout', 'Chased Entry', 'FOMO', 1),
                                                  ('m1', 'Pullback', 'Followed Plan', None, None)):
        journal.set_trade_tag_group(trades[id], 'Setup', [setup])
        journal.set_trade_tag_group(trades[id], 'Execution', [execution])
        if mindset:
            journal.set_trade_tag_group(trades[id], 'Mindset', [mindset])
        if rating:
            journal.update_annotation(trades[id].id, {'rating': rating})
    journal.update_annotation(trades['f1'].id, {'planned_stop': 4998})
    return journal


def traded(journal):
    """The trades of three_trades() by their name in TRADES."""
    return {t.fills[0]['execution_id'][:2]: t for t in journal.trades()}


STAR = ' :material/star:'          # a rating chip reads '5' and a star


def tables(app):
    """The Compare tables, left to right and top to bottom, as (title, {name: (trades, net P&L, win %, average
    trade, mark)}): the cells as a trader reads them, the mark 'none' for a row without trades and 'small' for
    one with fewer than 10."""
    found = []
    for element in app.markdown:
        if "class='mwj-edge-table'" not in element.value:
            continue
        title = re.search(r"<th class='t'[^>]*>(.*?)</th>", element.value).group(1)
        rows = {}
        for mark, cells in re.findall(r"<tr class='(\w*)'[^>]*>(.*?)</tr>", element.value):
            values = [html.unescape(re.sub('<[^>]+>', '', cell)) for cell in re.findall(r'<td[^>]*>(.*?)</td>', cells)]
            rows[values[0]] = (*values[1:], mark)
        found.append((html.unescape(title), rows))
    return found


def chips(app):
    """The chips' words, in order, and the icon each shows: '' off, 'check' kept, 'close' left out."""
    return [(b.label, b.proto.icon.removeprefix(':material/').removesuffix(':')) for b in app.button
            if b.key and b.key.startswith('edge-chip-')]


def click(app, *labels):
    """Click chips by their words, in order: once keeps the trades with it, twice leaves them out, a third time
    stops using it."""
    for label in labels:
        next(b for b in app.button if b.key and b.key.startswith('edge-chip-') and b.label == label).click().run()
        assert not app.exception


def narrowed(app):
    """The sentence under the chips, or None when no chip is in use."""
    return next((c.value for c in app.caption if 'closed trades' in c.value), None)


def menus(app):
    """The menus (popovers) on the page: {label: help}."""
    return {p.proto.popover.label: p.proto.popover.help for p in app.get('popover')}


def listed_trades(app):
    """The trade ids listed under the tables, top to bottom."""
    return [b.key[len('edge-open-'):-len('-live')] for b in app.button if b.key and b.key.startswith('edge-open-')]


def test_compare_lists_every_tag_and_star_rating_with_totals_and_marks_small_samples():
    tagged_trades()
    app = open_tab(open_app('Edge'), 'Compare')
    assert widget(app.selectbox, 'Compare by').value == 'Tags'
    # The chips: each category in the library's order, the tags these trades carry, then the ratings they have.
    titles = [re.sub('<[^>]+>', '', m.value) for m in app.markdown if 'mwj-card-title' in m.value and '<style>' not in m.value]
    assert titles == ['Setup', 'Execution', 'Mindset', 'Rating']
    assert chips(app) == [('Breakout', ''), ('Pullback', ''), ('Followed Plan', ''), ('Chased Entry', ''), ('Calm', ''),
                          ('FOMO', ''), ('5' + STAR, ''), ('1' + STAR, '')]
    assert narrowed(app) is None
    # Every stylesheet is one <style>, so like every other it takes no room on the page (theme.CSS).
    assert all(m.value.count('<style>') == 1 for m in app.markdown if '<style>' in m.value)
    # Totals, then a table per category with every tag in order, 0 trades faded, then the trades with none of them;
    # then the ratings, best first. Three trades in all: every number is a small sample.
    small = 'small'
    assert tables(app) == [
        ('Totals', {'All trades': ('3', '+$588', '66.7%', '+$196', small), 'Wins': ('2', '+$692', '100.0%', '+$346', small),
                    'Losses': ('1', '-$104', '0.0%', '-$104', small)}),
        ('Setup', {'Breakout': ('2', '+$92', '50.0%', '+$46', small), 'Pullback': ('1', '+$496', '100.0%', '+$496', small),
                   'Reversal': ('0', '—', '—', '—', 'none'), 'Range': ('0', '—', '—', '—', 'none')}),
        ('Execution', {'Followed Plan': ('2', '+$692', '100.0%', '+$346', small),
                       'Chased Entry': ('1', '-$104', '0.0%', '-$104', small), 'Exited Early': ('0', '—', '—', '—', 'none'),
                       'Let Winner Run': ('0', '—', '—', '—', 'none'), 'Moved Stop': ('0', '—', '—', '—', 'none')}),
        ('Mindset', {'Calm': ('1', '+$196', '100.0%', '+$196', small), 'Patient': ('0', '—', '—', '—', 'none'),
                     'FOMO': ('1', '-$104', '0.0%', '-$104', small), 'Hesitant': ('0', '—', '—', '—', 'none'),
                     'Tilted': ('0', '—', '—', '—', 'none'), 'No Mindset tag': ('1', '+$496', '100.0%', '+$496', small)}),
        ('Rating', {'5 stars': ('1', '+$196', '100.0%', '+$196', small), '4 stars': ('0', '—', '—', '—', 'none'),
                            '3 stars': ('0', '—', '—', '—', 'none'), '2 stars': ('0', '—', '—', '—', 'none'),
                            '1 star': ('1', '-$104', '0.0%', '-$104', small),
                            'Not rated': ('1', '+$496', '100.0%', '+$496', small)})]
    page = next(m.value for m in app.markdown if "<th class='t' style='color:#65CA98'>Setup</th>" in m.value)
    assert f"style='background:#65CA98;color:{theme.ink_on('#65CA98')}'>Breakout<" in page
    rating = next(m.value for m in app.markdown if '>Rating</th>' in m.value)
    assert f"background:{theme.RATING_COLOURS[5]}" in rating and f"background:{theme.RATING_COLOURS[1]}" in rating
    note = next(c for c in app.caption if 'fewer than 10 trades' in c.value)
    assert '100 to 150' in note.proto.help
    # Under the tables: the equity curve of these trades, and the trades, newest first.
    assert len(spec(app, 'Net total')['data'][0]['y']) == 3
    trades = traded(Journal())
    assert listed_trades(app) == [trades['m1'].id, trades['f2'].id, trades['f1'].id]


def test_a_chip_keeps_then_leaves_out_then_stops_and_says_so_in_words():
    tagged_trades()
    app = open_tab(open_app('Edge'), 'Compare')
    click(app, 'Breakout')
    assert narrowed(app) == 'With Breakout · 2 of 3 closed trades'
    assert ('Breakout', 'check') in chips(app)
    setup = dict(tables(app))['Setup']
    assert setup['Breakout'][0] == '2' and setup['Pullback'] == ('0', '—', '—', '—', 'none')
    assert dict(tables(app))['Totals']['All trades'][:2] == ('2', '+$92')
    assert len(spec(app, 'Net total')['data'][0]['y']) == 2 and len(listed_trades(app)) == 2
    # Ratings narrow like tags: a trade has one, so leaving out the 1-star trades drops the impulsive one.
    click(app, '1' + STAR, '1' + STAR)
    assert ('1' + STAR, 'close') in chips(app)
    assert narrowed(app) == 'With Breakout, without 1 star · 1 of 3 closed trades'
    assert dict(tables(app))['Rating']['1 star'] == ('0', '—', '—', '—', 'none')
    click(app, 'Breakout')
    assert narrowed(app) == 'Without Breakout and 1 star · 1 of 3 closed trades'
    assert dict(tables(app))['Setup']['Pullback'][0] == '1'
    click(app, 'Breakout')
    assert ('Breakout', '') in chips(app) and narrowed(app) == 'Without 1 star · 2 of 3 closed trades'
    # Still applied after another tab was open, and every other way to compare uses the same trades.
    open_tab(app, 'Overview')
    open_tab(app, 'Compare')
    assert narrowed(app) == 'Without 1 star · 2 of 3 closed trades'
    widget(app.selectbox, 'Compare by').set_value('Direction').run()
    assert next(d.value for d in app.dataframe if 'Direction' in d.value.columns)['Trades'].tolist() == [2]
    assert len(listed_trades(app)) == 2
    widget(app.selectbox, 'Compare by').set_value('Tags').run()
    # One way to stop using them all.
    widget(app.button, 'Clear All').click().run()
    assert narrowed(app) is None and all(icon == '' for _, icon in chips(app))
    assert dict(tables(app))['Totals']['All trades'][0] == '3'
    click(app, 'Pullback', 'FOMO')                            # no Pullback was taken in FOMO
    assert said(app.info, 'No closed trade matches these chips') and not tables(app)


def test_tag_tables_follow_the_filters_and_leave_retired_tags_out_of_the_chips():
    journal = tagged_trades()
    journal.edit_tag(next(iter(tagging.ids(journal.tag_library(), ['FOMO']))), retired=True)
    app = open_tab(open_app('Edge'), 'Compare')
    assert 'FOMO' not in [label for label, _ in chips(app)]
    assert dict(tables(app))['Mindset']['FOMO'][0] == '1'                  # its trade still counts
    click(app, 'Chased Entry')
    assert narrowed(app) == 'With Chased Entry · 1 of 3 closed trades'
    # The page's own Filters come first. No trade they keep was a Chased Entry: that chip is no longer offered,
    # and nothing stays applied that the page does not show.
    widget(app.multiselect, 'Tags').set_value(['Followed Plan']).run()
    assert not app.exception
    assert [label for label, _ in chips(app)][:3] == ['Breakout', 'Pullback', 'Followed Plan']
    assert narrowed(app) is None
    assert dict(tables(app))['Setup']['Breakout'][:2] == ('1', '+$196')
    click(app, 'Pullback')
    assert narrowed(app) == 'With Pullback · 1 of 2 closed trades'


def test_typed_tags_have_a_plain_row_for_the_trades_without_one():
    journal = tagged_trades()
    journal.update_annotation(traded(journal)['f1'].id, {}, add_tags=['Scalp'])     # typed: filed under Other tags
    app = open_tab(open_app('Edge'), 'Compare')
    other = dict(tables(app))['Other tags']
    assert list(other) == ['Scalp', 'No Other tag'] and other['No Other tag'][0] == '2'


def test_the_trade_list_opens_a_trade_on_trades_and_shows_more_on_request(monkeypatch):
    tagged_trades()
    trades = traded(Journal())
    monkeypatch.setattr(edge, 'LIST_LIMIT', 2)
    app = open_tab(open_app('Edge'), 'Compare')
    assert listed_trades(app) == [trades['m1'].id, trades['f2'].id]
    row = next(m.value for m in app.markdown if "class='mwj-edge-trade'" in m.value and 'FOMO' in m.value)
    assert '>Long</b> 1' in row and '-&#36;104.00' in row and '1 star' in row and '>Breakout<' in row
    widget(app.button, 'Show More Trades').click().run()
    assert listed_trades(app) == [trades['m1'].id, trades['f2'].id, trades['f1'].id]
    # A click opens that trade on Trades, not a Daily Report Card.
    app.button(key=f'edge-open-{trades["f2"].id}-live').click().run()
    assert not app.exception
    assert app.session_state[common.VIEW_KEY] == 'Trades'
    assert app.session_state['selected-trade-live'] == trades['f2'].id
    assert said(app.caption, '2 of 3') and not any(t.label.startswith('What I learned') for t in app.text_area)


def test_save_as_playbook_keeps_the_kept_tags_with_a_name_and_colour():
    tagged_trades()
    app = open_tab(open_app('Edge'), 'Compare')
    assert 'Save as Playbook' not in menus(app)
    click(app, '5' + STAR)                                    # a rating is not a tag: nothing to save yet
    assert 'Save as Playbook' not in menus(app)
    click(app, 'Breakout', 'Followed Plan', 'FOMO', 'FOMO')
    assert narrowed(app) == 'With Breakout, Followed Plan and 5 stars, without FOMO · 1 of 3 closed trades'
    assert 'not part of a playbook' in menus(app)['Save as Playbook']
    widget(app.text_input, 'Playbook name').set_value('A+')
    widget(app.color_picker, 'Playbook color').set_value('#E2CF32')
    widget(app.button, 'Save Playbook').click().run()
    assert not app.exception and said(app.success, 'Saved A+')
    library = Journal().tag_library()
    (book,) = library['playbooks'].values()
    # The kept tags, one per category; what is left out and the stars are not part of it.
    assert book['name'] == 'A+' and book['color'] == '#E2CF32'
    assert book['rules'] == {'Setup': list(tagging.ids(library, ['Breakout'])),
                             'Execution': list(tagging.ids(library, ['Followed Plan']))}
    # Then it works where playbooks work: Compare by Playbooks, in its colour.
    widget(app.button, 'Clear All').click().run()
    widget(app.selectbox, 'Compare by').set_value('Playbooks').run()
    table = next(d for d in app.dataframe if 'Playbook' in d.value.columns)
    assert table.value.set_index('Playbook')['Trades'].to_dict() == {'A+': 1, 'No matching playbook': 2}
    assert 'background-color: #E2CF32' in table.proto.arrow_data.styler.styles
    # Two kept tags in one category are not a playbook: a playbook takes any one of them.
    widget(app.selectbox, 'Compare by').set_value('Tags').run()
    click(app, 'Breakout', 'Pullback')
    assert said(app.info, 'Keep one Setup tag to save a playbook')
    assert not any(t.label == 'Playbook name' for t in app.text_input)
    # Its colour is changed where playbooks are edited, and the tags stay as they were.
    open_tab(show(app, 'Tags'), 'Playbooks')
    widget(app.selectbox, 'Edit playbook').set_value(book['id']).run()
    assert widget(app.color_picker, 'Playbook color').value == '#E2CF32'
    widget(app.color_picker, 'Playbook color').set_value('#45BF89')
    widget(app.button, 'Save Playbook').click().run()
    assert not app.exception
    assert Journal().tag_library()['playbooks'][book['id']] == {**book, 'color': '#45BF89'}


def test_compare_says_so_when_no_trade_has_a_tag():
    three_trades()
    app = open_tab(open_app('Edge'), 'Compare')
    assert not chips(app) and said(app.info, 'None of these trades has a tag or a Rating yet')
    assert listed_trades(app)                                 # the trades and their curve are still there
    widget(app.selectbox, 'Compare by').set_value('Weekday').run()
    assert next(d.value for d in app.dataframe if 'Weekday' in d.value.columns)['Trades'].sum() == 3


def compared(app, field):
    """The Compare table by `field`, top to bottom, as {name: Net P&L}."""
    widget(app.selectbox, 'Compare by').set_value(field).run()
    assert not app.exception
    table = next(d.value for d in app.dataframe if field in d.value.columns)
    return dict(zip(table[field], table['Net P&L']))


def test_every_table_but_the_tag_tables_lists_the_worst_net_pnl_first():
    """f1 +$196 entered at 10:00 on Friday, f2 -$104 at 15:00 on Friday, m1 +$496 at 09:30 on Monday, New York time."""
    tagged_trades()
    app = open_tab(open_app('Edge'), 'Compare')
    assert widget(app.selectbox, 'Compare by').options == list(GROUPS)
    assert list(compared(app, 'Entry hour')) == ['15:00', '10:00', '09:00']        # not 09:00, 10:00, 15:00
    assert list(compared(app, 'Weekday')) == ['Friday', 'Monday']
    assert list(compared(app, 'Direction')) == ['long']
    # The tag tables keep your own order: Breakout (+$92) stays above Pullback (+$496), and Rating 5 stars to 1.
    widget(app.selectbox, 'Compare by').set_value('Tags').run()
    assert list(dict(tables(app))['Setup'])[:2] == ['Breakout', 'Pullback']
    assert list(dict(tables(app))['Rating'])[:5] == ['5 stars', '4 stars', '3 stars', '2 stars', '1 star']


def numbers(values):
    """A chart's numbers as a list: Plotly sends a column of numbers packed, as {'dtype', 'bdata'}."""
    if isinstance(values, dict):
        import base64
        import numpy
        return numpy.frombuffer(base64.b64decode(values['bdata']), dtype=values['dtype']).tolist()
    return list(values)


def test_one_compare_list_for_stats_the_compare_widget_and_after_exit(home):
    journal = tagged_trades()
    page = journal.save_dashboard('Bars', [{'kind': 'Compare', 'title': 'By hour', 'group': 'Entry hour'},
                                           {'kind': 'Compare', 'title': 'By tag', 'group': 'Tags'}])
    app = open_tab(open_app('Edge'), 'After Exit')
    assert widget(app.selectbox, 'Compare by').options == list(GROUPS)
    open_tab(app, 'Dashboards')
    assert widget(app.selectbox, 'Dashboard page').value == page
    assert widget(app.selectbox, 'Compare by').options == list(GROUPS)       # under Customize Page
    hours, tags = (json.loads(c.proto.spec) for c in app.get('plotly_chart'))
    # The bars, lowest first.
    assert hours['data'][0]['x'] == ['15:00', '10:00', '09:00'] and numbers(hours['data'][0]['y']) == [-104, 196, 496]
    assert not hours['layout'].get('showlegend')
    assert 'Category' not in hours['data'][0]['hovertemplate']                 # an hour has no tag category
    # Tag bars keep the library's order: category by category in its colour, each category's tags as it lists them.
    assert [(bars['name'], bars['x'], numbers(bars['y'])) for bars in tags['data']] == [
        ('Setup', ['Breakout', 'Pullback'], [46, 496]),
        ('Execution', ['Followed Plan', 'Chased Entry'], [346, -104]),
        ('Mindset', ['Calm', 'FOMO'], [196, -104])]
    assert tags['data'][0]['marker']['color'] == '#65CA98' and tags['layout']['showlegend']
    assert tags['data'][0]['hovertemplate'].startswith('Category=')


def test_the_overview_calendar_counts_what_stats_counts_and_opens_a_days_report_card():
    journal = three_trades()
    journal.ingest([(Execution('motivewave', 'paper', 'NQZ6', side, 1, price, at, execution_id=at), {})
                    for side, price, at in (('buy', 20000, '2026-10-13T14:00:00Z'),
                                            ('sell', 20010, '2026-10-13T14:05:00Z'))])
    app = open_app('Edge')
    assert said(app.markdown, '### Trading Calendar')
    cells = calendar_cells(app)
    assert cells['9'][1:] == ('+$92', '2 trades') and cells['12'][1:] == ('+$496', '1 trade')
    assert cells['13'][1:] == ('+$200', '1 trade')
    # The Filters above Stats narrow it too.
    widget(app.multiselect, 'Markets').set_value(['ES']).run()
    assert not app.exception
    cells = calendar_cells(app)
    assert cells['13'] == (['mwj-cal'], None, None) and cells['Month'][1:] == ('+$588', '3 trades')
    # A day opens its Daily Report Card on Trades.
    next(b for b in app.button if b.key == 'overview-calendar-live-day-2026-10-12').click().run()
    assert not app.exception and app.session_state[common.VIEW_KEY] == 'Trades'
    assert widget(app.date_input, 'Trading day').value.isoformat() == '2026-10-12'


def test_after_exit_reads_the_reviews_once_not_once_per_trade(monkeypatch):
    """Each Stats row already knows its stop: comparing reads only each trade's own prices."""
    tagged_trades()
    app = open_tab(open_app('Edge'), 'After Exit')
    reads = []
    original = Journal.annotations
    monkeypatch.setattr(Journal, 'annotations', lambda self: reads.append(self) or original(self))
    press(app, 'Compare')
    assert said(app.caption, '3 trades · ')
    assert len(reads) <= 1


def test_the_period_above_stats_keeps_this_month_last_month_or_the_last_three_and_stays_chosen():
    """A monthly review starts with Last Month: the period chosen above Stats is what every number counts."""
    import datetime as dt
    from zoneinfo import ZoneInfo
    first = dt.datetime.now(ZoneInfo(NY)).date().replace(day=1)
    months = [first]
    for _ in range(5):
        months.append((months[-1] - dt.timedelta(days=1)).replace(day=1))
    journal = Journal()
    for n, month in enumerate([months[0], months[1], months[2], months[5]]):      # this month, last, 2 back, 5 back
        opened = dt.datetime.combine(month, dt.time(14, 30), dt.timezone.utc)
        journal.ingest([(Execution('motivewave', 'paper', 'ESZ6', 'buy', 1, 5000, opened.isoformat(),
                                   execution_id=f'p{n}a', commission=2), {}),
                        (Execution('motivewave', 'paper', 'ESZ6', 'sell', 1, 5000 + n + 1,
                                   (opened + dt.timedelta(minutes=5)).isoformat(), execution_id=f'p{n}b',
                                   commission=2), {})])
    app = open_app('Edge')
    period = app.get('button_group')[0]
    assert list(period.options) == ['This Month', 'Last Month', 'Last 3 Months', 'All'] and period.value == 'All'
    assert said(app.markdown, '4 closed trades')
    expected = {'This Month': '1 of 4 closed trades', 'Last Month': '1 of 4 closed trades',
                'Last 3 Months': '3 of 4 closed trades'}
    shown = {'This Month': months[:1], 'Last Month': months[1:2], 'Last 3 Months': months[:3]}
    for choice, line in expected.items():
        app.get('button_group')[0].set_value(choice).run()
        assert not app.exception and said(app.markdown, line), choice
        # The calendar on Overview counts the same trades: only the months of the period.
        assert app.selectbox(key='overview-calendar-live-month').options == [
            month.strftime('%B %Y') for month in shown[choice]], choice
    show(app, 'Trades')
    show(app, 'Edge')
    assert app.get('button_group')[0].value == 'Last 3 Months'                      # still chosen
