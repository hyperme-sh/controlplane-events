"""Poll controlplane_event_deliveries and dispatch the same handlers as before."""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

from pydantic import BaseModel, ValidationError
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from controlplane_events.envelope import ControlPlaneEventEnvelope
from controlplane_events.envelope_parse import (
    parse_envelope_bytes,
    payload_matches_envelope_tenant,
)
from controlplane_events.store import asyncpg_dsn, envelope_to_bytes, normalize_async_url
from controlplane_events.topics import CONTROLPLANE_EVENTS_V1

logger = logging.getLogger(__name__)

Handler = Callable[[ControlPlaneEventEnvelope, Any], Awaitable[None]]


async def dispatch_controlplane_envelope(
    value: bytes | None,
    routes: dict[str, tuple[type[BaseModel], Handler]],
) -> None:
    """Parse one controlplane.events.v1 envelope and run the matching handler."""

    envelope = parse_envelope_bytes(value)
    if envelope is None:
        return
    route = routes.get(envelope.event_type)
    if route is None:
        return
    model_cls, handler = route
    try:
        typed = model_cls.model_validate(envelope.payload)
    except ValidationError:
        logger.warning(
            "Payload validation failed for event_type=%s",
            envelope.event_type,
            exc_info=False,
        )
        return
    if not payload_matches_envelope_tenant(envelope, typed):
        logger.warning(
            "Skipping event due to tenant mismatch: event_type=%s envelope=%s payload_tenant=%s",
            envelope.event_type,
            envelope.tenant_id,
            getattr(typed, "tenant_id", None),
        )
        return
    await handler(envelope, typed)


def backoff_seconds(attempts: int, *, fixed: float | None) -> float:
    if fixed is not None:
        return max(0.0, fixed)
    return float(min(60, 2 ** max(attempts - 1, 0)))


class RoutingControlPlaneConsumer:
    """
    Parses JSON envelopes, validates payload with Pydantic, checks tenant_id,
    then dispatches to the handler for event_type.

    ``topics`` is accepted so existing call sites stay positional. The log is
    one table; the topic name is not stored.
    """

    def __init__(
        self,
        database_url: str,
        topics: list[str],
        group_id: str,
        routes: dict[str, tuple[type[BaseModel], Handler]],
        *,
        client_id: str = "controlplane-routing-consumer",
        auto_offset_reset: str = "earliest",
        max_attempts: int = 5,
        poll_interval_s: float = 0.5,
        lease_seconds: int = 30,
        batch_size: int = 16,
        retry_backoff_s: float | None = None,
        worker_id: str | None = None,
    ) -> None:
        self._database_url = database_url
        self._topics = topics or [CONTROLPLANE_EVENTS_V1]
        self._group_id = group_id
        self._routes = routes
        self._client_id = client_id
        self._auto_offset_reset = auto_offset_reset
        self._max_attempts = max_attempts
        self._poll_interval_s = poll_interval_s
        self._lease_seconds = lease_seconds
        self._batch_size = batch_size
        self._retry_backoff_s = retry_backoff_s
        self._worker_id = worker_id or f"{client_id}-{uuid.uuid4().hex[:8]}"
        self._engine: AsyncEngine | None = None
        self._task: asyncio.Task[None] | None = None
        self._listen_task: asyncio.Task[None] | None = None
        self._stop = asyncio.Event()
        self._wakeup = asyncio.Event()
        self._cursor_ready = False

    async def handle_message(
        self,
        *,
        key: bytes | None,
        value: bytes | None,
        headers: dict[str, bytes],
    ) -> None:
        """Dispatch one raw envelope. Tests call this without starting the poller."""

        _ = key, headers
        await dispatch_controlplane_envelope(value, self._routes)

    async def start(self) -> None:
        if self._task is not None:
            return
        self._engine = create_async_engine(
            normalize_async_url(self._database_url),
            pool_pre_ping=True,
        )
        self._cursor_ready = False
        self._stop.clear()
        self._listen_task = asyncio.create_task(self._listen())
        self._task = asyncio.create_task(self._run_loop())
        logger.info(
            "Postgres event consumer started group=%s topics=%s",
            self._group_id,
            self._topics,
        )

    async def stop(self) -> None:
        self._stop.set()
        self._wakeup.set()
        for task in (self._task, self._listen_task):
            if task is None:
                continue
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        self._task = None
        self._listen_task = None
        if self._engine is not None:
            await self._engine.dispose()
            self._engine = None
        logger.info("Postgres event consumer stopped group=%s", self._group_id)

    async def _ensure_cursor(self) -> None:
        """Create the group cursor once. ``latest`` must win before fan-out."""

        if self._cursor_ready:
            return
        assert self._engine is not None
        async with self._engine.begin() as conn:
            await conn.execute(
                text("SELECT ensure_controlplane_event_cursor(:group, :mode)"),
                {"group": self._group_id, "mode": self._auto_offset_reset},
            )
        self._cursor_ready = True

    async def poll_once(self) -> int:
        """Fan out, claim a batch, dispatch. Returns how many handlers ran."""

        if self._engine is None:
            self._engine = create_async_engine(
                normalize_async_url(self._database_url),
                pool_pre_ping=True,
            )
        await self._ensure_cursor()
        async with self._engine.begin() as conn:
            await conn.execute(
                text("SELECT fanout_controlplane_event_deliveries(:group, :max_attempts)"),
                {"group": self._group_id, "max_attempts": self._max_attempts},
            )
        claimed = await self._claim()
        handled = 0
        for row in claimed:
            event_id = int(row["event_id"])
            attempts = int(row["attempts"])
            try:
                await dispatch_controlplane_envelope(
                    envelope_to_bytes(row["envelope"]),
                    self._routes,
                )
            except Exception as exc:
                logger.exception(
                    "handle failed group=%s event_id=%s attempts=%s",
                    self._group_id,
                    event_id,
                    attempts,
                )
                await self._fail(event_id, attempts, exc)
            else:
                await self._ack(event_id)
                handled += 1
        return handled

    async def reset_cursor(self, last_event_id: int = 0) -> None:
        """Replay deliveries with event_id greater than last_event_id."""

        if self._engine is None:
            raise RuntimeError("Consumer engine is not open")
        async with self._engine.begin() as conn:
            await conn.execute(
                text("SELECT reset_controlplane_event_cursor(:group, :last_id)"),
                {"group": self._group_id, "last_id": last_event_id},
            )

    async def _claim(self) -> list[Any]:
        assert self._engine is not None
        async with self._engine.begin() as conn:
            result = await conn.execute(
                text(
                    """
                    SELECT event_id, attempts, envelope
                    FROM claim_controlplane_event_deliveries(
                        :group, :worker, :limit, :lease
                    )
                    """
                ),
                {
                    "group": self._group_id,
                    "worker": self._worker_id,
                    "limit": self._batch_size,
                    "lease": self._lease_seconds,
                },
            )
            return list(result.mappings().all())

    async def _ack(self, event_id: int) -> None:
        assert self._engine is not None
        async with self._engine.begin() as conn:
            await conn.execute(
                text(
                    """
                    UPDATE controlplane_event_deliveries
                    SET status = 'delivered',
                        delivered_at = now(),
                        claimed_by = NULL,
                        lease_expires_at = NULL,
                        last_error = NULL,
                        updated_at = now()
                    WHERE consumer_group = :group
                      AND event_id = :event_id
                      AND status = 'claimed'
                      AND claimed_by = :worker
                    """
                ),
                {
                    "group": self._group_id,
                    "event_id": event_id,
                    "worker": self._worker_id,
                },
            )

    async def _fail(self, event_id: int, attempts: int, exc: BaseException) -> None:
        assert self._engine is not None
        delay = backoff_seconds(attempts, fixed=self._retry_backoff_s)
        error = f"{type(exc).__name__}: {exc}"[:2000]
        async with self._engine.begin() as conn:
            await conn.execute(
                text(
                    """
                    UPDATE controlplane_event_deliveries
                    SET status = CASE
                            WHEN attempts >= max_attempts THEN 'dead'
                            ELSE 'pending'
                        END,
                        available_at = CASE
                            WHEN attempts >= max_attempts THEN available_at
                            ELSE now() + make_interval(secs => CAST(:delay AS double precision))
                        END,
                        last_error = :error,
                        claimed_by = NULL,
                        lease_expires_at = NULL,
                        updated_at = now()
                    WHERE consumer_group = :group
                      AND event_id = :event_id
                      AND status = 'claimed'
                      AND claimed_by = :worker
                    """
                ),
                {
                    "group": self._group_id,
                    "event_id": event_id,
                    "worker": self._worker_id,
                    "delay": delay,
                    "error": error,
                },
            )

    async def _run_loop(self) -> None:
        try:
            while not self._stop.is_set():
                try:
                    await self._ensure_cursor()
                    await self.poll_once()
                except Exception:
                    logger.exception("event poll failed group=%s", self._group_id)
                self._wakeup.clear()
                try:
                    await asyncio.wait_for(self._wakeup.wait(), timeout=self._poll_interval_s)
                except TimeoutError:
                    pass
        except asyncio.CancelledError:
            raise

    async def _listen(self) -> None:
        """Wake the poller on NOTIFY. Polling continues if LISTEN cannot connect."""

        try:
            import asyncpg
        except ImportError:
            logger.info("asyncpg missing; event consumer will poll only")
            return
        try:
            conn = await asyncpg.connect(asyncpg_dsn(self._database_url))
        except Exception:
            logger.warning(
                "LISTEN %s unavailable; polling every %ss",
                "controlplane_events",
                self._poll_interval_s,
                exc_info=True,
            )
            return

        def _on_notify(*_args: object) -> None:
            self._wakeup.set()

        await conn.add_listener("controlplane_events", _on_notify)
        try:
            while not self._stop.is_set():
                await asyncio.sleep(self._poll_interval_s)
        except asyncio.CancelledError:
            raise
        finally:
            await conn.close()
