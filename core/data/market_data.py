"""
Data Layer — Market Data Provider
Abstracts real-time + historical data from multiple broker/data APIs.
Implements an adapter pattern so swapping providers requires zero agent changes.
"""
import asyncio
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import AsyncIterator

from core.agents.base_agent import OHLCV, OrderBook, OrderBookLevel


class MarketDataProvider(ABC):
    """Contract all data providers must satisfy."""

    @abstractmethod
    async def get_candles(
        self, symbol: str, timeframe: str, limit: int = 300
    ) -> list[OHLCV]:
        ...

    @abstractmethod
    async def get_current_price(self, symbol: str) -> float:
        ...

    @abstractmethod
    async def get_order_book(self, symbol: str, depth: int = 20) -> OrderBook:
        ...

    @abstractmethod
    async def stream_price(self, symbol: str) -> AsyncIterator[float]:
        ...


class AlpacaProvider(MarketDataProvider):
    """Alpaca Markets — US equities and crypto."""

    def __init__(self, api_key: str, secret_key: str, base_url: str):
        self.api_key = api_key
        self.secret_key = secret_key
        self.base_url = base_url
        self._headers = {
            "APCA-API-KEY-ID": api_key,
            "APCA-API-SECRET-KEY": secret_key,
        }

    async def get_candles(self, symbol: str, timeframe: str, limit: int = 300) -> list[OHLCV]:
        import aiohttp
        tf_map = {"1m": "1Min", "5m": "5Min", "15m": "15Min", "1h": "1Hour", "1d": "1Day"}
        tf = tf_map.get(timeframe, "1Day")

        url = f"https://data.alpaca.markets/v2/stocks/{symbol}/bars"
        params = {"timeframe": tf, "limit": limit, "adjustment": "all"}

        async with aiohttp.ClientSession(headers=self._headers) as session:
            async with session.get(url, params=params) as resp:
                resp.raise_for_status()
                data = await resp.json()

        return [
            OHLCV(
                timestamp=_parse_ts(bar["t"]),
                open=bar["o"], high=bar["h"], low=bar["l"],
                close=bar["c"], volume=bar["v"],
            )
            for bar in data.get("bars", [])
        ]

    async def get_current_price(self, symbol: str) -> float:
        import aiohttp
        url = f"https://data.alpaca.markets/v2/stocks/{symbol}/quotes/latest"
        async with aiohttp.ClientSession(headers=self._headers) as session:
            async with session.get(url) as resp:
                resp.raise_for_status()
                data = await resp.json()
        q = data.get("quote", {})
        return (q.get("ap", 0) + q.get("bp", 0)) / 2 or q.get("ap", 0)

    async def get_order_book(self, symbol: str, depth: int = 20) -> OrderBook:
        # Alpaca doesn't provide L2 for free — return synthetic from quote
        price = await self.get_current_price(symbol)
        bids = [OrderBookLevel(price=price * (1 - 0.001 * i), size=100.0) for i in range(depth)]
        asks = [OrderBookLevel(price=price * (1 + 0.001 * i), size=100.0) for i in range(depth)]
        return OrderBook(bids=bids, asks=asks, timestamp=time.time())

    async def stream_price(self, symbol: str) -> AsyncIterator[float]:
        """WebSocket price stream — simplified polling fallback shown here."""
        while True:
            yield await self.get_current_price(symbol)
            await asyncio.sleep(1)


class BinanceProvider(MarketDataProvider):
    """Binance — crypto spot, falling back to Yahoo Finance for Forex currency pairs."""

    BASE = "https://api.binance.com"
    FOREX_PAIRS = {"EURUSD", "GBPUSD", "USDJPY", "AUDUSD", "USDCAD", "EURGBP", "EURJPY"}

    def __init__(self, api_key: str = "", secret: str = ""):
        self.api_key = api_key
        self.secret = secret

    def _normalize_symbol(self, symbol: str) -> str:
        return symbol.replace("/", "").replace("-", "").replace("=", "").upper()

    async def get_candles(self, symbol: str, timeframe: str, limit: int = 300) -> list[OHLCV]:
        symbol = self._normalize_symbol(symbol)
        if symbol in self.FOREX_PAIRS:
            return await self._get_yahoo_candles(symbol, timeframe, limit)

        import aiohttp
        url = f"{self.BASE}/api/v3/klines"
        params = {"symbol": symbol, "interval": timeframe, "limit": min(limit, 1000)}
        async with aiohttp.ClientSession() as session:
            async with session.get(url, params=params) as resp:
                data = await resp.json()

        return [
            OHLCV(
                timestamp=row[0] / 1000,
                open=float(row[1]), high=float(row[2]),
                low=float(row[3]), close=float(row[4]),
                volume=float(row[5]),
            )
            for row in data
        ]

    async def get_current_price(self, symbol: str) -> float:
        symbol = self._normalize_symbol(symbol)
        if symbol in self.FOREX_PAIRS:
            return await self._get_yahoo_current_price(symbol)

        import aiohttp
        async with aiohttp.ClientSession() as session:
            async with session.get(f"{self.BASE}/api/v3/ticker/price", params={"symbol": symbol}) as resp:
                data = await resp.json()
        return float(data["price"])

    async def get_order_book(self, symbol: str, depth: int = 20) -> OrderBook:
        symbol = self._normalize_symbol(symbol)
        if symbol in self.FOREX_PAIRS:
            return await self._get_yahoo_order_book(symbol, depth)

        import aiohttp
        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"{self.BASE}/api/v3/depth", params={"symbol": symbol, "limit": depth}
            ) as resp:
                data = await resp.json()

        bids = [OrderBookLevel(price=float(b[0]), size=float(b[1])) for b in data["bids"]]
        asks = [OrderBookLevel(price=float(a[0]), size=float(a[1])) for a in data["asks"]]
        return OrderBook(bids=bids, asks=asks, timestamp=time.time())

    async def stream_price(self, symbol: str) -> AsyncIterator[float]:
        symbol = self._normalize_symbol(symbol)
        if symbol in self.FOREX_PAIRS:
            while True:
                yield await self._get_yahoo_current_price(symbol)
                await asyncio.sleep(1)

        import aiohttp
        ws_url = f"wss://stream.binance.com:9443/ws/{symbol.lower()}@trade"
        async with aiohttp.ClientSession() as session:
            async with session.ws_connect(ws_url) as ws:
                async for msg in ws:
                    if msg.type == aiohttp.WSMsgType.TEXT:
                        import json
                        data = json.loads(msg.data)
                        yield float(data["p"])

    async def _get_yahoo_candles(self, symbol: str, timeframe: str, limit: int) -> list[OHLCV]:
        import aiohttp
        tf_map = {"1m": ("1m", "2d"), "5m": ("5m", "5d"), "15m": ("15m", "10d"), "1h": ("1h", "30d"), "1d": ("1d", "365d")}
        interval, range_val = tf_map.get(timeframe, ("1h", "30d"))
        
        yahoo_symbol = f"{symbol}=X"
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{yahoo_symbol}"
        params = {"interval": interval, "range": range_val}
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
        
        async with aiohttp.ClientSession(headers=headers) as session:
            async with session.get(url, params=params) as resp:
                resp.raise_for_status()
                data = await resp.json()
                
        result = data["chart"]["result"][0]
        timestamps = result.get("timestamp", [])
        quote = result["indicators"]["quote"][0]
        
        candles = []
        for i in range(len(timestamps)):
            o = quote["open"][i]
            h = quote["high"][i]
            l = quote["low"][i]
            c = quote["close"][i]
            v = quote["volume"][i] or 0.0
            
            if o is not None and h is not None and l is not None and c is not None:
                candles.append(OHLCV(
                    timestamp=float(timestamps[i]),
                    open=float(o), high=float(h),
                    low=float(l), close=float(c),
                    volume=float(v),
                ))
        return candles[-limit:]

    async def _get_yahoo_current_price(self, symbol: str) -> float:
        candles = await self._get_yahoo_candles(symbol, "1m", 1)
        if candles:
            return candles[-1].close
        return 0.0

    async def _get_yahoo_order_book(self, symbol: str, depth: int = 20) -> OrderBook:
        price = await self._get_yahoo_current_price(symbol)
        bids = [OrderBookLevel(price=price * (1 - 0.0001 * (i + 1)), size=1.0) for i in range(depth)]
        asks = [OrderBookLevel(price=price * (1 + 0.0001 * (i + 1)), size=1.0) for i in range(depth)]
        return OrderBook(bids=bids, asks=asks, timestamp=time.time())


_NSE_SECURITY_ID_MAP: dict[str, str] = {
    # NSE Equities
    "RELIANCE":    "1333",
    "TCS":         "11536",
    "INFY":        "1594",
    "HDFCBANK":    "1348",
    "ICICIBANK":   "4963",
    "HINDUNILVR":  "1394",
    "SBIN":        "3045",
    "BAJFINANCE":  "317",
    "WIPRO":       "3787",
    "TATAMOTORS":  "3456",
    "AXISBANK":    "5900",
    "KOTAKBANK":   "1922",
    "MARUTI":      "10999",
    "NTPC":        "11630",
    "ONGC":        "11543",
    "BHARTIARTL":  "10604",
    "ASIANPAINT":  "236",
    "LT":          "11483",
    "TITAN":       "3506",
    "SUNPHARMA":   "3351",
    "HCLTECH":     "1363",
    "TATASTEEL":   "3499",
    "BAJAJFINSV":  "16669",
    "NESTLEIND":   "17963",
    "POWERGRID":   "14977",
    "DIVISLAB":    "15414",
    "DRREDDY":     "881",
    "EICHERMOT":   "910",
    "GRASIM":      "1232",
    "JSWSTEEL":    "11723",
    "ADANIENT":    "1253",
    "ADANIPORTS":  "15083",
    "ULTRACEMCO":  "2344",
    # Indices (IDX_I exchange, instrument_type=INDEX)
    "NIFTY":       "13",
    "NIFTY50":     "13",
    "BANKNIFTY":   "25",
    "FINNIFTY":    "27",
    "NIFTYNXT50":  "26",
    "MIDCPNIFTY":  "442",
}

# Symbols that map to the IDX_I exchange (Dhan index segment)
_INDEX_SYMBOLS = frozenset({"NIFTY", "NIFTY50", "BANKNIFTY", "FINNIFTY", "NIFTYNXT50", "MIDCPNIFTY"})


class DhanProvider(MarketDataProvider):
    """
    Dhan market-data provider for Indian markets (NSE/BSE equities, F&O, indices).

    Candle intervals supported by Dhan historical API:
      intraday  → 1, 5, 15, 25, 60  (minutes)
      daily     → "D"

    Timeframe strings accepted: "1m", "5m", "15m", "25m", "1h", "1d"

    Symbol resolution: pass a numeric security_id directly or a ticker like
    "RELIANCE" — resolved via the static NSE instrument map and cached.
    Index symbols (NIFTY, BANKNIFTY, etc.) automatically use the IDX_I exchange.
    """

    _TF_MAP = {
        "1m":  ("intraday", 1),
        "5m":  ("intraday", 5),
        "15m": ("intraday", 15),
        "25m": ("intraday", 25),
        "1h":  ("intraday", 60),
        "1d":  ("daily",    "D"),
    }

    def __init__(
        self,
        client_id: str,
        access_token: str,
        default_exchange: str = "NSE_EQ",
        instrument_type: str = "EQUITY",
    ):
        try:
            import dhanhq as _dh
            ctx = _dh.DhanContext(client_id, access_token)
            self._dhan = _dh.dhanhq(ctx)
        except ImportError:
            raise RuntimeError("dhanhq is not installed — run `pip install dhanhq`")
        self._default_exchange = default_exchange
        self._instrument_type = instrument_type
        self._symbol_cache: dict[str, str] = {}

    def _resolve_security_id(self, symbol: str) -> str:
        if symbol.lstrip("-").isdigit():
            return symbol
        upper = symbol.upper()
        if upper in self._symbol_cache:
            return self._symbol_cache[upper]
        sid = _NSE_SECURITY_ID_MAP.get(upper)
        if sid:
            self._symbol_cache[upper] = sid
            return sid
        return symbol

    def _exchange_and_itype(self, symbol: str) -> tuple[str, str]:
        """Return (exchange_segment, instrument_type) for a symbol."""
        upper = symbol.upper()
        if upper in _INDEX_SYMBOLS:
            return "IDX_I", "INDEX"
        return self._default_exchange, self._instrument_type

    async def get_candles(self, symbol: str, timeframe: str, limit: int = 300) -> list[OHLCV]:
        import asyncio
        from datetime import datetime, timedelta

        security_id = self._resolve_security_id(symbol)
        exchange, itype = self._exchange_and_itype(symbol)
        tf_type, tf_interval = self._TF_MAP.get(timeframe, ("intraday", 60))

        now = datetime.now()
        if tf_type == "daily":
            from_dt = now - timedelta(days=limit * 2)
        else:
            minutes_needed = limit * int(tf_interval)
            from_dt = now - timedelta(minutes=minutes_needed * 1.5 + 60)

        from_date = from_dt.strftime("%Y-%m-%d")
        to_date = now.strftime("%Y-%m-%d")

        loop = asyncio.get_event_loop()
        try:
            if tf_type == "daily":
                raw = await loop.run_in_executor(
                    None,
                    lambda: self._dhan.historical_daily_data(
                        security_id, exchange, itype, from_date, to_date,
                    ),
                )
            else:
                raw = await loop.run_in_executor(
                    None,
                    lambda: self._dhan.intraday_minute_data(
                        security_id, exchange, itype, from_date, to_date,
                        interval=int(tf_interval),
                    ),
                )
        except Exception as e:
            raise RuntimeError(f"DhanProvider.get_candles failed for {symbol}: {e}") from e

        raw_data = raw.get("data") if isinstance(raw, dict) else None
        data = raw_data if isinstance(raw_data, dict) else {}
        if not data and isinstance(raw, dict) and raw.get("status") == "failure":
            raise RuntimeError(f"Dhan API error: {raw.get('remarks', raw)}")
        opens  = data.get("open",      [])
        highs  = data.get("high",      [])
        lows   = data.get("low",       [])
        closes = data.get("close",     [])
        vols   = data.get("volume",    [0] * len(opens))
        times  = data.get("timestamp", [])

        candles = []
        for i in range(len(opens)):
            try:
                ts_val = times[i] if i < len(times) else 0
                ts = _parse_ts(ts_val) if isinstance(ts_val, str) else float(ts_val)
                candles.append(OHLCV(
                    timestamp=ts,
                    open=float(opens[i]),
                    high=float(highs[i]),
                    low=float(lows[i]),
                    close=float(closes[i]),
                    volume=float(vols[i]) if i < len(vols) else 0.0,
                ))
            except Exception:
                continue

        return candles[-limit:]

    async def get_current_price(self, symbol: str) -> float:
        import asyncio
        security_id = self._resolve_security_id(symbol)
        exchange, _ = self._exchange_and_itype(symbol)
        loop = asyncio.get_event_loop()
        try:
            raw = await loop.run_in_executor(
                None,
                lambda: self._dhan.quote_data({exchange: [security_id]}),
            )
            if isinstance(raw, dict) and raw.get("status") == "success":
                seg_data = raw.get("data", {}).get(exchange, {})
                entry = seg_data.get(security_id) or (list(seg_data.values())[0] if seg_data else {})
                ltp = entry.get("last_price", entry.get("ltp", entry.get("close", 0)))
                if ltp:
                    return float(ltp)
        except Exception:
            pass
        candles = await self.get_candles(symbol, "1d", 1)
        return candles[-1].close if candles else 0.0

    async def get_order_book(self, symbol: str, depth: int = 20) -> OrderBook:
        # Dhan v2 has no public L2 orderbook API — return synthetic from LTP
        price = await self.get_current_price(symbol)
        bids = [OrderBookLevel(price=price * (1 - 0.0001 * (i + 1)), size=100.0) for i in range(depth)]
        asks = [OrderBookLevel(price=price * (1 + 0.0001 * (i + 1)), size=100.0) for i in range(depth)]
        return OrderBook(bids=bids, asks=asks, timestamp=time.time())

    async def stream_price(self, symbol: str) -> AsyncIterator[float]:
        while True:
            yield await self.get_current_price(symbol)
            await asyncio.sleep(1)


class MockProvider(MarketDataProvider):
    """
    Deterministic mock for testing — generates realistic synthetic OHLCV using GBM.
    """

    def __init__(self, seed: int = 42):
        import numpy as np
        self._rng = np.random.default_rng(seed)

    async def get_candles(self, symbol: str, timeframe: str, limit: int = 300) -> list[OHLCV]:
        import numpy as np

        price = 50000.0 if "BTC" in symbol else 100.0
        mu = 0.0001
        sigma = 0.015
        candles = []
        ts = time.time() - limit * 60

        for i in range(limit):
            ret = self._rng.normal(mu, sigma)
            open_ = price
            close = price * (1 + ret)
            high = max(open_, close) * (1 + abs(self._rng.normal(0, 0.003)))
            low = min(open_, close) * (1 - abs(self._rng.normal(0, 0.003)))
            vol = self._rng.uniform(1000, 5000) * (1 + abs(ret) * 10)
            candles.append(OHLCV(ts + i * 60, round(open_, 4), round(high, 4), round(low, 4), round(close, 4), round(vol, 2)))
            price = close

        return candles

    async def get_current_price(self, symbol: str) -> float:
        candles = await self.get_candles(symbol, "1m", 1)
        return candles[-1].close

    async def get_order_book(self, symbol: str, depth: int = 20) -> OrderBook:
        price = await self.get_current_price(symbol)
        bids = [OrderBookLevel(price=price * (1 - 0.0005 * (i + 1)), size=float(self._rng.uniform(0.5, 5))) for i in range(depth)]
        asks = [OrderBookLevel(price=price * (1 + 0.0005 * (i + 1)), size=float(self._rng.uniform(0.5, 5))) for i in range(depth)]
        return OrderBook(bids=bids, asks=asks, timestamp=time.time())

    async def stream_price(self, symbol: str) -> AsyncIterator[float]:
        while True:
            yield await self.get_current_price(symbol)
            await asyncio.sleep(1)


def _parse_ts(ts_str: str) -> float:
    from datetime import datetime, timezone
    return datetime.fromisoformat(ts_str.replace("Z", "+00:00")).timestamp()
