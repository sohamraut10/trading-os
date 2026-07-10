"""
Integration test for the full multi-agent pipeline.
Uses MockProvider — no real API keys required.
"""
import asyncio
import pytest
import numpy as np
import time

from core.agents.base_agent import MarketContext, OHLCV, OrderBook, OrderBookLevel
from core.agents.technical_agent import TechnicalAnalystAgent
from core.agents.sentiment_agent import SentimentAgent
from core.agents.quant_agent import QuantAgent
from core.agents.order_flow_agent import OrderFlowAgent
from core.agents.devils_advocate_agent import DevilsAdvocateAgent
from core.agents.meta_agent import ConsensusEngine
from core.agents.base_agent import Signal
from core.monitoring.regime_detector import detect_regime
from core.risk.risk_engine import RiskEngine, PortfolioState
from core.backtest.backtester import Backtester
from core.data.market_data import MockProvider


def make_candles(n: int = 300, trend: float = 0.001, seed: int = 42) -> list[OHLCV]:
    rng = np.random.default_rng(seed)
    price = 50000.0
    candles = []
    for i in range(n):
        ret = rng.normal(trend, 0.015)
        open_ = price
        close = price * (1 + ret)
        high = max(open_, close) * (1 + abs(rng.normal(0, 0.003)))
        low = min(open_, close) * (1 - abs(rng.normal(0, 0.003)))
        vol = rng.uniform(1000, 5000)
        candles.append(OHLCV(time.time() + i * 60, round(open_, 4), round(high, 4), round(low, 4), round(close, 4), round(vol, 2)))
        price = close
    return candles


def make_order_book(price: float) -> OrderBook:
    bids = [OrderBookLevel(price=price * (1 - 0.0005 * (i + 1)), size=2.5) for i in range(20)]
    asks = [OrderBookLevel(price=price * (1 + 0.0005 * (i + 1)), size=2.5) for i in range(20)]
    return OrderBook(bids=bids, asks=asks, timestamp=time.time())


@pytest.fixture
def ctx():
    candles = make_candles(300, trend=0.002)   # slight uptrend
    price = candles[-1].close
    return MarketContext(
        asset="BTC/USDT",
        timeframe="1h",
        candles=candles,
        current_price=price,
        order_book=make_order_book(price),
        news_headlines=["Bitcoin surges as ETF approval imminent", "Institutions accumulating BTC"],
        regime=detect_regime(candles),
    )


@pytest.mark.asyncio
async def test_technical_agent(ctx):
    agent = TechnicalAnalystAgent()
    decision = await agent.analyze(ctx)
    assert decision.signal in (Signal.BUY, Signal.SELL, Signal.HOLD)
    assert 0 <= decision.confidence <= 100
    assert decision.reasoning


@pytest.mark.asyncio
async def test_sentiment_agent_heuristic(ctx):
    agent = SentimentAgent(api_key="")  # no key → heuristic mode
    decision = await agent.analyze(ctx)
    assert decision.signal in (Signal.BUY, Signal.SELL, Signal.HOLD)
    assert decision.confidence >= 0


@pytest.mark.asyncio
async def test_quant_agent(ctx):
    agent = QuantAgent()
    decision = await agent.analyze(ctx)
    assert decision.signal in (Signal.BUY, Signal.SELL, Signal.HOLD)
    assert "z_score" in decision.indicators


@pytest.mark.asyncio
async def test_order_flow_agent(ctx):
    agent = OrderFlowAgent()
    decision = await agent.analyze(ctx)
    assert decision.signal in (Signal.BUY, Signal.SELL, Signal.HOLD)
    assert "bid_ask_imbalance" in decision.indicators


@pytest.mark.asyncio
async def test_devils_advocate(ctx):
    agent = DevilsAdvocateAgent()
    decision = await agent.analyze(ctx)
    # In clean conditions, DA should not veto
    assert decision.signal in (Signal.SELL, Signal.HOLD)


@pytest.mark.asyncio
async def test_full_pipeline(ctx):
    tech = TechnicalAnalystAgent()
    sent = SentimentAgent(api_key="")
    quant = QuantAgent()
    of = OrderFlowAgent()
    da = DevilsAdvocateAgent()
    meta = ConsensusEngine()

    decisions = await asyncio.gather(
        tech.analyze(ctx),
        sent.analyze(ctx),
        quant.analyze(ctx),
        of.analyze(ctx),
    )
    da_decision = await da.analyze(ctx)
    signal = meta.evaluate(
        asset=ctx.asset,
        request_id="test-001",
        regime=ctx.regime,
        decisions=list(decisions),
        da_decision=da_decision,
    )

    assert signal.asset == "BTC/USDT"
    assert isinstance(signal.final_decision, bool)
    assert len(signal.agents) >= 4
    assert signal.reason
    result = signal.to_dict()
    assert "final_decision" in result
    assert "confidence" in result
    print("\n✓ Full pipeline result:")
    import json
    print(json.dumps(result, indent=2))


@pytest.mark.asyncio
async def test_risk_engine(ctx):
    meta = ConsensusEngine()
    tech = TechnicalAnalystAgent()
    sent = SentimentAgent(api_key="")
    quant = QuantAgent()
    of = OrderFlowAgent()
    da = DevilsAdvocateAgent()

    decisions = await asyncio.gather(tech.analyze(ctx), sent.analyze(ctx), quant.analyze(ctx), of.analyze(ctx))
    da_d = await da.analyze(ctx)
    signal = meta.evaluate("BTC/USDT", "test-002", ctx.regime, list(decisions), da_d)

    portfolio = PortfolioState(
        equity=100_000.0, cash=100_000.0, open_trades=2,
        daily_pnl_pct=0.01, max_daily_drawdown_pct=0.005,
        positions={"ETH/USDT": 10_000.0},
    )
    risk = RiskEngine()
    result = risk.check(signal, portfolio, ctx.current_price)
    assert result.status is not None
    print(f"\n✓ Risk check: {result.status.value}, size={result.approved_position_size_usd:.0f} USD")


@pytest.mark.asyncio
async def test_backtester():
    provider = MockProvider(seed=99)
    candles = await provider.get_candles("BTC/USDT", "1h", 600)
    bt = Backtester(warmup_bars=200, sl_pct=0.02, tp_pct=0.04)
    result = await bt.run("BTC/USDT", candles)
    assert result.total_trades >= 0
    summary = result.summary()
    print("\n✓ Backtest summary:")
    import json
    print(json.dumps(summary, indent=2))
