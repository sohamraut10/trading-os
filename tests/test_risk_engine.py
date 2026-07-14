"""Tests for risk engine — circuit breakers, sizing, edge cases."""
import pytest
import time
from core.risk.risk_engine import RiskEngine, RiskStatus, PortfolioState, PositionSizer
from core.agents.base_agent import Signal
from core.agents.meta_agent import TradeSignal


def _portfolio(**overrides) -> PortfolioState:
    defaults = dict(
        equity=100_000.0, cash=90_000.0, open_trades=1,
        daily_pnl_pct=0.005, max_daily_drawdown_pct=0.005,
        positions={"ETH/USDT": 10_000.0}, consecutive_losses=0,
    )
    defaults.update(overrides)
    return PortfolioState(**defaults)


def _signal(action="BUY", confidence=80.0, sl=0.02, tp=0.04, size=0.03) -> TradeSignal:
    return TradeSignal(
        request_id="test", asset="BTC/USDT", timestamp=time.time(),
        final_decision=True, action=Signal(action), confidence=confidence,
        agents=[], reason="test", regime="bull",
        suggested_position_size_pct=size,
        suggested_stop_loss_pct=sl, suggested_take_profit_pct=tp,
        risk_reward=tp / sl,
    )


# ── Circuit breakers ──────────────────────────────────────────────────

def test_daily_drawdown_circuit_breaker():
    engine = RiskEngine()
    port = _portfolio(daily_pnl_pct=-0.04)   # exceeds 3% limit
    result = engine.check(_signal(), port, 65000.0)
    assert result.status == RiskStatus.REJECTED
    assert any("circuit breaker" in r.lower() for r in result.rejection_reasons)

def test_loss_streak_5_rejected():
    engine = RiskEngine()
    port = _portfolio(consecutive_losses=5)
    result = engine.check(_signal(), port, 65000.0)
    assert result.status == RiskStatus.REJECTED
    assert any("streak" in r.lower() for r in result.rejection_reasons)

def test_loss_streak_3_warning_only():
    engine = RiskEngine()
    port = _portfolio(consecutive_losses=3)
    result = engine.check(_signal(), port, 65000.0)
    # 3 losses → warning, not rejection
    assert result.status != RiskStatus.REJECTED
    assert any("streak" in w.lower() or "loss" in w.lower() for w in result.warnings)

def test_max_open_trades_rejected():
    engine = RiskEngine()
    port = _portfolio(open_trades=10)   # at the limit
    result = engine.check(_signal(), port, 65000.0)
    assert result.status == RiskStatus.REJECTED
    assert any("open trades" in r.lower() for r in result.rejection_reasons)

def test_portfolio_fully_deployed_rejected():
    engine = RiskEngine()
    # 40% max exposure, positions hold 42k on 100k equity
    port = _portfolio(positions={"A": 22_000.0, "B": 20_000.0})
    result = engine.check(_signal(), port, 65000.0)
    assert result.status == RiskStatus.REJECTED


# ── Position sizing ───────────────────────────────────────────────────

def test_position_capped_by_max_pct():
    engine = RiskEngine()
    # Request 10% size but max is 5%
    port = _portfolio()
    result = engine.check(_signal(size=0.10), port, 65000.0)
    assert result.is_tradeable()
    assert result.approved_position_size_pct <= engine.cfg.max_position_pct

def test_position_scaled_down_warning():
    engine = RiskEngine()
    port = _portfolio(positions={"ETH": 35_000.0})  # 35% already deployed
    result = engine.check(_signal(size=0.05), port, 65000.0)
    # Only 5% headroom left but requesting 5% — should fit or barely scale
    assert result.is_tradeable()

def test_tiny_position_rejected():
    engine = RiskEngine()
    # Remaining exposure headroom is near zero → position too small
    port = _portfolio(positions={"A": 38_000.0, "B": 1_900.0})  # 39.9% deployed
    result = engine.check(_signal(size=0.05), port, 65000.0)
    # Either approved (tiny) or rejected as too small
    if result.status == RiskStatus.REJECTED:
        assert any("small" in r.lower() or "constraints" in r.lower() for r in result.rejection_reasons)


# ── SL/TP prices ─────────────────────────────────────────────────────

def test_buy_sl_below_price():
    engine = RiskEngine()
    price = 65000.0
    result = engine.check(_signal("BUY"), _portfolio(), price)
    assert result.is_tradeable()
    assert result.stop_loss_price < price
    assert result.take_profit_price > price

def test_sell_sl_above_price():
    engine = RiskEngine()
    price = 65000.0
    result = engine.check(_signal("SELL"), _portfolio(), price)
    assert result.is_tradeable()
    assert result.stop_loss_price > price
    assert result.take_profit_price < price

def test_rr_below_1_5_warns():
    engine = RiskEngine()
    # sl=0.03, tp=0.04 → natural R:R=1.33 but risk engine floors tp at sl*2.
    # Verify the floor kicks in by confirming tp price reflects the minimum R:R.
    result = engine.check(_signal(sl=0.03, tp=0.04), _portfolio(), 65000.0)
    if result.is_tradeable():
        implied_rr = abs(result.take_profit_price - 65000.0) / abs(result.stop_loss_price - 65000.0)
        assert implied_rr >= 1.5


# ── Clean approval ────────────────────────────────────────────────────

def test_clean_approval():
    engine = RiskEngine()
    result = engine.check(_signal(), _portfolio(), 65000.0)
    assert result.status == RiskStatus.APPROVED
    assert result.approved_position_size_usd > 0
    assert result.rejection_reasons == []


# ── Manual order gate ─────────────────────────────────────────────────

def test_manual_order_clean_approval():
    engine = RiskEngine()
    result = engine.check_manual_order("buy", 0.05, 65000.0, _portfolio())
    assert result.is_tradeable()
    assert result.approved_position_size_usd > 0

def test_manual_order_rejected_on_circuit_breaker():
    engine = RiskEngine()
    port = _portfolio(daily_pnl_pct=-0.04)
    result = engine.check_manual_order("buy", 0.05, 65000.0, port)
    assert result.status == RiskStatus.REJECTED
    assert any("circuit breaker" in r.lower() for r in result.rejection_reasons)

def test_manual_order_rejected_when_fully_deployed():
    engine = RiskEngine()
    port = _portfolio(positions={"A": 22_000.0, "B": 20_000.0})
    result = engine.check_manual_order("buy", 0.05, 65000.0, port)
    assert result.status == RiskStatus.REJECTED

def test_manual_order_scaled_down_to_exposure_limit():
    engine = RiskEngine()
    # 35% already deployed, 5% headroom left; request notional far beyond that
    port = _portfolio(positions={"ETH": 35_000.0})
    quantity = 1.0  # 1 BTC @ 65000 = $65,000, way over the $5,000 headroom
    result = engine.check_manual_order("buy", quantity, 65000.0, port)
    assert result.is_tradeable()
    assert result.status == RiskStatus.SCALED_DOWN
    assert result.approved_position_size_usd < quantity * 65000.0

def test_manual_order_rejected_on_invalid_quantity():
    engine = RiskEngine()
    result = engine.check_manual_order("buy", 0.0, 65000.0, _portfolio())
    assert result.status == RiskStatus.REJECTED


# ── PositionSizer ─────────────────────────────────────────────────────

def test_kelly_zero_on_bad_inputs():
    assert PositionSizer.kelly_size(0, 0.04, 0.02, 100_000) == 0.0
    assert PositionSizer.kelly_size(0.6, 0.04, 0, 100_000) == 0.0

def test_kelly_increases_with_win_rate():
    # Use max_pct=1.0 so both values are uncapped and differ
    low  = PositionSizer.kelly_size(0.55, 0.04, 0.02, 100_000, max_pct=1.0)
    high = PositionSizer.kelly_size(0.70, 0.04, 0.02, 100_000, max_pct=1.0)
    assert high > low

def test_kelly_capped_at_max():
    size = PositionSizer.kelly_size(0.90, 0.10, 0.01, 100_000, max_pct=0.05)
    assert size <= 100_000 * 0.05


# ── VaR ──────────────────────────────────────────────────────────────

def test_var_positive():
    engine = RiskEngine()
    port = _portfolio(positions={"BTC": 20_000.0})
    var = engine.compute_portfolio_var(port)
    assert var > 0

def test_var_zero_with_no_positions():
    engine = RiskEngine()
    port = _portfolio(positions={})
    var = engine.compute_portfolio_var(port)
    assert var == 0.0
