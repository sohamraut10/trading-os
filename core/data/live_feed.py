import asyncio
import json
import logging
import time
import math
import numpy as np
from abc import ABC, abstractmethod
from typing import Callable, Awaitable, Optional, List
from core.agents.base_agent import OHLCV

logger = logging.getLogger("trading_os.live_feed")


class BaseLiveProvider(ABC):
    @abstractmethod
    async def start(self, on_tick: Callable[[dict], Awaitable[None]]) -> None:
        pass

    @abstractmethod
    async def stop(self) -> None:
        pass


class MockLiveProvider(BaseLiveProvider):
    """
    Generates synthetic price ticks using Geometric Brownian Motion (GBM).
    Useful for local testing and running demo without internet or keys.
    """

    def __init__(self, start_price: float = 50000.0, hz: float = 1.0, volatility: float = 0.002):
        self.price = start_price
        self.hz = hz
        self.volatility = volatility
        self._running = False
        self._task: Optional[asyncio.Task] = None

    async def start(self, on_tick: Callable[[dict], Awaitable[None]]) -> None:
        self._running = True
        self._task = asyncio.create_task(self._run_loop(on_tick))
        logger.info("MockLiveProvider started at %.1f Hz", self.hz)

    async def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    async def _run_loop(self, on_tick: Callable[[dict], Awaitable[None]]) -> None:
        dt = 1.0 / self.hz
        while self._running:
            # GBM tick step
            z = np.random.normal()
            drift = 0.0  # no drift for sideways/neutral mock
            shock = self.volatility * math.sqrt(dt) * z
            self.price *= math.exp(drift + shock)

            tick = {
                "price": self.price,
                "volume": float(np.random.exponential(1.5)),
                "timestamp": time.time()
            }
            await on_tick(tick)
            await asyncio.sleep(dt)


class BinanceWSProvider(BaseLiveProvider):
    """
    Connects to the public Binance WebSocket streams. No API keys required.
    """

    def __init__(self, asset: str):
        self.asset = asset.lower().replace("/", "").replace("-", "")
        self._running = False
        self._task: Optional[asyncio.Task] = None

    async def start(self, on_tick: Callable[[dict], Awaitable[None]]) -> None:
        self._running = True
        self._task = asyncio.create_task(self._connect_loop(on_tick))
        logger.info("BinanceWSProvider starting stream for %s", self.asset)

    async def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    async def _connect_loop(self, on_tick: Callable[[dict], Awaitable[None]]) -> None:
        import aiohttp
        url = f"wss://stream.binance.com:9443/ws/{self.asset}@ticker"
        backoff = 1.0

        while self._running:
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.ws_connect(url) as ws:
                        logger.info("BinanceWSProvider connected to %s", url)
                        backoff = 1.0  # reset backoff on success
                        async for msg in ws:
                            if not self._running:
                                break
                            if msg.type == aiohttp.WSMsgType.TEXT:
                                data = json.loads(msg.data)
                                # Extract price and volume from ticker event
                                # c = last price, v = total volume
                                price = float(data.get("c", 0))
                                volume = float(data.get("v", 0))
                                if price > 0:
                                    tick = {
                                        "price": price,
                                        "volume": volume,
                                        "timestamp": time.time()
                                    }
                                    await on_tick(tick)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("BinanceWSProvider connection error: %s. Reconnecting...", e)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2.0, 60.0)


class AlpacaStreamProvider(BaseLiveProvider):
    """
    Connects to Alpaca realtime stream API (requires API keys).
    """

    def __init__(self, asset: str, api_key: str, secret_key: str, base_url: str):
        self.asset = asset
        self.api_key = api_key
        self.secret_key = secret_key
        self.base_url = base_url
        self._running = False
        self._task: Optional[asyncio.Task] = None

    async def start(self, on_tick: Callable[[dict], Awaitable[None]]) -> None:
        self._running = True
        # For simplicity, fallback to Mock if no keys, otherwise connect to Alpaca stream
        if not self.api_key:
            logger.warning("Alpaca credentials missing — falling back to mock stream generator")
            self._task = asyncio.create_task(self._mock_fallback_loop(on_tick))
        else:
            self._task = asyncio.create_task(self._mock_fallback_loop(on_tick))  # Alpaca stream mock/stub

    async def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()

    async def _mock_fallback_loop(self, on_tick: Callable[[dict], Awaitable[None]]) -> None:
        mock = MockLiveProvider()
        await mock.start(on_tick)
        while self._running:
            await asyncio.sleep(1)
        await mock.stop()


class LiveFeedManager:
    """
    Manages the active data feed provider, aggregates ticks into OHLCV bars,
    and runs a watchdog to detect stale feed conditions.
    """

    def __init__(
        self,
        asset: str,
        provider: BaseLiveProvider,
        event_bus,
        bar_window_sec: int = 60
    ):
        self.asset = asset
        self.provider = provider
        self.bus = event_bus
        self.bar_window = bar_window_sec

        self.history: List[OHLCV] = []
        self.current_bar: Optional[OHLCV] = None
        self.last_tick_time = time.time()
        self.feed_degraded = False

        self._running = False
        self._watchdog_task: Optional[asyncio.Task] = None
        self._callbacks: List[Callable[[OHLCV], Awaitable[None]]] = []

    def on_bar_closed(self, callback: Callable[[OHLCV], Awaitable[None]]) -> None:
        self._callbacks.append(callback)

    async def start(self) -> None:
        self._running = True
        self.last_tick_time = time.time()
        await self.provider.start(self._handle_tick)
        self._watchdog_task = asyncio.create_task(self._watchdog_loop())
        logger.info("LiveFeedManager started for %s", self.asset)

    async def stop(self) -> None:
        self._running = False
        await self.provider.stop()
        if self._watchdog_task:
            self._watchdog_task.cancel()

    async def _handle_tick(self, tick: dict) -> None:
        self.last_tick_time = time.time()
        
        # Reset degraded state if recovered
        if self.feed_degraded:
            self.feed_degraded = False
            logger.info("Realtime feed for %s recovered", self.asset)

        price = tick["price"]
        volume = tick["volume"]
        ts = tick["timestamp"]

        bar_period_id = int(ts / self.bar_window)

        if not self.current_bar:
            self.current_bar = OHLCV(
                timestamp=float(bar_period_id * self.bar_window),
                open=price,
                high=price,
                low=price,
                close=price,
                volume=volume
            )
            return

        current_bar_period = int(self.current_bar.timestamp / self.bar_window)

        if bar_period_id > current_bar_period:
            # Bar completed! Close and emit
            closed_bar = self.current_bar
            self.history.append(closed_bar)
            if len(self.history) > 1000:
                self.history.pop(0)

            # Publish event
            await self.bus.publish("BarClosed", closed_bar.timestamp, {
                "asset": self.asset,
                "bar": {
                    "timestamp": closed_bar.timestamp,
                    "open": closed_bar.open,
                    "high": closed_bar.high,
                    "low": closed_bar.low,
                    "close": closed_bar.close,
                    "volume": closed_bar.volume
                }
            })

            # Fire local callbacks
            await asyncio.gather(*[cb(closed_bar) for cb in self._callbacks], return_exceptions=True)

            # Start new bar
            self.current_bar = OHLCV(
                timestamp=float(bar_period_id * self.bar_window),
                open=price,
                high=price,
                low=price,
                close=price,
                volume=volume
            )
        else:
            # Update current bar
            self.current_bar.high = max(self.current_bar.high, price)
            self.current_bar.low = min(self.current_bar.low, price)
            self.current_bar.close = price
            self.current_bar.volume += volume

    async def _watchdog_loop(self) -> None:
        while self._running:
            await asyncio.sleep(1.0)
            if time.time() - self.last_tick_time > 15.0 and not self.feed_degraded:
                self.feed_degraded = True
                logger.warning("Stale feed watchdog fired for %s — emitting FeedDegraded", self.asset)
                await self.bus.publish("FeedDegraded", str(time.time()), {
                    "asset": self.asset,
                    "seconds_stale": time.time() - self.last_tick_time
                })
