from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, Field


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class ControlPlaneEventEnvelope(BaseModel):
    """Tenant-scoped domain event wrapper (JSON on the wire)."""

    schema_version: int = Field(default=1, ge=1)
    event_type: str = Field(..., min_length=1, description="e.g. user.created.v1")
    service: str = Field(..., min_length=1, description="process name, e.g. gateway")
    tenant_id: uuid.UUID | None = Field(
        default=None,
        description="None for host-only / platform scope",
    )
    occurred_at: datetime = Field(default_factory=_utc_now)
    correlation_id: str | None = Field(
        default=None,
        description="Optional trace id (stringified UUID recommended)",
    )
    payload: dict[str, Any] = Field(default_factory=dict)
    bus_event_id: int | None = Field(
        default=None,
        exclude=True,
        description="controlplane_events.id for this delivery. Set by the poller, not stored in the row JSON.",
    )

    def transport_headers(self) -> list[tuple[str, bytes]]:
        """Duplicate key fields for a transport that can route without a JSON parse."""
        h: list[tuple[str, bytes]] = [
            ("event_type", self.event_type.encode("utf-8")),
            ("service", self.service.encode("utf-8")),
            ("schema_version", str(self.schema_version).encode("utf-8")),
        ]
        if self.tenant_id is not None:
            h.append(("tenant_id", str(self.tenant_id).encode("utf-8")))
        else:
            h.append(("context", b"host"))
        if self.correlation_id:
            h.append(("correlation_id", self.correlation_id.encode("utf-8")))
        return h
