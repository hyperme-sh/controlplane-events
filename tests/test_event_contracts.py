"""Preserved runtime wire-contract cases from the source extraction cut."""
import uuid
import pytest
from jsonschema import Draft202012Validator, ValidationError
from controlplane_events.contracts import event_contracts,event_schema,validate_event_envelope,validate_event_payload
from controlplane_events.envelope import ControlPlaneEventEnvelope

@pytest.mark.parametrize('event', sorted(event_contracts()['events']))
def test_all_registered_wire_schemas_are_valid(event):
    schema = event_schema(event)
    Draft202012Validator.check_schema(schema)
    assert schema['x-event-type'] == event
    assert schema['x-topic'] == 'controlplane.events.v1'
    assert schema['x-owner']

def test_payment_consumer_contract_rejects_bad_fields_and_tenant_mismatch():
    tid = uuid.uuid4()
    payload = {'tenant_id':str(tid), 'billing_record_id':str(uuid.uuid4())}
    validate_event_payload('payment.succeeded.v1', payload)
    for bad in ({**payload, 'amount_cents':-1}, {**payload, 'tenant_id':'not-a-uuid'},
                {**payload, 'prompt':'unpublished'}, {'tenant_id':str(tid)}):
        with pytest.raises(ValidationError):
            validate_event_payload('payment.succeeded.v1', bad)
    envelope = ControlPlaneEventEnvelope(event_type='payment.succeeded.v1', service='payment-service', tenant_id=tid, payload=payload)
    validate_event_envelope(envelope)
    with pytest.raises(ValueError, match='shared|controlplane.events.v1'):
        validate_event_envelope(envelope, topic='another-topic')
    with pytest.raises(ValueError, match='tenant'):
        validate_event_envelope(envelope.model_copy(update={'tenant_id':uuid.uuid4()}))
    with pytest.raises(KeyError):
        event_schema('payment.succeeded.v99')

def test_registry_reads_are_not_mutable_shared_state():
    registry = event_contracts()
    registry['events'].clear()
    assert event_contracts()['events']

def test_decimal_wire_cost_accepts_quantized_zero_and_exponents():
    from decimal import Decimal
    from controlplane_events.payloads.tokens_used import TokensUsedEvent
    for cost in (Decimal('0E-8'), Decimal('0.000000004').quantize(Decimal('0.00000001')), Decimal('1E+2')):
        payload = TokensUsedEvent(user_id=uuid.uuid4(), tenant_id=uuid.uuid4(), model='test',
                                  input_tokens=0, output_tokens=0, cost_usd=cost).to_envelope_payload()
        validate_event_payload('tokens.used.v1', payload)
    with pytest.raises(ValidationError):
        validate_event_payload('tokens.used.v1', {**payload, 'cost_usd':'-1E-8'})

def test_payment_v2_is_reserved_and_excludes_fine_location():
    payload = {'tenant_id':str(uuid.uuid4()), 'billing_record_id':str(uuid.uuid4()),
               'billing_country':'US', 'billing_subdivision':'US-TN'}
    validate_event_payload('payment.succeeded.v2', payload)
    assert event_contracts()['events']['payment.succeeded.v2']['status'] == 'reserved'
    validate_event_payload('payment.succeeded.v2', {**payload, 'billing_country':'CA', 'billing_subdivision':None})
    with pytest.raises(ValidationError):
        validate_event_payload('payment.succeeded.v2', {**payload, 'billing_subdivision':None})
    with pytest.raises(ValidationError):
        validate_event_payload('payment.succeeded.v2', {**payload, 'postal_code':'12345'})
