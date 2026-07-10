"""
Strategy Layer
Each strategy defines: which assets to watch, which timeframes to use,
and post-consensus filters specific to that strategy type.
The orchestrator picks a strategy per asset and routes accordingly.
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum
from typing import Any

from core.agents.base_agent import MarketContext, Signal
from core.agents.meta_agent import TradeSignal


class StrategyType(str, Enum):
    SCALPING = "scalping"
    SWING = "swing"
    MEAN_REVERSION = "mean_reversion"
    ARBITRAGE = "arbitrage"
    TREND_FOLLOW = "trend_follow"


@dataclass
class StrategyFilter:
    """Post-consensus gate specific to the strategy."""
    min_confidence: float
    min_rr: float                    # minimum risk:reward
    required_regime: list[str]       # which regimes this strategy works in
    max_hold_bars: int               # maximum hold duration
    allowed_signals: list[Signal]    # BUY only, SELL only, or both


class BaseStrategy(ABC):
    strategy_type: StrategyType
    default_timeframes: list[str]
    filter: StrategyFilter

    @abstractmethod
    def accepts(self, signal: TradeSignal, ctx: MarketContext) -> tuple[bool, str]:
        """Returns (pass, reason). Called after meta-agent consensus."""
        ...

    @abstractmethod
    def position_size_multiplier(self, signal: TradeSignal, ctx: MarketContext) -> float:
        """Override position size relative to base risk engine output."""
        ...

    def describe(self) -> dict:
        return {
            "type": self.strategy_type.value,
            "timeframes": self.default_timeframes,
            "min_confidence": self.filter.min_confidence,
            "min_rr": self.filter.min_rr,
            "required_regime": self.filter.required_regime,
        }
