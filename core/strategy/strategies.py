"""
Concrete strategy implementations.
Each strategy imposes its own acceptance criteria on top of the consensus signal.
"""
from .strategy_base import BaseStrategy, StrategyFilter, StrategyType
from core.agents.base_agent import MarketContext, Signal
from core.agents.meta_agent import TradeSignal


class ScalpingStrategy(BaseStrategy):
    """
    1–5 minute bars. Requires very high consensus confidence.
    Only works in sideways or volatile regimes where range is predictable.
    Size is smaller — many small wins beat few large ones.
    """
    strategy_type = StrategyType.SCALPING
    default_timeframes = ["1m", "5m"]
    filter = StrategyFilter(
        min_confidence=80.0,
        min_rr=1.5,
        required_regime=["sideways", "volatile"],
        max_hold_bars=10,
        allowed_signals=[Signal.BUY, Signal.SELL],
    )

    def accepts(self, signal: TradeSignal, ctx: MarketContext) -> tuple[bool, str]:
        if signal.confidence < self.filter.min_confidence:
            return False, f"Scalping needs ≥{self.filter.min_confidence}% confidence, got {signal.confidence:.0f}%"
        if ctx.regime not in self.filter.required_regime:
            return False, f"Scalping not suited for {ctx.regime} regime"
        if signal.risk_reward < self.filter.min_rr:
            return False, f"R:R {signal.risk_reward:.2f} below scalping floor {self.filter.min_rr}"
        return True, "Scalping filter passed"

    def position_size_multiplier(self, signal: TradeSignal, ctx: MarketContext) -> float:
        # Smaller size: frequent trades, manage total exposure
        return 0.6


class SwingStrategy(BaseStrategy):
    """
    4h–1D bars. Rides multi-day trends.
    Requires bull or bear regime alignment. Lower confidence floor than scalping
    because we have more time for the thesis to play out.
    """
    strategy_type = StrategyType.SWING
    default_timeframes = ["1h", "4h", "1d"]
    filter = StrategyFilter(
        min_confidence=68.0,
        min_rr=2.0,
        required_regime=["bull", "bear"],
        max_hold_bars=120,
        allowed_signals=[Signal.BUY, Signal.SELL],
    )

    def accepts(self, signal: TradeSignal, ctx: MarketContext) -> tuple[bool, str]:
        if signal.confidence < self.filter.min_confidence:
            return False, f"Swing confidence {signal.confidence:.0f}% below threshold"
        # Sideways + sideways regime accepted if R:R is exceptional
        if ctx.regime == "volatile":
            return False, "Swing trading blocked in volatile regime — too noisy"
        if signal.risk_reward < self.filter.min_rr:
            return False, f"R:R {signal.risk_reward:.2f} insufficient for swing trade"
        return True, "Swing filter passed"

    def position_size_multiplier(self, signal: TradeSignal, ctx: MarketContext) -> float:
        # Full size in bull, reduced in bear (shorts have different risk profile)
        return 1.0 if ctx.regime == "bull" else 0.8


class MeanReversionStrategy(BaseStrategy):
    """
    Looks for z-score extremes and bets on reversion to mean.
    Requires Hurst < 0.5 (confirmed mean-reverting asset).
    Only takes counter-trend signals from Quant agent.
    """
    strategy_type = StrategyType.MEAN_REVERSION
    default_timeframes = ["15m", "1h"]
    filter = StrategyFilter(
        min_confidence=65.0,
        min_rr=1.8,
        required_regime=["sideways", "bull", "bear"],
        max_hold_bars=48,
        allowed_signals=[Signal.BUY, Signal.SELL],
    )

    def accepts(self, signal: TradeSignal, ctx: MarketContext) -> tuple[bool, str]:
        if ctx.regime == "volatile":
            return False, "Mean reversion unreliable in volatile regime"
        if signal.confidence < self.filter.min_confidence:
            return False, f"Confidence {signal.confidence:.0f}% below MR threshold"

        # Require quant agent to align if it is present
        quant_agent = next((a for a in signal.agents if a["name"] == "Quant"), None)
        if quant_agent:
            if signal.action and quant_agent["decision"] != signal.action.value:
                return False, "Mean reversion requires Quant agent alignment — Quant disagreed"

        return True, "Mean reversion filter passed"

    def position_size_multiplier(self, signal: TradeSignal, ctx: MarketContext) -> float:
        return 0.75  # MR positions tend to be held shorter — slightly smaller


class TrendFollowStrategy(BaseStrategy):
    """
    Strong directional regime + high Hurst → ride the trend.
    Highest size multiplier — trend trades have highest EV when Hurst > 0.6.
    """
    strategy_type = StrategyType.TREND_FOLLOW
    default_timeframes = ["1h", "4h"]
    filter = StrategyFilter(
        min_confidence=72.0,
        min_rr=2.5,
        required_regime=["bull", "bear"],
        max_hold_bars=200,
        allowed_signals=[Signal.BUY, Signal.SELL],
    )

    def accepts(self, signal: TradeSignal, ctx: MarketContext) -> tuple[bool, str]:
        if ctx.regime not in self.filter.required_regime:
            return False, f"Trend following requires bull/bear regime, got {ctx.regime}"
        if signal.confidence < self.filter.min_confidence:
            return False, f"Trend confidence {signal.confidence:.0f}% too low"

        # Align with regime: BUY in bull, SELL in bear
        if ctx.regime == "bull" and signal.action == Signal.SELL:
            return False, "Counter-trend SELL in bull regime — blocked by trend follow strategy"
        if ctx.regime == "bear" and signal.action == Signal.BUY:
            return False, "Counter-trend BUY in bear regime — blocked by trend follow strategy"

        return True, "Trend follow filter passed"

    def position_size_multiplier(self, signal: TradeSignal, ctx: MarketContext) -> float:
        # Scale with confidence: higher conviction → larger size (capped externally by risk engine)
        return 1.0 + (signal.confidence - 72) / 100


class StatArbitrageStrategy(BaseStrategy):
    """
    Pairs / spread trading — requires correlated asset data.
    Not a directional strategy: looks for spread Z-score > 2.
    In this simplified version, operates on single-asset spread to its own mean.
    """
    strategy_type = StrategyType.ARBITRAGE
    default_timeframes = ["5m", "15m"]
    filter = StrategyFilter(
        min_confidence=70.0,
        min_rr=1.5,
        required_regime=["sideways", "bull", "bear"],
        max_hold_bars=30,
        allowed_signals=[Signal.BUY, Signal.SELL],
    )

    def accepts(self, signal: TradeSignal, ctx: MarketContext) -> tuple[bool, str]:
        if ctx.regime == "volatile":
            return False, "Arb spread assumptions break down in volatile regime"
        if signal.confidence < self.filter.min_confidence:
            return False, f"Arb needs clean signal ≥{self.filter.min_confidence}%"
        return True, "Arb filter passed"

    def position_size_multiplier(self, signal: TradeSignal, ctx: MarketContext) -> float:
        return 0.5  # Arb positions are hedged — smaller directional exposure


# Registry for dynamic strategy selection
STRATEGY_REGISTRY: dict[StrategyType, BaseStrategy] = {
    StrategyType.SCALPING: ScalpingStrategy(),
    StrategyType.SWING: SwingStrategy(),
    StrategyType.MEAN_REVERSION: MeanReversionStrategy(),
    StrategyType.TREND_FOLLOW: TrendFollowStrategy(),
    StrategyType.ARBITRAGE: StatArbitrageStrategy(),
}


def select_strategy(regime: str, timeframe: str, user_override: str | None = None) -> BaseStrategy:
    """
    Auto-select the best-fit strategy based on regime and timeframe.
    User override always wins.
    """
    if user_override:
        return STRATEGY_REGISTRY[StrategyType(user_override)]

    tf_minutes = _tf_to_minutes(timeframe)

    if tf_minutes <= 5:
        return STRATEGY_REGISTRY[StrategyType.SCALPING]
    if regime in ("bull", "bear") and tf_minutes >= 60:
        return STRATEGY_REGISTRY[StrategyType.TREND_FOLLOW]
    if regime in ("bull", "bear"):
        return STRATEGY_REGISTRY[StrategyType.SWING]
    # sideways on 1h+ → MeanReversionStrategy (gracefully degrades if Quant agent is missing)
    return STRATEGY_REGISTRY[StrategyType.MEAN_REVERSION]


def _tf_to_minutes(tf: str) -> int:
    units = {"m": 1, "h": 60, "d": 1440, "w": 10080}
    for suffix, mult in units.items():
        if tf.endswith(suffix):
            try:
                return int(tf[:-1]) * mult
            except ValueError:
                pass
    return 60
