from __future__ import annotations

import uuid

from pydantic import BaseModel, Field


class GroupMemberRemovedPayload(BaseModel):
    """Payload for group.member.removed.v1."""

    model_config = {"extra": "forbid", "str_strip_whitespace": True}

    user_id: uuid.UUID
    group_id: uuid.UUID
    tenant_id: uuid.UUID
    removed_by_user_id: uuid.UUID | None = Field(
        default=None,
        description="Actor from JWT sub when available",
    )

    def to_envelope_payload(self) -> dict[str, object]:
        return self.model_dump(mode="json")
