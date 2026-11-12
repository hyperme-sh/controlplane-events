import json
from pathlib import Path
import uuid
from unittest.mock import Mock, patch
import pytest
from jsonschema import ValidationError as SchemaError
from pydantic import ValidationError as ModelError
from controlplane_events import ControlPlaneEventEnvelope, PostgresEventPublisher
from controlplane_events.contracts import validate_event_payload, validate_event_envelope
from controlplane_events.payloads.accounting_events import ACCOUNTING_MODELS, accounting_event_key

FIXTURES = json.loads(Path(__file__).with_name('accounting_vectors.json').read_text())


@pytest.mark.parametrize('event',ACCOUNTING_MODELS)
def test_shared_wire_roundtrip_and_stable_identity(event):
    payload=FIXTURES[event]
    model=ACCOUNTING_MODELS[event]
    assert model(**payload).to_envelope_payload() == payload
    validate_event_payload(event,payload)
    envelope=ControlPlaneEventEnvelope(event_type=event,service='accounting',tenant_id=uuid.UUID(payload['tenant_id']),event_id=payload['event_id'],payload=payload)
    validate_event_envelope(envelope)
    duplicate=ControlPlaneEventEnvelope.model_validate_json(envelope.model_dump_json())
    assert accounting_event_key(envelope) == accounting_event_key(duplicate)
    with pytest.raises(ValueError,match='event_id'):
        validate_event_envelope(envelope.model_copy(update={'event_id':'changed'}))
    with pytest.raises(ValueError,match='tenant'):
        validate_event_envelope(envelope.model_copy(update={'tenant_id':uuid.uuid4()}))


@pytest.mark.parametrize('event',ACCOUNTING_MODELS)
@pytest.mark.parametrize('quantity',[{'amount':-1,'unit':'tokens'}, {'amount':True,'unit':'tokens'},
    {'amount':1.0,'unit':'tokens'},{'amount':2**63,'unit':'tokens'}, {'amount':1,'unit':'unknown'}])
def test_invalid_quantities(event,quantity):
    data=dict(FIXTURES[event],quantity=quantity)
    with pytest.raises((SchemaError,ModelError)):
        validate_event_payload(event,data)
    with pytest.raises(ModelError):
        ACCOUNTING_MODELS[event](**data)


@pytest.mark.parametrize('event',ACCOUNTING_MODELS)
@pytest.mark.parametrize('field',['wallet','currency','provider_id','stripe_customer_id','fee','tax','payout_id','email','name','metadata','future_field'])
def test_strict_version_rejects_private_or_undeclared_fields(event,field):
    data=dict(FIXTURES[event],**{field:'private'})
    with pytest.raises((SchemaError,ModelError)):
        validate_event_payload(event,data)
    with pytest.raises(ModelError):
        ACCOUNTING_MODELS[event](**data)


@pytest.mark.parametrize('event',ACCOUNTING_MODELS)
def test_publisher_validates_before_storage(event):
    payload=FIXTURES[event]
    publisher=PostgresEventPublisher('postgresql://unused','accounting')
    with patch('controlplane_events.publisher.insert_event_sync',return_value=1) as insert:
        publisher.publish_in_session(Mock(),tenant_id=uuid.UUID(payload['tenant_id']),event_type=event,payload=payload)
        assert insert.call_args.args[1].event_id == payload['event_id']
        insert.reset_mock()
        with pytest.raises(ValueError):
            publisher.publish_in_session(Mock(),tenant_id=uuid.uuid4(),event_type=event,payload=payload)
        insert.assert_not_called()


def test_duplicate_reordering_and_older_envelope_compatibility():
    seen=set()
    for event,payload in reversed(list(FIXTURES.items())*2):
        envelope=ControlPlaneEventEnvelope(event_type=event,service='accounting',tenant_id=uuid.UUID(payload['tenant_id']),event_id=payload['event_id'],payload=payload)
        seen.add(accounting_event_key(envelope))
    assert len(seen)==4
    old=ControlPlaneEventEnvelope(event_type='old.event.v1',service='old')
    assert 'event_id' not in old.model_dump(mode='json')
    assert ControlPlaneEventEnvelope.model_validate(dict(old.model_dump(),future_field=True)).event_id is None
