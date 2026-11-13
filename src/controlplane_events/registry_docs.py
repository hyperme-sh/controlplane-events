"""Deterministic public tables derived from the installed event registry."""
from controlplane_events.contracts import event_contracts


def render_tables(registry=None):
    registry = event_contracts() if registry is None else registry
    rows = ['| Event type | Owner | Status | Schema |', '| --- | --- | --- | --- |']
    for event, entry in sorted(registry['events'].items()):
        rows.append(f"| `{event}` | `{entry['owner']}` | {entry['status']} | `{entry['schema']}` |")
    rows += ['', '| Subscription | Consumer | Group | Event types |', '| --- | --- | --- | --- |']
    for subscription, group in sorted(registry['consumer_groups'].items()):
        entry = registry['subscriptions'][subscription]
        events = ', '.join(f'`{event}`' for event in entry['event_types'])
        rows.append(f"| `{subscription}` | `{entry['consumer']}` | `{group}` | {events} |")
    return '\n'.join(rows)
