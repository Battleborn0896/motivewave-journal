"""After Exit: what the price did in the minutes after a trade's exit (post_exit), held until the trade's stop or
breakeven. One sentence on the trade, and averages by tag, playbook, market or time on the Stats tab of that name."""
import pandas as pd
import streamlit as st

import common
from mwjournal import risk, units
from mwjournal.post_exit import AFTER_EXIT_MINUTES, COMPLETE, best_post_exit, runner_groups
from mwjournal.workspace import GROUPS, group_labels

# What a trade with none of its tags or playbooks counts under.
NONE = {'Tags': 'Untagged', 'Playbooks': 'No matching playbook'}


def line(journal, trade, unit) -> str | None:
    """What the price did in the AFTER_EXIT_MINUTES after a closed trade's exit, in one sentence with its amount in
    `unit`, the MAE / MFE unit. None for an open, incomplete or merged trade, and for one whose contract has no
    dollars per point yet (the trade says so above it)."""
    if not trade.closed or not trade.complete or getattr(trade, 'merge_leg_ids', None) or trade.point_value is None:
        return None
    plan = risk.plan_for(journal, trade)
    result = best_post_exit(journal, trade, plan.stop)
    stop = 'your stop' if result['held_to'] == 'stop' else 'breakeven'
    if result['termination'] == 'stop-already-touched-at-exit':
        return f'You exited at or past {stop}, so there was nothing left to hold.'
    if result['termination'] == 'not-over':
        return f'The {AFTER_EXIT_MINUTES} minutes after your exit are not over yet.'
    if result['coverage'] not in COMPLETE:
        return (f"{'Not every price was' if result['observations'] else 'No prices were'} recorded in the "
                f'{AFTER_EXIT_MINUTES} minutes after your exit. Keep the study on a chart of this contract while you '
                'trade.')
    opening = f'In the {AFTER_EXIT_MINUTES} minutes after you exited, price'
    if not result['extra_mfe']:
        return (f'{opening} reached {stop} without going any further your way.' if result['stop_touched'] else
                f'{opening} went no further your way and did not reach {stop}.')
    further = units.runner_value(result['extra_mfe'], unit, risk=plan.risk,
                                 tick=units.tick_size(trade.symbol, journal.display_settings()['tick_sizes']),
                                 multiplier=trade.point_value, quantity=result['exit_qty'])
    words = (f'{opening} went {common.excursion_amount(further, unit, trade.currency)} further your way '
             + (f'before reaching {stop}.' if result['stop_touched'] else f'and did not reach {stop}.'))
    # In R without a stop there is no R amount: '—', and why, as under the MAE tile.
    return f'{words} {plan.why}' if further is None and unit == 'r' and plan.why else words


def stats(journal, rows):
    """Which trades left room after the exit: the minutes and stop of the trade's sentence (line), averaged by any of
    the groups Compare uses (GROUPS), the most further your way first; tags keep your own order, each under its
    category. Each row already knows its stop, so a trade reads only its own prices. They are read after every exit,
    so the table is built only when Compare Runners is pressed: the choice sits in a form."""
    st.caption(f'The {AFTER_EXIT_MINUTES} minutes after each exit, from recorded ticks, held until your stop, or '
               'breakeven when the trade had none. An exit at or past the stop counts as 0, as there was nothing '
               'left to hold.')
    # The choice last compared stays chosen while another Stats tab is open.
    remember = common.keep({common.key(journal, 'runner-group'): GROUPS[0]})
    with st.form(common.key(journal, 'runner-edge'), border=False):
        group = st.selectbox('Compare by', GROUPS, key=common.key(journal, 'runner-group'))
        asked = st.form_submit_button('Compare Runners', on_click=remember,
                                      help='Reads the prices after every exit in your filters.')
    if not asked:
        return
    selected = [r for r in rows if r['status'] == 'closed' and r.get('net') is not None and not r.get('merge_leg_ids')]
    trades = {t.id: t for t in journal.trades()}
    if any(r.get('merge_leg_ids') for r in rows):
        st.caption('Merged trades are left out: their original trades can have different stops.')
    results = [{**row, **best_post_exit(journal, trades[row['id']], row['stop'])}
               for row in selected if row['id'] in trades]
    zone, library = common.display_zone(journal), journal.tag_library()
    unit = common.excursion_unit(journal)
    sizes = journal.display_settings()['tick_sizes']
    for row in results:
        # A tag by its id: two categories can each have a tag of one name.
        row['group'] = (row['tag_ids'] if group == 'Tags' else group_labels(row, group, zone)) or [NONE[group]]
        for name in ('extra_mfe', 'extra_mae'):
            row[name] = units.runner_value(row.get(name), unit, risk=row.get('initial_risk'),
                                           tick=units.tick_size(row['symbol'], sizes),
                                           multiplier=row.get('point_value'), quantity=row.get('exit_qty'))
    groups = runner_groups(results)
    excluded = sum(r['coverage'] not in COMPLETE or r.get('extra_mfe') is None for r in results)
    st.caption(f'{len(results)} trade{"" if len(results) == 1 else "s"} · {excluded} left out'
               + (f' · a trade counts under each of its {group.lower()}' if group in NONE else ''),
               help='Left out: a trade whose minutes after the exit were not all recorded, or with no amount in this '
                    'unit.')
    if groups:
        label = units.suffix(unit, results[0]['currency'])
        name = group.removesuffix('s') if group in NONE else group
        if group == 'Tags':
            groups = tag_order(library, groups)
        table = pd.DataFrame(groups).rename(columns={
            'category': 'Category', 'group': name, 'trades': 'Trades',
            'avg_extra_peak': f'Average extra peak ({label})', 'avg_adverse_after_exit': f'Average move against ({label})',
            'avg_extra_peak_R': 'Extra peak in R', 'additional_1R_reached_pct': '1R more reached (%)',
            'trades_with_r': 'Trades with a stop'})
        money = {column: common.money_column(column, results[0]['currency']) for column in table.columns
                 if unit == 'currency' and column.startswith('Average')}
        st.dataframe(table, hide_index=True, width='stretch', column_config=money)
    else:
        st.info(f'None of these trades has all of the {AFTER_EXIT_MINUTES} minutes after its exit recorded'
                + (' and a stop for R' if unit == 'r' else '') + '. Keep the study on a chart of each contract you '
                'trade.')
    st.caption('Hindsight to test, not profit you missed',
               help='A stop touch is not a fill, and fees, slippage and trailing stops are not modelled, so test '
                    'any rule on new trades first.')


def tag_order(library, groups) -> list[dict]:
    """runner_groups by tag id, as a trader reads tags: each category in the library's order and its tags in theirs,
    named, with the category first, then the trades with no tag."""
    tags = library['tags']

    def place(row):
        tag = tags.get(row['group'])
        return (0, library['groups'][tag['group']]['order'], tag['order']) if tag else (1, 0, 0)
    return [{'category': tags[row['group']]['group'] if row['group'] in tags else '',
             **row, 'group': tags[row['group']]['name'] if row['group'] in tags else row['group']}
            for row in sorted(groups, key=place)]
