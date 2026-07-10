"""
News & Sentiment Data Feed
Aggregates from NewsAPI, Reddit (via Pushshift/PRAW), and Twitter/X.
All fetches are async and cached via Redis with configurable TTL.
"""
import asyncio
import hashlib
import json
import time
from typing import Any

import aiohttp

try:
    import redis.asyncio as aioredis
    _REDIS_AVAILABLE = True
except ImportError:
    _REDIS_AVAILABLE = False


CACHE_TTL = 300  # 5 minutes


class NewsCache:
    """Simple Redis-backed async cache with in-memory fallback."""

    def __init__(self, redis_url: str = ""):
        self._redis: Any = None
        self._memory: dict[str, tuple[str, float]] = {}
        self._redis_url = redis_url

    async def connect(self):
        if _REDIS_AVAILABLE and self._redis_url:
            try:
                self._redis = await aioredis.from_url(self._redis_url)
            except Exception:
                pass  # fall through to memory cache

    async def get(self, key: str) -> dict | None:
        if self._redis:
            try:
                val = await self._redis.get(key)
                return json.loads(val) if val else None
            except Exception:
                pass
        # memory fallback
        if key in self._memory:
            val, ts = self._memory[key]
            if time.time() - ts < CACHE_TTL:
                return json.loads(val)
        return None

    async def set(self, key: str, data: dict) -> None:
        payload = json.dumps(data)
        if self._redis:
            try:
                await self._redis.setex(key, CACHE_TTL, payload)
                return
            except Exception:
                pass
        self._memory[key] = (payload, time.time())

    def _key(self, namespace: str, asset: str) -> str:
        return f"trading_os:{namespace}:{hashlib.md5(asset.encode()).hexdigest()}"


class NewsFeed:
    def __init__(
        self,
        news_api_key: str = "",
        reddit_client_id: str = "",
        reddit_secret: str = "",
        redis_url: str = "",
    ):
        self._news_api_key = news_api_key
        self._reddit_cid = reddit_client_id
        self._reddit_secret = reddit_secret
        self._cache = NewsCache(redis_url)

    async def setup(self):
        await self._cache.connect()

    async def get_news_headlines(self, asset: str, limit: int = 20) -> list[str]:
        cache_key = self._cache._key("news", asset)
        cached = await self._cache.get(cache_key)
        if cached:
            return cached["headlines"]

        headlines = await self._fetch_newsapi(asset, limit)
        await self._cache.set(cache_key, {"headlines": headlines})
        return headlines

    async def get_social_sentiment(self, asset: str) -> dict:
        cache_key = self._cache._key("social", asset)
        cached = await self._cache.get(cache_key)
        if cached:
            return cached

        reddit_data = await self._fetch_reddit(asset)
        sentiment = {"reddit": reddit_data, "timestamp": time.time()}
        await self._cache.set(cache_key, sentiment)
        return sentiment

    async def get_macro_context(self) -> dict:
        """
        Returns macro indicators: VIX, Fed calendar, upcoming events.
        In production: connects to FRED API, Quandl, or Bloomberg Terminal.
        """
        return {
            "vix": 18.5,
            "sp500_1d_change_pct": 0.3,
            "near_fed_event": False,
            "days_to_earnings": 30,
        }

    async def _fetch_newsapi(self, asset: str, limit: int) -> list[str]:
        if not self._news_api_key:
            return _MOCK_HEADLINES.get(asset.split("/")[0], [])

        query = asset.replace("/", " OR ").replace("USDT", "").strip()
        url = "https://newsapi.org/v2/everything"
        params = {
            "q": query,
            "sortBy": "publishedAt",
            "pageSize": limit,
            "language": "en",
            "apiKey": self._news_api_key,
        }
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, params=params, timeout=aiohttp.ClientTimeout(total=5)) as resp:
                    data = await resp.json()
            return [a["title"] for a in data.get("articles", []) if a.get("title")]
        except Exception:
            return []

    async def _fetch_reddit(self, asset: str) -> dict:
        """Fetch Reddit mentions from wallstreetbets / crypto subreddits."""
        ticker = asset.split("/")[0]
        subreddits = "wallstreetbets+investing+CryptoCurrency+Bitcoin"
        url = f"https://www.reddit.com/r/{subreddits}/search.json"
        params = {"q": ticker, "sort": "hot", "limit": 25, "t": "day"}
        headers = {"User-Agent": "TradingOS/1.0"}

        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, params=params, headers=headers,
                                       timeout=aiohttp.ClientTimeout(total=5)) as resp:
                    data = await resp.json()
            posts = data.get("data", {}).get("children", [])
            titles = [p["data"]["title"] for p in posts]
            scores = [p["data"]["score"] for p in posts]
            return {
                "mention_count": len(titles),
                "avg_score": sum(scores) / len(scores) if scores else 0,
                "titles": titles[:10],
            }
        except Exception:
            return {"mention_count": 0, "avg_score": 0, "titles": []}


# Mock headlines for when no API key is configured
_MOCK_HEADLINES: dict[str, list[str]] = {
    "BTC": [
        "Bitcoin surges past $70,000 as institutional demand grows",
        "BlackRock ETF sees record Bitcoin inflows",
        "MicroStrategy adds another 5,000 BTC to treasury",
        "Bitcoin network hash rate hits all-time high",
        "Fed signals no rate cuts — crypto markets respond positively",
    ],
    "ETH": [
        "Ethereum ETF approval drives bullish sentiment",
        "DeFi TVL reaches record highs on Ethereum",
        "Layer-2 adoption accelerating on Ethereum network",
    ],
    "AAPL": [
        "Apple reports record iPhone sales in Q4",
        "Apple Vision Pro supply constraints easing",
        "Warren Buffett increases Apple stake",
    ],
}
