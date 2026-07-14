import numpy as np
from datetime import datetime
from typing import Optional, Tuple
from core.agents.base_agent import MarketContext, Signal, TradeHypothesis
from core.strategy.strategy_base import BaseStrategy, StrategyType
from core.strategy.strategies import STRATEGY_REGISTRY
from core.agents.quant_agent import _hurst_exponent, _historical_volatility, _returns

_INDEX_SYMBOLS = {"NIFTY", "BANKNIFTY", "FINNIFTY", "NIFTYNXT50", "MIDCPNIFTY", "SENSEX"}


class StrategySelector:
    """
    Selects exactly ONE strategy per cycle using:
    - Current regime
    - Hurst exponent
    - Realized volatility percentile
    - Time-of-day session filter
    
    Emits a TradeHypothesis containing the direction bias, entry zones, stop/take profit thresholds,
    and agent evidence mappings.
    """

    def __init__(self):
        self._pinned_strategy: Optional[StrategyType] = None

    def pin_strategy(self, strategy_type: Optional[StrategyType]) -> None:
        """Manually overrides auto-selection until unpinned."""
        self._pinned_strategy = strategy_type

    def get_pinned_strategy(self) -> Optional[StrategyType]:
        return self._pinned_strategy

    def select(self, ctx: MarketContext) -> Tuple[BaseStrategy, str, float, float]:
        """
        Picks exactly ONE strategy using the selection matrix.
        Returns (selected_strategy, reason, hurst, vol_percentile)
        """
        if self._pinned_strategy and self._pinned_strategy in STRATEGY_REGISTRY:
            return STRATEGY_REGISTRY[self._pinned_strategy], "User pinned override", 0.5, 0.5

        closes = np.array([c.close for c in ctx.candles])
        
        # 1. Compute Hurst Exponent
        hurst = 0.5
        if len(closes) >= 20:
            hurst = _hurst_exponent(closes[-100:])

        # 2. Compute Realized Volatility Percentile
        returns = _returns(closes)
        vol_percentile = 0.5
        if len(returns) >= 20:
            current_vol = _historical_volatility(returns)
            # Calculate rolling volatility distribution
            vols = []
            for i in range(len(returns) - 20 + 1):
                vols.append(returns[i:i+20].std() * np.sqrt(252))
            if vols:
                vol_percentile = sum(1 for v in vols if v <= current_vol) / len(vols)

        # 3. Time-of-day session check
        session = "US_SESSION"
        if len(ctx.candles) > 0:
            from datetime import timezone
            dt = datetime.fromtimestamp(ctx.candles[-1].timestamp, timezone.utc)
            hour = dt.hour
            if 9 <= hour < 16:
                session = "US_SESSION"
            elif 0 <= hour < 8:
                session = "ASIA_SESSION"
            else:
                session = "EU_SESSION"

        regime = ctx.regime.lower()

        # Selection Matrix
        # Indices mean-revert ~60% of the time regardless of Hurst/regime — hardwire MR.
        # Swing/TrendFollow on an index produces false trending signals and gets blocked
        # by the swing confidence floor repeatedly.
        if ctx.asset.upper() in _INDEX_SYMBOLS:
            selected_type = StrategyType.MEAN_REVERSION
            reason = f"Index instrument — mean reversion strategy (Hurst={hurst:.2f}, regime={regime})"
        elif vol_percentile > 0.95:
            selected_type = StrategyType.SCALPING
            reason = f"Extreme volatility percentile ({vol_percentile:.2f} > 0.95) - Scalping mode triggered (tight stop loss)"
        elif regime == "volatile":
            selected_type = StrategyType.SCALPING
            reason = f"Volatile market regime - Scalping strategy selected"
        elif hurst > 0.55 and regime in ("bull", "bear"):
            selected_type = StrategyType.TREND_FOLLOW
            reason = f"Trending market: Hurst exponent {hurst:.2f} > 0.55 inside {regime} regime"
        elif regime == "sideways":
            selected_type = StrategyType.MEAN_REVERSION
            reason = f"Sideways regime — Mean reversion strategy (Hurst {hurst:.2f}, Vol {vol_percentile:.2f})"
        else:
            selected_type = StrategyType.SWING
            reason = f"Default Swing strategy selected (Regime: {regime}, Hurst: {hurst:.2f}, Vol: {vol_percentile:.2f})"

        strategy = STRATEGY_REGISTRY.get(selected_type, STRATEGY_REGISTRY[StrategyType.SWING])
        return strategy, reason, hurst, vol_percentile

    def emit_hypothesis(self, strategy: BaseStrategy, ctx: MarketContext, hurst: float, vol_percentile: float) -> TradeHypothesis:
        """
        Emits a structured trade hypothesis for the selected strategy.
        """
        current_price = ctx.current_price
        regime = ctx.regime.lower()

        # Determine bias
        direction = Signal.HOLD
        if strategy.strategy_type == StrategyType.TREND_FOLLOW:
            direction = Signal.BUY if regime == "bull" else Signal.SELL
        elif strategy.strategy_type == StrategyType.MEAN_REVERSION:
            closes = np.array([c.close for c in ctx.candles])
            mean = closes[-20:].mean() if len(closes) >= 20 else current_price
            direction = Signal.BUY if current_price < mean else Signal.SELL
        elif strategy.strategy_type == StrategyType.SCALPING:
            direction = Signal.BUY if regime == "bull" else Signal.SELL
        else:
            direction = Signal.BUY if regime == "bull" else Signal.SELL

        # Entry, Stop Loss, and Take Profit
        if direction == Signal.BUY:
            entry_zone = (current_price * 0.995, current_price * 1.002)
            suggested_sl = current_price * 0.985
            suggested_tp = current_price * 1.03
        else:
            entry_zone = (current_price * 0.998, current_price * 1.005)
            suggested_sl = current_price * 1.015
            suggested_tp = current_price * 0.97

        confirming = []
        refuting = []

        if strategy.strategy_type == StrategyType.TREND_FOLLOW:
            confirming = ["Technical EMA stack alignment", "Quant Hurst > 0.55"]
            refuting = ["OrderFlow POC resistance break", "DevilsAdvocate overextension flag"]
        elif strategy.strategy_type == StrategyType.MEAN_REVERSION:
            confirming = ["Quant z-score extreme", "OrderFlow pivot support zone"]
            refuting = ["Technical strong breakout momentum", "Quant Hurst > 0.55"]
        else:
            confirming = ["Technical RSI divergence", "Sentiment bullish consensus"]
            refuting = ["DevilsAdvocate macro shock flag"]

        return TradeHypothesis(
            strategy_name=strategy.strategy_type.value,
            direction_bias=direction,
            entry_zone=entry_zone,
            suggested_sl=suggested_sl,
            suggested_tp=suggested_tp,
            holding_horizon=f"{strategy.filter.max_hold_bars} bars",
            confirming_evidence=confirming,
            refuting_evidence=refuting,
        )
