"""Reusable optional tag suggestions. Nothing is applied until the trader chooses it."""
DEFAULT_CATALOG = {
    'Breakout': 'Setup', 'Pullback': 'Setup', 'Reversal': 'Setup', 'Range': 'Setup',
    'Followed plan': 'Execution', 'Chased entry': 'Execution', 'Exited early': 'Execution',
    'Let winner run': 'Execution',
    'Patient': 'Mindset', 'Focused': 'Mindset', 'FOMO': 'Mindset', 'Hesitant': 'Mindset',
}


def tag_groups(catalog, annotations):
    groups = {}
    for name, group in catalog.items():
        groups.setdefault(group, []).append(name)
    for annotation in annotations.values():
        for name in annotation.get('tags', []):
            if name not in catalog:
                groups.setdefault('Other tags', []).append(name)
    return {group: sorted(set(names)) for group, names in groups.items()}
