from __future__ import annotations

import uuid

from pydantic import BaseModel, Field


class PaymentSucceededPayload(BaseModel):
    """payment.succeeded.v3 — opaque processor references only."""

    model_config = {"extra": "forbid", "str_strip_whitespace": True}

    tenant_id: uuid.UUID
    billing_record_id: uuid.UUID
    processor_session_reference: str | None = Field(default=None, max_length=255)
    processor_reference: str | None = Field(default=None, max_length=255)
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


class PaymentSucceededV4Payload(PaymentSucceededPayload):
    """Reserved v4 jurisdiction contract; producer rollout belongs to the payment packages."""

    model_config = {"extra": "forbid", "str_strip_whitespace": True,
                    "json_schema_extra": {
                        "if": {"properties": {"billing_country": {"const": "US"}}},
                        "then": {"properties": {"billing_subdivision": {"type": "string"}}},
                        "else": {"properties": {"billing_subdivision": {"type": "null"}}},
                    }}

    billing_country: str = Field(..., pattern=r"^[A-Z]{2}$")
    billing_subdivision: str | None = Field(..., pattern=r"^US-[A-Z]{2}$")
