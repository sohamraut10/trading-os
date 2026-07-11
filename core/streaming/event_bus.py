import asyncio
import json
import logging
import time
import uuid
from typing import Any, Callable, Awaitable, Dict, List
from pydantic import BaseModel
from core.streaming.kafka_bus import make_bus

logger = logging.getLogger("trading_os.event_bus")


class TypedEvent(BaseModel):
    event_id: str
    cycle_id: str
    ts: float
    type: str
    payload: Any


HandlerFn = Callable[[dict], Awaitable[None]]


class EventBus:
    """
    Typed event bus managing publishing and subscribing to all pipeline events.
    Broadcasting is fanned out to both internal handlers (e.g. WebSocket, database)
    and Kafka if configured.
    """

    def __init__(self, bootstrap_servers: str = ""):
        self.underlying_bus = make_bus(bootstrap_servers)
        self.handlers: Dict[str, List[HandlerFn]] = {}
        self.event_log: List[dict] = []

    def on(self, event_type: str, handler: HandlerFn) -> None:
        """Register a handler for a specific event type, or '*' for all event types."""
        self.handlers.setdefault(event_type, []).append(handler)

    async def publish(self, event_type: str, cycle_id: str, payload: Any) -> dict:
        """Publish a typed event to the bus."""
        event = TypedEvent(
            event_id=str(uuid.uuid4()),
            cycle_id=str(cycle_id),
            ts=time.time(),
            type=event_type,
            payload=payload
        )
        event_dict = event.model_dump()
        self.event_log.append(event_dict)

        # Publish to underlying bus (Kafka or InMemoryBus)
        if hasattr(self.underlying_bus, "publish"):
            await self.underlying_bus.publish("trading.events", event_dict)
        elif hasattr(self.underlying_bus, "_send"):
            # SignalProducer sends to custom topics
            await self.underlying_bus._send("trading.events", event_dict)

        # Trigger registered handlers (direct type or wildcard)
        handlers = self.handlers.get(event_type, []) + self.handlers.get("*", [])
        if handlers:
            await asyncio.gather(*[h(event_dict) for h in handlers], return_exceptions=True)

        return event_dict

    def get_cycle_events(self, cycle_id: str) -> List[dict]:
        """Fetch all events matching a specific cycle ID in order of arrival."""
        return [e for e in self.event_log if e["cycle_id"] == cycle_id]
