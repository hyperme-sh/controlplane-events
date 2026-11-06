"""Owner HTTP delivery with durable receipts and at-least-once handlers.

Handlers must be idempotent: a process crash after their domain commit but before
this receipt commits causes redelivery. No consumer reads another owner's SQL.
"""
import hashlib
import uuid
from sqlalchemy import bindparam,text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import create_async_engine
from controlplane_events.contracts import event_contracts, validate_event_envelope
from controlplane_events.store import normalize_async_url


def producer_owns_event(service, event_type):
    """The authenticated producer may submit only events registered to that owner."""
    entry = event_contracts()['events'].get(event_type)
    owner = entry.get('owner') if isinstance(entry, dict) else None
    return isinstance(service, str) and bool(service) and owner == service


class HttpRoutingConsumer:
    def __init__(self,database_url,routes):
        self.routes=routes
        self.engine=create_async_engine(normalize_async_url(database_url),pool_pre_ping=True)

    async def start(self):
        async with self.engine.connect() as db:
            await db.execute(text('SELECT 1 FROM event_inbox LIMIT 1'))

    async def stop(self):
        await self.engine.dispose()

    async def receive(self,event_id,envelope):
        validate_event_envelope(envelope)
        if not producer_owns_event(envelope.service, envelope.event_type):
            raise ValueError('Producer cannot submit an event owned by another service')
        if envelope.tenant_id is None or envelope.event_type not in self.routes:
            raise ValueError('No tenant-scoped handler for this event')
        event_id=uuid.UUID(str(event_id))
        model,handler=self.routes[envelope.event_type]
        payload=model.model_validate(envelope.payload)
        serialized=envelope.model_dump(mode='json')
        async with self.engine.begin() as db:
            await db.execute(text("SELECT set_config('app.current_tenant_id',:tenant,true)"),{'tenant':str(envelope.tenant_id)})
            await db.execute(text('SELECT pg_advisory_xact_lock(:key)'),
                             {'key':int.from_bytes(hashlib.sha256(event_id.bytes).digest()[:8],signed=True)})
            previous=await db.scalar(text('SELECT envelope FROM event_inbox WHERE event_id=:id'),{'id':event_id})
            if previous is not None:
                if previous!=serialized:raise ValueError('Event identity reused with different facts')
                return False
            await handler(envelope,payload)
            await db.execute(text('INSERT INTO event_inbox(event_id,tenant_id,envelope) VALUES (:id,:tenant,:envelope)')
                .bindparams(bindparam('envelope',type_=JSONB)),
                {'id':event_id,'tenant':envelope.tenant_id,'envelope':serialized})
        return True


def consumer_router():
    import hmac
    import json
    import os
    from pathlib import Path
    from fastapi import APIRouter,HTTPException,Request
    from controlplane_events.http_inbox import Delivery
    router=APIRouter()

    @router.post('/internal/events')
    async def receive_event(request:Request,delivery:Delivery):
        path=os.environ.get('EVENT_PRODUCER_TOKENS_FILE')
        if not path:raise HTTPException(503,'Event credentials not configured')
        expected=json.loads(Path(path).read_text()).get(delivery.envelope.service)
        if not expected or not hmac.compare_digest(request.headers.get('authorization','').encode(),('Bearer '+expected).encode()):
            raise HTTPException(401,'Producer credential required')
        if not producer_owns_event(delivery.envelope.service, delivery.envelope.event_type):
            raise HTTPException(403,'Producer cannot submit an event owned by another service')
        if request.headers.get('idempotency-key')!=str(delivery.event_id):
            raise HTTPException(400,'Idempotency key must match event identity')
        consumer=getattr(request.app.state,'event_consumer',None)
        if not isinstance(consumer,HttpRoutingConsumer):raise HTTPException(503,'HTTP consumer not configured')
        try:
            created=await consumer.receive(delivery.event_id,delivery.envelope)
        except ValueError as exc:
            raise HTTPException(409,str(exc)) from exc
        return {'committed':True,'duplicate':not created,'event_id':str(delivery.event_id)}
    return router
