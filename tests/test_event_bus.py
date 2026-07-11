import pytest
import asyncio
from core.streaming.event_bus import EventBus


@pytest.mark.asyncio
async def test_event_bus_round_trip():
    bus = EventBus()
    received_events = []

    async def test_handler(event_dict: dict):
        received_events.append(event_dict)

    # Register wildcard subscriber
    bus.on("*", test_handler)

    cycle_id = "test-cycle-123"
    payload = {"hello": "world"}
    event_dict = await bus.publish("BarClosed", cycle_id, payload)

    assert event_dict["type"] == "BarClosed"
    assert event_dict["cycle_id"] == cycle_id
    assert event_dict["payload"] == payload

    # Give a tiny async pause
    await asyncio.sleep(0.01)

    assert len(received_events) == 1
    assert received_events[0]["event_id"] == event_dict["event_id"]
    assert received_events[0]["payload"] == payload

    # Test cycle filtering
    cycle_events = bus.get_cycle_events(cycle_id)
    assert len(cycle_events) == 1
    assert cycle_events[0]["event_id"] == event_dict["event_id"]
