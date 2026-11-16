"""Bus de messages asynchrone : Kafka (production) ou mémoire (démo autonome).

Les deux implémentations exposent la même API :

    await bus.start()
    await bus.publish(topic, value, key=...)
    async for topic, value in bus.subscribe([topics], group="mon-service"):
        ...

Sémantique identique à Kafka : chaque *consumer group* reçoit sa propre copie
du flux (fan-out), les producteurs n'attendent jamais les consommateurs.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from collections import defaultdict
from typing import Any, AsyncIterator

from .config import Topics, settings

log = logging.getLogger("bus")


def _dumps(value: Any) -> bytes:
    return json.dumps(value, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


class BusStats:
    """Compteurs de débit par topic (messages/s, octets/s)."""

    def __init__(self) -> None:
        self.count: dict[str, int] = defaultdict(int)
        self.bytes: dict[str, int] = defaultdict(int)
        self._last_count: dict[str, int] = {}
        self._last_bytes: dict[str, int] = {}
        self._last_t = time.monotonic()
        self.rate: dict[str, float] = {}
        self.byte_rate: dict[str, float] = {}

    def record(self, topic: str, size: int) -> None:
        self.count[topic] += 1
        self.bytes[topic] += size

    def snapshot(self) -> dict[str, Any]:
        now = time.monotonic()
        dt = max(now - self._last_t, 1e-6)
        if dt > 0.9:
            for topic, n in self.count.items():
                self.rate[topic] = (n - self._last_count.get(topic, 0)) / dt
            for topic, b in self.bytes.items():
                self.byte_rate[topic] = (b - self._last_bytes.get(topic, 0)) / dt
            self._last_count = dict(self.count)
            self._last_bytes = dict(self.bytes)
            self._last_t = now
        return {
            "total": dict(self.count),
            "rate": {k: round(v, 1) for k, v in self.rate.items()},
            "bytes_rate": {k: round(v) for k, v in self.byte_rate.items()},
            "bytes_total": dict(self.bytes),
        }


class MemoryBus:
    """Bus en mémoire (asyncio.Queue par consumer group) — même sémantique que Kafka."""

    kind = "memory"

    def __init__(self, maxsize: int = 50_000) -> None:
        self._groups: dict[str, list[tuple[set[str], asyncio.Queue]]] = defaultdict(list)
        self._maxsize = maxsize
        self.stats = BusStats()
        self.dropped = 0

    async def start(self) -> None:  # pragma: no cover - rien à faire
        return None

    async def stop(self) -> None:
        return None

    async def publish(self, topic: str, value: dict[str, Any], key: str | None = None) -> None:
        payload = _dumps(value)
        self.stats.record(topic, len(payload))
        for subs in self._groups.values():
            for topics, queue in subs:
                if topic in topics:
                    try:
                        queue.put_nowait((topic, value))
                    except asyncio.QueueFull:
                        self.dropped += 1

    async def subscribe(
        self, topics: list[str], group: str, from_beginning: bool = False
    ) -> AsyncIterator[tuple[str, dict[str, Any]]]:
        queue: asyncio.Queue = asyncio.Queue(self._maxsize)
        entry = (set(topics), queue)
        self._groups[group].append(entry)
        try:
            while True:
                yield await queue.get()
        finally:
            self._groups[group].remove(entry)


class KafkaBus:
    """Bus Kafka via aiokafka (producteur idempotent, consumers en groupe)."""

    kind = "kafka"

    def __init__(self, bootstrap: str) -> None:
        self.bootstrap = bootstrap
        self._producer = None
        self.stats = BusStats()
        self.dropped = 0

    async def start(self) -> None:
        from aiokafka import AIOKafkaProducer

        await self._ensure_topics()
        self._producer = AIOKafkaProducer(
            bootstrap_servers=self.bootstrap,
            value_serializer=_dumps,
            key_serializer=lambda k: k.encode() if k else None,
            linger_ms=20,  # micro-batching
            compression_type="gzip",
            acks=1,
        )
        for attempt in range(30):
            try:
                await self._producer.start()
                break
            except Exception as error:  # Kafka pas encore prêt
                log.warning("Kafka indisponible (%s), nouvel essai %s/30", error, attempt + 1)
                await asyncio.sleep(2)
        else:
            raise RuntimeError(f"Impossible de joindre Kafka sur {self.bootstrap}")
        log.info("Kafka connecté : %s", self.bootstrap)

    async def _ensure_topics(self) -> None:
        from aiokafka.admin import AIOKafkaAdminClient, NewTopic

        for attempt in range(30):
            admin = AIOKafkaAdminClient(bootstrap_servers=self.bootstrap)
            try:
                await admin.start()
                existing = set(await admin.list_topics())
                new = [
                    NewTopic(t, num_partitions=settings.kafka_partitions, replication_factor=1)
                    for t in Topics.ALL
                    if t not in existing
                ]
                if new:
                    try:
                        await admin.create_topics(new)
                    except Exception as error:  # créé par un autre service entre-temps
                        log.debug("create_topics: %s", error)
                    log.info("Topics créés : %s", [t.name for t in new])
                return
            except Exception as error:
                log.warning("Admin Kafka indisponible (%s), nouvel essai %s/30", error, attempt + 1)
                await asyncio.sleep(2)
            finally:
                await admin.close()

    async def stop(self) -> None:
        if self._producer:
            await self._producer.stop()

    async def publish(self, topic: str, value: dict[str, Any], key: str | None = None) -> None:
        payload = _dumps(value)
        self.stats.record(topic, len(payload))
        # send() n'attend pas l'accusé de réception : envoi asynchrone, batché.
        await self._producer.send(topic, value=value, key=key)

    async def subscribe(
        self, topics: list[str], group: str, from_beginning: bool = False
    ) -> AsyncIterator[tuple[str, dict[str, Any]]]:
        from aiokafka import AIOKafkaConsumer

        consumer = AIOKafkaConsumer(
            *topics,
            bootstrap_servers=self.bootstrap,
            group_id=group,
            auto_offset_reset="earliest" if from_beginning else "latest",
            value_deserializer=lambda b: json.loads(b.decode("utf-8")),
            enable_auto_commit=True,
        )
        await consumer.start()
        try:
            async for msg in consumer:
                yield msg.topic, msg.value
        finally:
            await consumer.stop()


def create_bus(kind: str | None = None):
    kind = kind or settings.bus
    if kind == "kafka":
        return KafkaBus(settings.kafka_bootstrap)
    return MemoryBus()
