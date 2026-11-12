"""Additive named metrics retain strict legacy parsing and stable delivery IDs."""
import json
import uuid
from contextlib import contextmanager
from unittest.mock import Mock

import pytest
from pydantic import ValidationError
from jsonschema import ValidationError as SchemaError

from controlplane_events import ExperimentConversionV2Payload, named_conversion_payload
from controlplane_events.contracts import validate_event_payload
from controlplane_events.delivery import delivery_body
from controlplane_events.envelope import ControlPlaneEventEnvelope
from controlplane_events.inbox import receive


def named():
    return dict(experiment_key="signup", variant="control", assignment_hash="a" * 64,
                surface="s3r.ch", conversion_key="sign_in_completed")


@pytest.mark.parametrize("metric", [None, "", "person@example.invalid", "sign_in_completed\n", 1])
def test_required_metric_and_strict_wire(metric):
    value = named()
    if metric is None:
        del value["conversion_key"]
    else:
        value["conversion_key"] = metric
    with pytest.raises(ValidationError):
        ExperimentConversionV2Payload.model_validate(value)
    with pytest.raises(SchemaError):
        validate_event_payload("experiment.conversion.v2", value)


def test_metric_allowlist_and_mixed_destination_support():
    for supported in [frozenset(), frozenset({"experiment.conversion.v1"})]:
        with pytest.raises(ValueError, match="does not support"):
            named_conversion_payload(named(), allowed_metrics=frozenset({"sign_in_completed"}),
                                     supported_event_types=supported)
    both = frozenset({"experiment.conversion.v1", "experiment.conversion.v2"})
    with pytest.raises(ValueError, match="not configured"):
        named_conversion_payload(named(), allowed_metrics=frozenset({"checkout_started"}),
                                 supported_event_types=both)
    assert named_conversion_payload(named(), allowed_metrics=frozenset({"sign_in_completed"}),
                                    supported_event_types=both).to_envelope_payload() == named()
    with pytest.raises(SchemaError):
        validate_event_payload("experiment.conversion.v1", named())
    legacy = named()
    del legacy["conversion_key"]
    validate_event_payload("experiment.conversion.v1", legacy)


def test_duplicate_delivery_identity_does_not_count_twice_and_rejects_changed_metric():
    envelope = ControlPlaneEventEnvelope(event_type="experiment.conversion.v2",
        service="controlplane-events", tenant_id=uuid.uuid4(), payload=named())
    event_id = uuid.uuid4()
    first = delivery_body(event_id, envelope)
    assert delivery_body(event_id, envelope) == first
    assert json.loads(first)["event_id"] == str(event_id)
    db = Mock()
    db.execute.return_value.scalar.side_effect = [event_id, None, None]
    db.scalar.return_value = envelope.model_dump(mode="json")
    class Engine:
        @contextmanager
        def begin(self):
            yield db
    forward = Mock()
    assert receive(Engine(), event_id, envelope, producer="controlplane-events", forward=forward)
    assert not receive(Engine(), event_id, envelope, producer="controlplane-events", forward=forward)
    changed = envelope.model_copy(update={"payload": dict(named(), conversion_key="checkout_started")})
    with pytest.raises(ValueError, match="different facts"):
        receive(Engine(), event_id, changed, producer="controlplane-events", forward=forward)
    assert forward.call_count == 1
