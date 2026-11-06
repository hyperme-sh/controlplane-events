"""HTTP transport for the events owner's inbox; install the http extra."""
from contextlib import asynccontextmanager
import hmac
import json
import os
from pathlib import Path
import uuid

from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel, ConfigDict
from sqlalchemy import create_engine, text
from controlplane_events.envelope import ControlPlaneEventEnvelope
from controlplane_events.http_consumer import producer_owns_event
from controlplane_events.inbox import receive


class Delivery(BaseModel):
    model_config = ConfigDict(extra='forbid')
    event_id: uuid.UUID
    envelope: ControlPlaneEventEnvelope


def create_app(*, engine=None, producers=None):
    owned_engine=engine is None
    @asynccontextmanager
    async def lifespan(app):
        app.state.engine=engine or create_engine(Path(os.environ['DATABASE_URL_FILE']).read_text().strip(),pool_pre_ping=True)
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
    def ingest(request:Request,delivery:Delivery):
        expected=request.app.state.producers.get(delivery.envelope.service)
        supplied=request.headers.get('authorization','')
        if not expected or not hmac.compare_digest(supplied.encode(),('Bearer '+expected).encode()):
            raise HTTPException(401,'Producer credential required')
        if not producer_owns_event(delivery.envelope.service, delivery.envelope.event_type):
            raise HTTPException(403,'Producer cannot submit an event owned by another service')
        if request.headers.get('idempotency-key') != str(delivery.event_id):
            raise HTTPException(400,'Idempotency key must match event identity')
        try:
            created=receive(request.app.state.engine,delivery.event_id,delivery.envelope,producer=delivery.envelope.service)
        except ValueError as exc:
            raise HTTPException(409,str(exc)) from exc
        return {'event_id':str(delivery.event_id),'committed':True,'duplicate':not created}
    return app
