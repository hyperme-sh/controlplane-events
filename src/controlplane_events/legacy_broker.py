"""Optional append to the existing event log on the inbox receipt transaction.

The compatibility destination is fixed, not supplied by an HTTP producer. The
owner role needs INSERT on this log and USAGE on its existing sequence only;
legacy payload reads, cursor writes and business-owner access are unnecessary.
"""
from sqlalchemy import bindparam, text
from sqlalchemy.dialects.postgresql import JSONB
from controlplane_events.store import envelope_params

_APPEND = text("""INSERT INTO public.controlplane_events(event_type, tenant_id, envelope)
VALUES (:event_type, :tenant_id, :envelope)""").bindparams(bindparam('envelope', type_=JSONB))


def append_legacy_event(connection, event_id, envelope):
    # The UUID is retained by event_inbox; the historical log retains its integer
    # sequence, rows and consumer cursors. No receipt or cursor is reset here.
    connection.execute(_APPEND, envelope_params(envelope))
