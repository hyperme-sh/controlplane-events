"""Versioned wire contracts, independent of publisher and consumer implementations."""
from __future__ import annotations

from functools import lru_cache
from importlib.resources import files
import json
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker

from controlplane_events.envelope import ControlPlaneEventEnvelope
from controlplane_events.topics import CONTROLPLANE_EVENTS_V1


def event_contracts() -> dict[str, Any]:
    """Return a fresh registry including owners, status and consumer group IDs."""
    return json.loads(files('controlplane_events').joinpath('schemas/registry.json').read_text())


def event_schema(event_type: str) -> dict[str, Any]:
    """Read the immutable schema for a registered event type; unknown types fail."""
    entry = event_contracts()['events'][event_type]
    return json.loads(files('controlplane_events').joinpath('schemas', entry['schema']).read_text())


@lru_cache(maxsize=128)
def _validator(event_type: str) -> Draft202012Validator:
    schema = event_schema(event_type)
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema, format_checker=FormatChecker())


def validate_event_payload(event_type: str, payload: dict[str, Any]) -> None:
    """Validate JSON wire values with format checking, without coercing or importing owners."""
    _validator(event_type).validate(payload)
    from controlplane_events.payloads.accounting_events import ACCOUNTING_MODELS
    if event_type in ACCOUNTING_MODELS:
        ACCOUNTING_MODELS[event_type].model_validate(payload)


def validate_event_envelope(envelope: ControlPlaneEventEnvelope, *, topic: str = CONTROLPLANE_EVENTS_V1) -> None:
    """Validate payload and tenant identity for a known event on the shared topic."""
    if topic != CONTROLPLANE_EVENTS_V1:
        raise ValueError('event contract requires controlplane.events.v1')
    validate_event_payload(envelope.event_type, envelope.payload)
    from controlplane_events.payloads.accounting_events import ACCOUNTING_MODELS
    if envelope.event_type in ACCOUNTING_MODELS:
        if envelope.event_id != envelope.payload['event_id']:
            raise ValueError('accounting envelope event_id must match payload')
    tenant_id = envelope.payload.get('tenant_id')
    if tenant_id is not None and str(envelope.tenant_id) != str(tenant_id):
        raise ValueError('event payload tenant does not match envelope tenant')
