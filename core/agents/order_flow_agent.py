"""
Market Structure & Order Flow Agent
Analyzes liquidity zones, order book imbalance, and smart money footprints.
"""
import numpy as np
from dataclasses import dataclass
from .base_agent import BaseAgent, AgentDecision, AgentName, MarketContext, Signal, OHLCV, OrderBook


@dataclass
class OrderFlowMetrics:
    bid_ask_imbalance: float       # (bid_vol - ask_vol) / total — positive = buy pressure
    depth_ratio: float             # top-5 bids / top-5 asks
    support_distance_pct: float    # % distance to nearest support
    resistance_distance_pct: float # % distance to nearest resistance
    liquidity_void_below: bool     # thin order book = fast price travel
    liquidity_void_above: bool
    delta: float                   # buy volume - sell volume (from candle data proxy)
    cumulative_delta_trend: str    # "rising" | "falling" | "flat"
    large_order_detected: bool     # institutional-size order detected
    volume_profile_poc: float      # Point of Control (highest volume price)


def _identify_sr_levels(candles: list[OHLCV], lookback: int = 50) -> tuple[list[float], list[float]]:
    """
    Identify support/resistance via swing high/low clustering.
    Simple pivot-point approach; production would use ML clustering.
    """
    highs = [c.high for c in candles[-lookback:]]
    lows = [c.low for c in candles[-lookback:]]

    supports = []
    resistances = []

    for i in range(2, len(lows) - 2):
        if lows[i] < lows[i - 1] and lows[i] < lows[i + 1] and \
           lows[i] < lows[i - 2] and lows[i] < lows[i + 2]:
            supports.append(lows[i])

    for i in range(2, len(highs) - 2):
        if highs[i] > highs[i - 1] and highs[i] > highs[i + 1] and \
           highs[i] > highs[i - 2] and highs[i] > highs[i + 2]:
            resistances.append(highs[i])

    return supports, resistances


def _volume_profile(candles: list[OHLCV], bins: int = 20) -> float:
    """Returns Point of Control (price with highest traded volume)."""
    prices = np.array([(c.high + c.low) / 2 for c in candles])
    volumes = np.array([c.volume for c in candles])

    if len(prices) < 2:
        return prices[-1] if len(prices) else 0.0

    price_range = np.linspace(prices.min(), prices.max(), bins)
    bin_vols = np.zeros(bins - 1)

    for price, vol in zip(prices, volumes):
        idx = np.searchsorted(price_range, price) - 1
        idx = np.clip(idx, 0, bins - 2)
        bin_vols[idx] += vol

    poc_idx = np.argmax(bin_vols)
    return float((price_range[poc_idx] + price_range[poc_idx + 1]) / 2)


def _candle_delta(candles: list[OHLCV]) -> tuple[float, str]:
    """
    Proxy for buy/sell delta using candle body direction and volume.
    If close > open → bullish candle → attribute volume to buyers.
    """
    deltas = []
    for c in candles[-30:]:
        body = c.close - c.open
        total_range = c.high - c.low or 1
        buy_ratio = (body / total_range + 1) / 2  # map to [0,1]
        delta = c.volume * (2 * buy_ratio - 1)
        deltas.append(delta)

    cum_delta = float(np.sum(deltas))
    trend = "rising" if deltas[-5:] > [0] * 5 else "falling" if all(d < 0 for d in deltas[-5:]) else "flat"

    # simplify
    last_5 = deltas[-5:]
    if sum(last_5) > 0:
        trend = "rising"
    elif sum(last_5) < 0:
        trend = "falling"
    else:
        trend = "flat"

    return cum_delta, trend


def _order_book_metrics(ob: OrderBook, current_price: float) -> tuple[float, float, bool, bool]:
    """Returns imbalance, depth_ratio, void_below, void_above."""
    if not ob or not ob.bids or not ob.asks:
        return 0.0, 1.0, False, False

    # Top-10 levels
    top_bids = ob.bids[:10]
    top_asks = ob.asks[:10]

    bid_vol = sum(b.size for b in top_bids)
    ask_vol = sum(a.size for a in top_asks)
    total_vol = bid_vol + ask_vol

    imbalance = (bid_vol - ask_vol) / total_vol if total_vol > 0 else 0.0
    depth_ratio = bid_vol / ask_vol if ask_vol > 0 else 1.0

    # Liquidity void detection: compare top-5 and 6-20 level volumes
    deep_bids = ob.bids[5:20] if len(ob.bids) > 5 else []
    deep_asks = ob.asks[5:20] if len(ob.asks) > 5 else []

    shallow_bid_avg = bid_vol / len(top_bids) if top_bids else 0
    deep_bid_avg = sum(b.size for b in deep_bids) / len(deep_bids) if deep_bids else shallow_bid_avg

    void_below = deep_bid_avg < shallow_bid_avg * 0.3 if deep_bids else False
    void_above_asks = ob.asks[5:20] if len(ob.asks) > 5 else []
    shallow_ask_avg = ask_vol / len(top_asks) if top_asks else 0
    deep_ask_avg = sum(a.size for a in void_above_asks) / len(void_above_asks) if void_above_asks else shallow_ask_avg
    void_above = deep_ask_avg < shallow_ask_avg * 0.3 if void_above_asks else False

    return imbalance, depth_ratio, void_below, void_above


def _large_order_check(candles: list[OHLCV], threshold_multiplier: float = 5.0) -> bool:
    volumes = np.array([c.volume for c in candles])
    if len(volumes) < 10:
        return False
    avg_vol = volumes[:-1].mean()
    return bool(volumes[-1] > avg_vol * threshold_multiplier)


def compute_order_flow_metrics(ctx: MarketContext) -> OrderFlowMetrics:
    price = ctx.current_price
    candles = ctx.candles
    ob = ctx.order_book

    supports, resistances = _identify_sr_levels(candles)

    nearest_support = max((s for s in supports if s < price), default=price * 0.95)
    nearest_resistance = min((r for r in resistances if r > price), default=price * 1.05)

    support_dist = (price - nearest_support) / price * 100
    resistance_dist = (nearest_resistance - price) / price * 100

    poc = _volume_profile(candles)
    delta, delta_trend = _candle_delta(candles)

    if ob:
        imbalance, depth_ratio, void_below, void_above = _order_book_metrics(ob, price)
    else:
        # Estimate from candle data when no order book
        last_candle = candles[-1]
        body_ratio = (last_candle.close - last_candle.open) / (last_candle.high - last_candle.low + 1e-9)
        imbalance = float(body_ratio * 0.5)
        depth_ratio = 1.0 + imbalance
        void_below = void_above = False

    return OrderFlowMetrics(
        bid_ask_imbalance=imbalance,
        depth_ratio=depth_ratio,
        support_distance_pct=support_dist,
        resistance_distance_pct=resistance_dist,
        liquidity_void_below=void_below,
        liquidity_void_above=void_above,
        delta=delta,
        cumulative_delta_trend=delta_trend,
        large_order_detected=_large_order_check(candles),
        volume_profile_poc=poc,
    )


def _of_signal(m: OrderFlowMetrics, price: float) -> tuple[Signal, float, list[str]]:
    bull_pts = 0.0
    bear_pts = 0.0
    reasons = []

    # Order book imbalance
    if m.bid_ask_imbalance > 0.2:
        bull_pts += 25
        reasons.append(f"Buy-side order book imbalance: {m.bid_ask_imbalance:.2%}")
    elif m.bid_ask_imbalance < -0.2:
        bear_pts += 25
        reasons.append(f"Sell-side order book imbalance: {m.bid_ask_imbalance:.2%}")

    # Delta / order flow direction
    if m.cumulative_delta_trend == "rising":
        bull_pts += 20
        reasons.append("Rising cumulative delta — buyers absorbing supply")
    elif m.cumulative_delta_trend == "falling":
        bear_pts += 20
        reasons.append("Falling cumulative delta — sellers in control")

    # Proximity to support/resistance
    if m.support_distance_pct < 1.0:
        bull_pts += 15
        reasons.append(f"Price near key support (dist={m.support_distance_pct:.2f}%)")
    if m.resistance_distance_pct < 1.0:
        bear_pts += 15
        reasons.append(f"Price near key resistance (dist={m.resistance_distance_pct:.2f}%)")

    # Volume Profile POC
    if abs(price - m.volume_profile_poc) / price < 0.005:
        reasons.append(f"Price at high-volume POC {m.volume_profile_poc:.4f} — expect rejection or breakout")

    # Smart money / large orders
    if m.large_order_detected:
        dominant = "bullish" if bull_pts >= bear_pts else "bearish"
        if dominant == "bullish":
            bull_pts += 15
        else:
            bear_pts += 15
        reasons.append(f"Institutional-size order detected — {dominant} bias")

    # Liquidity void = fast move expected
    warnings = []
    if m.liquidity_void_above and bull_pts > bear_pts:
        bull_pts += 10
        reasons.append("Liquidity void above — potential rapid upward move")
    if m.liquidity_void_below and bear_pts > bull_pts:
        bear_pts += 10
        reasons.append("Liquidity void below — risk of fast drop")
    if m.liquidity_void_above or m.liquidity_void_below:
        warnings.append("liquidity_void_detected")

    total = bull_pts + bear_pts
    if total == 0:
        return Signal.HOLD, 50.0, ["No significant order flow signal"]

    if bull_pts > bear_pts:
        confidence = 50 + (bull_pts / total - 0.5) * 90
        return Signal.BUY, round(min(confidence, 93), 1), reasons
    elif bear_pts > bull_pts:
        confidence = 50 + (bear_pts / total - 0.5) * 90
        return Signal.SELL, round(min(confidence, 93), 1), reasons
    return Signal.HOLD, 50.0, ["Balanced order flow — no edge"]


class OrderFlowAgent(BaseAgent):
    name = AgentName.ORDER_FLOW
    MIN_CANDLES = 30

    async def _analyze(self, ctx: MarketContext) -> AgentDecision:
        if len(ctx.candles) < self.MIN_CANDLES:
            return AgentDecision(
                agent_name=self.name,
                signal=Signal.HOLD,
                confidence=50.0,
                reasoning="Insufficient candle data for order flow analysis",
                warnings=["low_data"],
            )

        m = compute_order_flow_metrics(ctx)
        signal, confidence, reasons = _of_signal(m, ctx.current_price)

        return AgentDecision(
            agent_name=self.name,
            signal=signal,
            confidence=confidence,
            reasoning=" | ".join(reasons),
            indicators={
                "bid_ask_imbalance": round(m.bid_ask_imbalance, 4),
                "depth_ratio": round(m.depth_ratio, 3),
                "delta_trend": m.cumulative_delta_trend,
                "support_dist_pct": round(m.support_distance_pct, 3),
                "resistance_dist_pct": round(m.resistance_distance_pct, 3),
                "poc": round(m.volume_profile_poc, 4),
                "large_order": m.large_order_detected,
            },
            warnings=["liquidity_void"] if (m.liquidity_void_above or m.liquidity_void_below) else [],
        )
