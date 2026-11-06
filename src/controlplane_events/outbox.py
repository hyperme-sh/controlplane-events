"""Service-owned transactional outbox; destination access is HTTP, never SQL.

Apply DDL from the owning service migration chain with its search_path. The
relay connects as that owner only. Delivery is at least once: receivers must
commit an inbox receipt together with their domain write before acknowledging.
"""
from __future__ import annotations

import uuid
from typing import Callable
from sqlalchemy import bindparam, text
from sqlalchemy.dialects.postgresql import JSONB
from controlplane_events.contracts import validate_event_envelope
from controlplane_events.envelope import ControlPlaneEventEnvelope

OUTBOX_DDL = """
CREATE TABLE service_outbox (
    id uuid PRIMARY KEY,
    tenant_id uuid NOT NULL,
    envelope jsonb NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    delivered_at timestamptz
);
CREATE INDEX service_outbox_pending ON service_outbox(created_at, id)
    WHERE delivered_at IS NULL;
ALTER TABLE service_outbox ENABLE ROW LEVEL SECURITY;
ALTER TABLE service_outbox FORCE ROW LEVEL SECURITY;
CREATE POLICY service_outbox_tenant ON service_outbox
    USING (tenant_id = nullif(current_setting('app.current_tenant_id', true), '')::uuid)
    WITH CHECK (tenant_id = nullif(current_setting('app.current_tenant_id', true), '')::uuid);
"""

_INSERT = text("""
INSERT INTO service_outbox(id, tenant_id, envelope)
VALUES (:id, :tenant, :envelope)
""").bindparams(bindparam("envelope", type_=JSONB))


def enqueue(session, envelope: ControlPlaneEventEnvelope, *, event_id: uuid.UUID) -> None:
    """No commit: the domain write and event succeed or fail together."""
    validate_event_envelope(envelope)
    if envelope.tenant_id is None:
        raise ValueError("service outbox requires tenant scope")
    session.execute(_INSERT, {"id": event_id, "tenant": envelope.tenant_id,
                              "envelope": envelope.model_dump(mode="json")})


def relay_batch(engine, tenant_id: uuid.UUID, send: Callable, *, limit: int = 100) -> int:
    """Claim only this owner's tenant rows and acknowledge after receiver commit.

    The caller supplies a bounded HTTP transport and a trusted tenant inventory.
    Holding row locks during delivery avoids leases and overlapping workers;
    failure rolls back acknowledgements and safely retries the same event IDs.
    """
    if type(limit) is not int or not 1 <= limit <= 1000:
        raise ValueError("limit must be between 1 and 1000")
    with engine.begin() as connection:
        connection.execute(text("SELECT set_config('app.current_tenant_id', :tenant, true)"),
                           {"tenant": str(tenant_id)})
        rows = connection.execute(text("""
            SELECT id, envelope FROM service_outbox
            WHERE tenant_id = :tenant AND delivered_at IS NULL
            ORDER BY created_at, id LIMIT :limit FOR UPDATE SKIP LOCKED
        """), {"tenant": tenant_id, "limit": limit}).all()
        for event_id, payload in rows:
            envelope = ControlPlaneEventEnvelope.model_validate(payload)
            validate_event_envelope(envelope)
            if envelope.tenant_id != tenant_id:
                raise ValueError("outbox tenant differs from envelope")
            send(event_id, envelope)
            connection.execute(text("UPDATE service_outbox SET delivered_at = now() WHERE id = :id"),
                               {"id": event_id})
        return len(rows)
