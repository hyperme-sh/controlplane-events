from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any

from pydantic import BaseModel, ValidationError

from controlplane_events.consumer import AioKafkaEventConsumerBase
from controlplane_events.envelope import ControlPlaneEventEnvelope
from controlplane_events.envelope_parse import (
    parse_envelope_bytes,
    payload_matches_envelope_tenant,
)

logger = logging.getLogger(__name__)

Handler = Callable[[ControlPlaneEventEnvelope, Any], Awaitable[None]]


class RoutingControlPlaneConsumer(AioKafkaEventConsumerBase):
    """
    Parses JSON envelopes, validates payload with Pydantic, checks tenant_id,
    then dispatches to the handler for event_type.
    """

    def __init__(
        self,
        bootstrap_servers: str,
        topics: list[str],
        group_id: str,
        routes: dict[str, tuple[type[BaseModel], Handler]],
        *,
        client_id: str = "controlplane-routing-consumer",
        auto_offset_reset: str = "earliest",
    ) -> None:
        super().__init__(
            bootstrap_servers,
            topics,
            group_id,
            client_id=client_id,
            auto_offset_reset=auto_offset_reset,
        )
        self._routes = routes

    async def handle_message(
        self,
        *,
        key: bytes | None,
        value: bytes | None,
        headers: dict[str, bytes],
    ) -> None:
        _ = key, headers
        envelope = parse_envelope_bytes(value)
        if envelope is None:
            return
        route = self._routes.get(envelope.event_type)
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
