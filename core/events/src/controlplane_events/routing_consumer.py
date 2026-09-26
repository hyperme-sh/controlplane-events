"""Poll controlplane_event_deliveries and dispatch the same handlers as before."""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

from pydantic import BaseModel, ValidationError
from sqlalchemy import BigInteger, bindparam, text
from sqlalchemy.dialects.postgresql import ARRAY
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


def _is_registration_lock_timeout(exc: BaseException) -> bool:
    """True when registration's SHARE lock hit ``lock_timeout`` (SQLSTATE 55P03)."""

    pending: list[BaseException] = [exc]
    seen: set[int] = set()
    while pending:
        current = pending.pop()
        if id(current) in seen:
            continue
        seen.add(id(current))
        code = getattr(current, "sqlstate", None) or getattr(current, "pgcode", None)
        if code == "55P03" or "lock timeout" in str(current).lower():
            return True
        for nxt in (getattr(current, "orig", None), current.__cause__, current.__context__):
            if isinstance(nxt, BaseException):
                pending.append(nxt)
    return False


async def dispatch_controlplane_envelope(
    value: bytes | None,
    routes: dict[str, tuple[type[BaseModel], Handler]],
    *,
    bus_event_id: int | None = None,
) -> None:
    """Parse one controlplane.events.v1 envelope and run the matching handler."""

    envelope = parse_envelope_bytes(value)
    if envelope is None:
        return
    if bus_event_id is not None:
        envelope.bus_event_id = bus_event_id
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
        self._registered = False

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
        self._registered = False
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

    async def _ensure_registered(self) -> bool:
        """Register this group's event types once. ``earliest`` backfills committed rows.

        False means the SHARE lock timed out. The caller retries on the next poll.
        """

        if self._registered:
            return True
        assert self._engine is not None
        if not self._routes:
            self._registered = True
            return True
        event_types = ",".join(sorted(self._routes))
        try:
            async with self._engine.begin() as conn:
                await conn.execute(
                    text(
                        """
                        SELECT register_controlplane_event_consumer(
                            :group, string_to_array(:types, ','), :mode, :max_attempts
                        )
                        """
                    ),
                    {
                        "group": self._group_id,
                        "types": event_types,
                        "mode": self._auto_offset_reset,
                        "max_attempts": self._max_attempts,
                    },
                )
        except Exception as exc:
            if _is_registration_lock_timeout(exc):
                logger.warning("registration lock busy; retrying next poll")
                return False
            raise
        self._registered = True
        return True

    def _handler_timeout_s(self) -> float:
        """Stay inside the claim lease so a slow handler is failed before reclaim."""

        return max(1.0, float(self._lease_seconds) - 5.0)

    async def poll_once(self) -> int:
        """Register, then claim and handle one row at a time. Returns handlers that acked."""

        if self._engine is None:
            self._engine = create_async_engine(
                normalize_async_url(self._database_url),
                pool_pre_ping=True,
            )
        if not await self._ensure_registered():
            return 0
        handled = 0
        # A row failed in this pass must not be claimed again before the next poll,
        # even when retry backoff is zero.
        touched: list[int] = []
        for _ in range(max(1, self._batch_size)):
            claimed = await self._claim(limit=1, exclude_ids=touched)
            if not claimed:
                break
            row = claimed[0]
            event_id = int(row["event_id"])
            touched.append(event_id)
            attempts = int(row["attempts"])
            try:
                await asyncio.wait_for(
                    dispatch_controlplane_envelope(
                        envelope_to_bytes(row["envelope"]),
                        self._routes,
                        bus_event_id=event_id,
                    ),
                    timeout=self._handler_timeout_s(),
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
        """Requeue deliveries with event_id greater than last_event_id."""

        if self._engine is None:
            raise RuntimeError("Consumer engine is not open")
        async with self._engine.begin() as conn:
            await conn.execute(
                text("SELECT replay_controlplane_event_deliveries(:group, :last_id)"),
                {"group": self._group_id, "last_id": last_event_id},
            )

    async def _claim(self, *, limit: int = 1, exclude_ids: list[int] | None = None) -> list[Any]:
        assert self._engine is not None
        exclude = [int(event_id) for event_id in (exclude_ids or [])]
        async with self._engine.begin() as conn:
            result = await conn.execute(
                text(
                    """
                    SELECT event_id, attempts, envelope
                    FROM claim_controlplane_event_deliveries(
                        :group, :worker, :limit, :lease, :exclude
                    )
                    """
                ).bindparams(bindparam("exclude", type_=ARRAY(BigInteger))),
                {
                    "group": self._group_id,
                    "worker": self._worker_id,
                    "limit": limit,
                    "lease": self._lease_seconds,
                    "exclude": exclude,
                },
            )
            return list(result.mappings().all())

    async def _ack(self, event_id: int) -> None:
        assert self._engine is not None
        async with self._engine.begin() as conn:
            result = await conn.execute(
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
        if result.rowcount == 0:
            logger.warning(
                "ack matched 0 rows group=%s event_id=%s worker=%s",
                self._group_id,
                event_id,
                self._worker_id,
            )

    async def _fail(self, event_id: int, attempts: int, exc: BaseException) -> None:
        assert self._engine is not None
        delay = backoff_seconds(attempts, fixed=self._retry_backoff_s)
        error = f"{type(exc).__name__}: {exc}"[:2000]
        async with self._engine.begin() as conn:
            result = await conn.execute(
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
        if result.rowcount == 0:
            logger.warning(
                "fail matched 0 rows group=%s event_id=%s worker=%s",
                self._group_id,
                event_id,
                self._worker_id,
            )

    async def _run_loop(self) -> None:
        try:
            while not self._stop.is_set():
                try:
                    if await self._ensure_registered():
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
        """Wake the poller on NOTIFY. Reconnect with backoff; polling continues meanwhile."""

        try:
            import asyncpg
        except ImportError:
            logger.info("asyncpg missing; event consumer will poll only")
            return

        def _on_notify(*_args: object) -> None:
            self._wakeup.set()

        delay = 1.0
        while not self._stop.is_set():
            conn = None
            try:
                conn = await asyncpg.connect(asyncpg_dsn(self._database_url))
                delay = 1.0
                await conn.add_listener("controlplane_events", _on_notify)
                while not self._stop.is_set():
                    if getattr(conn, "is_closed", lambda: False)():
                        break
                    await asyncio.sleep(self._poll_interval_s)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.warning(
                    "LISTEN %s dropped; retry in %.0fs (poll every %ss)",
                    "controlplane_events",
                    delay,
                    self._poll_interval_s,
                    exc_info=True,
                )
            finally:
                if conn is not None:
                    try:
                        await conn.close()
                    except Exception:
                        logger.debug("LISTEN connection close failed", exc_info=True)
            if self._stop.is_set():
                return
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=delay)
            except TimeoutError:
                pass
            delay = min(delay * 2, 30.0)
