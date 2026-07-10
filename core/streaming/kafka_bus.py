"""
Kafka Event Bus
Decouples signal generation from downstream consumers (DB writer, dashboard, alerts).
Producers publish TradeSignal events; consumers subscribe independently.

Topics:
  trading.signals   — every TradeSignal (true and false)
  trading.trades    — bracket order fills
  trading.metrics   — cycle latency + agent confidence stats

Run with: docker-compose up kafka
"""
import asyncio
import json
import logging
import time
from dataclasses import dataclass
from typing import AsyncIterator, Callable, Awaitable

log = logging.getLogger("trading_os.kafka")

try:
    from aiokafka import AIOKafkaProducer, AIOKafkaConsumer
    _KAFKA_AVAILABLE = True
except ImportError:
    _KAFKA_AVAILABLE = False

TOPIC_SIGNALS = "trading.signals"
TOPIC_TRADES  = "trading.trades"
TOPIC_METRICS = "trading.metrics"


@dataclass
class KafkaConfig:
    bootstrap_servers: str = "localhost:9092"
    client_id: str = "trading-os"
    group_id: str = "trading-os-consumers"
    auto_offset_reset: str = "latest"
    compression_type: str = "gzip"
    max_batch_size: int = 16384
    linger_ms: int = 10


class SignalProducer:
    """
    Publishes TradeSignal events to Kafka.
    Falls back to a no-op if Kafka is unavailable (dev mode).
    """

    def __init__(self, cfg: KafkaConfig):
        self._cfg = cfg
        self._producer = None
        self._available = _KAFKA_AVAILABLE

    async def start(self) -> None:
        if not self._available:
            log.warning("aiokafka not installed — running in no-op mode")
            return
        self._producer = AIOKafkaProducer(
            bootstrap_servers=self._cfg.bootstrap_servers,
            client_id=self._cfg.client_id,
            value_serializer=lambda v: json.dumps(v).encode(),
            compression_type=self._cfg.compression_type,
            linger_ms=self._cfg.linger_ms,
            max_batch_size=self._cfg.max_batch_size,
        )
        try:
            await self._producer.start()
            log.info("Kafka producer connected to %s", self._cfg.bootstrap_servers)
        except Exception as e:
            log.warning("Kafka unavailable (%s) — falling back to no-op", e)
            self._producer = None

    async def stop(self) -> None:
        if self._producer:
            await self._producer.stop()

    async def publish_signal(self, signal_dict: dict) -> None:
        await self._send(TOPIC_SIGNALS, signal_dict)

    async def publish_trade(self, trade_dict: dict) -> None:
        await self._send(TOPIC_TRADES, trade_dict)

    async def publish_metrics(self, metrics: dict) -> None:
        await self._send(TOPIC_METRICS, metrics)

    async def _send(self, topic: str, payload: dict) -> None:
        if not self._producer:
            return
        try:
            await self._producer.send_and_wait(topic, value=payload)
        except Exception as e:
            log.error("Kafka send failed (topic=%s): %s", topic, e)


HandlerFn = Callable[[dict], Awaitable[None]]


class SignalConsumer:
    """
    Subscribes to one or more Kafka topics and dispatches events to handlers.
    Each handler is an async callable receiving the deserialized dict.
    """

    def __init__(self, cfg: KafkaConfig, topics: list[str]):
        self._cfg = cfg
        self._topics = topics
        self._consumer = None
        self._handlers: dict[str, list[HandlerFn]] = {t: [] for t in topics}
        self._running = False

    def on(self, topic: str, handler: HandlerFn) -> None:
        self._handlers.setdefault(topic, []).append(handler)

    async def start(self) -> None:
        if not _KAFKA_AVAILABLE:
            log.warning("aiokafka not installed — consumer inactive")
            return
        self._consumer = AIOKafkaConsumer(
            *self._topics,
            bootstrap_servers=self._cfg.bootstrap_servers,
            group_id=self._cfg.group_id,
            auto_offset_reset=self._cfg.auto_offset_reset,
            value_deserializer=lambda v: json.loads(v.decode()),
            enable_auto_commit=True,
        )
        try:
            await self._consumer.start()
            log.info("Kafka consumer subscribed to %s", self._topics)
        except Exception as e:
            log.warning("Kafka consumer failed to start (%s)", e)
            self._consumer = None

    async def run(self) -> None:
        if not self._consumer:
            return
        self._running = True
        try:
            async for msg in self._consumer:
                if not self._running:
                    break
                handlers = self._handlers.get(msg.topic, [])
                await asyncio.gather(
                    *[h(msg.value) for h in handlers],
                    return_exceptions=True,
                )
        finally:
            await self._consumer.stop()

    def stop(self) -> None:
        self._running = False


class InMemoryBus:
    """
    Drop-in replacement for Kafka in tests and dev mode.
    Same pub/sub interface; no network required.
    """

    def __init__(self):
        self._handlers: dict[str, list[HandlerFn]] = {}
        self._log: list[dict] = []

    def on(self, topic: str, handler: HandlerFn) -> None:
        self._handlers.setdefault(topic, []).append(handler)

    async def publish(self, topic: str, payload: dict) -> None:
        self._log.append({"topic": topic, "payload": payload, "ts": time.time()})
        handlers = self._handlers.get(topic, [])
        await asyncio.gather(*[h(payload) for h in handlers], return_exceptions=True)

    async def publish_signal(self, signal_dict: dict) -> None:
        await self.publish(TOPIC_SIGNALS, signal_dict)

    async def publish_trade(self, trade_dict: dict) -> None:
        await self.publish(TOPIC_TRADES, trade_dict)

    async def publish_metrics(self, metrics: dict) -> None:
        await self.publish(TOPIC_METRICS, metrics)

    def get_log(self, topic: str | None = None) -> list[dict]:
        if topic:
            return [e for e in self._log if e["topic"] == topic]
        return list(self._log)


def make_bus(bootstrap_servers: str = "") -> SignalProducer | InMemoryBus:
    """Factory: returns real Kafka producer if configured, else in-memory bus."""
    if bootstrap_servers and _KAFKA_AVAILABLE:
        cfg = KafkaConfig(bootstrap_servers=bootstrap_servers)
        return SignalProducer(cfg)
    return InMemoryBus()
