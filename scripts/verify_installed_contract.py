"""Consumer checks against the installed wheel, outside the source checkout."""
import asyncio
import hashlib
import importlib.metadata
import json
from pathlib import Path
import sys
import uuid

import controlplane_events as events
from controlplane_events.contracts import event_contracts, event_schema
from controlplane_events.routing_consumer import dispatch_controlplane_envelope

contract = json.loads(Path(sys.argv[1]).read_text())
distribution = importlib.metadata.distribution('controlplane-events')
actual = {str(p) for p in distribution.files if str(p).startswith('controlplane_events/') and not str(p).endswith('.pyc')}
assert actual == set(contract['packaged_file_sha256'])
for name, digest in contract['packaged_file_sha256'].items():
    assert hashlib.sha256(distribution.locate_file(name).read_bytes()).hexdigest() == digest, name
assert sorted(events.__all__) == contract['public_root_exports']
for name in events.__all__:
    assert hasattr(events, name), name
assert events.CONTROLPLANE_EVENTS_V1 == 'controlplane.events.v1'
for event in event_contracts()['events']:
    assert event_schema(event)['x-event-type'] == event

async def consume():
    tenant, billing = uuid.uuid4(), uuid.uuid4()
    payload = events.PaymentSucceededPayload(tenant_id=tenant, billing_record_id=billing, amount_cents=42)
    envelope = events.ControlPlaneEventEnvelope(event_type='payment.succeeded.v3', service='payment-service', tenant_id=tenant, payload=payload.to_envelope_payload())
    received = []
    async def handler(actual, typed):
        received.append((actual.bus_event_id, typed.tenant_id, typed.billing_record_id))
    routes = {'payment.succeeded.v3': (events.PaymentSucceededPayload, handler)}
    await dispatch_controlplane_envelope(envelope.model_dump_json().encode(), routes, bus_event_id=7)
    assert received == [(7, tenant, billing)]
    assert 'bus_event_id' not in envelope.model_dump(mode='json')
    assert dict(envelope.transport_headers())['tenant_id'] == str(tenant).encode()
    received.clear()
    invalid = [None, b'not-json', envelope.model_copy(update={'tenant_id': uuid.uuid4()}).model_dump_json().encode(),
               envelope.model_copy(update={'payload': {**envelope.payload, 'amount_cents': -1}}).model_dump_json().encode()]
    for raw in invalid:
        await dispatch_controlplane_envelope(raw, routes)
    assert received == [], 'Malformed or cross-tenant deliveries reached the consumer'

asyncio.run(consume())
print('Installed event runtime, schemas, exports and tenant-bound consumer dispatch qualified')
