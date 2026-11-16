import uuid
import pytest
from jsonschema import ValidationError
from controlplane_events.contracts import validate_event_payload
from controlplane_events.http_consumer import producer_owns_event

EVENT='plane.lease.usage.recorded.v1'


def payload():
    return dict(tenant_id=str(uuid.uuid4()),lease_id=str(uuid.uuid4()),reading_id=str(uuid.uuid4()),
                meter='token_digest_count',quantity=10,batch_id='batch-1',created_at='2026-01-01T00:00:00Z')


def test_count_notification_owner_and_shape():
    validate_event_payload(EVENT,payload())
    assert producer_owns_event('hypermesh-plane',EVENT)
    assert not producer_owns_event('payment-service',EVENT)


@pytest.mark.parametrize('change',[{'quantity':-1},{'quantity':True},{'quantity':1.5},{'quantity':2**63},
    {'meter':'unknown'},{'prompt':'private'},{'amount_cents':1},{'batch_id':'bad batch'},{'reading_id':'not-uuid'}])
def test_no_prompt_money_or_untyped_count(change):
    with pytest.raises(ValidationError):validate_event_payload(EVENT,payload()|change)
