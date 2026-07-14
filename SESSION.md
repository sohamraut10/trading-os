# Trading OS — Build Session Log

End-to-end record of what was designed, built, fixed, and shipped in this session.

---

## What Was Built

A complete **AI-powered Trading Operating System** with a multi-agent consensus architecture. The system runs 4 specialist AI agents in parallel, passes their decisions through a Devil's Advocate auditor, and feeds everything into a Meta-Agent Consensus Engine that produces a final TRUE/FALSE trade signal with Kelly-sized position sizing.

---

## Architecture

```
Market Data → [Technical | Sentiment | Quant | OrderFlow] ──┐
                                                              ├→ Meta-Agent Consensus → Risk Engine → Broker
                                                Devil's Advocate (auditor, veto power) ──────────────┘
```

### Agents

| Agent | Method | Signal Source |
|---|---|---|
| **Technical Analyst** | Pure NumPy | RSI, MACD, EMA, Bollinger Bands, VWAP, ATR — scoring rubric → 50–95% confidence |
| **Sentiment & News** | Claude Haiku (Anthropic SDK) | Structured JSON prompt; keyword heuristic fallback with 20% confidence penalty |
| **Quant & Statistical** | R/S analysis | Hurst exponent (trend vs. mean-reversion regime), Z-score, rolling Sharpe, Kelly EV |
| **Market Structure** | Pivot-based | S/R levels, volume profile POC, bid/ask imbalance, liquidity voids, large-order detection |
| **Devil's Advocate** | Rule-based | 9 risk flags: earnings risk, macro shock, overextension, correlated crash, loss streak, near-event, spread, gap, regime mismatch — returns SELL veto or HOLD |

### Meta-Agent Consensus Rules
- DA veto: ≥85% SELL confidence → block entire signal
- Filter: agents below 55% confidence excluded from vote
- TRUE signal requires: ≥3/4 agents agree **AND** average confidence ≥68%
- Regime multiplier applied to weights (bull/bear/volatile/ranging)
- Position size: half-Kelly criterion

---

## Files Created (41 Python files, ~5,760 lines)

```
trading-os/
├── config/
│   └── settings.py               # Pydantic v2 BaseSettings with SettingsConfigDict
├── api/
│   └── main.py                   # FastAPI — /analyze /backtest /backtest/optimize /metrics /ws/signals
├── core/
│   ├── agents/
│   │   ├── base_agent.py         # Signal enum, OHLCV/OrderBook dataclasses, BaseAgent ABC
│   │   ├── technical_agent.py    # Pure NumPy indicators
│   │   ├── sentiment_agent.py    # Claude Haiku + keyword fallback
│   │   ├── quant_agent.py        # Hurst/Z-score/Kelly/Sharpe
│   │   ├── order_flow_agent.py   # S/R/VWAP/imbalance/delta
│   │   ├── devils_advocate_agent.py
│   │   └── meta_agent.py         # Consensus engine + TradeSignal
│   ├── data/
│   │   ├── market_data.py        # AlpacaProvider, BinanceProvider, MockProvider (GBM)
│   │   └── news_feed.py          # NewsCache (Redis + in-memory), NewsAPI + Reddit stubs
│   ├── risk/
│   │   └── risk_engine.py        # Gate chain: circuit breaker → exposure → Kelly → SL/TP prices
│   ├── execution/
│   │   └── broker_interface.py   # AlpacaBroker, PaperBroker (slippage sim), SmartOrderRouter
│   ├── backtest/
│   │   ├── backtester.py         # Walk-forward bar-by-bar, Sharpe/Sortino/DD/profit factor
│   │   └── optimizer.py          # Grid search — 70/30 in/out-of-sample, asyncio.Semaphore(8)
│   ├── monitoring/
│   │   ├── metrics.py            # Custom Gauge/Counter/Histogram → Prometheus text format
│   │   ├── regime_detector.py    # HV + trend strength + VIX → bull/bear/volatile/ranging
│   │   ├── trade_journal.py      # Claude Haiku post-trade analysis, JSONL persistence
│   │   └── alerts.py             # AlertRouter: Telegram + Console
│   ├── learning/
│   │   └── adaptive_weights.py   # EMA accuracy + calibration scoring, recalibrates every 20 trades
│   ├── streaming/
│   │   └── kafka_bus.py          # aiokafka SignalProducer/Consumer + InMemoryBus fallback
│   ├── strategy/
│   │   ├── strategy_base.py      # StrategyType enum, StrategyFilter, BaseStrategy ABC
│   │   └── strategies.py         # Scalping, Swing, MeanReversion, TrendFollow, StatArb + registry
│   └── orchestrator.py           # Full cycle pipeline + PortfolioOrchestrator (N assets concurrent)
├── infrastructure/
│   ├── docker-compose.yml        # API + PostgreSQL + Redis + Kafka + Zookeeper + Grafana + Prometheus
│   ├── init.sql                  # Tables: signals, trades, agent_decisions, agent_performance
│   ├── prometheus.yml            # Scrapes /metrics every 10s
│   └── grafana/
│       ├── datasources/prometheus.yml
│       └── dashboards/
│           ├── provisioning.yml
│           └── trading_os.json   # 12-panel dashboard
├── tests/
│   ├── test_pipeline.py          # 8 integration tests
│   ├── test_strategy.py          # 22 strategy tests
│   ├── test_risk_engine.py       # 17 risk engine tests
│   ├── test_backtest.py          # 11 backtest tests
│   └── test_orchestrator.py      # 9 orchestrator tests
├── scripts/
│   └── run_demo.py
├── pytest.ini                    # asyncio_mode = auto
└── requirements.txt
```

---

## Bugs Fixed During Session

| Bug | Root Cause | Fix |
|---|---|---|
| OOM / exit 137 on test suite | `scope="module"` fixtures kept 500-candle agent pipelines alive across all modules simultaneously | Reduced candles to 400, removed custom `event_loop` fixture, added `pytest.ini` with `asyncio_mode = auto` |
| History empty in orchestrator tests | `_history.append()` only ran inside `run_forever()` loop; tests calling `run_cycle()` directly saw empty history | Extracted `_append_history()` helper called from both success and error paths |
| R:R warning test false positive | Risk engine always overrode `tp_pct` with `sl × 2.0` regardless of signal's suggested TP, so ratio was always 2.0 | Changed to `tp_pct = raw_tp if raw_tp >= min_tp else min_tp`; test updated to verify floor behaviour |
| Kelly test values equal | Both win rates (0.55 and 0.70) hit `max_pct=0.05` cap | Test passes `max_pct=1.0` to allow uncapped comparison |
| Pydantic v2 deprecation (11 warnings) | `Field(env=...)` and `class Config:` patterns from Pydantic v1 | Migrated to `SettingsConfigDict`, removed `Field(env=...)`, sub-configs changed to `BaseModel` |
| `aiohttp` missing | Not in venv | `uv pip install aiohttp` |
| Edit string mismatch in orchestrator.py | Unicode box-drawing chars caused match failure | Re-read exact bytes, matched correctly |

---

## Test Results

```
67 passed in 15.42s
```

- `test_pipeline.py`      — 8 tests  (end-to-end agent pipeline)
- `test_strategy.py`      — 22 tests (strategy selection, filters, position multipliers)
- `test_risk_engine.py`   — 17 tests (Kelly sizing, gate chain, SL/TP, circuit breaker)
- `test_backtest.py`      — 11 tests (walk-forward correctness, equity curve, SL/TP hit)
- `test_orchestrator.py`  — 9 tests  (full cycle, history tracking, portfolio orchestrator)

---

## Key Design Decisions

**Pure NumPy indicators** — no TA-Lib dependency; all indicators (RSI, MACD, EMA, Bollinger, VWAP, ATR) implemented from scratch for portability.

**Hurst exponent via R/S analysis** — classifies regime as trending (H > 0.55) or mean-reverting (H < 0.45) to weight agent signals accordingly.

**Half-Kelly position sizing** — full Kelly is theoretically optimal but practically dangerous; half-Kelly halves the bet size, reducing drawdown significantly.

**70/30 walk-forward split in optimizer** — in-sample grid search on 70% of candles, out-of-sample validation on remaining 30%. Ranked by `combined_sharpe = (in + out) / 2` to penalise overfit.

**InMemoryBus fallback** — Kafka is optional; `make_bus()` returns `InMemoryBus` when no Kafka brokers are configured, so the system runs locally without Docker.

**Custom Prometheus metrics** — no `prometheus_client` library; `Gauge`, `Counter`, `Histogram` classes render to OpenMetrics text format directly, keeping the dependency footprint minimal.

---

## How to Run

### Local (no Docker)
```bash
cd trading-os
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
uvicorn api.main:app --reload --port 8000
```

### Full stack (Docker Compose)
```bash
cd infrastructure
docker compose up -d
```
- API: http://localhost:8000
- Grafana: http://localhost:3000 (admin/admin)
- Prometheus: http://localhost:9090

### Key endpoints
```
GET  /health                  — liveness + portfolio equity
POST /analyze                 — run full multi-agent consensus on an asset
POST /backtest                — walk-forward backtest
POST /backtest/optimize       — grid-search parameter optimization
GET  /portfolio               — current equity, positions, P&L
GET  /agents/performance      — per-agent accuracy and weights
GET  /metrics                 — Prometheus metrics scrape
WS   /ws/signals              — real-time signal stream
```

### Run tests
```bash
pytest tests/ -v
```

### Deploy to Vercel

Serverless functions can't hold a WebSocket open or run an indefinite
background loop, and give no guarantee of in-memory state surviving between
invocations — so the deployment differs from Docker/local in three ways:

- `/ws/signals` and `/ws/events` still exist (for Docker/local clients) but
  the dashboard instead polls `GET /events/recent` (same Postgres `events`
  table, just pulled instead of pushed).
- `live_suggestions_loop`'s always-on background task is replaced by
  `GET /cron/tick`, wired up as a Vercel Cron in `vercel.json`
  (`*/10 * * * *` by default — adjust for your plan's cron limits).
- Portfolio equity/cash resume from the latest `portfolio_snapshots` row on
  every cold start instead of resetting to the hardcoded starting equity.
  Per-symbol open positions are **not** persisted (no positions table yet),
  so they — and any unrealized P&L they represented — are still lost across
  a cold start. `AdaptiveWeightManager`'s learned weights also still persist
  to a local JSON file (`/tmp/agent_performance.json`), which is
  serverless-ephemeral, so weight recalibration can reset between cold
  starts; only equity/cash/events/signals are Postgres-backed.

Required setup:
1. **An external Postgres** — Vercel doesn't host one. Point `DATABASE_URL`
   at a managed instance (Neon, Supabase, Railway, etc.) and load
   `infrastructure/init.sql` against it once.
2. **Vercel project env vars**: `DATABASE_URL`, `MOCK_MODE=true` (for a demo
   deployment with no exchange/broker keys), `CRON_SECRET` (Vercel
   auto-sends `Authorization: Bearer $CRON_SECRET` on cron requests — this
   is what `GET /cron/tick` checks), `API_AUTH_TOKEN` (protects trade/portfolio
   mutation endpoints), and whichever of `ANTHROPIC_API_KEY` /
   `ALPACA_API_KEY` / `BINANCE_API_KEY` / `NEWS_API_KEY` you actually have.
3. **Dashboard build env var**: set `VITE_API_BASE=/api` on the Vercel
   project so the dashboard calls `/api/*` (where `api/index.py` mounts the
   backend) instead of same-origin root paths.
4. `vercel deploy` (or connect the GitHub repo in the Vercel dashboard).

`vercel.json` builds the dashboard as a static site and `api/index.py` as
the API's Python serverless function. `api/index.py` builds its own
top-level FastAPI app and includes `api.main`'s router under `/api` — it
does **not** use `app.mount()`, because Starlette's `Mount` doesn't forward
ASGI lifespan events to a mounted sub-app, which would silently skip
`state.db.connect()` (and the portfolio resume) entirely.

### Deploy to Render or Fly.io (alternative to Vercel)

Both run the existing Docker image as a real long-lived container instead
of serverless functions, so **none of the Vercel workarounds above are
needed** — `live_suggestions_loop`, the `/ws/*` WebSocket endpoints, and
in-memory `AppState` all work exactly as designed, no code changes required.
`infrastructure/Dockerfile` is now a multi-stage build: it builds the
dashboard first and copies the compiled assets in, so a single container
serves both the API and a real built dashboard at `/` (previously the
Dockerfile only copied source and never ran `npm run build`, so `/` would
have served the raw, unprocessed `dashboard/index.html`).

**Render**: `render.yaml` is a Blueprint — connect the repo in the Render
dashboard and it provisions a Postgres instance plus the web service from
`infrastructure/Dockerfile` automatically. Set the `sync: false` env vars
(`API_AUTH_TOKEN`, `ANTHROPIC_API_KEY`, etc.) in the dashboard after first
deploy.

**Fly.io**:
```bash
fly apps create trading-os
fly postgres create --name trading-os-db
fly postgres attach trading-os-db      # sets DATABASE_URL automatically
fly secrets set API_AUTH_TOKEN=... ANTHROPIC_API_KEY=...
fly deploy
```

Both configs pin to a single instance/machine — `AppState` (portfolio,
event log, websocket clients) is process-local, same reason
`infrastructure/Dockerfile`'s `CMD` uses `--workers 1`. Redis/Kafka stay
optional on either platform; both already degrade to in-memory
implementations when unconfigured.

---

## Repository

https://github.com/sohamraut10/trading-os
