"""
FastAPI — Trading OS Main Entry Point
Exposes REST endpoints for signal generation, backtesting, portfolio status, and system health.
WebSocket endpoint streams live signal decisions.
"""
import asyncio
import logging
import os
import time
import uuid
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, APIRouter, HTTPException, WebSocket, WebSocketDisconnect, BackgroundTasks, Response, Depends, Header
from fastapi.responses import HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from config.settings import settings
from core.data.market_data import BinanceProvider, AlpacaProvider, MockProvider
from core.data.news_feed import NewsFeed
from core.risk.risk_engine import RiskEngine, PortfolioState
from core.execution.broker_interface import PaperBroker, SmartOrderRouter, AlpacaBroker
from core.backtest.backtester import Backtester
from core.backtest.optimizer import BacktestOptimizer, ParamGrid
from core.monitoring.alerts import AlertRouter
from core.monitoring.trade_journal import TradeJournal
from core.monitoring.metrics import metrics as trading_metrics
from core.learning.adaptive_weights import AdaptiveWeightManager
from core.streaming.event_bus import EventBus
from core.persistence.repository import Repository
from core.orchestrator import Orchestrator

log = logging.getLogger("trading_os.api")


# ── Application State ─────────────────────────────────────────────────────────

class AppState:
    def __init__(self):
        self.risk = RiskEngine()

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
        self.pinned_strategy: str | None = None
        self._ws_events_clients: list[WebSocket] = []
        self.db = Repository(settings.database_url)
        self.event_bus.on("*", self.db.record_event)

        # One Orchestrator per (asset, timeframe), lazily created and reused
        # across cycles so /analyze and the live-suggestions loop share a
        # single pipeline implementation instead of two independent copies.
        self.orchestrators: dict[tuple[str, str], Orchestrator] = {}

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


def require_cron_secret(authorization: str | None = Header(default=None)) -> None:
    """
    Gate for POST /cron/tick. Fails closed (unlike require_api_key): with no
    cron_secret configured the endpoint is disabled outright, since it exists
    only to be wired into a scheduler (Vercel Cron) and has no legitimate
    unauthenticated caller.
    """
    if not settings.cron_secret:
        raise HTTPException(status_code=503, detail="Cron endpoint not configured (cron_secret unset)")
    if authorization != f"Bearer {settings.cron_secret}":
        raise HTTPException(status_code=401, detail="Invalid or missing cron secret")


def _get_or_create_orchestrator(asset: str, timeframe: str, candle_limit: int) -> Orchestrator:
    """
    One Orchestrator per (asset, timeframe), reused across calls so /analyze
    and live_suggestions_loop run the exact same pipeline implementation
    (agents, strategy selection, risk gating, execution) rather than two
    independently-maintained copies that can silently drift apart.
    """
    key = (asset, timeframe)
    orch = state.orchestrators.get(key)
    if orch is None:
        orch = Orchestrator(
            asset=asset,
            timeframe=timeframe,
            data_provider=state.market_data,
            news_feed=state.news_feed,
            portfolio=state.portfolio,
            router=state.router,
            alerts=state.alerts,
            journal=state.journal,
            weights_manager=state.weights_manager,
            candle_limit=candle_limit,
            cycle_interval_sec=settings.live_suggestions_interval_sec,
            event_bus=state.event_bus,
            repository=state.db,
        )
        state.orchestrators[key] = orch
    orch._candle_limit = candle_limit
    return orch


async def run_consensus_cycle(asset: str, timeframe: str, candle_limit: int, execute_if_signal: bool) -> dict:
    orch = _get_or_create_orchestrator(asset, timeframe, candle_limit)
    orch._auto_execute = execute_if_signal
    orch._strategy_override = state.pinned_strategy

    result = await orch.run_cycle()
    if result.error:
        return {"error": result.error, "asset": asset, "request_id": result.request_id}

    signal = result.signal
    signal_dict = signal.to_dict()
    signal_dict["current_price"] = result.current_price
    signal_dict["executed"] = result.executed

    rr = result.risk_result
    if rr is not None:
        signal_dict["risk_check"] = {
            "status": rr.status.value,
            "approved_size_pct": rr.approved_position_size_pct,
            "approved_size_usd": rr.approved_position_size_usd,
            "stop_loss_price": rr.stop_loss_price,
            "take_profit_price": rr.take_profit_price,
            "size_usd": rr.approved_position_size_usd,
            "sl_price": rr.stop_loss_price,
            "tp_price": rr.take_profit_price,
            "rejections": rr.rejection_reasons,
            "warnings": rr.warnings,
        }

    try:
        trading_metrics.update_cycle(asset, result.cycle_ms, signal_dict)
    except Exception:
        log.exception("Failed to update metrics for %s", asset)

    return signal_dict


async def live_suggestions_loop():
    """Loop indefinitely through the configured watchlist, pushing live suggestions to the websocket."""
    if not settings.enable_live_suggestions:
        log.info("Live suggestions loop disabled via settings.enable_live_suggestions")
        return
    assets = [a.strip() for a in settings.live_suggestions_assets.split(",") if a.strip()]
    while True:
        for asset in assets:
            await run_consensus_cycle(asset, timeframe="1h", candle_limit=300, execute_if_signal=False)
            await asyncio.sleep(settings.live_suggestions_interval_sec)


@asynccontextmanager
async def lifespan(app: FastAPI):
    await state.news_feed.setup()
    await state.db.connect()

    # Resume from the last known portfolio state instead of resetting to the
    # hardcoded starting equity — critical on serverless, where every cold
    # start otherwise gets a fresh in-memory PortfolioState.
    snapshot = await state.db.load_latest_portfolio_snapshot()
    if snapshot:
        state.portfolio.equity = float(snapshot["equity"])
        state.portfolio.cash = float(snapshot["cash"])
        state.portfolio.daily_pnl_pct = float(snapshot["daily_pnl_pct"])
        state.portfolio.open_trades = int(snapshot["open_trades"])
        # /portfolio, /health, and /metrics all read equity/cash from the
        # broker's own ledger, not from state.portfolio — for PaperBroker
        # (the default with no Alpaca key) that ledger is otherwise just as
        # in-memory-only as state.portfolio was, so it needs the same resume.
        # Only cash can be restored: PaperBroker derives reported equity as
        # cash + open-positions-value, and per-symbol positions aren't
        # captured in portfolio_snapshots, so open positions — and any
        # unrealized P&L they represented — are still lost across a cold
        # start. Equity will read as (resumed cash + 0 positions) until a
        # new trade opens a position again.
        if isinstance(state.broker, PaperBroker):
            state.broker._cash = state.portfolio.cash
        log.info("Resumed portfolio from snapshot: equity=%.2f", state.portfolio.equity)

    # There's no persistent process to run this loop in on serverless
    # platforms (e.g. Vercel sets VERCEL=1) — a Vercel Cron hitting
    # POST /cron/tick replaces it there instead.
    loop_task = None
    if not os.environ.get("VERCEL"):
        loop_task = asyncio.create_task(live_suggestions_loop())
    try:
        yield
    finally:
        if loop_task:
            loop_task.cancel()
        await state.db.close()


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

# Routes are defined on `router` (below) rather than `app` directly so
# api/index.py (the Vercel entrypoint) can include the exact same route
# definitions under an /api prefix on its own top-level FastAPI instance.
# Mounting sub-apps via app.mount() doesn't run their lifespan — this
# avoids that trap by never mounting an app-with-a-lifespan as a sub-app.
router = APIRouter()

# Serve the built dashboard's assets if it's been built; skip otherwise
# rather than creating empty directories in the working tree on every boot.
from fastapi.staticfiles import StaticFiles
_dashboard_assets_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "dashboard", "dist", "assets")
if os.path.isdir(_dashboard_assets_dir):
    app.mount("/assets", StaticFiles(directory=_dashboard_assets_dir), name="assets")
else:
    log.info("Dashboard build not found at %s — skipping /assets mount", _dashboard_assets_dir)


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


@router.get("/", response_class=HTMLResponse)
@router.get("/dashboard", response_class=HTMLResponse)
async def get_dashboard():
    import os
    dashboard_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "dashboard", "dist", "index.html")
    if not os.path.exists(dashboard_path):
        dashboard_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "dashboard", "index.html")
    with open(dashboard_path, "r", encoding="utf-8") as f:
        return f.read()


@router.get("/candles")
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


@router.get("/health")
async def health():
    return {
        "status": "ok",
        "timestamp": time.time(),
        "version": "1.0.0",
        "portfolio_equity": (await state.broker.get_account())["equity"],
    }


@router.get("/metrics", response_class=Response)
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


@router.post("/analyze")
async def analyze(req: AnalyzeRequest, x_api_key: str | None = Header(default=None, alias="X-API-Key")) -> dict:
    """
    Core endpoint: run full multi-agent consensus pipeline on a given asset.
    Returns the complete TradeSignal including per-agent decisions and explainability.
    Requires the API key when execute_if_signal=True, since that path can place real orders.
    """
    if req.execute_if_signal:
        require_api_key(x_api_key)
    return await run_consensus_cycle(req.asset, req.timeframe, req.candle_limit, req.execute_if_signal)


@router.post("/backtest")
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


@router.post("/backtest/optimize")
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


@router.get("/portfolio")
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


@router.get("/agents/performance")
async def agent_performance() -> dict:
    return state.weights_manager.get_performance_report()


@router.post("/portfolio/reset")
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


@router.post("/trade/submit")
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
    asyncio.create_task(state.db.snapshot_portfolio(state.portfolio))
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


@router.post("/portfolio/close")
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
    asyncio.create_task(state.db.snapshot_portfolio(state.portfolio))
    return {
        "status": "closed",
        "asset": req.asset,
        "qty": filled_order.filled_qty,
        "price": filled_order.avg_fill_price,
    }


class StrategySelectRequest(BaseModel):
    strategy: str | None

@router.post("/strategy/select")
async def post_strategy_select(req: StrategySelectRequest, _: None = Depends(require_api_key)):
    state.pinned_strategy = req.strategy
    return {"status": "success", "pinned_strategy": req.strategy}


@router.get("/cycles/{cycle_id}/events")
async def get_cycle_events(cycle_id: str):
    return state.event_bus.get_cycle_events(cycle_id)


@router.get("/events/recent")
async def get_recent_events(after: str | None = None, limit: int = 200):
    """
    Polling replacement for /ws/events on deployments that can't hold a
    WebSocket open (e.g. Vercel serverless functions). Returns events in the
    same {event_id, cycle_id, ts, type, payload} shape the dashboard's event
    reducer already expects. Pass the last-seen event_id as `after` to fetch
    only what's new. Requires DB persistence (state.db) to be connected;
    returns an empty list otherwise.
    """
    limit = max(1, min(limit, 500))
    return await state.db.fetch_recent_events(after=after, limit=limit)


@router.get("/cron/tick")
async def cron_tick(_: None = Depends(require_cron_secret)) -> dict:
    """
    Runs one consensus cycle per watchlist asset. Replaces live_suggestions_loop
    on serverless platforms with no persistent process to run a background
    loop in. Vercel Cron invokes paths with GET and, when a CRON_SECRET env
    var is set on the project, automatically sends
    `Authorization: Bearer <CRON_SECRET>` — matching require_cron_secret
    exactly, so no extra wiring is needed beyond vercel.json's crons entry.
    """
    if not settings.enable_live_suggestions:
        return {"status": "disabled", "results": []}
    assets = [a.strip() for a in settings.live_suggestions_assets.split(",") if a.strip()]
    results = []
    for asset in assets:
        signal_dict = await run_consensus_cycle(asset, timeframe="1h", candle_limit=300, execute_if_signal=False)
        results.append({"asset": asset, "final_decision": signal_dict.get("final_decision", "ERROR")})
    return {"status": "ok", "results": results}


@router.websocket("/ws/events")
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


@router.websocket("/ws/signals")
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


app.include_router(router)


