from __future__ import annotations

import uuid
from datetime import datetime, timezone

from pydantic import BaseModel, Field

from controlplane_events.envelope import ControlPlaneEventEnvelope
from controlplane_events.topics import BUDGET_DEPLETED_V1


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class BudgetDepletedEvent(BaseModel):
    """Typed payload for budget.depleted.v1 — LLM/agent fiat budget (not crypto lamports)."""

    model_config = {"extra": "forbid", "str_strip_whitespace": True}

    budget_id: uuid.UUID
    tenant_id: uuid.UUID
    user_id: uuid.UUID | None = None
    spent_cents: int = Field(..., ge=0)
    ceiling_cents: int = Field(..., ge=0)

    def to_envelope_payload(self) -> dict[str, object]:
        return self.model_dump(mode="json")

    @classmethod
    def from_envelope_payload(cls, payload: dict[str, object]) -> BudgetDepletedEvent:
        return cls.model_validate(payload)

    def wrap_envelope(
        self,
        *,
        service: str,
        tenant_uuid: uuid.UUID | None = None,
        correlation_id: str | None = None,
    ) -> ControlPlaneEventEnvelope:
        return ControlPlaneEventEnvelope(
            event_type=BUDGET_DEPLETED_V1,
            service=service,
            tenant_id=tenant_uuid,
            correlation_id=correlation_id,
            payload=self.to_envelope_payload(),
            occurred_at=_utc_now(),
        )
