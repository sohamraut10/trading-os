"""
Base contract every agent must implement.
Agents are stateless — all context is passed via MarketContext.
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any
import time
import uuid


class Signal(str, Enum):
    BUY = "BUY"
    SELL = "SELL"
    HOLD = "HOLD"


class AgentName(str, Enum):
    TECHNICAL = "Technical"
    SENTIMENT = "Sentiment"
    QUANT = "Quant"
    ORDER_FLOW = "OrderFlow"
    DEVILS_ADVOCATE = "DevilsAdvocate"
    META = "Meta"


@dataclass
class OHLCV:
    timestamp: float
    open: float
    high: float
    low: float
    close: float
    volume: float


@dataclass
class OrderBookLevel:
    price: float
    size: float


@dataclass
class OrderBook:
    bids: list[OrderBookLevel]
    asks: list[OrderBookLevel]
    timestamp: float


@dataclass
class MarketContext:
    """Immutable snapshot of market state passed to every agent."""
    asset: str
    timeframe: str                         # "1m", "5m", "1h", "1d"
    candles: list[OHLCV]                   # ordered oldest→newest
    current_price: float
    order_book: OrderBook | None = None
    news_headlines: list[str] = field(default_factory=list)
    sentiment_raw: dict[str, Any] = field(default_factory=dict)
    macro_context: dict[str, Any] = field(default_factory=dict)
    portfolio_context: dict[str, Any] = field(default_factory=dict)
    regime: str = "unknown"                # bull / bear / sideways / volatile
    request_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    timestamp: float = field(default_factory=time.time)


@dataclass
class AgentDecision:
    agent_name: AgentName
    signal: Signal
    confidence: float                      # 0–100
    reasoning: str
    indicators: dict[str, Any] = field(default_factory=dict)
    latency_ms: float = 0.0
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "name": self.agent_name.value,
            "decision": self.signal.value,
            "confidence": round(self.confidence, 1),
            "reasoning": self.reasoning,
            "indicators": self.indicators,
            "warnings": self.warnings,
            "latency_ms": round(self.latency_ms, 2),
        }


class BaseAgent(ABC):
    """All agents inherit from this. analyze() must be non-blocking."""

    name: AgentName

    async def analyze(self, ctx: MarketContext) -> AgentDecision:
        t0 = time.perf_counter()
        decision = await self._analyze(ctx)
        decision.latency_ms = (time.perf_counter() - t0) * 1000
        self._validate(decision)
        return decision

    @abstractmethod
    async def _analyze(self, ctx: MarketContext) -> AgentDecision:
        ...

    def _validate(self, d: AgentDecision) -> None:
        if not (0 <= d.confidence <= 100):
            raise ValueError(f"{self.name}: confidence {d.confidence} out of range")
        if not d.reasoning:
            raise ValueError(f"{self.name}: reasoning must not be empty")
