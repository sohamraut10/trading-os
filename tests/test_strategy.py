"""Tests for the strategy layer — acceptance filters and size multipliers."""
import pytest
from core.strategy.strategies import (
    ScalpingStrategy, SwingStrategy, MeanReversionStrategy,
    TrendFollowStrategy, StatArbitrageStrategy, select_strategy, STRATEGY_REGISTRY,
)
from core.strategy.strategy_base import StrategyType
from core.agents.base_agent import MarketContext, Signal
from core.agents.meta_agent import TradeSignal
import time


def _make_signal(action: str, confidence: float, regime: str, agents_override=None) -> TradeSignal:
    agents = agents_override or [
        {"name": "Technical",  "decision": action, "confidence": confidence},
        {"name": "Sentiment",  "decision": action, "confidence": confidence},
        {"name": "Quant",      "decision": action, "confidence": confidence},
        {"name": "OrderFlow",  "decision": action, "confidence": confidence},
    ]
    return TradeSignal(
        request_id="test",
        asset="BTC/USDT",
        timestamp=time.time(),
        final_decision=(action in ("BUY", "SELL")),
        action=Signal(action) if action in ("BUY", "SELL") else None,
        confidence=confidence,
        agents=agents,
        reason="test",
        regime=regime,
        suggested_position_size_pct=0.02,
        suggested_stop_loss_pct=0.02,
        suggested_take_profit_pct=0.04,
        risk_reward=2.0,
    )


def _ctx(regime: str) -> MarketContext:
    from tests.test_pipeline import make_candles
    candles = make_candles(200)
    return MarketContext(
        asset="BTC/USDT", timeframe="1h",
        candles=candles, current_price=candles[-1].close,
        regime=regime,
    )


# ── select_strategy ──────────────────────────────────────────────────

def test_select_scalping_on_short_tf():
    s = select_strategy("volatile", "1m")
    assert s.strategy_type == StrategyType.SCALPING

def test_select_swing_bull():
    s = select_strategy("bull", "1h")
    assert s.strategy_type in (StrategyType.SWING, StrategyType.TREND_FOLLOW)

def test_select_mean_reversion_sideways():
    s = select_strategy("sideways", "1h")
    assert s.strategy_type == StrategyType.MEAN_REVERSION

def test_select_trend_follow_bear_4h():
    s = select_strategy("bear", "4h")
    assert s.strategy_type == StrategyType.TREND_FOLLOW

def test_user_override_wins():
    s = select_strategy("bull", "1h", user_override="scalping")
    assert s.strategy_type == StrategyType.SCALPING


# ── ScalpingStrategy ─────────────────────────────────────────────────

def test_scalping_rejects_low_confidence():
    s = ScalpingStrategy()
    sig = _make_signal("BUY", 70.0, "sideways")   # needs 80
    ok, reason = s.accepts(sig, _ctx("sideways"))
    assert not ok
    assert "confidence" in reason.lower()

def test_scalping_rejects_bull_regime():
    s = ScalpingStrategy()
    sig = _make_signal("BUY", 88.0, "bull")
    ok, _ = s.accepts(sig, _ctx("bull"))
    assert not ok

def test_scalping_passes_volatile_high_conf():
    s = ScalpingStrategy()
    sig = _make_signal("BUY", 85.0, "volatile")
    sig.risk_reward = 2.0
    ok, _ = s.accepts(sig, _ctx("volatile"))
    assert ok

def test_scalping_size_multiplier_below_one():
    s = ScalpingStrategy()
    sig = _make_signal("BUY", 85.0, "sideways")
    assert s.position_size_multiplier(sig, _ctx("sideways")) < 1.0


# ── SwingStrategy ─────────────────────────────────────────────────────

def test_swing_rejects_volatile():
    s = SwingStrategy()
    sig = _make_signal("BUY", 75.0, "volatile")
    ok, reason = s.accepts(sig, _ctx("volatile"))
    assert not ok
    assert "volatile" in reason.lower()

def test_swing_accepts_bull():
    s = SwingStrategy()
    sig = _make_signal("BUY", 75.0, "bull")
    sig.risk_reward = 2.5
    ok, _ = s.accepts(sig, _ctx("bull"))
    assert ok

def test_swing_full_size_in_bull():
    s = SwingStrategy()
    sig = _make_signal("BUY", 75.0, "bull")
    assert s.position_size_multiplier(sig, _ctx("bull")) == 1.0

def test_swing_reduced_size_in_bear():
    s = SwingStrategy()
    sig = _make_signal("SELL", 75.0, "bear")
    assert s.position_size_multiplier(sig, _ctx("bear")) < 1.0


# ── TrendFollowStrategy ───────────────────────────────────────────────

def test_trend_follow_blocks_counter_trend_buy_in_bear():
    s = TrendFollowStrategy()
    sig = _make_signal("BUY", 80.0, "bear")
    ok, reason = s.accepts(sig, _ctx("bear"))
    assert not ok
    assert "counter-trend" in reason.lower()

def test_trend_follow_blocks_counter_trend_sell_in_bull():
    s = TrendFollowStrategy()
    sig = _make_signal("SELL", 80.0, "bull")
    ok, reason = s.accepts(sig, _ctx("bull"))
    assert not ok

def test_trend_follow_accepts_bull_buy():
    s = TrendFollowStrategy()
    sig = _make_signal("BUY", 80.0, "bull")
    sig.risk_reward = 3.0
    ok, _ = s.accepts(sig, _ctx("bull"))
    assert ok

def test_trend_follow_size_scales_with_confidence():
    s = TrendFollowStrategy()
    low_sig  = _make_signal("BUY", 72.0, "bull")
    high_sig = _make_signal("BUY", 92.0, "bull")
    ctx = _ctx("bull")
    assert s.position_size_multiplier(high_sig, ctx) > s.position_size_multiplier(low_sig, ctx)


# ── MeanReversionStrategy ─────────────────────────────────────────────

def test_mr_rejects_without_quant_alignment():
    s = MeanReversionStrategy()
    # Quant votes SELL but signal is BUY
    agents = [
        {"name": "Technical",  "decision": "BUY",  "confidence": 70},
        {"name": "Sentiment",  "decision": "BUY",  "confidence": 70},
        {"name": "Quant",      "decision": "SELL", "confidence": 70},
        {"name": "OrderFlow",  "decision": "BUY",  "confidence": 70},
    ]
    sig = _make_signal("BUY", 70.0, "sideways", agents_override=agents)
    ok, reason = s.accepts(sig, _ctx("sideways"))
    assert not ok
    assert "quant" in reason.lower()

def test_mr_rejects_volatile():
    s = MeanReversionStrategy()
    sig = _make_signal("BUY", 70.0, "volatile")
    ok, _ = s.accepts(sig, _ctx("volatile"))
    assert not ok

def test_mr_accepts_with_quant():
    s = MeanReversionStrategy()
    agents = [
        {"name": "Technical", "decision": "BUY", "confidence": 70},
        {"name": "Sentiment", "decision": "BUY", "confidence": 70},
        {"name": "Quant",     "decision": "BUY", "confidence": 70},
        {"name": "OrderFlow", "decision": "BUY", "confidence": 70},
    ]
    sig = _make_signal("BUY", 70.0, "sideways", agents_override=agents)
    sig.risk_reward = 2.0
    ok, _ = s.accepts(sig, _ctx("sideways"))
    assert ok


# ── StatArbitrageStrategy ─────────────────────────────────────────────

def test_arb_half_size():
    s = StatArbitrageStrategy()
    sig = _make_signal("BUY", 75.0, "sideways")
    assert s.position_size_multiplier(sig, _ctx("sideways")) == 0.5

def test_arb_rejects_volatile():
    s = StatArbitrageStrategy()
    sig = _make_signal("BUY", 80.0, "volatile")
    ok, _ = s.accepts(sig, _ctx("volatile"))
    assert not ok
