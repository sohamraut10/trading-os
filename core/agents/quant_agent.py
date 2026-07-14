"""
Quant & Statistical Agent
Mean reversion, momentum, volatility regime, and correlation analytics.
All pure NumPy/SciPy — no external calls.
"""
import numpy as np
from dataclasses import dataclass
from scipy import stats
from .base_agent import BaseAgent, AgentDecision, AgentName, MarketContext, Signal, OHLCV
from core.data.instruments import INDEX_UNDERLYINGS


@dataclass
class QuantMetrics:
    z_score: float              # distance from rolling mean in std devs
    hurst_exponent: float       # <0.5 mean-reverting, >0.5 trending, =0.5 random walk
    sharpe_rolling: float       # 30-period rolling Sharpe
    volatility_pct: float       # annualized historical volatility
    momentum_score: float       # rate-of-change composite
    skewness: float
    kurtosis: float
    prob_profit: float          # historical win rate for similar setups
    expected_value: float       # EV based on historical outcomes


def _returns(closes: np.ndarray) -> np.ndarray:
    return np.diff(closes) / closes[:-1]


def _z_score(closes: np.ndarray, window: int = 20) -> float:
    if len(closes) < window:
        return 0.0
    window_data = closes[-window:]
    mean = window_data.mean()
    std = window_data.std()
    return float((closes[-1] - mean) / std) if std != 0 else 0.0


def _hurst_exponent(ts: np.ndarray, lags_range: range = range(2, 20)) -> float:
    """
    R/S analysis to estimate Hurst exponent.
    H < 0.5 → mean-reverting
    H ≈ 0.5 → random walk
    H > 0.5 → trending
    """
    lags = []
    rs_vals = []
    for lag in lags_range:
        if lag >= len(ts):
            continue
        segments = [ts[i:i + lag] for i in range(0, len(ts) - lag, lag)]
        if not segments:
            continue
        rs_list = []
        for seg in segments:
            mean_seg = seg.mean()
            deviation = np.cumsum(seg - mean_seg)
            r = deviation.max() - deviation.min()
            s = seg.std()
            if s > 0:
                rs_list.append(r / s)
        if rs_list:
            lags.append(np.log(lag))
            rs_vals.append(np.log(np.mean(rs_list)))

    if len(lags) < 3:
        return 0.5  # insufficient data → assume random walk

    slope, _, _, _, _ = stats.linregress(lags, rs_vals)
    return float(np.clip(slope, 0.0, 1.0))


def _rolling_sharpe(returns: np.ndarray, window: int = 30, risk_free: float = 0.05 / 252) -> float:
    if len(returns) < window:
        return 0.0
    r = returns[-window:]
    excess = r - risk_free
    std = r.std()
    if std == 0:
        return 0.0
    return float(excess.mean() / std * np.sqrt(252))


def _historical_volatility(returns: np.ndarray, window: int = 20) -> float:
    if len(returns) < window:
        return float(returns.std() * np.sqrt(252)) if len(returns) > 1 else 0.0
    return float(returns[-window:].std() * np.sqrt(252))


def _momentum(closes: np.ndarray) -> float:
    """Composite: average of 5/10/20 period ROC."""
    rocs = []
    for p in [5, 10, 20]:
        if len(closes) > p:
            roc = (closes[-1] - closes[-p - 1]) / closes[-p - 1] * 100
            rocs.append(roc)
    return float(np.mean(rocs)) if rocs else 0.0


def _prob_profit_ev(returns: np.ndarray, z: float) -> tuple[float, float]:
    """
    Given current z-score, find historically similar setups and compute win rate + EV.
    Simplified: look at returns following similar z-score ranges in the historical window.
    """
    if len(returns) < 40:
        return 0.5, 0.0

    # Reconstruct rolling z-scores
    window = 20
    closes_proxy = np.cumprod(1 + np.concatenate([[0], returns]))
    zs = np.array([
        (closes_proxy[i] - closes_proxy[max(0, i - window):i].mean()) /
        (closes_proxy[max(0, i - window):i].std() + 1e-9)
        for i in range(window, len(closes_proxy))
    ])
    forward_returns = returns[window:]

    # Find similar z-score windows (within ±0.5 of current z)
    mask = np.abs(zs[:-1] - z) < 0.5
    similar_returns = forward_returns[mask]

    if len(similar_returns) < 5:
        return 0.5, 0.0

    wins = (similar_returns > 0).mean()
    ev = similar_returns.mean() * 100  # as percentage
    return float(wins), float(ev)


def compute_quant_metrics(candles: list[OHLCV]) -> QuantMetrics:
    closes = np.array([c.close for c in candles])
    returns = _returns(closes)

    z = _z_score(closes)
    prob, ev = _prob_profit_ev(returns, z)

    return QuantMetrics(
        z_score=z,
        hurst_exponent=_hurst_exponent(closes[-100:]) if len(closes) >= 20 else 0.5,
        sharpe_rolling=_rolling_sharpe(returns),
        volatility_pct=_historical_volatility(returns) * 100,
        momentum_score=_momentum(closes),
        skewness=float(stats.skew(returns)) if len(returns) > 3 else 0.0,
        kurtosis=float(stats.kurtosis(returns)) if len(returns) > 3 else 0.0,
        prob_profit=prob,
        expected_value=ev,
    )


_INDEX_SYMBOLS = INDEX_UNDERLYINGS


def _quant_signal(m: QuantMetrics, is_index: bool = False, iv_rank: float = -1.0) -> tuple[Signal, float, str]:
    score_bull = 0.0
    score_bear = 0.0
    reasons = []
    warnings = []

    # Mean reversion play: z-score extremes
    # For indices: require stronger z-score (±2.0 vs ±1.5) since we're buying options
    # and need high conviction — a weak extreme can reverse further on macro noise.
    # Also, if IV rank is known and too low (<60), buying options is expensive relative
    # to realized vol — suppress the signal.
    if m.hurst_exponent < 0.45:
        mr_threshold = 2.0 if is_index else 1.5
        if m.z_score < -mr_threshold:
            score_bull += 25
            reasons.append(
                f"Mean-reversion: z={m.z_score:.2f} (threshold={mr_threshold:.1f}), H={m.hurst_exponent:.2f}"
            )
            if is_index and iv_rank > 75:
                # High vol rank = expensive options; IV crush risk even if direction is right
                score_bull *= 0.7
                warnings.append(
                    f"IV crush risk: vol rank {iv_rank:.0f} > 75 — options expensive, confidence penalized"
                )
        elif m.z_score > mr_threshold:
            score_bear += 25
            reasons.append(
                f"Mean-reversion short: z={m.z_score:.2f} (threshold={mr_threshold:.1f}), H={m.hurst_exponent:.2f}"
            )
            if is_index and iv_rank > 75:
                score_bear *= 0.7
                warnings.append(
                    f"IV crush risk: vol rank {iv_rank:.0f} > 75 — options expensive, confidence penalized"
                )

    # Trend-following: Hurst > 0.55 + momentum
    # For indices: momentum signals are less reliable due to macro sensitivity — halve the score
    elif m.hurst_exponent > 0.55:
        trend_weight = 10 if is_index else 20
        if m.momentum_score > 0.5:
            score_bull += trend_weight
            reasons.append(f"Trend regime H={m.hurst_exponent:.2f}, momentum={m.momentum_score:.2f}%")
        elif m.momentum_score < -0.5:
            score_bear += trend_weight
            reasons.append(f"Downtrend H={m.hurst_exponent:.2f}, momentum={m.momentum_score:.2f}%")

    # Rolling Sharpe — skip for indices (spot Sharpe doesn't reflect options P&L dynamics)
    if not is_index:
        if m.sharpe_rolling > 1.0:
            score_bull += 15
            reasons.append(f"Strong rolling Sharpe: {m.sharpe_rolling:.2f}")
        elif m.sharpe_rolling < -1.0:
            score_bear += 15
            reasons.append(f"Negative rolling Sharpe: {m.sharpe_rolling:.2f}")

    # Historical probability (valid for both stocks and indices)
    if m.prob_profit > 0.60:
        score_bull += 20
        reasons.append(f"Historical win rate: {m.prob_profit:.0%}, EV={m.expected_value:.2f}%")
    elif m.prob_profit < 0.40:
        score_bear += 20
        reasons.append(f"Historical win rate unfavorable: {m.prob_profit:.0%}")

    # Volatility risk flag
    vol_cap = 40 if is_index else 80  # indices at 40%+ annualized = stressed market
    if m.volatility_pct > vol_cap:
        score_bull *= 0.7
        score_bear *= 0.7
        warnings.append(f"High volatility {m.volatility_pct:.1f}% annualized — confidence penalized")

    # Fat tails
    if m.kurtosis > 5:
        warnings.append(f"Fat tails detected (kurtosis={m.kurtosis:.1f}) — tail risk elevated")

    total = score_bull + score_bear
    if total == 0:
        return Signal.HOLD, 50.0, "No statistical edge detected", warnings

    if score_bull > score_bear:
        confidence = 50 + (score_bull / total - 0.5) * 90
        return Signal.BUY, round(min(confidence, 92), 1), " | ".join(reasons), warnings
    elif score_bear > score_bull:
        confidence = 50 + (score_bear / total - 0.5) * 90
        return Signal.SELL, round(min(confidence, 92), 1), " | ".join(reasons), warnings
    return Signal.HOLD, 50.0, " | ".join(reasons), warnings


class QuantAgent(BaseAgent):
    name = AgentName.QUANT
    MIN_CANDLES = 60
    MIN_CANDLES_INDEX = 30  # indices have clean daily data; fewer bars needed

    async def _analyze(self, ctx: MarketContext) -> AgentDecision:
        is_index = ctx.asset.upper() in _INDEX_SYMBOLS
        min_candles = self.MIN_CANDLES_INDEX if is_index else self.MIN_CANDLES
        if len(ctx.candles) < min_candles:
            return AgentDecision(
                agent_name=self.name,
                signal=Signal.HOLD,
                confidence=50.0,
                reasoning=f"Insufficient history: need {min_candles}, got {len(ctx.candles)}",
                warnings=["low_data"],
            )

        m = compute_quant_metrics(ctx.candles)
        signal, confidence, reasoning, warnings = _quant_signal(
            m, is_index=is_index, iv_rank=ctx.iv_rank
        )

        return AgentDecision(
            agent_name=self.name,
            signal=signal,
            confidence=confidence,
            reasoning=reasoning,
            indicators={
                "z_score": round(m.z_score, 3),
                "hurst_exponent": round(m.hurst_exponent, 3),
                "sharpe_rolling": round(m.sharpe_rolling, 3),
                "volatility_pct": round(m.volatility_pct, 2),
                "momentum_score": round(m.momentum_score, 3),
                "prob_profit": round(m.prob_profit, 3),
                "expected_value": round(m.expected_value, 3),
                "kurtosis": round(m.kurtosis, 3),
            },
            warnings=warnings,
        )
