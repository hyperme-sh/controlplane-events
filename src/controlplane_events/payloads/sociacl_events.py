from __future__ import annotations

import uuid
from typing import Literal

from pydantic import BaseModel, Field


class SociaclShareCreatedPayload(BaseModel):
    """sociacl.share.created.v1 — no raw token."""

    model_config = {"extra": "forbid", "str_strip_whitespace": True}

    tenant_id: uuid.UUID
    share_id: uuid.UUID
    mode: str = Field(..., max_length=32)
    resource_kind: str = Field(..., max_length=32)
    token_prefix: str = Field(..., max_length=16)
    actor_sub: str | None = Field(default=None, max_length=512)

    def to_envelope_payload(self) -> dict[str, object]:
        return self.model_dump(mode="json")


class SociaclShareResolvedPayload(BaseModel):
    """sociacl.share.resolved.v1"""

    model_config = {"extra": "forbid", "str_strip_whitespace": True}

    tenant_id: uuid.UUID
    share_id: uuid.UUID
    active: bool
    actor_sub: str | None = None

    def to_envelope_payload(self) -> dict[str, object]:
        return self.model_dump(mode="json")


class SociaclAccessDecidedPayload(BaseModel):
    """sociacl.access.decided.v1"""

    model_config = {"extra": "forbid", "str_strip_whitespace": True}

    tenant_id: uuid.UUID
    share_id: uuid.UUID
    decision: str = Field(..., max_length=32)
    reason_code: str = Field(..., max_length=64)
    actor_sub: str | None = None

    def to_envelope_payload(self) -> dict[str, object]:
        return self.model_dump(mode="json")


class SociaclRecoverySessionPayload(BaseModel):
    """sociacl.recovery.session.started.v1"""

    model_config = {"extra": "forbid", "str_strip_whitespace": True}

    tenant_id: uuid.UUID
    session_id: uuid.UUID
    state: Literal["pending", "awaiting_proof", "completed", "failed"]
    idempotency_key: str = Field(..., max_length=128)

    def to_envelope_payload(self) -> dict[str, object]:
        return self.model_dump(mode="json")
