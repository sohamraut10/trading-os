"""Tests for backtester output correctness and metric validity."""
import pytest
import numpy as np
import time as _time
from core.backtest.backtester import Backtester, BacktestResult
from core.data.market_data import MockProvider
from core.agents.base_agent import OHLCV

# Reduced candle counts to stay within memory limits when run alongside other tests
_N = 400
_WARMUP = 200


def _bullish_candles():
    provider = MockProvider(seed=42)
    import asyncio
    return asyncio.get_event_loop().run_until_complete(
        provider.get_candles("BTC/USDT", "1h", _N)
    )


def _bearish_candles():
    rng = np.random.default_rng(99)
    price = 50000.0
    candles = []
    for i in range(_N):
        ret = rng.normal(-0.002, 0.015)
        o, c = price, price * (1 + ret)
        h = max(o, c) * (1 + abs(rng.normal(0, 0.003)))
        l = min(o, c) * (1 - abs(rng.normal(0, 0.003)))
        v = rng.uniform(1000, 5000)
        candles.append(OHLCV(_time.time() - (_N - i) * 3600,
                             round(o, 2), round(h, 2), round(l, 2), round(c, 2), round(v, 2)))
        price = c
    return candles


@pytest.fixture(scope="module")
def candles_bullish():
    return _bullish_candles()


@pytest.fixture(scope="module")
def candles_bearish():
    return _bearish_candles()


async def _run(candles, **kw):
    bt = Backtester(warmup_bars=_WARMUP, **kw)
    return await bt.run("BTC/USDT", candles)


@pytest.mark.asyncio
async def test_backtest_runs_without_error(candles_bullish):
    result = await _run(candles_bullish)
    assert isinstance(result, BacktestResult)


@pytest.mark.asyncio
async def test_equity_curve_starts_at_one(candles_bullish):
    result = await _run(candles_bullish)
    assert result.equity_curve[0] == pytest.approx(1.0)


@pytest.mark.asyncio
async def test_win_rate_in_range(candles_bullish):
    result = await _run(candles_bullish)
    assert 0.0 <= result.win_rate <= 1.0


@pytest.mark.asyncio
async def test_max_drawdown_non_negative(candles_bullish):
    result = await _run(candles_bullish)
    assert result.max_drawdown_pct >= 0.0


@pytest.mark.asyncio
async def test_profit_factor_non_negative(candles_bullish):
    result = await _run(candles_bullish)
    if result.total_trades > 0:
        assert result.profit_factor >= 0.0


@pytest.mark.asyncio
async def test_trade_count_consistent_with_equity_curve(candles_bullish):
    result = await _run(candles_bullish)
    assert len(result.equity_curve) == result.total_trades + 1


@pytest.mark.asyncio
async def test_avg_hold_bars_positive(candles_bullish):
    result = await _run(candles_bullish)
    if result.total_trades > 0:
        assert result.avg_hold_bars > 0


@pytest.mark.asyncio
async def test_summary_keys_present(candles_bullish):
    result = await _run(candles_bullish)
    expected = {"total_trades", "win_rate", "sharpe_ratio", "sortino_ratio",
                "max_drawdown_pct", "total_return_pct", "profit_factor", "avg_hold_bars"}
    assert expected.issubset(result.summary().keys())


@pytest.mark.asyncio
async def test_empty_result_on_insufficient_candles():
    candles = [OHLCV(_time.time(), 100, 101, 99, 100, 1000) for _ in range(10)]
    result = await _run(candles)
    assert result.total_trades == 0


@pytest.mark.asyncio
async def test_sl_tp_respected(candles_bullish):
    result = await _run(candles_bullish, sl_pct=0.02, tp_pct=0.04)
    for trade in result.trades[:-1]:
        assert trade.hit_sl or trade.hit_tp


@pytest.mark.asyncio
async def test_bearish_backtest_runs(candles_bearish):
    result = await _run(candles_bearish)
    assert isinstance(result, BacktestResult)
    assert 0.0 <= result.win_rate <= 1.0
