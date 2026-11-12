"""Experiment contracts accept only configured labels and pseudonymous assignments."""
import uuid
from unittest.mock import Mock, patch

import pytest
from jsonschema import ValidationError as SchemaError
from pydantic import ValidationError as ModelError

from controlplane_events import (
    ExperimentConversionPayload,
    ExperimentExposurePayload,
    PostgresEventPublisher,
    hash_experiment_assignment,
)
from controlplane_events.contracts import validate_event_envelope, validate_event_payload
from controlplane_events.envelope import ControlPlaneEventEnvelope

EVENTS = [
    ("experiment.exposure.v1", ExperimentExposurePayload),
    ("experiment.conversion.v1", ExperimentConversionPayload),
]
PRIVATE_FIELDS = [
    "email", "name", "wallet", "wallet_address", "sea_public_key", "SEA_public_key",
    "user_id", "raw_user_id", "cookie", "cookie_value", "ip", "ip_address",
    "user_agent", "date_of_birth", "dob", "age", "prompt", "completion", "messages",
    "message", "message_body", "prompt_body", "properties", "metadata",
]


def payload():
    return dict(experiment_key="signup-layout", variant="treatment", surface="portal",
                assignment_hash=hash_experiment_assignment("assignment-example", secret=b"s" * 32))


@pytest.mark.parametrize("event,model", EVENTS)
@pytest.mark.parametrize("surface", ["s3r.ch", "hyperme.sh", "portal"])
def test_experiment_wire_roundtrip(event, model, surface):
    data = dict(payload(), surface=surface)
    typed = model(**data)
    assert typed.to_envelope_payload() == data
    validate_event_payload(event, typed.to_envelope_payload())
    validate_event_payload(event, dict(data, release_tag="0.24.0-test.12"))
    envelope = ControlPlaneEventEnvelope(event_type=event, service="portal",
                                        tenant_id=uuid.uuid4(), payload=data)
    validate_event_envelope(envelope)
    with pytest.raises(ValueError, match="controlplane.events.v1"):
        validate_event_envelope(envelope, topic="analytics.external")


@pytest.mark.parametrize("event,model", EVENTS)
@pytest.mark.parametrize("field", PRIVATE_FIELDS)
def test_reject_private_fields_in_models_and_wire_schema(event, model, field):
    data = dict(payload(), **{field: "private-example"})
    with pytest.raises(ModelError):
        model(**data)
    with pytest.raises(SchemaError):
        validate_event_payload(event, data)


@pytest.mark.parametrize("event,model", EVENTS)
@pytest.mark.parametrize("change", [
    {"assignment_hash": "person@example.invalid"}, {"assignment_hash": str(uuid.UUID(int=1))},
    {"assignment_hash": "0x" + "a" * 40}, {"assignment_hash": "a" * 64 + "\n"},
    {"assignment_hash": "a" * 63}, {"assignment_hash": "A" * 64},
    {"variant": {"email": "private-example"}}, {"experiment_key": "person@example.invalid"},
    {"variant": "message body with spaces"}, {"release_tag": {"cookie": "secret"}},
    {"surface": "unknown"}, {"surface": "portal\n"}, {"experiment_key": ""},
])
def test_reject_raw_assignments_and_unstructured_labels(event, model, change):
    data = dict(payload(), **change)
    with pytest.raises(ModelError):
        model(**data)
    with pytest.raises(SchemaError):
        validate_event_payload(event, data)


@pytest.mark.parametrize("event,model", EVENTS)
@pytest.mark.parametrize("field", ["experiment_key", "variant", "assignment_hash", "surface"])
def test_required_experiment_fields(event, model, field):
    data = payload()
    del data[field]
    with pytest.raises(ModelError):
        model(**data)
    with pytest.raises(SchemaError):
        validate_event_payload(event, data)


def test_assignment_hash_is_keyed_stable_and_does_not_contain_identifier():
    first = hash_experiment_assignment("assignment-example", secret=b"s" * 32)
    assert len(first) == 64
    assert "assignment-example" not in first
    assert first == hash_experiment_assignment("assignment-example", secret=b"s" * 32)
    assert first != hash_experiment_assignment("assignment-example", secret=b"t" * 32)
    assert first != hash_experiment_assignment("another-assignment", secret=b"s" * 32)
    with pytest.raises(ValueError):
        hash_experiment_assignment("assignment-example", secret=b"short")
    with pytest.raises(ValueError):
        hash_experiment_assignment("", secret=b"s" * 32)


@pytest.mark.parametrize("event,model", EVENTS)
def test_publisher_rejects_untyped_private_payload_before_storage(event, model):
    publisher = PostgresEventPublisher("postgresql://unused", "portal")
    session = Mock()
    with patch("controlplane_events.publisher.insert_event_sync", return_value=7) as insert:
        assert publisher.publish_in_session(session, tenant_id=None, event_type=event, payload=payload()) == 7
        insert.reset_mock()
        for field in PRIVATE_FIELDS:
            with pytest.raises(ValueError, match="^invalid experiment event payload$") as error:
                publisher.publish_in_session(session, tenant_id=None, event_type=event,
                                             payload=dict(payload(), **{field: "private-example"}))
            assert "private-example" not in str(error.value)
        insert.assert_not_called()
