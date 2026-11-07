"""Events-owner durable HTTP inbox. Acknowledgement follows transaction commit."""
import uuid
from sqlalchemy import bindparam, text
from sqlalchemy.dialects.postgresql import JSONB
from controlplane_events.contracts import validate_event_envelope
from controlplane_events.http_consumer import producer_owns_event

INBOX_DDL = '''
CREATE TABLE event_inbox (
    event_id uuid PRIMARY KEY,
    tenant_id uuid NOT NULL,
    envelope jsonb NOT NULL,
    received_at timestamptz NOT NULL DEFAULT now()
);
ALTER TABLE event_inbox ENABLE ROW LEVEL SECURITY;
ALTER TABLE event_inbox FORCE ROW LEVEL SECURITY;
CREATE POLICY event_inbox_tenant ON event_inbox
USING (tenant_id = nullif(current_setting('app.current_tenant_id',true),'')::uuid)
WITH CHECK (tenant_id = nullif(current_setting('app.current_tenant_id',true),'')::uuid);
'''


def receive(engine, event_id, envelope, *, producer, forward=None):
    event_id=uuid.UUID(str(event_id))
    validate_event_envelope(envelope)
    if envelope.service != producer or envelope.tenant_id is None:
        raise ValueError('Envelope does not match producer and tenant authority')
    if not producer_owns_event(producer, envelope.event_type):
        raise ValueError('Producer cannot submit an event owned by another service')
    payload=envelope.model_dump(mode='json')
    with engine.begin() as db:
        db.execute(text("SELECT set_config('app.current_tenant_id',:tenant,true)"),{'tenant':str(envelope.tenant_id)})
        inserted=db.execute(text('''INSERT INTO event_inbox(event_id,tenant_id,envelope)
            VALUES (:id,:tenant,:envelope) ON CONFLICT DO NOTHING RETURNING event_id''')
            .bindparams(bindparam('envelope',type_=JSONB)),
            {'id':event_id,'tenant':envelope.tenant_id,'envelope':payload}).scalar()
        if inserted is None:
            previous=db.scalar(text('SELECT envelope FROM event_inbox WHERE event_id=:id'),{'id':event_id})
            if previous != payload:
                raise ValueError('Event identity reused with different facts')
        elif forward is not None:
            # Both the receipt and compatibility append commit on this connection.
            # A failed append rolls back the receipt, preserving the producer ID.
            forward(db, event_id, envelope)
        return inserted is not None
