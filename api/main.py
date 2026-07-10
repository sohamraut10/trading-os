"""
FastAPI — Trading OS Main Entry Point
Exposes REST endpoints for signal generation, backtesting, portfolio status, and system health.
WebSocket endpoint streams live signal decisions.
"""
import asyncio
import time
import uuid
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect, BackgroundTasks, Response
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from config.settings import settings
from core.agents import (
    TechnicalAnalystAgent, SentimentAgent, QuantAgent,
    OrderFlowAgent, DevilsAdvocateAgent, ConsensusEngine,
    MarketContext, TradeSignal,
)
from core.data.market_data import BinanceProvider, AlpacaProvider, MockProvider
from core.data.news_feed import NewsFeed
from core.risk.risk_engine import RiskEngine, PortfolioState
from core.execution.broker_interface import PaperBroker, SmartOrderRouter
from core.backtest.backtester import Backtester
from core.backtest.optimizer import BacktestOptimizer, ParamGrid
from core.monitoring.regime_detector import detect_regime
from core.monitoring.alerts import AlertRouter
from core.monitoring.trade_journal import TradeJournal
from core.monitoring.metrics import metrics as trading_metrics
from core.learning.adaptive_weights import AdaptiveWeightManager


# ── Application State ─────────────────────────────────────────────────────────

class AppState:
    def __init__(self):
        self.technical = TechnicalAnalystAgent()
        self.sentiment = SentimentAgent(api_key=settings.anthropic_api_key)
        self.quant = QuantAgent()
        self.order_flow = OrderFlowAgent()
        self.da = DevilsAdvocateAgent()
        self.meta = ConsensusEngine()
        self.risk = RiskEngine()

        if settings.binance_api_key:
            self.market_data = BinanceProvider(settings.binance_api_key, settings.binance_secret)
        elif settings.alpaca_api_key:
            self.market_data = AlpacaProvider(settings.alpaca_api_key, settings.alpaca_secret_key, settings.alpaca_base_url)
        else:
            self.market_data = MockProvider()

        self.news_feed = NewsFeed(
            news_api_key=settings.news_api_key,
            redis_url=settings.redis_url,
        )
        self.broker = PaperBroker()
        self.router = SmartOrderRouter(self.broker, slippage_tolerance_bps=settings.slippage_tolerance_bps)
        self.journal = TradeJournal(api_key=settings.anthropic_api_key)
        self.alerts = AlertRouter()
        self.weights_manager = AdaptiveWeightManager()

        if settings.telegram_bot_token:
            self.alerts.add_telegram(settings.telegram_bot_token, settings.telegram_chat_id)

        # In-memory portfolio state (production: load from DB)
        self.portfolio = PortfolioState(
            equity=100_000.0,
            cash=100_000.0,
            open_trades=0,
            daily_pnl_pct=0.0,
            max_daily_drawdown_pct=0.0,
            positions={},
        )

        # Active WebSocket connections for live streaming
        self._ws_clients: list[WebSocket] = []


state = AppState()


@asynccontextmanager
async def lifespan(app: FastAPI):
    await state.news_feed.setup()
    yield


app = FastAPI(
    title="Trading OS",
    version="1.0.0",
    description="Multi-Agent Consensus Trading System",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Request / Response Models ─────────────────────────────────────────────────

class AnalyzeRequest(BaseModel):
    asset: str = Field(..., example="BTCUSDT")
    timeframe: str = Field("1h", example="1h")
    candle_limit: int = Field(300, ge=50, le=1000)
    execute_if_signal: bool = Field(False)


class BacktestRequest(BaseModel):
    asset: str
    timeframe: str = "1h"
    candle_limit: int = Field(500, ge=200, le=2000)


# ── Endpoints ─────────────────────────────────────────────────────────────────

@app.get("/health")
async def health():
    return {
        "status": "ok",
        "timestamp": time.time(),
        "version": "1.0.0",
        "portfolio_equity": (await state.broker.get_account())["equity"],
    }


@app.get("/metrics", response_class=Response)
async def prometheus_metrics():
    """Prometheus-compatible /metrics endpoint."""
    account = await state.broker.get_account()
    positions = await state.broker.get_positions()
    trading_metrics.update_portfolio(
        equity=account["equity"],
        exposure=sum(abs(v.get("value", 0)) for v in positions.values()) / max(account["equity"], 1),
        daily_pnl=state.portfolio.daily_pnl_pct,
        open_trades=len(positions),
        consecutive_losses=state.portfolio.consecutive_losses,
        circuit_breaker=state.portfolio.daily_pnl_pct <= -state.risk.cfg.max_daily_drawdown,
    )
    return Response(content=trading_metrics.render(), media_type="text/plain; version=0.0.4")


@app.post("/analyze")
async def analyze(req: AnalyzeRequest, background_tasks: BackgroundTasks) -> dict:
    """
    Core endpoint: run full multi-agent consensus pipeline on a given asset.
    Returns the complete TradeSignal including per-agent decisions and explainability.
    """
    request_id = str(uuid.uuid4())

    # ── Fetch data ────────────────────────────────────────────────────────────
    candles, current_price = await asyncio.gather(
        state.market_data.get_candles(req.asset, req.timeframe, req.candle_limit),
        state.market_data.get_current_price(req.asset),
    )

    order_book, headlines, sentiment_raw, macro_ctx = await asyncio.gather(
        state.market_data.get_order_book(req.asset),
        state.news_feed.get_news_headlines(req.asset),
        state.news_feed.get_social_sentiment(req.asset),
        state.news_feed.get_macro_context(),
    )

    regime = detect_regime(candles, vix=macro_ctx.get("vix", 20))

    ctx = MarketContext(
        asset=req.asset,
        timeframe=req.timeframe,
        candles=candles,
        current_price=current_price,
        order_book=order_book,
        news_headlines=headlines,
        sentiment_raw=sentiment_raw,
        macro_context=macro_ctx,
        portfolio_context={"active_strategy": "swing", "consecutive_losses": state.portfolio.consecutive_losses},
        regime=regime,
        request_id=request_id,
    )

    # ── Run agents in parallel ────────────────────────────────────────────────
    agent_decisions = await asyncio.gather(
        state.technical.analyze(ctx),
        state.sentiment.analyze(ctx),
        state.quant.analyze(ctx),
        state.order_flow.analyze(ctx),
    )

    da_decision = await state.da.analyze(ctx)

    # ── Consensus ─────────────────────────────────────────────────────────────
    signal = state.meta.evaluate(
        asset=req.asset,
        request_id=request_id,
        regime=regime,
        decisions=list(agent_decisions),
        da_decision=da_decision,
    )

    # ── Risk check ────────────────────────────────────────────────────────────
    risk_result = state.risk.check(signal, state.portfolio, current_price)
    signal_dict = signal.to_dict()
    signal_dict["risk_check"] = {
        "status": risk_result.status.value,
        "approved_size_pct": risk_result.approved_position_size_pct,
        "approved_size_usd": risk_result.approved_position_size_usd,
        "stop_loss_price": risk_result.stop_loss_price,
        "take_profit_price": risk_result.take_profit_price,
        "rejections": risk_result.rejection_reasons,
        "warnings": risk_result.warnings,
    }

    # ── Execute if requested and signal is true ────────────────────────────────
    if req.execute_if_signal and signal.final_decision and risk_result.is_tradeable():
        background_tasks.add_task(_execute_trade, signal, risk_result, current_price)

    # ── Log, alert, and metrics async ────────────────────────────────────────
    cycle_ms = (time.time() - float(signal.timestamp)) * 1000
    background_tasks.add_task(trading_metrics.update_cycle, req.asset, cycle_ms, signal_dict)
    background_tasks.add_task(state.journal.log_signal, signal, current_price)
    background_tasks.add_task(state.alerts.signal_generated, signal)
    background_tasks.add_task(_broadcast_ws, signal_dict)

    return signal_dict


@app.post("/backtest")
async def backtest(req: BacktestRequest) -> dict:
    candles = await state.market_data.get_candles(req.asset, req.timeframe, req.candle_limit)
    bt = Backtester()
    result = await bt.run(req.asset, candles, req.timeframe)
    return result.summary()


class OptimizeRequest(BaseModel):
    asset: str
    timeframe: str = "1h"
    candle_limit: int = Field(800, ge=400, le=2000)
    top_k: int = Field(5, ge=1, le=10)


@app.post("/backtest/optimize")
async def optimize(req: OptimizeRequest) -> dict:
    """Grid search over SL/TP parameters with walk-forward validation."""
    candles = await state.market_data.get_candles(req.asset, req.timeframe, req.candle_limit)
    optimizer = BacktestOptimizer(top_k=req.top_k)
    results = await optimizer.optimize(req.asset, candles, req.timeframe)
    return {
        "asset": req.asset,
        "timeframe": req.timeframe,
        "candles_used": len(candles),
        "combinations_tested": len(optimizer.grid.combinations()),
        "results": [r.summary() for r in results],
    }


@app.get("/portfolio")
async def portfolio() -> dict:
    account = await state.broker.get_account()
    positions = await state.broker.get_positions()
    return {
        "equity": account["equity"],
        "cash": account["cash"],
        "positions": positions,
        "daily_pnl_pct": state.portfolio.daily_pnl_pct,
        "open_trades": len(positions),
    }


@app.get("/agents/performance")
async def agent_performance() -> dict:
    return state.weights_manager.get_performance_report()


@app.post("/portfolio/reset")
async def reset_portfolio() -> dict:
    state.broker = PaperBroker()
    state.router = SmartOrderRouter(state.broker)
    return {"status": "reset", "initial_equity": 100_000.0}


@app.websocket("/ws/signals")
async def ws_signals(websocket: WebSocket):
    """
    WebSocket: streams every signal decision in real-time to connected dashboards.
    """
    await websocket.accept()
    state._ws_clients.append(websocket)
    try:
        while True:
            await websocket.receive_text()  # keep-alive ping
    except WebSocketDisconnect:
        state._ws_clients.remove(websocket)


# ── Background helpers ────────────────────────────────────────────────────────

async def _execute_trade(signal: TradeSignal, risk_result, current_price: float):
    bracket = await state.router.execute_bracket(signal, risk_result, current_price)
    state.portfolio.open_trades += 1


async def _broadcast_ws(data: dict):
    import json
    dead = []
    for ws in state._ws_clients:
        try:
            await ws.send_json(data)
        except Exception:
            dead.append(ws)
    for ws in dead:
        state._ws_clients.remove(ws)
