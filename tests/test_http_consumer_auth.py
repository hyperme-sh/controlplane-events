import uuid
import pytest
from controlplane_events.envelope import ControlPlaneEventEnvelope
from controlplane_events.http_consumer import producer_owns_event
from controlplane_events.inbox import receive


def test_authenticated_producer_cannot_submit_another_owners_event():
    assert producer_owns_event("hypermesh-plane", "hypermesh.lease.outcome.v1")
    assert producer_owns_event("payment-service", "payment.obligation.recorded.v1")
    assert not producer_owns_event("payment-service", "hypermesh.lease.outcome.v1")
    assert not producer_owns_event("hypermesh-plane", "payment.obligation.recorded.v1")
    assert not producer_owns_event("payment-service", "not.a.registered.event")
    assert not producer_owns_event("", "payment.obligation.recorded.v1")


def test_inbox_refuses_another_owners_event_before_insert():
    class Engine:
        def begin(self):
            raise AssertionError("cross-owner event reached the inbox")

    tenant = uuid.uuid4()
    observed = "2026-11-06T00:00:00+00:00"
    envelope = ControlPlaneEventEnvelope(
        event_type="hypermesh.lease.outcome.v1", service="payment-service", tenant_id=tenant,
        payload={"tenant_id": str(tenant), "lease_id": str(uuid.uuid4()),
                 "billing_record_id": str(uuid.uuid4()), "host_org_id": str(uuid.uuid4()),
                 "host_wallet": "wallet", "kind": "p1_hardware", "outcome": "ended",
                 "certification": "passed", "delivered_cents": 100, "observed_at": observed})
    with pytest.raises(ValueError, match="another service"):
        receive(Engine(), uuid.uuid4(), envelope, producer="payment-service")
