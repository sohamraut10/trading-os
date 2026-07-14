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

logging.basicConfig(
    level=logging.INFO,
    format="%(levelname)s [%(name)s] %(message)s",
    force=True,
)

from fastapi import FastAPI, APIRouter, HTTPException, WebSocket, WebSocketDisconnect, BackgroundTasks, Response, Depends, Header
from fastapi.responses import HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from config.settings import settings
from core.data.market_data import BinanceProvider, AlpacaProvider, DhanProvider, MockProvider
from core.data.news_feed import NewsFeed
from core.risk.risk_engine import RiskEngine, PortfolioState
from core.execution.broker_interface import PaperBroker, SmartOrderRouter, AlpacaBroker, DhanBroker
from core.backtest.backtester import Backtester
from core.backtest.optimizer import BacktestOptimizer, ParamGrid
from core.monitoring.alerts import AlertRouter
from core.monitoring.trade_journal import TradeJournal
from core.monitoring.metrics import metrics as trading_metrics
from core.learning.adaptive_weights import AdaptiveWeightManager
from core.streaming.event_bus import EventBus
from core.persistence.repository import Repository
from core.orchestrator import Orchestrator
from core.data.instruments import scrip_master
from core.data.scanner import market_scanner
from core.monitoring.position_monitor import PositionMonitor

log = logging.getLogger("trading_os.api")


# ── Application State ─────────────────────────────────────────────────────────

def _build_broker():
    """
    Broker priority: Dhan (if DHAN_CLIENT_ID set) → Alpaca (if ALPACA_API_KEY set) → PaperBroker.
    All failures degrade gracefully to PaperBroker so the app always boots.
    """
    dhan_configured = bool(settings.dhan_client_id and settings.dhan_access_token)
    if dhan_configured:
        try:
            return DhanBroker(
                client_id=settings.dhan_client_id,
                access_token=settings.dhan_access_token,
                default_exchange=settings.dhan_default_exchange,
            )
        except Exception as e:
            if settings.environment == "production":
                raise RuntimeError("Failed to initialize DhanBroker in production") from e
            log.exception("Failed to initialize DhanBroker — falling back to Alpaca/PaperBroker")

    alpaca_configured = (
        settings.alpaca_api_key and
        not settings.alpaca_api_key.startswith("your_")
    )
    if alpaca_configured:
        try:
            return AlpacaBroker(
                api_key=settings.alpaca_api_key,
                secret_key=settings.alpaca_secret_key,
                base_url=settings.alpaca_base_url,
            )
        except Exception as e:
            if settings.environment == "production":
                raise RuntimeError("Failed to initialize AlpacaBroker in production") from e
            log.exception("Failed to initialize AlpacaBroker — falling back to PaperBroker")
    return PaperBroker()


class AppState:
    def __init__(self):
        self.risk = RiskEngine()

        mock_mode = (
            os.environ.get("MOCK_MODE", "false").lower() == "true"
        )
        self.mock_mode = mock_mode
        if mock_mode:
            self.market_data = MockProvider()
        elif settings.dhan_client_id and settings.dhan_access_token:
            try:
                self.market_data = DhanProvider(
                    client_id=settings.dhan_client_id,
                    access_token=settings.dhan_access_token,
                    default_exchange=settings.dhan_default_exchange,
                )
            except Exception:
                log.exception("Failed to initialize DhanProvider — falling back to BinanceProvider")
                self.market_data = BinanceProvider(settings.binance_api_key or "", settings.binance_secret or "")
        else:
            self.market_data = BinanceProvider(settings.binance_api_key or "", settings.binance_secret or "")

        # Secondary providers — kept alive alongside the primary so the
        # dashboard can chart US stocks even when Dhan is the active broker.
        self.secondary_providers: dict[str, MarketDataProvider] = {}
        alpaca_also_configured = (
            settings.alpaca_api_key and
            not settings.alpaca_api_key.startswith("your_") and
            not isinstance(self.market_data, AlpacaProvider)
        )
        if alpaca_also_configured:
            try:
                self.secondary_providers["alpaca"] = AlpacaProvider(
                    api_key=settings.alpaca_api_key,
                    secret_key=settings.alpaca_secret_key,
                    base_url=settings.alpaca_base_url,
                )
                log.info("AlpacaProvider registered as secondary data source")
            except Exception:
                log.exception("Failed to initialize secondary AlpacaProvider")

        self.news_feed = NewsFeed(
            news_api_key=settings.news_api_key,
            redis_url=settings.redis_url,
        )
        self.broker = _build_broker()
        self.router = SmartOrderRouter(self.broker, slippage_tolerance_bps=settings.slippage_tolerance_bps)
        self.journal = TradeJournal(
            api_key=settings.anthropic_api_key,
            model=settings.anthropic_model,
            gemini_api_key=settings.gemini_api_key,
            gemini_model=settings.gemini_model,
            provider=settings.llm_provider,
        )
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
    orch._strategy_override = state.pinned_strategy

    result = await orch.run_cycle(execute=execute_if_signal)
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


def _ist_now():
    """Return current time in IST (UTC+5:30) without an external library."""
    from datetime import datetime, timezone, timedelta
    return datetime.now(timezone.utc).astimezone(timezone(timedelta(hours=5, minutes=30)))


_MCX_KEYWORDS = frozenset({
    "GOLD", "SILVER", "CRUDEOIL", "NATURALGAS", "COPPER", "ZINC", "LEAD",
    "NICKEL", "ALUMINIUM", "MENTHAOIL", "KAPAS", "COTTON", "CARDAMOM",
    "GOLDM", "GOLDMINI", "GOLDGUINEA", "GOLDPETAL", "GOLDTEN",
    "SILVERM", "SILVERMIC", "CRUDEOILM", "NATGASMINI",
})


def _is_asset_live(asset: str) -> bool:
    """
    Return True if the asset's exchange is currently open for trading.
    - NSE equity / F&O / indices: Mon–Fri 09:15–15:30 IST
    - MCX commodities:            Mon–Fri 09:00–23:30 IST
    """
    from datetime import time as _time
    now = _ist_now()
    if now.weekday() >= 5:          # Saturday=5, Sunday=6
        return False
    t = now.time()
    # is_mcx requires scrip_master loaded; fall back to keyword set if not
    is_mcx = scrip_master.is_mcx(asset) or asset.upper() in _MCX_KEYWORDS
    if is_mcx:
        return _time(9, 0) <= t <= _time(23, 30)
    return _time(9, 15) <= t <= _time(15, 30)


async def live_suggestions_loop():
    """Rotate through the full tradeable universe or a fixed watchlist, running consensus on each symbol."""
    if not settings.enable_live_suggestions:
        log.info("Live suggestions loop disabled")
        return

    if settings.scan_mode == "full_market":
        market_scanner.refresh()
        log.info(
            "Full-market scan: %d instruments, batch=%d, ~%.0f min per full rotation",
            market_scanner.universe_size,
            settings.scan_batch_size,
            market_scanner.universe_size / settings.scan_batch_size * settings.live_suggestions_interval_sec / 60,
        )
        while True:
            batch = market_scanner.next_batch(settings.scan_batch_size)
            live_batch = [a for a in batch if _is_asset_live(a)]
            skipped = len(batch) - len(live_batch)
            if skipped:
                log.info("Scanning batch [%d/%d]: %d live, %d skipped (market closed)",
                         market_scanner.pointer, market_scanner.universe_size, len(live_batch), skipped)
            else:
                log.info("Scanning batch [%d/%d]: %s",
                         market_scanner.pointer, market_scanner.universe_size, ", ".join(live_batch))
            for asset in live_batch:
                await run_consensus_cycle(asset, timeframe="1h", candle_limit=300, execute_if_signal=settings.auto_execute_signals)
            await asyncio.sleep(settings.live_suggestions_interval_sec)
    else:
        assets = [a.strip() for a in settings.live_suggestions_assets.split(",") if a.strip()]
        log.info("Watchlist scan: %d assets", len(assets))
        while True:
            for asset in assets:
                if not _is_asset_live(asset):
                    continue
                await run_consensus_cycle(asset, timeframe="1h", candle_limit=300, execute_if_signal=settings.auto_execute_signals)
                await asyncio.sleep(settings.live_suggestions_interval_sec)


@asynccontextmanager
async def lifespan(app: FastAPI):
    await state.news_feed.setup()
    await state.db.connect()
    try:
        await scrip_master.ensure_loaded()
        if settings.scan_mode == "full_market":
            market_scanner.refresh()
    except Exception:
        log.warning("Scrip master unavailable at startup — instrument lookup will use fallback")

    # Resume from the last known portfolio state instead of resetting to the
    # hardcoded starting equity — critical on serverless, where every cold
    # start otherwise gets a fresh in-memory PortfolioState.
    snapshot = await state.db.load_latest_portfolio_snapshot()
    if snapshot:
        state.portfolio.equity = float(snapshot["equity"])
        state.portfolio.cash = float(snapshot["cash"])
        state.portfolio.daily_pnl_pct = float(snapshot["daily_pnl_pct"])
        # Do NOT restore open_trades from snapshot — it can drift if positions
        # are closed externally (e.g., Dhan auto-squareoff) or if prior orders
        # failed silently. Always re-sync from the broker's live position count.
        if isinstance(state.broker, PaperBroker):
            state.broker._cash = state.portfolio.cash
        log.info("Resumed portfolio from snapshot: equity=%.2f", state.portfolio.equity)

    # Sync open_trades from the broker's actual live positions so the risk
    # engine never blocks trading due to a stale in-memory counter.
    try:
        live_positions = await state.broker.get_positions()
        state.portfolio.open_trades = len(live_positions)
        log.info("Synced open_trades from broker: %d open positions", state.portfolio.open_trades)
    except Exception:
        log.warning("Could not sync open_trades from broker at startup — defaulting to 0")
        state.portfolio.open_trades = 0

    # There's no persistent process to run this loop in on serverless
    # platforms (e.g. Vercel sets VERCEL=1) — a Vercel Cron hitting
    # POST /cron/tick replaces it there instead.
    monitor_task = None
    loop_task = None
    if not os.environ.get("VERCEL"):
        loop_task = asyncio.create_task(live_suggestions_loop())
        position_monitor = PositionMonitor(
            broker=state.broker,
            market_data=state.market_data,
            portfolio=state.portfolio,
            risk_cfg=state.risk.cfg,
            alert_router=state.alerts,
            weights_manager=state.weights_manager,
        )
        monitor_task = asyncio.create_task(position_monitor.run_forever())
    try:
        yield
    finally:
        if monitor_task:
            monitor_task.cancel()
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
async def get_candles_endpoint(asset: str, timeframe: str = "1h", limit: int = 100, source: str = ""):
    provider = state.secondary_providers.get(source) if source else None
    if provider is None:
        provider = state.market_data
    try:
        candles = await provider.get_candles(asset, timeframe, limit)
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


@app.get("/")
async def root():
    """Landing / docs redirect for Vercel and local health checks."""
    return {
        "name": "Trading OS",
        "version": "1.0.0",
        "status": "ok",
        "docs": "/docs",
        "health": "/health",
        "endpoints": {
            "POST /analyze": "Multi-agent consensus signal for an asset",
            "POST /backtest": "Walk-forward backtest",
            "POST /backtest/optimize": "Grid-search SL/TP optimization",
            "GET /portfolio": "Paper portfolio status",
            "GET /agents/performance": "Adaptive agent weight report",
            "GET /metrics": "Prometheus metrics",
            "WS /ws/signals": "Live signal stream (limited on serverless)",
        },
    }


@app.get("/health")
async def health():
    return {
        "status": "ok",
        "timestamp": time.time(),
        "version": "1.0.0",
        "portfolio_equity": (await state.broker.get_account())["equity"],
    }


@router.get("/system")
async def system_metrics():
    """MacBook hardware metrics for the infrastructure panel."""
    try:
        import psutil
        mem = psutil.virtual_memory()
        cpu = psutil.cpu_percent(interval=None)
        disk = psutil.disk_usage("/")
        # On macOS, mem.used reflects "wired" memory only.
        # total - available gives true active+wired+compressed usage.
        ram_used = mem.total - mem.available
        return {
            "ram_used_gb": round(ram_used / 1e9, 1),
            "ram_total_gb": round(mem.total / 1e9, 1),
            "ram_pct": mem.percent,
            "cpu_pct": cpu,
            "disk_used_gb": round(disk.used / 1e9, 1),
            "disk_total_gb": round(disk.total / 1e9, 1),
            "disk_pct": disk.percent,
        }
    except ImportError:
        return {"ram_used_gb": 0, "ram_total_gb": 8, "ram_pct": 0, "cpu_pct": 0,
                "disk_used_gb": 0, "disk_total_gb": 256, "disk_pct": 0}


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
    trading_metrics.update_system()
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
    from core.execution.broker_interface import DhanBroker
    currency = "INR" if isinstance(state.broker, DhanBroker) else "USD"
    # Derive daily P&L from live position data (sum of unrealized + realized)
    total_unrealized = sum(p.get("unrealized_pnl", 0) for p in positions.values())
    total_realized   = sum(p.get("realized_pnl", 0)   for p in positions.values())
    total_pnl = total_unrealized + total_realized
    equity = account["equity"]
    daily_pnl_pct = (total_pnl / equity * 100) if equity else 0.0
    return {
        "equity": equity,
        "cash": account["cash"],
        "positions": positions,
        "daily_pnl_pct": daily_pnl_pct,
        "daily_pnl": round(total_pnl, 2),
        "open_trades": len(positions),
        "mode": "MOCK" if state.mock_mode else "LIVE",
        "currency": currency,
    }


@router.get("/positions")
async def get_positions_enriched() -> dict:
    """Enriched position data with live P&L and active SL/TP order prices."""
    from core.execution.broker_interface import DhanBroker
    positions = await state.broker.get_positions()
    open_orders: list[dict] = []
    if isinstance(state.broker, DhanBroker):
        open_orders = await state.broker.get_open_orders()

    sl_map: dict[str, float] = {}
    tp_map: dict[str, float] = {}
    for o in open_orders:
        sym = o.get("tradingSymbol", "")
        otype = o.get("orderType", "")
        trigger = float(o.get("triggerPrice", 0) or 0)
        price = float(o.get("price", 0) or 0)
        if otype in ("STOP_LOSS", "STOP_LOSS_MARKET", "SL", "SLM"):
            sl_map[sym] = trigger or price
        elif otype == "LIMIT" and price:
            tp_map[sym] = price

    result = {}
    for sym, pos in positions.items():
        enriched = {
            **pos,
            "sl_price": sl_map.get(sym),
            "tp_price": tp_map.get(sym),
        }
        # Enrich options positions (symbol format: "NIFTY-25600-CE")
        parts = sym.split("-")
        if len(parts) == 3 and parts[2] in ("CE", "PE"):
            underlying = parts[0]
            try:
                strike = float(parts[1])
            except ValueError:
                strike = 0.0
            option_type = parts[2]
            try:
                spot = await state.market_data.get_current_price(underlying) or 0.0
            except Exception:
                spot = 0.0
            # Approximate delta using linear moneyness model (no Black-Scholes needed)
            # ATM ≈ 0.5, shifts ±0.25 per 1% moneyness. CE delta positive, PE negative.
            approx_delta = None
            if spot > 0 and strike > 0:
                moneyness = (spot - strike) / spot
                if option_type == "PE":
                    moneyness = -moneyness
                approx_delta = round(max(0.05, min(0.95, 0.5 + moneyness * 25)), 2)
                if option_type == "PE":
                    approx_delta = -approx_delta
            enriched.update({
                "is_options": True,
                "underlying": underlying,
                "strike": strike,
                "option_type": option_type,
                "approx_delta": approx_delta,
            })
        result[sym] = enriched
    return result


@router.get("/agents/performance")
async def agent_performance() -> dict:
    return state.weights_manager.get_performance_report()


@router.post("/portfolio/reset")
async def reset_portfolio(_: None = Depends(require_api_key)) -> dict:
    state.broker = _build_broker()
    state.router = SmartOrderRouter(state.broker, slippage_tolerance_bps=settings.slippage_tolerance_bps)
    account = await state.broker.get_account()
    status = "reconnected" if isinstance(state.broker, AlpacaBroker) else "reset"
    return {"status": status, "initial_equity": account["equity"]}


@router.post("/trade/submit")
async def submit_trade(req: TradeRequest, _: None = Depends(require_api_key)):
    from core.execution.broker_interface import Order, OrderType, OrderStatus
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
    if filled_order.status == OrderStatus.FILLED:
        state.portfolio.open_trades += 1
        asyncio.create_task(state.db.snapshot_portfolio(state.portfolio))
    return {
        "status": filled_order.status.value,
        "avg_fill_price": filled_order.avg_fill_price,
        "quantity": filled_order.filled_qty,
        "side": filled_order.side,
        "broker_error": filled_order.metadata.get("error"),
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

    # Use Alpaca's native close_position (cancels pending orders atomically)
    # Fallback to manual market sell for PaperBroker and other adapters
    if hasattr(state.broker, "close_position_native"):
        filled_order = await state.broker.close_position_native(req.asset)
    else:
        price = await state.market_data.get_current_price(req.asset)
        order = Order(
            asset=req.asset,
            side="sell",
            quantity=pos["qty"],
            order_type=OrderType.MARKET,
            limit_price=price,
        )
        filled_order = await state.broker.submit_order(order)

    broker_error = filled_order.metadata.get("error")
    if not broker_error:
        state.portfolio.open_trades = max(0, state.portfolio.open_trades - 1)
        # Return the tracked position value to available cash
        reclaimed = state.portfolio.positions.pop(req.asset, 0.0)
        if reclaimed > 0:
            state.portfolio.cash = min(state.portfolio.equity, state.portfolio.cash + reclaimed)
        asyncio.create_task(state.db.snapshot_portfolio(state.portfolio))
    return {
        "status": "closed" if not broker_error else "failed",
        "asset": req.asset,
        "qty": filled_order.filled_qty,
        "price": filled_order.avg_fill_price,
        "broker_error": broker_error,
    }


_ALPACA_PAIRS = [
    {"symbol": "AAPL",   "name": "Apple Inc.",       "exchange": "NASDAQ", "type": "equity", "data_source": "alpaca"},
    {"symbol": "MSFT",   "name": "Microsoft Corp.",  "exchange": "NASDAQ", "type": "equity", "data_source": "alpaca"},
    {"symbol": "NVDA",   "name": "NVIDIA Corp.",     "exchange": "NASDAQ", "type": "equity", "data_source": "alpaca"},
    {"symbol": "TSLA",   "name": "Tesla Inc.",       "exchange": "NASDAQ", "type": "equity", "data_source": "alpaca"},
    {"symbol": "AMZN",   "name": "Amazon.com Inc.",  "exchange": "NASDAQ", "type": "equity", "data_source": "alpaca"},
    {"symbol": "GOOGL",  "name": "Alphabet Inc.",    "exchange": "NASDAQ", "type": "equity", "data_source": "alpaca"},
    {"symbol": "META",   "name": "Meta Platforms",   "exchange": "NASDAQ", "type": "equity", "data_source": "alpaca"},
    {"symbol": "BTCUSD", "name": "Bitcoin / USD",    "exchange": "CRYPTO", "type": "crypto", "data_source": "alpaca"},
    {"symbol": "ETHUSD", "name": "Ethereum / USD",   "exchange": "CRYPTO", "type": "crypto", "data_source": "alpaca"},
    {"symbol": "SPY",    "name": "S&P 500 ETF",      "exchange": "NYSE",   "type": "etf",    "data_source": "alpaca"},
]

_BINANCE_PAIRS = [
    {"symbol": "BTCUSDT",  "name": "Bitcoin / USDT",   "exchange": "BINANCE", "type": "crypto", "data_source": ""},
    {"symbol": "ETHUSDT",  "name": "Ethereum / USDT",  "exchange": "BINANCE", "type": "crypto", "data_source": ""},
    {"symbol": "SOLUSDT",  "name": "Solana / USDT",    "exchange": "BINANCE", "type": "crypto", "data_source": ""},
    {"symbol": "BNBUSDT",  "name": "BNB / USDT",       "exchange": "BINANCE", "type": "crypto", "data_source": ""},
    {"symbol": "XRPUSDT",  "name": "XRP / USDT",       "exchange": "BINANCE", "type": "crypto", "data_source": ""},
    {"symbol": "ADAUSDT",  "name": "Cardano / USDT",   "exchange": "BINANCE", "type": "crypto", "data_source": ""},
    {"symbol": "DOTUSDT",  "name": "Polkadot / USDT",  "exchange": "BINANCE", "type": "crypto", "data_source": ""},
]


@router.get("/pairs/suggest")
async def suggest_pairs() -> dict:
    """
    Return the configured watchlist as pair dicts.
    For Dhan: resolves every symbol in LIVE_SUGGESTIONS_ASSETS via the scrip
    master so security_id, exchange, and lot_size are always current.
    """
    if isinstance(state.broker, DhanBroker):
        assets = [a.strip() for a in settings.live_suggestions_assets.split(",") if a.strip()]
        pairs = scrip_master.watchlist_pairs(assets)
        if "alpaca" in state.secondary_providers:
            pairs = pairs + _ALPACA_PAIRS
        return {"broker": "DhanBroker", "pairs": pairs}

    if isinstance(state.broker, AlpacaBroker):
        return {"broker": "AlpacaBroker", "pairs": _ALPACA_PAIRS}

    return {"broker": "PaperBroker", "pairs": _BINANCE_PAIRS}


@router.get("/pairs/search")
async def search_pairs(q: str = "") -> dict:
    """
    Search for any tradeable instrument.
    For Dhan: searches the full scrip master (50k+ instruments).
    """
    q = q.strip()
    if isinstance(state.broker, DhanBroker):
        broker = "DhanBroker"
        if not q:
            assets = [a.strip() for a in settings.live_suggestions_assets.split(",") if a.strip()]
            pairs = scrip_master.watchlist_pairs(assets)
        else:
            pairs = scrip_master.search(q, limit=15)
        if "alpaca" in state.secondary_providers:
            q_up = q.upper()
            alpaca = [p for p in _ALPACA_PAIRS if not q or q_up in p["symbol"] or q_up in p["name"].upper()]
            pairs = pairs + alpaca
        return {"broker": broker, "pairs": pairs[:20], "query": q}

    pairs = _ALPACA_PAIRS if isinstance(state.broker, AlpacaBroker) else _BINANCE_PAIRS
    broker = "AlpacaBroker" if isinstance(state.broker, AlpacaBroker) else "PaperBroker"
    if q:
        q_up = q.upper()
        pairs = [p for p in pairs if q_up in p["symbol"].upper() or q_up in p["name"].upper()]
    return {"broker": broker, "pairs": pairs, "query": q}


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


@router.get("/options/expiries")
async def options_expiries(symbol: str = "NIFTY") -> dict:
    """Return available expiry dates for an options underlying via Dhan."""
    if not isinstance(state.broker, DhanBroker):
        return {"expiries": []}
    upper = symbol.strip().upper()
    inst = scrip_master.resolve(upper)
    if not inst:
        return {"expiries": []}
    security_id, exchange = inst.security_id, inst.exchange
    try:
        loop = asyncio.get_event_loop()
        raw = await loop.run_in_executor(
            None, lambda: state.broker._dhan.expiry_list(int(security_id), exchange)
        )
        expiries = raw.get("data", {}).get("data", [])
        return {"expiries": expiries}
    except Exception:
        log.exception("Failed to fetch expiry list for %s", symbol)
        return {"expiries": []}


@router.get("/options/chain")
async def options_chain(symbol: str = "NIFTY", expiry: str = "") -> dict:
    """Return option chain near ATM (±15 strikes) via Dhan."""
    if not isinstance(state.broker, DhanBroker):
        return {"symbol": symbol, "expiry": expiry, "spot": 0.0, "strikes": []}
    upper = symbol.strip().upper()
    inst = scrip_master.resolve(upper)
    if not inst or not expiry:
        return {"symbol": symbol, "expiry": expiry, "spot": 0.0, "strikes": []}
    security_id, exchange = inst.security_id, inst.exchange
    try:
        loop = asyncio.get_event_loop()
        raw = await loop.run_in_executor(
            None, lambda: state.broker._dhan.option_chain(int(security_id), exchange, expiry)
        )
        chain_data = raw.get("data", {}).get("data", {})
        spot = float(chain_data.get("last_price", 0.0))
        oc = chain_data.get("oc", {})

        # Parse all strikes and sort them
        all_strikes = []
        for strike_str, legs in oc.items():
            try:
                strike_val = float(strike_str)
            except ValueError:
                continue
            ce = legs.get("ce", {}) or {}
            pe = legs.get("pe", {}) or {}
            all_strikes.append({
                "strike": strike_val,
                "ce": {
                    "security_id": ce.get("security_id"),
                    "ltp": ce.get("last_price", 0.0),
                    "oi": ce.get("oi", 0),
                    "iv": ce.get("implied_volatility", 0.0),
                    "delta": (ce.get("greeks") or {}).get("delta", 0.0),
                    "volume": ce.get("volume", 0),
                },
                "pe": {
                    "security_id": pe.get("security_id"),
                    "ltp": pe.get("last_price", 0.0),
                    "oi": pe.get("oi", 0),
                    "iv": pe.get("implied_volatility", 0.0),
                    "delta": (pe.get("greeks") or {}).get("delta", 0.0),
                    "volume": pe.get("volume", 0),
                },
            })

        all_strikes.sort(key=lambda s: s["strike"])

        # Find ATM index and slice ±15 strikes
        if all_strikes and spot > 0:
            atm_idx = min(
                range(len(all_strikes)),
                key=lambda i: abs(all_strikes[i]["strike"] - spot),
            )
            lo = max(0, atm_idx - 15)
            hi = min(len(all_strikes), atm_idx + 16)
            near_strikes = all_strikes[lo:hi]
        else:
            near_strikes = all_strikes

        return {"symbol": upper, "expiry": expiry, "spot": spot, "strikes": near_strikes}
    except Exception:
        log.exception("Failed to fetch option chain for %s %s", symbol, expiry)
        return {"symbol": symbol, "expiry": expiry, "spot": 0.0, "strikes": []}


@router.get("/trades/history")
async def trades_history(days: int = 30) -> dict:
    """Return executed trade history from Dhan (up to 90 days)."""
    from core.execution.broker_interface import DhanBroker
    if not isinstance(state.broker, DhanBroker):
        return {"trades": []}
    raw = await state.broker.get_trade_history(days=min(days, 90))
    trades = []
    for t in raw:
        expiry_raw = t.get("drvExpiryDate") or ""
        expiry = expiry_raw if expiry_raw and not expiry_raw.startswith("0001") else None
        strike_raw = t.get("drvStrikePrice")
        strike = float(strike_raw) if strike_raw and float(strike_raw) > 0 else None
        opt_type = t.get("drvOptionType") or ""
        option_type = opt_type if opt_type in ("CALL", "PUT") else None
        trades.append({
            "trade_id":   t.get("tradeId") or t.get("orderId", ""),
            "order_id":   t.get("orderId", ""),
            "symbol":     t.get("tradingSymbol") or t.get("customSymbol", ""),
            "side":       t.get("transactionType", ""),
            "qty":        float(t.get("tradedQuantity", 0) or 0),
            "price":      float(t.get("tradedPrice", 0) or 0),
            "exchange":   t.get("exchangeSegment", ""),
            "product":    t.get("productType", ""),
            "order_type": t.get("orderType", ""),
            "strike":     strike,
            "option_type": option_type,
            "expiry":     expiry,
            "time":       t.get("createTime") or t.get("updateTime", ""),
        })
    return {"trades": trades}


@router.get("/validate/prices")
async def validate_prices(symbol: str = "RELIANCE") -> dict:
    """
    Return the same symbol's price from every available Dhan source so you can
    compare real-time quote, last intraday bar, and daily VWAP close side-by-side.
    Only meaningful when DhanProvider is active.
    """
    from datetime import datetime, timezone
    if not isinstance(state.market_data, DhanProvider):
        return {"error": "validate/prices requires DhanProvider (set DHAN_CLIENT_ID + DHAN_ACCESS_TOKEN)"}

    provider: DhanProvider = state.market_data
    security_id = provider._resolve_security_id(symbol)
    exchange, _ = provider._exchange_and_itype(symbol)
    loop = asyncio.get_event_loop()
    sources: dict = {}

    # 1. Real-time quote_data
    try:
        raw = await loop.run_in_executor(
            None,
            lambda: provider._dhan.quote_data({exchange: [security_id]}),
        )
        if isinstance(raw, dict) and raw.get("status") == "success":
            seg = raw.get("data", {}).get(exchange, {})
            entry = seg.get(security_id) or (list(seg.values())[0] if seg else {})
            ltp = entry.get("last_price", entry.get("ltp", entry.get("close", 0)))
            sources["quote_data"] = {"status": "success", "ltp": ltp}
        else:
            remarks = raw.get("remarks", "") if isinstance(raw, dict) else str(raw)
            sources["quote_data"] = {"status": "failure", "remarks": remarks}
    except Exception as e:
        sources["quote_data"] = {"status": "error", "error": str(e)}

    # 2. Last 1-minute intraday bar
    try:
        bars = await provider.get_candles(symbol, "1m", 5)
        if bars:
            last = bars[-1]
            ts_utc = datetime.fromtimestamp(last.timestamp, tz=timezone.utc).isoformat()
            sources["last_1m_bar"] = {"status": "success", "close": last.close, "bar_time_utc": ts_utc}
        else:
            sources["last_1m_bar"] = {"status": "no_data"}
    except Exception as e:
        sources["last_1m_bar"] = {"status": "error", "error": str(e)}

    # 3. Daily VWAP close
    try:
        bars = await provider.get_candles(symbol, "1d", 2)
        if bars:
            last = bars[-1]
            date_utc = datetime.fromtimestamp(last.timestamp, tz=timezone.utc).date().isoformat()
            sources["daily_close"] = {"status": "success", "close": last.close, "date_utc": date_utc}
        else:
            sources["daily_close"] = {"status": "no_data"}
    except Exception as e:
        sources["daily_close"] = {"status": "error", "error": str(e)}

    return {"symbol": symbol, "security_id": security_id, "exchange": exchange, "sources": sources}


app.include_router(router)


