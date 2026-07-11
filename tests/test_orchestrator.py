"""Tests for the Orchestrator — single cycle, strategy wiring, error recovery."""
import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from core.orchestrator import Orchestrator, CycleResult
from core.data.market_data import MockProvider
from core.data.news_feed import NewsFeed
from core.risk.risk_engine import PortfolioState
from core.execution.broker_interface import PaperBroker, SmartOrderRouter
from core.monitoring.alerts import AlertRouter
from core.monitoring.trade_journal import TradeJournal
from core.learning.adaptive_weights import AdaptiveWeightManager


def _make_orchestrator(auto_execute=False) -> Orchestrator:
    provider = MockProvider(seed=42)
    news = NewsFeed()
    portfolio = PortfolioState(
        equity=100_000.0, cash=100_000.0, open_trades=0,
        daily_pnl_pct=0.0, max_daily_drawdown_pct=0.0, positions={},
    )
    broker = PaperBroker()
    router = SmartOrderRouter(broker)
    alerts = AlertRouter()          # console-only, no network
    journal = TradeJournal()        # no LLM key — skips AI analysis
    weights = AdaptiveWeightManager(persistence_path="/tmp/test_weights.json")

    return Orchestrator(
        asset="BTCUSDT",
        timeframe="1h",
        data_provider=provider,
        news_feed=news,
        portfolio=portfolio,
        router=router,
        alerts=alerts,
        journal=journal,
        weights_manager=weights,
        candle_limit=300,
        cycle_interval_sec=999,     # never fires in tests
        auto_execute=auto_execute,
    )


@pytest.mark.asyncio
async def test_single_cycle_returns_result():
    orch = _make_orchestrator()
    result = await orch.run_cycle()
    assert isinstance(result, CycleResult)
    assert result.asset == "BTCUSDT"
    assert result.error is None


@pytest.mark.asyncio
async def test_cycle_increments_count():
    orch = _make_orchestrator()
    assert orch.cycle_count == 0
    await orch.run_cycle()
    assert orch.cycle_count == 1
    await orch.run_cycle()
    assert orch.cycle_count == 2


@pytest.mark.asyncio
async def test_cycle_sets_last_signal():
    orch = _make_orchestrator()
    assert orch.last_signal is None
    await orch.run_cycle()
    assert orch.last_signal is not None


@pytest.mark.asyncio
async def test_cycle_result_has_strategy():
    orch = _make_orchestrator()
    result = await orch.run_cycle()
    assert result.strategy_name in (
        "scalping", "swing", "mean_reversion", "trend_follow", "arbitrage"
    )


@pytest.mark.asyncio
async def test_cycle_populates_history():
    orch = _make_orchestrator()
    await orch.run_cycle()
    await orch.run_cycle()
    history = orch.recent_history(10)
    assert len(history) == 2
    assert all("asset" in h for h in history)


@pytest.mark.asyncio
async def test_cycle_strategy_reason_populated():
    orch = _make_orchestrator()
    result = await orch.run_cycle()
    assert isinstance(result.strategy_reason, str)
    assert len(result.strategy_reason) > 0


@pytest.mark.asyncio
async def test_no_execution_when_auto_execute_false():
    orch = _make_orchestrator(auto_execute=False)
    result = await orch.run_cycle()
    assert result.executed is False


@pytest.mark.asyncio
async def test_cycle_recovers_from_data_error():
    orch = _make_orchestrator()
    # Patch data provider to raise
    orch._data.get_candles = AsyncMock(side_effect=RuntimeError("network error"))
    result = await orch.run_cycle()
    assert result.error is not None
    assert "network error" in result.error


@pytest.mark.asyncio
async def test_cycle_survives_order_book_failure():
    orch = _make_orchestrator()
    orch._data.get_order_book = AsyncMock(side_effect=RuntimeError("order book unavailable"))
    result = await orch.run_cycle()
    # A synthetic order book should be substituted so the cycle still completes
    assert result.error is None
    assert result.signal is not None


@pytest.mark.asyncio
async def test_cycle_result_always_has_risk_result():
    orch = _make_orchestrator()
    result = await orch.run_cycle()
    assert result.error is None
    assert result.risk_result is not None
    assert result.current_price > 0


@pytest.mark.asyncio
async def test_portfolio_orchestrator_status():
    from core.orchestrator import PortfolioOrchestrator
    provider = MockProvider(seed=1)
    news = NewsFeed()
    portfolio = PortfolioState(100_000, 100_000, 0, 0, 0, {})
    broker = PaperBroker()
    router = SmartOrderRouter(broker)
    alerts = AlertRouter()
    journal = TradeJournal()
    weights = AdaptiveWeightManager(persistence_path="/tmp/test_port_weights.json")

    watchlist = [
        {"asset": "BTCUSDT", "timeframe": "1h"},
        {"asset": "ETHUSDT", "timeframe": "1h"},
    ]
    port_orch = PortfolioOrchestrator(
        watchlist=watchlist,
        data_provider=provider,
        news_feed=news,
        portfolio=portfolio,
        router=router,
        alerts=alerts,
        journal=journal,
        weights_manager=weights,
    )
    status = port_orch.status()
    assert len(status) == 2
    assert {s["asset"] for s in status} == {"BTCUSDT", "ETHUSDT"}
