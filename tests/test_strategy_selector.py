import pytest
from core.strategy.selector import StrategySelector
from core.strategy.strategy_base import StrategyType
from core.agents.base_agent import MarketContext, OHLCV, Signal, OrderBook
import time
import numpy as np


def create_mock_context(regime: str, price: float, trend_direction: float = 0.0) -> MarketContext:
    # Generate 100 mock candles
    candles = []
    base_price = price
    for i in range(100):
        # Add a trend if specified
        base_price += trend_direction
        candles.append(OHLCV(
            timestamp=time.time() - (100 - i) * 60,
            open=base_price,
            high=base_price * 1.001,
            low=base_price * 0.999,
            close=base_price,
            volume=100.0
        ))
    return MarketContext(
        asset="BTCUSDT",
        timeframe="1h",
        candles=candles,
        current_price=base_price,
        regime=regime,
        order_book=OrderBook(bids=[], asks=[], timestamp=time.time())
    )


def test_strategy_selector_pinned():
    selector = StrategySelector()
    ctx = create_mock_context("bull", 10000.0)

    # Pin TrendFollow
    selector.pin_strategy(StrategyType.TREND_FOLLOW)
    strategy, reason, hurst, vol_pct = selector.select(ctx)
    assert strategy.strategy_type == StrategyType.TREND_FOLLOW
    assert "override" in reason.lower()

    # Unpin
    selector.pin_strategy(None)
    strategy, reason, hurst, vol_pct = selector.select(ctx)
    assert strategy.strategy_type != StrategyType.ARBITRAGE  # should select based on regime


def test_strategy_selector_volatile():
    selector = StrategySelector()
    ctx = create_mock_context("volatile", 10000.0)
    strategy, reason, hurst, vol_pct = selector.select(ctx)
    assert strategy.strategy_type == StrategyType.SCALPING
    assert "volat" in reason.lower()


def test_strategy_selector_trending():
    selector = StrategySelector()
    # High Hurst exponent via a strong upward trend
    ctx = create_mock_context("bull", 10000.0, trend_direction=10.0)
    strategy, reason, hurst, vol_pct = selector.select(ctx)
    # Since we added a trend, Hurst should be high (> 0.55)
    assert strategy.strategy_type in (StrategyType.TREND_FOLLOW, StrategyType.SWING)


def test_emit_hypothesis():
    selector = StrategySelector()
    ctx = create_mock_context("bull", 10000.0, trend_direction=1.0)
    strategy, reason, hurst, vol_pct = selector.select(ctx)
    hypothesis = selector.emit_hypothesis(strategy, ctx, hurst, vol_pct)

    assert hypothesis.strategy_name == strategy.strategy_type.value
    assert hypothesis.direction_bias in (Signal.BUY, Signal.SELL, Signal.HOLD)
    assert len(hypothesis.confirming_evidence) > 0
    assert len(hypothesis.refuting_evidence) > 0
