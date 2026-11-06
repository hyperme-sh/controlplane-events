"""SQL for the Postgres event log. Schema lives in Alembic 0046."""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy import bindparam, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncEngine

from controlplane_events.envelope import ControlPlaneEventEnvelope

INSERT_EVENT = text(
    """
    INSERT INTO controlplane_events (event_type, tenant_id, envelope)
    VALUES (:event_type, :tenant_id, :envelope)
    RETURNING id
    """
).bindparams(bindparam("envelope", type_=JSONB))

NOTIFY_CHANNEL = "controlplane_events"


def envelope_params(envelope: ControlPlaneEventEnvelope) -> dict[str, Any]:
    return {
        "event_type": envelope.event_type,
        "tenant_id": envelope.tenant_id,
        "envelope": envelope.model_dump(mode="json"),
    }


def insert_event_sync(session: Any, envelope: ControlPlaneEventEnvelope) -> int:
    """Insert on the caller's transaction. The caller commits."""

    row = session.execute(INSERT_EVENT, envelope_params(envelope)).one()
    return int(row[0])


async def insert_event(session: Any, envelope: ControlPlaneEventEnvelope) -> int:
    row = (await session.execute(INSERT_EVENT, envelope_params(envelope))).one()
    return int(row[0])


def envelope_to_bytes(envelope: Any) -> bytes:
    if isinstance(envelope, (bytes, bytearray)):
        return bytes(envelope)
    if isinstance(envelope, str):
        return envelope.encode("utf-8")
    if isinstance(envelope, dict):
        return json.dumps(envelope).encode("utf-8")
    raise TypeError(f"unexpected envelope type {type(envelope)!r}")


def normalize_sync_url(url: str) -> str:
    """Select the installed psycopg 3 driver for a plain Postgres URL.

    SQLAlchemy's default for ``postgresql://`` is psycopg2, which the http
    extra does not install.
    """
    text = url.strip()
    if text.startswith("postgresql+psycopg://"):
        return text
    for prefix in ("postgresql+asyncpg://", "postgresql+psycopg2://", "postgresql://", "postgres://"):
        if text.startswith(prefix):
            return "postgresql+psycopg://" + text.removeprefix(prefix)
    return text


def normalize_async_url(url: str) -> str:
    if url.startswith("postgresql://"):
        return "postgresql+asyncpg://" + url.removeprefix("postgresql://")
    if url.startswith("postgres://"):
        return "postgresql+asyncpg://" + url.removeprefix("postgres://")
    if url.startswith("postgresql+psycopg2://"):
        return "postgresql+asyncpg://" + url.removeprefix("postgresql+psycopg2://")
    if url.startswith("postgresql+psycopg://"):
        return "postgresql+asyncpg://" + url.removeprefix("postgresql+psycopg://")
    return url


def asyncpg_dsn(url: str) -> str:
    normalized = normalize_async_url(url)
    if normalized.startswith("postgresql+asyncpg://"):
        return "postgresql://" + normalized.removeprefix("postgresql+asyncpg://")
    return normalized


async def prune_events(engine: AsyncEngine, retention: str = "30 days") -> int:
    """Delete retained events. See prune_controlplane_events in migration 0046."""

    async with engine.begin() as conn:
        row = (
            await conn.execute(
                text("SELECT prune_controlplane_events(CAST(:retention AS interval))"),
                {"retention": retention},
            )
        ).one()
    return int(row[0])


def delivery_counts_sync(connection: Any) -> tuple[int, int]:
    """Return pending/claimed depth and dead deliveries visible to the caller.

    This read-only aggregate is owned by the event store. The caller owns the
    connection, transaction, role and timeouts; no tenant scope is widened here.
    No event, tenant or consumer identifiers leave this interface.
    """
    row = connection.execute(
        text(
            """
            SELECT
              COUNT(*) FILTER (WHERE status IN ('pending', 'claimed')) AS depth,
              COUNT(*) FILTER (WHERE status = 'dead') AS dead
            FROM controlplane_event_deliveries
            """
        )
    ).one()
    return int(row.depth), int(row.dead)
