from __future__ import annotations

import uuid

from pydantic import BaseModel, Field


class PaymentSucceededPayload(BaseModel):
    """payment.succeeded.v1 — no card numbers; Stripe ids only."""

    model_config = {"extra": "forbid", "str_strip_whitespace": True}

    tenant_id: uuid.UUID
    billing_record_id: uuid.UUID
    stripe_session_id: str | None = Field(default=None, max_length=255)
    stripe_payment_intent_id: str | None = Field(default=None, max_length=255)
    amount_cents: int | None = Field(default=None, ge=0)
    currency: str | None = Field(default=None, max_length=8)
    user_id: uuid.UUID | None = None

    def to_envelope_payload(self) -> dict[str, object]:
        return self.model_dump(mode="json")


class BudgetAllocatedPayload(BaseModel):
    """budget.allocated.v1"""

    model_config = {"extra": "forbid", "str_strip_whitespace": True}

    tenant_id: uuid.UUID
    budget_id: uuid.UUID
    user_id: uuid.UUID | None = None
    delta_lamports: int
    allocated_after: int
    ceiling_lamports: int

    def to_envelope_payload(self) -> dict[str, object]:
        return self.model_dump(mode="json")
