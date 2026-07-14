"""
Market Regime Detector
Classifies current market state as: bull / bear / sideways / volatile
Uses volatility, trend strength, and correlation to VIX proxy.
Runs on every analysis cycle to inform all agents.
"""
import numpy as np
from core.agents.base_agent import OHLCV


_INDEX_SYMBOLS = {"NIFTY", "BANKNIFTY", "FINNIFTY", "NIFTYNXT50", "MIDCPNIFTY", "SENSEX"}


def detect_regime(candles: list[OHLCV], vix: float = 20.0, asset: str = "") -> str:
    """
    Returns one of: "bull", "bear", "sideways", "volatile"

    Decision logic:
    - volatile if: VIX above threshold OR recent HV > 40% annualized
      VIX threshold: 20 for Indian indices (INDIAVIX scale), 25 for all others
    - bull if: 50-period return > 5% AND trend strong
    - bear if: 50-period return < -5% AND trend strong
    - sideways otherwise
    """
    if len(candles) < 20:
        return "unknown"

    closes = np.array([c.close for c in candles])
    returns = np.diff(closes) / closes[:-1]

    # Historical volatility (20-day annualized)
    hv = returns[-20:].std() * np.sqrt(252) * 100 if len(returns) >= 20 else 0.0

    # Trend: compare last close to 50-period mean
    lookback = min(50, len(closes) - 1)
    period_return = (closes[-1] - closes[-lookback]) / closes[-lookback] * 100

    # Trend strength: R² of linear regression on closes
    x = np.arange(lookback)
    y = closes[-lookback:]
    if len(x) > 2:
        corr = np.corrcoef(x, y)[0, 1]
        trend_strength = abs(corr)
    else:
        trend_strength = 0.0

    # Indian indices use INDIAVIX scale: >20 is already elevated (vs US VIX >25)
    is_indian_index = asset.upper() in _INDEX_SYMBOLS
    vix_threshold = 20 if is_indian_index else 25

    # Regime classification
    if vix > vix_threshold or hv > 40:
        return "volatile"

    if period_return > 5 and trend_strength > 0.7:
        return "bull"

    if period_return < -5 and trend_strength > 0.7:
        return "bear"

    return "sideways"


def multi_timeframe_regimes(
    candles_by_tf: dict[str, list[OHLCV]], vix: float = 20.0, asset: str = ""
) -> dict[str, str]:
    """Detect regime per timeframe. Use for multi-timeframe validation."""
    return {tf: detect_regime(candles, vix, asset=asset) for tf, candles in candles_by_tf.items()}


def regime_consensus(regimes: dict[str, str]) -> str:
    """
    If shorter and longer timeframes agree → high conviction.
    If they disagree → return 'unknown' to add uncertainty.
    """
    values = list(regimes.values())
    if len(set(values)) == 1:
        return values[0]

    # Prefer the longer timeframe regime (more reliable)
    tf_order = ["1d", "4h", "1h", "15m", "5m", "1m"]
    for tf in tf_order:
        if tf in regimes:
            return regimes[tf]

    return values[0]
