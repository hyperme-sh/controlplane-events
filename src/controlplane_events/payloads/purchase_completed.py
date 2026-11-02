from __future__ import annotations

import uuid
from datetime import datetime, timezone

from pydantic import BaseModel, Field

from controlplane_events.envelope import ControlPlaneEventEnvelope
from controlplane_events.topics import PURCHASE_COMPLETED_V1


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class PurchaseCompletedEvent(BaseModel):
    """Typed payload for purchase.completed.v1."""

    model_config = {"extra": "forbid", "str_strip_whitespace": True}

    purchase_id: uuid.UUID
    listing_id: uuid.UUID
    buyer_id: uuid.UUID
    tenant_id: uuid.UUID
    license_key: str | None = Field(default=None, max_length=512)

    def to_envelope_payload(self) -> dict[str, object]:
        return self.model_dump(mode="json")

    @classmethod
    def from_envelope_payload(cls, payload: dict[str, object]) -> PurchaseCompletedEvent:
        return cls.model_validate(payload)

    def wrap_envelope(
        self,
        *,
        service: str,
        tenant_uuid: uuid.UUID | None = None,
        correlation_id: str | None = None,
    ) -> ControlPlaneEventEnvelope:
        return ControlPlaneEventEnvelope(
            event_type=PURCHASE_COMPLETED_V1,
            service=service,
            tenant_id=tenant_uuid,
            correlation_id=correlation_id,
            payload=self.to_envelope_payload(),
            occurred_at=_utc_now(),
        )
