"""Registry, installed schemas and published tables have one contract."""
import ast
from importlib.resources import files
from pathlib import Path
import re
import controlplane_events
from controlplane_events.contracts import event_contracts, event_schema
from controlplane_events.registry_docs import render_tables
from controlplane_events.http_consumer import producer_owns_event


def test_readme_matches_every_registered_event_and_subscription():
    text = (Path(__file__).resolve().parents[1]/'README.md').read_text()
    actual = text.split('<!-- event-registry:start -->')[1].split('<!-- event-registry:end -->')[0].strip()
    assert actual == render_tables()
    known = set(event_contracts()['events']) | {'controlplane.events.v1'}
    assert set(re.findall(r'[a-z][a-z0-9_.]+\.v[0-9]+', text)) <= known


def test_every_subscription_has_registered_events_and_unique_group():
    registry = event_contracts()
    assert set(registry['consumer_groups']) == set(registry['subscriptions'])
    assert len(set(registry['consumer_groups'].values())) == len(registry['consumer_groups'])
    for name, entry in registry['subscriptions'].items():
        assert entry['consumer'] and entry['event_types']
        assert set(entry['event_types']) <= set(registry['events'])
    for event, entry in registry['events'].items():
        schema = event_schema(event)
        assert schema['x-event-type'] == event
        assert schema['x-owner'] == entry['owner']


def test_runtime_event_literals_are_registered():
    known = set(event_contracts()['events']) | {'controlplane.events.v1'}
    for path in Path(controlplane_events.__file__).parent.rglob('*.py'):
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                if re.fullmatch(r'[a-z][a-z0-9_.]+\.v[0-9]+', node.value):
                    assert node.value in known, (path.name, node.value)


def test_plane_owner_alias_preserves_existing_producer_contract():
    for service in ['hypermesh','hypermesh-plane']:
        assert producer_owns_event(service,'hypermesh.lease.outcome.v1')
        assert producer_owns_event(service,'hypermesh.lease.started.v1')
        assert not producer_owns_event(service,'payment.succeeded.v3')
    assert not producer_owns_event('payment-service','hypermesh.lease.outcome.v1')
