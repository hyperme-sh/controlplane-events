"""Table-backed publisher. publish() commits its own row unless a session is bound."""

from __future__ import annotations

import logging
import uuid
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any, Iterator

from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from sqlalchemy.orm import Session

from controlplane_events.envelope import ControlPlaneEventEnvelope
from controlplane_events.store import insert_event, insert_event_sync, normalize_async_url

logger = logging.getLogger(__name__)

_FORBIDDEN_PAYLOAD_KEYS = ("prompt", "messages", "device_secret", "enroll_token")

_outbox_session: ContextVar[Session | None] = ContextVar("controlplane_outbox_session", default=None)


def refuse_forbidden_payload(payload: dict[str, Any]) -> None:
    for key in _FORBIDDEN_PAYLOAD_KEYS:
        if key in payload:
            raise ValueError(f"refusing to publish {key} on controlplane.events.v1")


@contextmanager
def bind_outbox_session(session: Session) -> Iterator[Session]:
    """publish() inserts into this session and does not commit."""

    token = _outbox_session.set(session)
    try:
        yield session
    finally:
        _outbox_session.reset(token)


class PostgresEventPublisher:
    """Default bus. One row in controlplane_events per publish()."""

    def __init__(self, database_url: str, service_name: str) -> None:
        self._database_url = database_url
        self._service_name = service_name
        self._engine: AsyncEngine | None = None
        self._owns_engine = False

    def _envelope(
        self,
        *,
        tenant_id: uuid.UUID | None,
        event_type: str,
        payload: dict[str, Any],
        correlation_id: str | None,
    ) -> ControlPlaneEventEnvelope:
        if event_type in {"experiment.exposure.v1", "experiment.conversion.v1", "experiment.conversion.v2"}:
            from jsonschema import ValidationError
            from controlplane_events.contracts import validate_event_payload
            try:
                validate_event_payload(event_type, payload)
            except ValidationError:
                raise ValueError("invalid experiment event payload") from None
        refuse_forbidden_payload(payload)
        envelope = ControlPlaneEventEnvelope(
            event_type=event_type,
            service=self._service_name,
            tenant_id=tenant_id,
            payload=payload,
            correlation_id=correlation_id,
        )
        from controlplane_events.payloads.accounting_events import ACCOUNTING_MODELS
        if event_type in ACCOUNTING_MODELS:
            from controlplane_events.contracts import validate_event_envelope
            envelope.event_id = payload.get('event_id')
            from jsonschema import ValidationError as SchemaError
            from pydantic import ValidationError as ModelError
            try:
                validate_event_envelope(envelope)
            except (SchemaError, ModelError, ValueError):
                # Contract failures must not include raw payload values in logs.
                raise ValueError('invalid accounting event payload') from None
        return envelope

    async def start(self) -> None:
        if self._engine is not None:
            return
        self._engine = create_async_engine(
            normalize_async_url(self._database_url),
            pool_pre_ping=True,
        )
        self._owns_engine = True
        logger.info("Postgres event publisher ready service=%s", self._service_name)

    async def stop(self) -> None:
        if self._engine is not None and self._owns_engine:
            await self._engine.dispose()
        self._engine = None
        self._owns_engine = False

    def publish_in_session(
        self,
        session: Session,
        *,
        tenant_id: uuid.UUID | None,
        event_type: str,
        payload: dict[str, Any],
        correlation_id: str | None = None,
    ) -> int:
        """Insert inside the caller's transaction. Rollback drops the event."""

        envelope = self._envelope(
            tenant_id=tenant_id,
            event_type=event_type,
            payload=payload,
            correlation_id=correlation_id,
        )
        return insert_event_sync(session, envelope)

    async def publish(
        self,
        *,
        tenant_id: uuid.UUID | None,
        event_type: str,
        payload: dict[str, Any],
        correlation_id: str | None = None,
    ) -> None:
        bound = _outbox_session.get()
        if bound is not None:
            self.publish_in_session(
                bound,
                tenant_id=tenant_id,
                event_type=event_type,
                payload=payload,
                correlation_id=correlation_id,
            )
            return
        if self._engine is None:
            await self.start()
        assert self._engine is not None
        envelope = self._envelope(
            tenant_id=tenant_id,
            event_type=event_type,
            payload=payload,
            correlation_id=correlation_id,
        )
        async with self._engine.begin() as conn:
            await insert_event(conn, envelope)


class OwnedOutboxPublisher(PostgresEventPublisher):
    """Persist in this service's schema; an HTTP relay owns delivery."""

    def publish_in_session(self, session, *, tenant_id, event_type, payload, correlation_id=None):
        from controlplane_events.outbox import enqueue
        envelope=self._envelope(tenant_id=tenant_id,event_type=event_type,
                                payload=payload,correlation_id=correlation_id)
        event_id=uuid.uuid4()
        enqueue(session,envelope,event_id=event_id)
        return event_id

    async def publish(self, *, tenant_id, event_type, payload, correlation_id=None):
        from sqlalchemy import text
        from controlplane_events.outbox import enqueue
        bound=_outbox_session.get()
        if bound is not None:
            self.publish_in_session(bound,tenant_id=tenant_id,event_type=event_type,
                                    payload=payload,correlation_id=correlation_id)
            return
        if self._engine is None:
            await self.start()
        envelope=self._envelope(tenant_id=tenant_id,event_type=event_type,
                                payload=payload,correlation_id=correlation_id)
        if tenant_id is None:
            raise ValueError('Owned domain events require tenant identity')
        async with self._engine.begin() as connection:
            await connection.execute(text("SELECT set_config('app.current_tenant_id',:tenant,true)"),{'tenant':str(tenant_id)})
            await connection.run_sync(lambda db: enqueue(db,envelope,event_id=uuid.uuid4()))
