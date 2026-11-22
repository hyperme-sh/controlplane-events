"""Versioned wire contracts, independent of publisher and consumer implementations."""
from __future__ import annotations

from functools import lru_cache
from importlib.resources import files
import json
import math
from statistics import median
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


def sustained_series_summary(samples: list[dict[str, int]]) -> dict[str, int | float | str]:
    """Summarize validated timed repetitions, excluding warm-up."""
    rates = [sample["decode_tokens"] * 1000 / sample["decode_elapsed_ms"] for sample in samples]
    return {
        "sample_count": len(rates),
        "median_tok_s": median(rates),
        "spread_tok_s": max(rates) - min(rates),
        "units": "tokens_per_second",
    }


def validate_event_payload(event_type: str, payload: dict[str, Any]) -> None:
    """Validate JSON wire values with format checking, without coercing or importing owners."""
    _validator(event_type).validate(payload)
    if event_type == "aiuditor.envelope.recorded.v2":
        from jsonschema import ValidationError

        measurement = payload["measurement"]
        if measurement["context_prompt_tokens"] + measurement["context_generated_tokens"] != payload["context"]:
            raise ValidationError("Measured tokens must span the declared context")
        expected_rate = measurement["decode_tokens"] * 1000 / measurement["decode_elapsed_ms"]
        # Permit floating point serialization rounding only.
        if not math.isclose(payload["decode_tok_s"], expected_rate, rel_tol=1e-9):
            raise ValidationError("Decode rate does not match measured tokens and time")
    if event_type == "aiuditor.envelope.recorded.v3":
        from jsonschema import ValidationError

        measurement = payload["measurement"]
        samples = measurement["samples"]
        raw_counts = [measurement[key] for key in (
            "context_prompt_tokens", "context_generated_tokens", "context_elapsed_ms",
        )]
        raw_counts.extend(value for sample in [measurement["warmup"], *samples] for value in sample.values())
        if any(type(value) is not int for value in raw_counts) or type(payload["measurement_summary"]["sample_count"]) is not int:
            raise ValidationError("Measurement counts and durations require JSON integers")
        if measurement["context_prompt_tokens"] + measurement["context_generated_tokens"] != payload["context"]:
            raise ValidationError("Measured tokens must span the declared context")
        expected = sustained_series_summary(samples)
        if any(
            not math.isclose(payload["measurement_summary"][key], expected[key], rel_tol=1e-9)
            for key in ("median_tok_s", "spread_tok_s")
        ) or not math.isclose(payload["decode_tok_s"], expected["median_tok_s"], rel_tol=1e-9):
            raise ValidationError("Series summary does not match the measured repetitions")
    if event_type == "plane.peer.handshaken.v1":
        from datetime import datetime
        from jsonschema import ValidationError
        try:
            parsed = datetime.fromisoformat(payload["observed_at"].replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                raise ValueError()
        except ValueError:
            raise ValidationError("Invalid peer observation timestamp") from None
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
