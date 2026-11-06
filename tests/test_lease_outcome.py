from datetime import datetime, timezone
import uuid
import pytest
from jsonschema import ValidationError
from controlplane_events.contracts import validate_event_payload, validate_event_envelope
from controlplane_events.envelope import ControlPlaneEventEnvelope
from controlplane_events.payloads.lease_outcome import LeaseOutcome, LEASE_OUTCOME_V1


def outcome():
    return LeaseOutcome(tenant_id=uuid.uuid4(), lease_id=uuid.uuid4(),
        billing_record_id=uuid.uuid4(), host_org_id=uuid.uuid4(), host_wallet="test-wallet",
        kind="p1_hardware", outcome="ended", certification="passed", delivered_cents=100,
        observed_at=datetime.now(timezone.utc))


def test_lease_facts_round_trip_and_tenant_binding():
    fact = outcome()
    payload = fact.model_dump(mode="json")
    validate_event_payload(LEASE_OUTCOME_V1, payload)
    assert LeaseOutcome.model_validate_json(fact.model_dump_json()) == fact
    envelope = ControlPlaneEventEnvelope(event_type=LEASE_OUTCOME_V1, service="hypermesh-plane",
                                         tenant_id=fact.tenant_id, payload=payload)
    validate_event_envelope(envelope)
    with pytest.raises(ValueError, match="tenant"):
        validate_event_envelope(envelope.model_copy(update={"tenant_id":uuid.uuid4()}))


@pytest.mark.parametrize("key,value", [("net_cents", 88), ("take_bps",1200),
    ("delivered_cents", -1), ("delivered_cents", True), ("certification", "assumed")])
def test_entitlements_and_invalid_facts_are_refused(key,value):
    with pytest.raises(ValidationError):
        validate_event_payload(LEASE_OUTCOME_V1, {**outcome().model_dump(mode="json"), key:value})
