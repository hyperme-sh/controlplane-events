"""Service-owned transactional outbox; destination access is HTTP, never SQL.

Apply DDL from the owning service migration chain with its search_path. The
relay connects as that owner only. Delivery is at least once: receivers must
commit an inbox receipt together with their domain write before acknowledging.
"""
from __future__ import annotations

import uuid
from typing import Callable
from sqlalchemy import bindparam, text, Text
from sqlalchemy.dialects.postgresql import JSONB, ARRAY
from controlplane_events.contracts import validate_event_envelope
from controlplane_events.delivery import MAX_DELIVERY_BYTES, delivery_body
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
    if len(delivery_body(event_id, envelope)) > MAX_DELIVERY_BYTES:
        raise ValueError("event delivery exceeds the receiver body limit")
    session.execute(_INSERT, {"id": event_id, "tenant": envelope.tenant_id,
                              "envelope": envelope.model_dump(mode="json")})


def relay_batch(engine, tenant_id: uuid.UUID, send: Callable, *, limit: int = 100,
                event_types=None, exclude_event_types=None) -> int:
    """Claim only this owner's tenant rows and acknowledge after receiver commit.

    The caller supplies a bounded HTTP transport and a trusted tenant inventory.
    Holding row locks during delivery avoids leases and overlapping workers;
    failure rolls back acknowledgements and safely retries the same event IDs.
    """
    if type(limit) is not int or not 1 <= limit <= 1000:
        raise ValueError("limit must be between 1 and 1000")
    def selected(values):
        if values is None:
            return None
        if isinstance(values, (str, bytes)):
            raise ValueError('Event filters must be nonempty collections')
        values = list(values)
        if not values or any(not isinstance(value, str) or not value.strip() for value in values):
            raise ValueError('Event filters must be nonempty collections')
        return values
    included, excluded = selected(event_types), selected(exclude_event_types)
    if included and excluded and set(included) & set(excluded):
        raise ValueError('Event filters overlap')
    predicate = ''
    bindings = []
    params = {'tenant': tenant_id, 'limit': limit}
    if included is not None:
        predicate += " AND envelope->>'event_type' = ANY(:included)"
        params['included'] = included
        bindings.append(bindparam('included', type_=ARRAY(Text())))
    if excluded is not None:
        predicate += " AND NOT (envelope->>'event_type' = ANY(:excluded))"
        params['excluded'] = excluded
        bindings.append(bindparam('excluded', type_=ARRAY(Text())))
    with engine.begin() as connection:
        connection.execute(text("SELECT set_config('app.current_tenant_id', :tenant, true)"),
                           {"tenant": str(tenant_id)})
        rows = connection.execute(text("""
            SELECT id, envelope FROM service_outbox
            WHERE tenant_id = :tenant AND delivered_at IS NULL
        """ + predicate + """
            ORDER BY created_at, id LIMIT :limit FOR UPDATE SKIP LOCKED
        """).bindparams(*bindings), params).all()
        for event_id, payload in rows:
            envelope = ControlPlaneEventEnvelope.model_validate(payload)
            validate_event_envelope(envelope)
            if envelope.tenant_id != tenant_id:
                raise ValueError("outbox tenant differs from envelope")
            send(event_id, envelope)
            connection.execute(text("UPDATE service_outbox SET delivered_at = now() WHERE id = :id"),
                               {"id": event_id})
        return len(rows)
