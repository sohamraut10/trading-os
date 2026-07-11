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

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect, BackgroundTasks, Response, Depends, Header
from fastapi.responses import HTMLResponse
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
from core.execution.broker_interface import PaperBroker, SmartOrderRouter, AlpacaBroker
from core.backtest.backtester import Backtester
from core.backtest.optimizer import BacktestOptimizer, ParamGrid
from core.monitoring.regime_detector import detect_regime
from core.monitoring.alerts import AlertRouter
from core.monitoring.trade_journal import TradeJournal
from core.monitoring.metrics import metrics as trading_metrics
from core.learning.adaptive_weights import AdaptiveWeightManager
from core.streaming.event_bus import EventBus
from core.strategy.selector import StrategySelector


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

        import os
        mock_mode = (
            os.environ.get("MOCK_MODE", "false").lower() == "true"
        )
        self.mock_mode = mock_mode
        if mock_mode:
            self.market_data = MockProvider()
        else:
            self.market_data = BinanceProvider(settings.binance_api_key or "", settings.binance_secret or "")

        self.news_feed = NewsFeed(
            news_api_key=settings.news_api_key,
            redis_url=settings.redis_url,
        )
        alpaca_configured = (
            settings.alpaca_api_key and
            not settings.alpaca_api_key.startswith("your_")
        )
        if alpaca_configured:
            self.broker = AlpacaBroker(
                api_key=settings.alpaca_api_key,
                secret_key=settings.alpaca_secret_key,
                base_url=settings.alpaca_base_url,
            )
        else:
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
        self.event_bus = EventBus(settings.kafka_bootstrap_servers)
        self.strategy_selector = StrategySelector()
        self.pinned_strategy: str | None = None
        self._ws_events_clients: list[WebSocket] = []

        # Wire event bus * publisher listener to broadcast to WebSocket clients
        async def broadcast_event(event_dict: dict):
            import json
            # Broadcast to ws_events_clients
            dead_events = []
            for ws in self._ws_events_clients:
                try:
                    await ws.send_json(event_dict)
                except Exception:
                    dead_events.append(ws)
            for ws in dead_events:
                if ws in self._ws_events_clients:
                    self._ws_events_clients.remove(ws)
                    
            # If event is FinalCall, also broadcast to ws_clients (signals WS)
            if event_dict["type"] == "FinalCall":
                dead_signals = []
                for ws in self._ws_clients:
                    try:
                        await ws.send_json(event_dict["payload"])
                    except Exception:
                        dead_signals.append(ws)
                for ws in dead_signals:
                    if ws in self._ws_clients:
                        self._ws_clients.remove(ws)
        
        self.event_bus.on("*", broadcast_event)


state = AppState()


def require_api_key(x_api_key: str | None = Header(default=None, alias="X-API-Key")) -> None:
    """
    Gate for state-changing endpoints (trade submission, portfolio mutation,
    strategy overrides). No-op when api_auth_token is unset, so local/dev use
    is unaffected until an operator opts in by setting the token.
    """
    if settings.api_auth_token and x_api_key != settings.api_auth_token:
        raise HTTPException(status_code=401, detail="Invalid or missing API key")


async def run_consensus_cycle(asset: str, timeframe: str, candle_limit: int, execute_if_signal: bool) -> dict:
    from core.agents.base_agent import MarketContext, OrderBook, OrderBookLevel
    request_id = str(uuid.uuid4())
    t0 = time.perf_counter()
    try:
        if state.mock_mode:
            candles = await state.market_data.get_candles(asset, timeframe, candle_limit)
            current_price = candles[-1].close
            order_book = await state.market_data.get_order_book(asset)
            headlines = ["Bitcoin institutional inflows continue", "Regulatory hurdles clear for Ethereum layer 2s"]
            sentiment_raw = {"reddit": {"mention_count": 10, "avg_score": 100, "titles": ["Bullish on BTC"]}, "timestamp": time.time()}
            macro_ctx = {"vix": 18.5, "sp500_1d_change_pct": 0.3, "near_fed_event": False, "days_to_earnings": 30}
        else:
            candles, current_price = await asyncio.gather(
                state.market_data.get_candles(asset, timeframe, candle_limit),
                state.market_data.get_current_price(asset),
            )

            try:
                order_book = await state.market_data.get_order_book(asset)
            except Exception:
                bids = [OrderBookLevel(price=current_price * (1 - 0.0001 * (i + 1)), size=1.0) for i in range(20)]
                asks = [OrderBookLevel(price=current_price * (1 + 0.0001 * (i + 1)), size=1.0) for i in range(20)]
                order_book = OrderBook(bids=bids, asks=asks, timestamp=time.time())

        # 1. Emit BarClosed to event bus
        await state.event_bus.publish("BarClosed", request_id, {
            "asset": asset,
            "bar": {
                "timestamp": time.time(),
                "open": current_price,
                "high": current_price,
                "low": current_price,
                "close": current_price,
                "volume": 1.0
            }
        })

        if not state.mock_mode:
            headlines, sentiment_raw, macro_ctx = await asyncio.gather(
                state.news_feed.get_news_headlines(asset),
                state.news_feed.get_social_sentiment(asset),
                state.news_feed.get_macro_context(),
            )

        regime = detect_regime(candles, vix=macro_ctx.get("vix", 20))
        # 2. Emit RegimeUpdated
        await state.event_bus.publish("RegimeUpdated", request_id, {"regime": regime})

        # 3. Strategy selection
        strategy, reason, hurst, vol_pct = state.strategy_selector.select(MarketContext(
            asset=asset, timeframe=timeframe, candles=candles, current_price=current_price, regime=regime
        ))
        
        # Override if pinned
        if state.pinned_strategy:
            from core.strategy.strategies import STRATEGY_REGISTRY
            from core.strategy.strategy_base import StrategyType
            strategy = STRATEGY_REGISTRY.get(StrategyType(state.pinned_strategy), strategy)
            reason = "User pinned override"

        await state.event_bus.publish("StrategySelected", request_id, {
            "strategy": strategy.strategy_type.value,
            "reason": reason,
            "hurst": hurst,
            "vol_percentile": vol_pct
        })

        hypothesis = state.strategy_selector.emit_hypothesis(strategy, MarketContext(
            asset=asset, timeframe=timeframe, candles=candles, current_price=current_price, regime=regime
        ), hurst, vol_pct)
        await state.event_bus.publish("HypothesisEmitted", request_id, hypothesis.__dict__ if hasattr(hypothesis, "__dict__") else hypothesis)

        ctx = MarketContext(
            asset=asset,
            timeframe=timeframe,
            candles=candles,
            current_price=current_price,
            order_book=order_book,
            news_headlines=headlines,
            sentiment_raw=sentiment_raw,
            macro_context=macro_ctx,
            portfolio_context={"active_strategy": strategy.strategy_type.value, "consecutive_losses": state.portfolio.consecutive_losses},
            regime=regime,
            request_id=request_id,
            hypothesis=hypothesis
        )

        # 4. Agent parallel analysis
        agent_decisions = await asyncio.gather(
            state.technical.analyze(ctx),
            state.sentiment.analyze(ctx),
            state.quant.analyze(ctx),
            state.order_flow.analyze(ctx),
        )
        
        # Emit ScreeningResult for each voting agent
        for d in agent_decisions:
            await state.event_bus.publish("ScreeningResult", request_id, d.to_dict())

        da_decision = await state.da.analyze(ctx)
        # Emit ScreeningResult for DA
        await state.event_bus.publish("ScreeningResult", request_id, da_decision.to_dict())

        # 5. Await evaluate with event_bus
        signal = await state.meta.evaluate(
            asset=asset,
            request_id=request_id,
            regime=regime,
            decisions=list(agent_decisions),
            da_decision=da_decision,
            hypothesis=hypothesis,
            event_bus=state.event_bus
        )

        await state.event_bus.publish("VerdictReached", request_id, signal.to_dict())

        risk_result = state.risk.check(signal, state.portfolio, current_price)
        
        await state.event_bus.publish("SanitizationApplied", request_id, {
            "status": risk_result.status.value,
            "approved_size_pct": risk_result.approved_position_size_pct,
            "stop_loss_price": risk_result.stop_loss_price,
            "take_profit_price": risk_result.take_profit_price,
            "sanitization_diff": risk_result.sanitization_diff
        })

        signal_dict = signal.to_dict()
        signal_dict["current_price"] = current_price
        signal_dict["risk_check"] = {
            "status": risk_result.status.value,
            "approved_size_pct": risk_result.approved_position_size_pct,
            "approved_size_usd": risk_result.approved_position_size_usd,
            "stop_loss_price": risk_result.stop_loss_price,
            "take_profit_price": risk_result.take_profit_price,
            "size_usd": risk_result.approved_position_size_usd,
            "sl_price": risk_result.stop_loss_price,
            "tp_price": risk_result.take_profit_price,
            "rejections": risk_result.rejection_reasons,
            "warnings": risk_result.warnings,
        }

        if execute_if_signal and signal.final_decision and risk_result.is_tradeable():
            asyncio.create_task(_execute_trade(signal, risk_result, current_price))
            await state.event_bus.publish("OrderPlaced", request_id, {
                "asset": asset,
                "side": signal.action.value if signal.action else "HOLD",
                "size_usd": risk_result.approved_position_size_usd,
                "price": current_price
            })

        await state.event_bus.publish("FinalCall", request_id, signal.to_dict())

        cycle_ms = (time.perf_counter() - t0) * 1000

        async def run_bg_task(func, *args):
            try:
                if asyncio.iscoroutinefunction(func):
                    await func(*args)
                else:
                    func(*args)
            except Exception:
                pass

        asyncio.create_task(run_bg_task(trading_metrics.update_cycle, asset, cycle_ms, signal_dict))
        asyncio.create_task(run_bg_task(state.journal.log_signal, signal, current_price))
        asyncio.create_task(run_bg_task(state.alerts.signal_generated, signal))

        return signal_dict
    except Exception as e:
        print(f"Error in consensus cycle for {asset}: {e}")
        return {}


async def live_suggestions_loop():
    # Loop indefinitely through major Forex and Crypto assets to push live suggestions to the websocket
    assets = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "EURUSD", "GBPUSD", "USDJPY"]
    while True:
        for asset in assets:
            await run_consensus_cycle(asset, timeframe="1h", candle_limit=300, execute_if_signal=False)
            await asyncio.sleep(15)


@asynccontextmanager
async def lifespan(app: FastAPI):
    await state.news_feed.setup()
    loop_task = asyncio.create_task(live_suggestions_loop())
    try:
        yield
    finally:
        loop_task.cancel()


app = FastAPI(
    title="Trading OS",
    version="1.0.0",
    description="Multi-Agent Consensus Trading System",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in settings.cors_allowed_origins.split(",") if o.strip()],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Initialize static files directory dynamically
import os
os.makedirs("dashboard/dist/assets", exist_ok=True)
from fastapi.staticfiles import StaticFiles
app.mount("/assets", StaticFiles(directory="dashboard/dist/assets"), name="assets")


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


class TradeRequest(BaseModel):
    asset: str
    side: str  # "buy" | "sell"
    quantity: float


class ClosePositionRequest(BaseModel):
    asset: str


@app.get("/", response_class=HTMLResponse)
@app.get("/dashboard", response_class=HTMLResponse)
async def get_dashboard():
    import os
    dashboard_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "dashboard", "dist", "index.html")
    if not os.path.exists(dashboard_path):
        dashboard_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "dashboard", "index.html")
    with open(dashboard_path, "r", encoding="utf-8") as f:
        return f.read()


@app.get("/candles")
async def get_candles_endpoint(asset: str, timeframe: str = "1h", limit: int = 100):
    try:
        candles = await state.market_data.get_candles(asset, timeframe, limit)
        return [
            {
                "time": c.timestamp,
                "open": c.open,
                "high": c.high,
                "low": c.low,
                "close": c.close,
                "volume": c.volume,
            }
            for c in candles
        ]
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


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
async def analyze(req: AnalyzeRequest, x_api_key: str | None = Header(default=None, alias="X-API-Key")) -> dict:
    """
    Core endpoint: run full multi-agent consensus pipeline on a given asset.
    Returns the complete TradeSignal including per-agent decisions and explainability.
    Requires the API key when execute_if_signal=True, since that path can place real orders.
    """
    if req.execute_if_signal:
        require_api_key(x_api_key)
    return await run_consensus_cycle(req.asset, req.timeframe, req.candle_limit, req.execute_if_signal)


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
        "mode": "MOCK" if state.mock_mode else "LIVE",
    }


@app.get("/agents/performance")
async def agent_performance() -> dict:
    return state.weights_manager.get_performance_report()


@app.post("/portfolio/reset")
async def reset_portfolio(_: None = Depends(require_api_key)) -> dict:
    if settings.alpaca_api_key:
        state.broker = AlpacaBroker(
            api_key=settings.alpaca_api_key,
            secret_key=settings.alpaca_secret_key,
            base_url=settings.alpaca_base_url,
        )
        state.router = SmartOrderRouter(state.broker, slippage_tolerance_bps=settings.slippage_tolerance_bps)
        account = await state.broker.get_account()
        return {"status": "reconnected", "initial_equity": account["equity"]}
    else:
        state.broker = PaperBroker()
        state.router = SmartOrderRouter(state.broker, slippage_tolerance_bps=settings.slippage_tolerance_bps)
        return {"status": "reset", "initial_equity": 100_000.0}


@app.post("/trade/submit")
async def submit_trade(req: TradeRequest, _: None = Depends(require_api_key)):
    from core.execution.broker_interface import Order, OrderType
    price = await state.market_data.get_current_price(req.asset)

    risk_result = state.risk.check_manual_order(req.side, req.quantity, price, state.portfolio)
    if not risk_result.is_tradeable():
        raise HTTPException(status_code=400, detail={"rejections": risk_result.rejection_reasons})

    approved_qty = risk_result.approved_position_size_usd / price

    order = Order(
        asset=req.asset,
        side=req.side.lower(),
        quantity=approved_qty,
        order_type=OrderType.MARKET,
        limit_price=price,
    )
    filled_order = await state.broker.submit_order(order)
    state.portfolio.open_trades += 1
    return {
        "status": filled_order.status.value,
        "avg_fill_price": filled_order.avg_fill_price,
        "quantity": filled_order.filled_qty,
        "side": filled_order.side,
        "risk_check": {
            "status": risk_result.status.value,
            "warnings": risk_result.warnings,
            "sanitization_diff": risk_result.sanitization_diff,
        },
    }


@app.post("/portfolio/close")
async def close_position(req: ClosePositionRequest, _: None = Depends(require_api_key)):
    positions = await state.broker.get_positions()
    if req.asset not in positions:
        raise HTTPException(status_code=400, detail="No position in this asset")
    
    pos = positions[req.asset]
    from core.execution.broker_interface import Order, OrderType
    price = await state.market_data.get_current_price(req.asset)
    order = Order(
        asset=req.asset,
        side="sell",
        quantity=pos["qty"],
        order_type=OrderType.MARKET,
        limit_price=price,
    )
    filled_order = await state.broker.submit_order(order)
    state.portfolio.open_trades = max(0, state.portfolio.open_trades - 1)
    return {
        "status": "closed",
        "asset": req.asset,
        "qty": filled_order.filled_qty,
        "price": filled_order.avg_fill_price,
    }


class StrategySelectRequest(BaseModel):
    strategy: str | None

@app.post("/strategy/select")
async def post_strategy_select(req: StrategySelectRequest, _: None = Depends(require_api_key)):
    state.pinned_strategy = req.strategy
    return {"status": "success", "pinned_strategy": req.strategy}


@app.get("/cycles/{cycle_id}/events")
async def get_cycle_events(cycle_id: str):
    return state.event_bus.get_cycle_events(cycle_id)


@app.websocket("/ws/events")
async def ws_events(websocket: WebSocket):
    """
    WebSocket: streams every consensus cycle event in real-time.
    """
    await websocket.accept()
    state._ws_events_clients.append(websocket)
    
    # Pre-populate client with the latest cycle events so dashboard shows data immediately
    if state.event_bus.event_log:
        latest_cycle_id = state.event_bus.event_log[-1]["cycle_id"]
        latest_cycle_events = [ev for ev in state.event_bus.event_log if ev["cycle_id"] == latest_cycle_id]
        for ev in latest_cycle_events:
            try:
                await websocket.send_json(ev)
            except Exception:
                pass

    try:
        while True:
            await websocket.receive_text()  # keep-alive ping
    except WebSocketDisconnect:
        if websocket in state._ws_events_clients:
            state._ws_events_clients.remove(websocket)


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
        if websocket in state._ws_clients:
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
