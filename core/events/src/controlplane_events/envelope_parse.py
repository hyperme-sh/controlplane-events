from __future__ import annotations

import json
import logging
import uuid
from typing import Any

from pydantic import BaseModel, ValidationError

from controlplane_events.envelope import ControlPlaneEventEnvelope

logger = logging.getLogger(__name__)


def parse_envelope_bytes(value: bytes | None) -> ControlPlaneEventEnvelope | None:
    """Parse JSON message body into a validated envelope, or None on failure."""
    if not value:
        return None
    try:
        data: Any = json.loads(value.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as e:
        logger.warning("Invalid event message JSON: %s", e)
        return None
    try:
        return ControlPlaneEventEnvelope.model_validate(data)
    except ValidationError as e:
        logger.warning("Invalid control plane envelope: %s", e)
        return None


def extract_payload_tenant_id(payload: BaseModel) -> uuid.UUID | None:
    """Read tenant_id from a typed payload (UUID or stringified UUID)."""
    raw = getattr(payload, "tenant_id", None)
    if raw is None:
        return None
    if isinstance(raw, uuid.UUID):
        return raw
    if isinstance(raw, str):
        try:
            return uuid.UUID(raw)
        except ValueError:
            logger.warning("Invalid tenant_id string on payload: %s", raw)
            return None
    return None


def tenant_ids_consistent(
    envelope: ControlPlaneEventEnvelope,
    payload_tenant_id: uuid.UUID | None,
) -> bool:
    """
    Return True if the message is safe to process for the tenant.

    When both envelope and payload carry a tenant id, they must match.
    Envelope-only or payload-only tenant is allowed unless both disagree.
    """
    env_tid = envelope.tenant_id
    if env_tid is not None and payload_tenant_id is not None:
        return env_tid == payload_tenant_id
    if env_tid is None and payload_tenant_id is not None:
        return False
    return True


def payload_matches_envelope_tenant(
    envelope: ControlPlaneEventEnvelope,
    payload: BaseModel,
) -> bool:
    """Combine extract + consistency check for typed payloads."""
    return tenant_ids_consistent(envelope, extract_payload_tenant_id(payload))
