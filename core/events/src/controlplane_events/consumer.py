from __future__ import annotations

import asyncio
import logging
from abc import ABC, abstractmethod
from typing import Any

from aiokafka import AIOKafkaConsumer
from aiokafka.errors import KafkaError

logger = logging.getLogger(__name__)


class AioKafkaEventConsumerBase(ABC):
    """Background consumer loop; subclass and implement handle_message."""

    def __init__(
        self,
        bootstrap_servers: str,
        topics: list[str],
        group_id: str,
        *,
        client_id: str = "controlplane-consumer",
        auto_offset_reset: str = "earliest",
    ) -> None:
        self._bootstrap = bootstrap_servers
        self._topics = topics
        self._group_id = group_id
        self._client_id = client_id
        self._auto_offset_reset = auto_offset_reset
        self._consumer: AIOKafkaConsumer | None = None
        self._task: asyncio.Task[None] | None = None
        self._stop = asyncio.Event()

    async def start(self) -> None:
        if self._consumer is not None:
            return
        self._consumer = AIOKafkaConsumer(
            *self._topics,
            bootstrap_servers=self._bootstrap,
            group_id=self._group_id,
            client_id=self._client_id,
            enable_auto_commit=True,
            auto_offset_reset=self._auto_offset_reset,
        )
        await self._consumer.start()
        self._stop.clear()
        self._task = asyncio.create_task(self._run_loop())
        logger.info(
            "Kafka consumer started group=%s topics=%s",
            self._group_id,
            self._topics,
        )

    async def stop(self) -> None:
        self._stop.set()
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        if self._consumer is not None:
            await self._consumer.stop()
            self._consumer = None
        logger.info("Kafka consumer stopped group=%s", self._group_id)

    async def _run_loop(self) -> None:
        assert self._consumer is not None
        try:
            async for msg in self._consumer:
                if self._stop.is_set():
                    break
                try:
                    headers: dict[str, bytes] = dict(msg.headers or [])
                    await self.handle_message(
                        key=msg.key,
                        value=msg.value,
                        headers=headers,
                    )
                except Exception:
                    logger.exception("handle_message failed; continuing")
        except asyncio.CancelledError:
            raise
        except KafkaError as e:
            logger.error("Kafka consumer loop error: %s", e)

    @abstractmethod
    async def handle_message(
        self,
        *,
        key: bytes | None,
        value: bytes | None,
        headers: dict[str, bytes],
    ) -> None:
        """Process one message."""
        raise NotImplementedError
