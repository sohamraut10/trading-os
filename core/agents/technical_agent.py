"""
Technical Analyst Agent
Computes RSI, MACD, EMA, VWAP, Bollinger Bands and scores trend conviction.
Pure math — no external API calls, sub-millisecond.
"""
import numpy as np
from dataclasses import dataclass
from .base_agent import BaseAgent, AgentDecision, AgentName, MarketContext, Signal, OHLCV


@dataclass
class TechnicalIndicators:
    rsi_14: float
    macd_line: float
    macd_signal: float
    macd_hist: float
    ema_9: float
    ema_21: float
    ema_50: float
    ema_200: float
    vwap: float
    bb_upper: float
    bb_mid: float
    bb_lower: float
    bb_pct_b: float           # (price - lower) / (upper - lower)
    atr_14: float
    volume_ratio: float       # current vol / 20-period avg vol


def _closes(candles: list[OHLCV]) -> np.ndarray:
    return np.array([c.close for c in candles])


def _highs(candles: list[OHLCV]) -> np.ndarray:
    return np.array([c.high for c in candles])


def _lows(candles: list[OHLCV]) -> np.ndarray:
    return np.array([c.low for c in candles])


def _volumes(candles: list[OHLCV]) -> np.ndarray:
    return np.array([c.volume for c in candles])


def _ema(prices: np.ndarray, period: int) -> np.ndarray:
    alpha = 2 / (period + 1)
    ema = np.empty_like(prices)
    ema[0] = prices[0]
    for i in range(1, len(prices)):
        ema[i] = alpha * prices[i] + (1 - alpha) * ema[i - 1]
    return ema


def _rsi(prices: np.ndarray, period: int = 14) -> float:
    deltas = np.diff(prices[-(period + 1):])
    gains = np.where(deltas > 0, deltas, 0.0)
    losses = np.where(deltas < 0, -deltas, 0.0)
    avg_gain = gains.mean()
    avg_loss = losses.mean()
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100 - 100 / (1 + rs)


def _macd(prices: np.ndarray) -> tuple[float, float, float]:
    ema12 = _ema(prices, 12)
    ema26 = _ema(prices, 26)
    macd_line = ema12 - ema26
    signal = _ema(macd_line, 9)
    hist = macd_line[-1] - signal[-1]
    return float(macd_line[-1]), float(signal[-1]), float(hist)


def _bollinger(prices: np.ndarray, period: int = 20, std_mult: float = 2.0):
    window = prices[-period:]
    mid = window.mean()
    std = window.std()
    upper = mid + std_mult * std
    lower = mid - std_mult * std
    pct_b = (prices[-1] - lower) / (upper - lower) if (upper - lower) != 0 else 0.5
    return float(upper), float(mid), float(lower), float(pct_b)


def _vwap(candles: list[OHLCV]) -> float:
    tp = np.array([(c.high + c.low + c.close) / 3 for c in candles])
    vol = _volumes(candles)
    return float(np.sum(tp * vol) / np.sum(vol)) if np.sum(vol) != 0 else candles[-1].close


def _atr(candles: list[OHLCV], period: int = 14) -> float:
    trs = []
    for i in range(1, len(candles)):
        h, l, pc = candles[i].high, candles[i].low, candles[i - 1].close
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    return float(np.mean(trs[-period:])) if trs else 0.0


def compute_indicators(candles: list[OHLCV]) -> TechnicalIndicators:
    closes = _closes(candles)
    vols = _volumes(candles)

    macd_line, macd_signal, macd_hist = _macd(closes)
    bb_upper, bb_mid, bb_lower, pct_b = _bollinger(closes)

    return TechnicalIndicators(
        rsi_14=_rsi(closes),
        macd_line=macd_line,
        macd_signal=macd_signal,
        macd_hist=macd_hist,
        ema_9=float(_ema(closes, 9)[-1]),
        ema_21=float(_ema(closes, 21)[-1]),
        ema_50=float(_ema(closes, 50)[-1]),
        ema_200=float(_ema(closes, 200)[-1]) if len(closes) >= 200 else float(closes.mean()),
        vwap=_vwap(candles),
        bb_upper=bb_upper,
        bb_mid=bb_mid,
        bb_lower=bb_lower,
        bb_pct_b=pct_b,
        atr_14=_atr(candles),
        volume_ratio=float(vols[-1] / vols[-20:].mean()) if len(vols) >= 20 else 1.0,
    )


def _score(ind: TechnicalIndicators, price: float) -> tuple[Signal, float, list[str]]:
    """
    Scoring rubric — each component contributes ±points.
    Final score maps to confidence; direction taken from net vote.
    """
    bullish_pts = 0.0
    bearish_pts = 0.0
    reasons: list[str] = []

    # RSI
    if ind.rsi_14 < 30:
        bullish_pts += 20
        reasons.append(f"RSI oversold ({ind.rsi_14:.1f})")
    elif ind.rsi_14 > 70:
        bearish_pts += 20
        reasons.append(f"RSI overbought ({ind.rsi_14:.1f})")
    elif 40 <= ind.rsi_14 <= 60:
        reasons.append(f"RSI neutral ({ind.rsi_14:.1f})")

    # MACD
    if ind.macd_hist > 0 and ind.macd_line > ind.macd_signal:
        bullish_pts += 15
        reasons.append("MACD bullish crossover")
    elif ind.macd_hist < 0 and ind.macd_line < ind.macd_signal:
        bearish_pts += 15
        reasons.append("MACD bearish crossover")

    # EMA stack (trend alignment)
    if ind.ema_9 > ind.ema_21 > ind.ema_50:
        bullish_pts += 20
        reasons.append("EMA stack bullish (9>21>50)")
    elif ind.ema_9 < ind.ema_21 < ind.ema_50:
        bearish_pts += 20
        reasons.append("EMA stack bearish (9<21<50)")

    # Price vs VWAP
    if price > ind.vwap:
        bullish_pts += 10
        reasons.append(f"Price above VWAP ({ind.vwap:.2f})")
    else:
        bearish_pts += 10
        reasons.append(f"Price below VWAP ({ind.vwap:.2f})")

    # Bollinger Band
    if ind.bb_pct_b < 0.15:
        bullish_pts += 15
        reasons.append("Price near lower BB — reversal zone")
    elif ind.bb_pct_b > 0.85:
        bearish_pts += 15
        reasons.append("Price near upper BB — overbought zone")

    # Volume confirmation
    if ind.volume_ratio > 1.5:
        dominant = "bullish" if bullish_pts > bearish_pts else "bearish"
        if dominant == "bullish":
            bullish_pts += 10
        else:
            bearish_pts += 10
        reasons.append(f"Volume spike {ind.volume_ratio:.1f}x avg — confirms {dominant} move")

    total = bullish_pts + bearish_pts
    if total == 0:
        return Signal.HOLD, 50.0, ["No clear technical signal"]

    if bullish_pts > bearish_pts:
        signal = Signal.BUY
        raw_confidence = (bullish_pts / total) * 100
    elif bearish_pts > bullish_pts:
        signal = Signal.SELL
        raw_confidence = (bearish_pts / total) * 100
    else:
        signal = Signal.HOLD
        raw_confidence = 50.0

    # Scale from [50,100] domain into [50,95] to be epistemically humble
    confidence = 50 + (raw_confidence - 50) * 0.9
    return signal, round(confidence, 1), reasons


class TechnicalAnalystAgent(BaseAgent):
    name = AgentName.TECHNICAL

    MIN_CANDLES = 50

    async def _analyze(self, ctx: MarketContext) -> AgentDecision:
        if len(ctx.candles) < self.MIN_CANDLES:
            return AgentDecision(
                agent_name=self.name,
                signal=Signal.HOLD,
                confidence=50.0,
                reasoning=f"Insufficient data: need {self.MIN_CANDLES} candles, got {len(ctx.candles)}",
                warnings=["low_data"],
            )

        ind = compute_indicators(ctx.candles)
        signal, confidence, reasons = _score(ind, ctx.current_price)

        # Regime override: in highly volatile regime, require stronger signal
        if ctx.regime == "volatile" and confidence < 75:
            return AgentDecision(
                agent_name=self.name,
                signal=Signal.HOLD,
                confidence=confidence,
                reasoning="Volatile regime — technical signal below threshold for safe entry",
                indicators=ind.__dict__,
                warnings=["regime_override"],
            )

        return AgentDecision(
            agent_name=self.name,
            signal=signal,
            confidence=confidence,
            reasoning=" | ".join(reasons),
            indicators={
                "rsi_14": round(ind.rsi_14, 2),
                "macd_hist": round(ind.macd_hist, 4),
                "ema_9": round(ind.ema_9, 4),
                "ema_21": round(ind.ema_21, 4),
                "vwap": round(ind.vwap, 4),
                "bb_pct_b": round(ind.bb_pct_b, 3),
                "volume_ratio": round(ind.volume_ratio, 2),
                "atr_14": round(ind.atr_14, 4),
            },
        )
