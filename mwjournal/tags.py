"""Starter tags. Nothing is ever applied to a trade until the trader clicks it.

A journal that has never had a tag library is given STARTERS once, as ordinary tags it can rename,
recolour, retire or delete. LEGACY_CATALOG is what 0.4.0 and earlier merged, hidden, into every
library; libraries from those versions keep exactly that set and are never given STARTERS. Every library
older than schema 4 is given the DAY category once, by that upgrade (store._schema_4).
"""

LEGACY_CATALOG = {
    'Breakout': 'Setup', 'Pullback': 'Setup', 'Reversal': 'Setup', 'Range': 'Setup',
    'Followed plan': 'Execution', 'Chased entry': 'Execution', 'Exited early': 'Execution',
    'Let winner run': 'Execution',
    'Patient': 'Mindset', 'Focused': 'Mindset', 'FOMO': 'Mindset', 'Hesitant': 'Mindset',
}
DEFAULT_CATALOG = LEGACY_CATALOG          # the old name, kept for code written against 0.4.0

# The one starter category of day tags: chosen on a day's Daily Report Card, worn by every trade of that day.
DAY = ('Day', '#E48DB9', ('Had a Plan', 'Kept Risk Limits', 'Respected My Stop'))
# (category, colour of its selected tags, tags in order). One colour per category, each distinct. DAY's tags are
# day tags; the others are trade tags.
STARTERS = (
    ('Setup', '#65CA98', ('Breakout', 'Pullback', 'Reversal', 'Range')),
    ('Execution', '#EBAB66', ('Followed Plan', 'Chased Entry', 'Exited Early', 'Let Winner Run', 'Moved Stop')),
    ('Mindset', '#BD9DE8', ('Calm', 'Patient', 'FOMO', 'Hesitant', 'Tilted')),
    DAY,
)
STARTER_VERSION = 1


def tag_groups(catalog, annotations):
    groups = {}
    for name, group in catalog.items():
        groups.setdefault(group, []).append(name)
    for annotation in annotations.values():
        for name in annotation.get('tags', []):
            if name not in catalog:
                groups.setdefault('Other tags', []).append(name)
    return {group: sorted(set(names)) for group, names in groups.items()}
