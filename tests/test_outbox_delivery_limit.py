import uuid
import pytest
from controlplane_events.delivery import MAX_DELIVERY_BYTES, delivery_body
from controlplane_events.envelope import ControlPlaneEventEnvelope
from controlplane_events.outbox import enqueue


def _billing(note):
    tenant = uuid.uuid4()
    return ControlPlaneEventEnvelope(
        event_type="billing.record.created.v2", service="platform", tenant_id=tenant,
        payload={"tenant_id": str(tenant), "scope": "tenant", "actor": "test",
                 "payment_type": "card", "currency": "usd", "status": "open",
                 "metadata": {"note": note}})


class _Session:
    def __init__(self):
        self.calls = 0

    def execute(self, *_args, **_kwargs):
        self.calls += 1


def test_oversized_delivery_is_rejected_before_the_outbox_insert():
    event_id = uuid.uuid4()
    envelope = _billing("x" * (MAX_DELIVERY_BYTES + 1))
    assert len(delivery_body(event_id, envelope)) > MAX_DELIVERY_BYTES
    session = _Session()
    with pytest.raises(ValueError, match="receiver body limit"):
        enqueue(session, envelope, event_id=event_id)
    assert session.calls == 0


def test_deliverable_event_still_reaches_the_outbox_insert():
    session = _Session()
    enqueue(session, _billing("ok"), event_id=uuid.uuid4())
    assert session.calls == 1
