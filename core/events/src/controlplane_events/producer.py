from __future__ import annotations

import logging

from aiokafka import AIOKafkaProducer

from controlplane_events.envelope import ControlPlaneEventEnvelope

logger = logging.getLogger(__name__)


class AioKafkaEventProducer:
    """Async Kafka producer for JSON envelopes."""

    def __init__(
        self,
        bootstrap_servers: str,
        *,
        client_id: str = "controlplane-events",
        acks: str | int = "all",
    ) -> None:
        self._bootstrap = bootstrap_servers
        self._client_id = client_id
        self._acks = acks
        self._producer: AIOKafkaProducer | None = None

    async def start(self) -> None:
        if self._producer is not None:
            return
        self._producer = AIOKafkaProducer(
            bootstrap_servers=self._bootstrap,
            client_id=self._client_id,
            acks=self._acks,
            enable_idempotence=True,
        )
        await self._producer.start()
        logger.info("Kafka producer started client_id=%s", self._client_id)

    async def stop(self) -> None:
        if self._producer is None:
            return
        await self._producer.stop()
        self._producer = None
        logger.info("Kafka producer stopped")

    async def send_envelope(
        self,
        topic: str,
        envelope: ControlPlaneEventEnvelope,
        *,
        key: bytes | None = None,
    ) -> None:
        if self._producer is None:
            raise RuntimeError("Producer not started; call start() first")
        value = envelope.model_dump_json().encode("utf-8")
        headers = envelope.kafka_headers()
        await self._producer.send(topic, value=value, key=key, headers=headers)
