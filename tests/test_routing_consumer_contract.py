"""Exercise the installed publisher wire shape through the real routing API."""
import asyncio
import uuid

import pytest

from controlplane_events import (
    ControlPlaneEventEnvelope,
    PaymentSucceededPayload,
    dispatch_controlplane_envelope,
)
from controlplane_events.store import envelope_to_bytes


@pytest.mark.parametrize('strict', [False, True])
def test_payment_wire_round_trip_preserves_tenant_and_delivery_identity(strict):
    tenant = uuid.uuid4()
    record = uuid.uuid4()
    envelope = ControlPlaneEventEnvelope(
        event_type='payment.succeeded.v3', service='payment-service',
        tenant_id=tenant, payload={'tenant_id': str(tenant), 'billing_record_id': str(record)},
    )
    received = []

    async def handler(event, payload):
        received.append((event.tenant_id, event.bus_event_id, payload.billing_record_id))

    asyncio.run(dispatch_controlplane_envelope(
        envelope_to_bytes(envelope.model_dump(mode='json')),
        {'payment.succeeded.v3': (PaymentSucceededPayload, handler)}, bus_event_id=123, strict=strict,
    ))
    assert received == [(tenant, 123, record)]


@pytest.mark.parametrize('strict', [False, True])
@pytest.mark.parametrize('invalid', ['tenant', 'payload', 'route', 'json', 'empty'])
def test_invalid_or_unrouted_wire_never_reaches_consumer(invalid, strict):
    tenant = uuid.uuid4()
    envelope = ControlPlaneEventEnvelope(
        event_type='payment.succeeded.v3', service='payment-service',
        tenant_id=tenant, payload={'tenant_id': str(tenant), 'billing_record_id': str(uuid.uuid4())},
    )
    if invalid == 'tenant':
        envelope.payload['tenant_id'] = str(uuid.uuid4())
    elif invalid == 'payload':
        envelope.payload.pop('billing_record_id')
    received = []

    async def handler(event, payload):
        received.append(event)

    wire = envelope_to_bytes(envelope.model_dump(mode='json'))
    if invalid == 'json':
        wire = b'invalid json'
    elif invalid == 'empty':
        wire = None
    routes = {} if invalid == 'route' else {'payment.succeeded.v3': (PaymentSucceededPayload, handler)}
    if strict:
        with pytest.raises(ValueError):
            asyncio.run(dispatch_controlplane_envelope(wire, routes, strict=True))
    else:
        asyncio.run(dispatch_controlplane_envelope(wire, routes))
    assert received == []


def test_strict_consumer_handle_message_does_not_log_rejected_wire(caplog):
    from controlplane_events import RoutingControlPlaneConsumer
    consumer = RoutingControlPlaneConsumer('postgresql://unused@localhost/unused', [], 'test', {}, strict_delivery=True)
    wire = b'{"event_type":null,"payload":{"private":"must-not-be-logged"}}'
    with pytest.raises(ValueError, match='Invalid control-plane envelope') as error:
        asyncio.run(consumer.handle_message(key=None, value=wire, headers={}))
    assert 'must-not-be-logged' not in str(error.value)
    assert 'must-not-be-logged' not in caplog.text


def test_strict_consumer_requires_boolean_configuration():
    from controlplane_events import RoutingControlPlaneConsumer
    with pytest.raises(ValueError, match='strict_delivery must be boolean'):
        RoutingControlPlaneConsumer('postgresql://unused@localhost/unused', [], 'test', {}, strict_delivery='false')
