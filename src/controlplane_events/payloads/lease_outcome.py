"""Lease execution facts. Entitlement and refund decisions belong to payment."""
from datetime import datetime
from typing import Literal
from uuid import UUID
from pydantic import BaseModel, ConfigDict, Field, AwareDatetime

LEASE_OUTCOME_V1 = "hypermesh.lease.outcome.v1"


class LeaseOutcome(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    tenant_id: UUID
    lease_id: UUID
    billing_record_id: UUID
    host_org_id: UUID
    host_wallet: str = Field(min_length=1)
    kind: Literal["p1_hardware", "p2_loaded_model"]
    outcome: Literal["started", "ended", "failed"]
    certification: Literal["passed", "failed", "unavailable"]
    delivered_cents: int = Field(ge=0)
    observed_at: AwareDatetime
