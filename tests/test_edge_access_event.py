import uuid
import pytest
from jsonschema import ValidationError
from controlplane_events.contracts import validate_event_payload
from controlplane_events.http_consumer import producer_owns_event


def test_edge_access_contract_binds_producer_and_excludes_secrets():
    payload = {'method':'GET', 'path':'/api/v1/users', 'trace_id':str(uuid.uuid4())}
    validate_event_payload('gateway_api_access_v1', payload)
    assert producer_owns_event('controlplane-edge', 'gateway_api_access_v1')
    assert not producer_owns_event('user-service', 'gateway_api_access_v1')
    for extra in ('authorization', 'body', 'query'):
        with pytest.raises(ValidationError):
            validate_event_payload('gateway_api_access_v1', {**payload, extra:'secret'})
