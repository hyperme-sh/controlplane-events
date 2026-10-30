from __future__ import annotations

import uuid
from datetime import datetime, timezone

from pydantic import BaseModel, EmailStr, Field

from controlplane_events.envelope import ControlPlaneEventEnvelope


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class UserCreatedEvent(BaseModel):
    """Typed payload for user.created.v1 (maps to ControlPlaneEventEnvelope.payload)."""

    model_config = {"extra": "forbid", "str_strip_whitespace": True}

    user_id: uuid.UUID
    email: EmailStr
    tenant_id: str | None = Field(
        default=None,
        description="Stringified tenant UUID or None for platform scope",
    )
    display_name: str | None = Field(default=None, max_length=255)

    def to_envelope_payload(self) -> dict[str, object]:
        return self.model_dump(mode="json")

    @classmethod
    def from_envelope_payload(cls, payload: dict[str, object]) -> UserCreatedEvent:
        return cls.model_validate(payload)

    def wrap_envelope(
        self,
        *,
        service: str,
        tenant_uuid: uuid.UUID | None = None,
        correlation_id: str | None = None,
    ) -> ControlPlaneEventEnvelope:
        return ControlPlaneEventEnvelope(
            event_type="user.created.v1",
            service=service,
            tenant_id=tenant_uuid,
            correlation_id=correlation_id,
            payload=self.to_envelope_payload(),
            occurred_at=_utc_now(),
        )
