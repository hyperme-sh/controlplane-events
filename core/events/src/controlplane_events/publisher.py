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
        refuse_forbidden_payload(payload)
        return ControlPlaneEventEnvelope(
            event_type=event_type,
            service=self._service_name,
            tenant_id=tenant_id,
            payload=payload,
            correlation_id=correlation_id,
        )

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
