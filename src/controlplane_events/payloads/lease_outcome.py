"""Lease execution facts. Entitlement and refund decisions belong to payment."""
from typing import Annotated, Literal
from uuid import UUID
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

LEASE_OUTCOME_V1 = "hypermesh.lease.outcome.v1"

# Route dispatch calls model_validate on a JSON-decoded dict. Model strict mode
# keeps delivered_cents an int; these two fields still accept their wire strings.
_WireUUID = Annotated[UUID, Field(strict=False)]
_WireTime = Annotated[AwareDatetime, Field(strict=False)]


class LeaseOutcome(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    tenant_id: _WireUUID
    lease_id: _WireUUID
    billing_record_id: _WireUUID
    host_org_id: _WireUUID
    host_wallet: str | None = Field(default=None, min_length=1)
    kind: Literal["p1_hardware", "p2_loaded_model"]
    outcome: Literal["started", "ended", "failed"]
    certification: Literal["passed", "failed", "unavailable"]
    delivered_cents: int = Field(ge=0)
    observed_at: _WireTime
