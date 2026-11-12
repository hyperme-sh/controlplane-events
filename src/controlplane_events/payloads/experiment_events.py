"""First-party experiment events contain configuration labels and a keyed hash only."""
from __future__ import annotations

import hashlib
import hmac
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

ExperimentLabel = Annotated[str, Field(pattern=r"^[a-z][a-z0-9_-]{0,63}$", max_length=64)]
ReleaseTag = Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._+-]{0,127}$", max_length=128)]


def hash_experiment_assignment(identifier: str, *, secret: bytes) -> str:
    """Hash an assignment identifier with a server-only secret; never publish the input."""
    if not isinstance(secret, bytes) or len(secret) < 32:
        raise ValueError("assignment hashing requires a server secret of at least 32 bytes")
    if not isinstance(identifier, str) or not identifier:
        raise ValueError("assignment identifier must be a nonempty string")
    return hmac.new(secret, b"experiment.assignment.v1\x00" + identifier.encode("utf-8"), hashlib.sha256).hexdigest()


class _ExperimentPayload(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, hide_input_in_errors=True)

    experiment_key: ExperimentLabel
    variant: ExperimentLabel
    assignment_hash: str = Field(pattern=r"^[a-f0-9]{64}$", min_length=64, max_length=64)
    surface: Literal["s3r.ch", "hyperme.sh", "portal"]
    release_tag: ReleaseTag | None = None

    def to_envelope_payload(self) -> dict[str, object]:
        return self.model_dump(mode="json", exclude_none=True)


class ExperimentExposurePayload(_ExperimentPayload):
    """The assigned variant was actually shown to the person."""


class ExperimentConversionPayload(_ExperimentPayload):
    """The conversion named by the experiment configuration occurred."""


class ExperimentConversionV2Payload(_ExperimentPayload):
    """A named conversion from the experiment's configured metric allowlist."""

    conversion_key: ExperimentLabel = Field(
        json_schema_extra={"not": {"pattern": "[^a-z0-9_-]"}}
    )

    def require_metric(self, allowed_metrics: frozenset[str]) -> None:
        """Validate against trusted experiment configuration before counting."""
        if self.conversion_key not in allowed_metrics:
            raise ValueError("conversion metric is not configured")


def named_conversion_payload(
    payload: dict[str, object], *, allowed_metrics: frozenset[str],
    supported_event_types: frozenset[str],
) -> ExperimentConversionV2Payload:
    """Refuse unsupported destinations; never downgrade a named metric to v1."""
    if "experiment.conversion.v2" not in supported_event_types:
        raise ValueError("destination does not support named conversions")
    typed = ExperimentConversionV2Payload.model_validate(payload)
    typed.require_metric(allowed_metrics)
    return typed
