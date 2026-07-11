import pytest
import asyncio
import time
from core.data.live_feed import LiveFeedManager, MockLiveProvider
from core.streaming.event_bus import EventBus


@pytest.mark.asyncio
async def test_live_feed_aggregates_bars():
    bus = EventBus()
    # Use high HZ and short bar window (1 second) to test aggregation quickly
    provider = MockLiveProvider(hz=10.0)
    manager = LiveFeedManager("BTCUSDT", provider, bus, bar_window_sec=1)

    closed_bars = []
    async def on_bar(bar):
        closed_bars.append(bar)

    manager.on_bar_closed(on_bar)

    # Start feed manager
    await manager.start()
    
    # Wait for a couple of bars to close
    await asyncio.sleep(2.5)
    
    await manager.stop()

    assert len(closed_bars) >= 1
    assert closed_bars[0].open > 0
    assert closed_bars[0].volume > 0


@pytest.mark.asyncio
async def test_watchdog_emits_degraded():
    bus = EventBus()
    # Provider that does not emit any ticks after starting
    class SilentProvider(MockLiveProvider):
        async def _run_loop(self, on_tick) -> None:
            # Do nothing to trigger watchdog
            while self._running:
                await asyncio.sleep(0.1)

    provider = SilentProvider()
    manager = LiveFeedManager("BTCUSDT", provider, bus, bar_window_sec=60)

    # Subscribe to FeedDegraded events
    degraded_events = []
    async def on_degraded(event):
        degraded_events.append(event)
    bus.on("FeedDegraded", on_degraded)

    await manager.start()

    # Artificially set last_tick_time to 20 seconds ago to trigger watchdog instantly
    manager.last_tick_time = time.time() - 20.0

    # Wait for watchdog to tick (runs every 1s)
    await asyncio.sleep(1.5)

    await manager.stop()

    assert len(degraded_events) == 1
    assert degraded_events[0]["payload"]["asset"] == "BTCUSDT"
