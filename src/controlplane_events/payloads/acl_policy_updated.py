from __future__ import annotations

import uuid
from typing import Literal

from pydantic import BaseModel, Field


class AclPolicyUpdatedPayload(BaseModel):
    """Payload for acl.policy.updated.v1."""

    model_config = {"extra": "forbid", "str_strip_whitespace": True}

    tenant_id: uuid.UUID
    operation: Literal["added", "removed", "replaced"] = Field(
        description="How the policy store changed",
    )
    ptype: str = Field(..., min_length=1, max_length=64)
    actor_sub: str | None = Field(
        default=None,
        description="JWT sub when available",
    )
    rule_preview: str | None = Field(
        default=None,
        max_length=512,
        description="Human-readable rule fragment for audit",
    )

    def to_envelope_payload(self) -> dict[str, object]:
        return self.model_dump(mode="json")
