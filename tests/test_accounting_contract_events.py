"""Neutral grant events cannot smuggle provider-specific settlement details."""
import uuid
import pytest
from jsonschema import ValidationError
from controlplane_events.contracts import validate_event_payload


@pytest.mark.parametrize('action', ['created', 'signed', 'bound', 'fulfilled'])
def test_accounting_grant_version(action):
    payload = dict(id=str(uuid.uuid4()), tenant_id=str(uuid.uuid4()),
                   parties=[dict(role='renter', subject='renter')], meters=['wall_time'],
                   accounting_backend='internal', accounting_ref=None,
                   until='2026-12-01T00:00:00+00:00', dispute_tip=None,
                   connection_id=None, lease_id=None, status='signed', signed_by='user')
    event = f'aiuditor.contract.{action}.v2'
    validate_event_payload(event, payload)
    validate_event_payload(event, dict(payload, accounting_backend='http', accounting_ref='opaque'))
    for change in (dict(rail='stripe'), dict(amount=12), dict(accounting_backend='unknown')):
        with pytest.raises(ValidationError):
            validate_event_payload(event, dict(payload, **change))
