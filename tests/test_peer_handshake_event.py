import uuid
import pytest
from jsonschema import ValidationError
from controlplane_events.contracts import validate_event_payload, validate_event_envelope
from controlplane_events.envelope import ControlPlaneEventEnvelope
from controlplane_events.http_consumer import producer_owns_event

EVENT='plane.peer.handshaken.v1'

def payload():
    return dict(tenant_id=str(uuid.uuid4()),peer_pin='AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=',
                observed_at='2026-11-16T00:00:00Z', capabilities=['peer.handshake'],outcome='accepted')

def test_owner_and_tenant():
    body=payload()
    validate_event_payload(EVENT,body)
    assert producer_owns_event('hypermesh-plane',EVENT)
    assert not producer_owns_event('user-service',EVENT)
    envelope=ControlPlaneEventEnvelope(service='hypermesh-plane',event_type=EVENT,tenant_id=uuid.uuid4(),payload=body)
    with pytest.raises(ValueError): validate_event_envelope(envelope)

@pytest.mark.parametrize('change',[{'prompt':'private'},{'peer_pin':'bad'},{'outcome':'unbounded error'},
    {'capabilities':['x']*33},{'capabilities':['bad space']},{'capabilities':['x','x']},{'tenant_id':'bad'}])
def test_strict_fields(change):
    with pytest.raises(ValidationError): validate_event_payload(EVENT,payload()|change)
