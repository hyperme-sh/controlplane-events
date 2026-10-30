from __future__ import annotations

import uuid
from datetime import datetime, timezone
from decimal import Decimal

from pydantic import BaseModel, Field

from controlplane_events.envelope import ControlPlaneEventEnvelope
from controlplane_events.topics import TOKENS_USED_V1


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class TokensUsedEvent(BaseModel):
    """Typed payload for tokens.used.v1."""

    model_config = {"extra": "forbid", "str_strip_whitespace": True}

    user_id: uuid.UUID
    tenant_id: uuid.UUID
    model: str = Field(..., max_length=256)
    input_tokens: int = Field(..., ge=0)
    output_tokens: int = Field(..., ge=0)
    cost_usd: Decimal = Field(..., ge=0, json_schema_extra={
        "pattern": r"^[+]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?$"
    })

    def to_envelope_payload(self) -> dict[str, object]:
        return self.model_dump(mode="json")

    @classmethod
    def from_envelope_payload(cls, payload: dict[str, object]) -> TokensUsedEvent:
        return cls.model_validate(payload)

    def wrap_envelope(
        self,
        *,
        service: str,
        tenant_uuid: uuid.UUID | None = None,
        correlation_id: str | None = None,
    ) -> ControlPlaneEventEnvelope:
        return ControlPlaneEventEnvelope(
            event_type=TOKENS_USED_V1,
            service=service,
            tenant_id=tenant_uuid,
            correlation_id=correlation_id,
            payload=self.to_envelope_payload(),
            occurred_at=_utc_now(),
        )
