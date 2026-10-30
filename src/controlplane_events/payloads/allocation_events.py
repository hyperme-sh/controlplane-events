"""Scaler contracts on controlplane.events.v1; commerce stays in its adapter."""

from __future__ import annotations

import uuid

from pydantic import BaseModel, ConfigDict

ALLOCATION_REQUESTED_V1 = "allocation.requested.v1"
ALLOCATION_ENDED_V1 = "allocation.ended.v1"
HOST_ENROLLED_V1 = "host.enrolled.v1"


class _ScalerPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tenant_id: uuid.UUID

    def to_envelope_payload(self) -> dict[str, object]:
        return self.model_dump(mode="json")


class AllocationRequestedPayload(_ScalerPayload):
    """A request can precede placement; hardware allocations have no workload."""

    allocation_id: uuid.UUID
    device_id: uuid.UUID | None = None
    workload_id: uuid.UUID | None = None


class AllocationEndedPayload(_ScalerPayload):
    allocation_id: uuid.UUID
    device_id: uuid.UUID | None = None
    workload_id: uuid.UUID | None = None


class HostEnrolledPayload(_ScalerPayload):
    device_id: uuid.UUID
