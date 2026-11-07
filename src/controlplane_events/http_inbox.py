"""HTTP transport for the events owner's inbox; install the http extra."""
from contextlib import asynccontextmanager
import asyncio
import hmac
import json
import os
from pathlib import Path
import uuid

from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel, ConfigDict, ValidationError
from sqlalchemy import create_engine, text
from controlplane_events.envelope import ControlPlaneEventEnvelope
from controlplane_events.delivery import MAX_DELIVERY_BYTES
from controlplane_events.http_consumer import producer_owns_event
from controlplane_events.inbox import receive
from controlplane_events.store import normalize_sync_url


class Delivery(BaseModel):
    model_config = ConfigDict(extra='forbid')
    event_id: uuid.UUID
    envelope: ControlPlaneEventEnvelope


def bearer_is_known(authorization: str, tokens) -> bool:
    supplied = (authorization or '').encode()
    known = False
    for token in tokens:
        expected = ('Bearer ' + token).encode()
        if len(supplied) == len(expected) and hmac.compare_digest(supplied, expected):
            known = True
    return known


async def read_limited_body(request: Request, limit: int = MAX_DELIVERY_BYTES) -> bytes:
    declared = request.headers.get('content-length')
    if declared is not None:
        try:
            size = int(declared)
        except ValueError:
            raise HTTPException(400, 'Content-Length must be an integer') from None
        if size > limit:
            raise HTTPException(413, 'Delivery body exceeds the size limit')
    body = bytearray()
    async for chunk in request.stream():
        if len(body) + len(chunk) > limit:
            raise HTTPException(413, 'Delivery body exceeds the size limit')
        body.extend(chunk)
    return bytes(body)


async def authenticated_delivery(request: Request, tokens: dict) -> Delivery:
    """Reject unknown bearers and oversized bodies before JSON parsing."""
    if not tokens or not bearer_is_known(request.headers.get('authorization', ''), tokens.values()):
        raise HTTPException(401, 'Producer credential required')
    raw = await read_limited_body(request)
    try:
        return Delivery.model_validate_json(raw)
    except ValidationError:
        raise HTTPException(400, 'Delivery body is not a valid event') from None


def create_app(*, engine=None, producers=None, forward=None):
    owned_engine=engine is None
    @asynccontextmanager
    async def lifespan(app):
        app.state.engine=engine or create_engine(normalize_sync_url(Path(os.environ['DATABASE_URL_FILE']).read_text()),pool_pre_ping=True)
        mode=os.environ.get('EVENTS_LEGACY_LOG_FORWARD', '0')
        if mode not in ('0', '1'):
            raise ValueError('EVENTS_LEGACY_LOG_FORWARD must be 0 or 1')
        app.state.forward=forward
        if mode == '1':
            if forward is not None:
                raise ValueError('Choose one configured forwarding destination')
            from controlplane_events.legacy_broker import append_legacy_event
            app.state.forward=append_legacy_event
        app.state.sync_engine=app.state.engine
        app.state.producers=producers if producers is not None else json.loads(Path(os.environ['EVENT_PRODUCER_TOKENS_FILE']).read_text())
        if not app.state.producers or any(not key or not value for key,value in app.state.producers.items()):
            raise ValueError('Producer-bound credentials are required')
        try:
            yield
        finally:
            if owned_engine:app.state.engine.dispose()
    app=FastAPI(lifespan=lifespan)

    @app.get('/health/ready')
    def ready(request:Request):
        with request.app.state.engine.connect() as db:
            db.execute(text('SELECT 1 FROM event_inbox LIMIT 1'))
        return {'ready':True}

    @app.post('/events')
    async def ingest(request:Request):
        delivery = await authenticated_delivery(request, request.app.state.producers)
        expected=request.app.state.producers.get(delivery.envelope.service)
        supplied=request.headers.get('authorization','')
        if not expected or not hmac.compare_digest(supplied.encode(),('Bearer '+expected).encode()):
            raise HTTPException(401,'Producer credential required')
        if not producer_owns_event(delivery.envelope.service, delivery.envelope.event_type):
            raise HTTPException(403,'Producer cannot submit an event owned by another service')
        if request.headers.get('idempotency-key') != str(delivery.event_id):
            raise HTTPException(400,'Idempotency key must match event identity')
        try:
            created=await asyncio.to_thread(
                receive, request.app.state.engine, delivery.event_id, delivery.envelope,
                producer=delivery.envelope.service, forward=request.app.state.forward)
        except ValueError as exc:
            raise HTTPException(409,str(exc)) from exc
        return {'event_id':str(delivery.event_id),'committed':True,'duplicate':not created}
    return app
