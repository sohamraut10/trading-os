# Graph Report - .  (2026-07-14)

## Corpus Check
- 89 files · ~75,118 words
- Verdict: corpus is large enough that graph structure adds value.

## Summary
- 1278 nodes · 3687 edges · 79 communities detected
- Extraction: 46% EXTRACTED · 54% INFERRED · 0% AMBIGUOUS · INFERRED: 1984 edges (avg confidence: 0.5)
- Token cost: 0 input · 0 output

## Community Hubs (Navigation)
- [[_COMMUNITY_Community 0|Community 0]]
- [[_COMMUNITY_Community 1|Community 1]]
- [[_COMMUNITY_Community 2|Community 2]]
- [[_COMMUNITY_Community 3|Community 3]]
- [[_COMMUNITY_Community 4|Community 4]]
- [[_COMMUNITY_Community 5|Community 5]]
- [[_COMMUNITY_Community 6|Community 6]]
- [[_COMMUNITY_Community 7|Community 7]]
- [[_COMMUNITY_Community 8|Community 8]]
- [[_COMMUNITY_Community 9|Community 9]]
- [[_COMMUNITY_Community 10|Community 10]]
- [[_COMMUNITY_Community 11|Community 11]]
- [[_COMMUNITY_Community 12|Community 12]]
- [[_COMMUNITY_Community 13|Community 13]]
- [[_COMMUNITY_Community 14|Community 14]]
- [[_COMMUNITY_Community 15|Community 15]]
- [[_COMMUNITY_Community 16|Community 16]]
- [[_COMMUNITY_Community 17|Community 17]]
- [[_COMMUNITY_Community 18|Community 18]]
- [[_COMMUNITY_Community 19|Community 19]]
- [[_COMMUNITY_Community 20|Community 20]]
- [[_COMMUNITY_Community 21|Community 21]]
- [[_COMMUNITY_Community 22|Community 22]]
- [[_COMMUNITY_Community 23|Community 23]]
- [[_COMMUNITY_Community 24|Community 24]]
- [[_COMMUNITY_Community 25|Community 25]]
- [[_COMMUNITY_Community 26|Community 26]]
- [[_COMMUNITY_Community 27|Community 27]]
- [[_COMMUNITY_Community 28|Community 28]]
- [[_COMMUNITY_Community 29|Community 29]]
- [[_COMMUNITY_Community 30|Community 30]]
- [[_COMMUNITY_Community 31|Community 31]]
- [[_COMMUNITY_Community 32|Community 32]]
- [[_COMMUNITY_Community 33|Community 33]]
- [[_COMMUNITY_Community 34|Community 34]]
- [[_COMMUNITY_Community 35|Community 35]]
- [[_COMMUNITY_Community 36|Community 36]]
- [[_COMMUNITY_Community 37|Community 37]]
- [[_COMMUNITY_Community 38|Community 38]]
- [[_COMMUNITY_Community 39|Community 39]]
- [[_COMMUNITY_Community 40|Community 40]]
- [[_COMMUNITY_Community 41|Community 41]]
- [[_COMMUNITY_Community 42|Community 42]]
- [[_COMMUNITY_Community 43|Community 43]]
- [[_COMMUNITY_Community 44|Community 44]]
- [[_COMMUNITY_Community 45|Community 45]]
- [[_COMMUNITY_Community 46|Community 46]]
- [[_COMMUNITY_Community 47|Community 47]]
- [[_COMMUNITY_Community 48|Community 48]]
- [[_COMMUNITY_Community 49|Community 49]]
- [[_COMMUNITY_Community 50|Community 50]]
- [[_COMMUNITY_Community 51|Community 51]]
- [[_COMMUNITY_Community 52|Community 52]]
- [[_COMMUNITY_Community 53|Community 53]]
- [[_COMMUNITY_Community 54|Community 54]]
- [[_COMMUNITY_Community 55|Community 55]]
- [[_COMMUNITY_Community 56|Community 56]]
- [[_COMMUNITY_Community 57|Community 57]]
- [[_COMMUNITY_Community 58|Community 58]]
- [[_COMMUNITY_Community 59|Community 59]]
- [[_COMMUNITY_Community 60|Community 60]]
- [[_COMMUNITY_Community 61|Community 61]]
- [[_COMMUNITY_Community 62|Community 62]]
- [[_COMMUNITY_Community 63|Community 63]]
- [[_COMMUNITY_Community 64|Community 64]]
- [[_COMMUNITY_Community 65|Community 65]]
- [[_COMMUNITY_Community 66|Community 66]]
- [[_COMMUNITY_Community 67|Community 67]]
- [[_COMMUNITY_Community 68|Community 68]]
- [[_COMMUNITY_Community 69|Community 69]]
- [[_COMMUNITY_Community 70|Community 70]]
- [[_COMMUNITY_Community 71|Community 71]]
- [[_COMMUNITY_Community 72|Community 72]]
- [[_COMMUNITY_Community 73|Community 73]]
- [[_COMMUNITY_Community 74|Community 74]]
- [[_COMMUNITY_Community 75|Community 75]]
- [[_COMMUNITY_Community 76|Community 76]]
- [[_COMMUNITY_Community 77|Community 77]]
- [[_COMMUNITY_Community 78|Community 78]]

## God Nodes (most connected - your core abstractions)
1. `Signal` - 123 edges
2. `TradeSignal` - 96 edges
3. `OHLCV` - 77 edges
4. `MarketContext` - 72 edges
5. `Orchestrator` - 61 edges
6. `PortfolioState` - 57 edges
7. `Backtester` - 56 edges
8. `DhanBroker` - 56 edges
9. `AlertRouter` - 54 edges
10. `AdaptiveWeightManager` - 52 edges

## Surprising Connections (you probably didn't know these)
- `Tests for AlpacaBroker's guard against a missing alpaca-trade-api install.  alpa` --uses--> `AlpacaBroker`  [INFERRED]
  tests/test_broker_interface.py → core/execution/broker_interface.py
- `Tests for api/main.py's request contracts: auth gating on state-changing endpoin` --uses--> `PaperBroker`  [INFERRED]
  tests/test_api.py → core/execution/broker_interface.py
- `Tests for the shared LLM client abstraction (core/llm/client.py) — provider sele` --uses--> `AnthropicLLM`  [INFERRED]
  tests/test_llm_client.py → core/llm/client.py
- `Tests for the shared LLM client abstraction (core/llm/client.py) — provider sele` --uses--> `GeminiLLM`  [INFERRED]
  tests/test_llm_client.py → core/llm/client.py
- `Tests for the Postgres persistence layer.  Skips automatically if no database is` --uses--> `TradeSignal`  [INFERRED]
  tests/test_persistence.py → core/agents/meta_agent.py

## Communities

### Community 0 - "Community 0"
Cohesion: 0.02
Nodes (125): ChainSummary, OIActivity, OptionChainAnalyzer, Option Chain Analyzer for NSE/BSE F&O. Consumes the Dhan option chain API respon, Expected 1 std-dev move from ATM straddle premium., Parses the Dhan option chain response and produces ChainSummary.      Dhan optio, Parse a raw Dhan option_chain() response and return ChainSummary.         Falls, Maximum pain = strike price where total option writers' pain is minimized (+117 more)

### Community 1 - "Community 1"
Cohesion: 0.04
Nodes (133): ABC, ConsoleAlerter, Alert System — Telegram + Console Fires real-time alerts on: signal generated, t, Return True only if the exchange for this asset is currently open (IST)., Routes alerts to all registered channels., BacktestResult, Backtesting Engine Runs the full agent pipeline over historical data to compute, Walk-forward backtester.     Simulates the full agent pipeline bar-by-bar using (+125 more)

### Community 2 - "Community 2"
Cohesion: 0.08
Nodes (87): AdaptiveWeightManager, Manages per-agent performance tracking and dynamic weight computation.     Weigh, AlertRouter, Backtester, OrderBookLevel, BaseModel, AlpacaBroker, DhanBroker (+79 more)

### Community 3 - "Community 3"
Cohesion: 0.07
Nodes (26): analyze(), _build_broker(), cron_tick(), _get_or_create_orchestrator(), get_positions_enriched(), get_recent_events(), _is_asset_live(), _ist_now() (+18 more)

### Community 4 - "Community 4"
Cohesion: 0.09
Nodes (10): InMemoryBus, KafkaConfig, make_bus(), Kafka Event Bus Decouples signal generation from downstream consumers (DB writer, Subscribes to one or more Kafka topics and dispatches events to handlers.     Ea, Drop-in replacement for Kafka in tests and dev mode.     Same pub/sub interface;, Factory: returns real Kafka producer if configured, else in-memory bus., Publishes TradeSignal events to Kafka.     Falls back to a no-op if Kafka is una (+2 more)

### Community 5 - "Community 5"
Cohesion: 0.09
Nodes (9): AlpacaStreamProvider, BaseLiveProvider, BinanceWSProvider, LiveFeedManager, MockLiveProvider, Connects to Alpaca realtime stream API (requires API keys)., Manages the active data feed provider, aggregates ticks into OHLCV bars,     and, Generates synthetic price ticks using Geometric Brownian Motion (GBM).     Usefu (+1 more)

### Community 6 - "Community 6"
Cohesion: 0.1
Nodes (12): Instrument, Dhan scrip master — dynamic instrument lookup.  Downloads Dhan's api-scrip-maste, Download and parse the scrip master if the cache is stale or empty., Resolve a symbol to its Instrument.         Priority: index → MCX near-month → N, Return the near-month MCX futures contract for a commodity name., Return the F&O lot size for an underlying (from FUTSTK rows). Falls back to 1., NSE equities that have stock futures — the liquid large/mid-cap universe., Full market scan universe: indices + MCX commodities + F&O-eligible equities. (+4 more)

### Community 7 - "Community 7"
Cohesion: 0.19
Nodes (19): _ctx(), _make_signal(), test_arb_half_size(), test_arb_rejects_volatile(), test_mr_accepts_with_quant(), test_mr_rejects_volatile(), test_mr_rejects_without_quant_alignment(), test_scalping_passes_volatile_high_conf() (+11 more)

### Community 8 - "Community 8"
Cohesion: 0.18
Nodes (21): _portfolio(), _signal(), test_buy_sl_below_price(), test_clean_approval(), test_daily_drawdown_circuit_breaker(), test_loss_streak_3_warning_only(), test_loss_streak_5_rejected(), test_manual_order_clean_approval() (+13 more)

### Community 9 - "Community 9"
Cohesion: 0.13
Nodes (8): Counter, Gauge, Histogram, Prometheus metrics for Trading OS. Exposes /metrics in OpenMetrics text format., Returns the full /metrics payload in Prometheus text format., Simplified histogram with fixed buckets., Central registry for all Trading OS Prometheus metrics.     Call update_* method, TradingMetrics

### Community 10 - "Community 10"
Cohesion: 0.09
Nodes (4): _parse_ts(), Data Layer — Market Data Provider Abstracts real-time + historical data from mul, WebSocket price stream — simplified polling fallback shown here., Return (security_id, exchange_segment, instrument_type) from scrip master.

### Community 11 - "Community 11"
Cohesion: 0.2
Nodes (17): _bearish_candles(), _bullish_candles(), candles_bearish(), candles_bullish(), Tests for backtester output correctness and metric validity., _run(), test_avg_hold_bars_positive(), test_backtest_runs_without_error() (+9 more)

### Community 12 - "Community 12"
Cohesion: 0.15
Nodes (10): AgentCard(), App(), buildTradePnL(), calcFees(), calcLegFee(), FeeCalculator(), fmtMoney(), ScannerCard() (+2 more)

### Community 13 - "Community 13"
Cohesion: 0.12
Nodes (2): authHeaders(), closePosition()

### Community 14 - "Community 14"
Cohesion: 0.17
Nodes (4): NewsCache, News & Sentiment Data Feed Aggregates from NewsAPI, Reddit (via Pushshift/PRAW),, Fetch Reddit mentions from wallstreetbets / crypto subreddits., Simple Redis-backed async cache with in-memory fallback.

### Community 15 - "Community 15"
Cohesion: 0.12
Nodes (0): 

### Community 16 - "Community 16"
Cohesion: 0.19
Nodes (8): _portfolio(), Tests for the Postgres persistence layer.  Skips automatically if no database is, _signal(), test_connect_to_unreachable_db_is_a_safe_noop(), test_load_latest_portfolio_snapshot_returns_most_recent(), test_record_signal_is_idempotent_on_conflict(), test_record_signal_persists_signal_and_agent_decisions(), test_snapshot_portfolio_persists()

### Community 17 - "Community 17"
Cohesion: 0.13
Nodes (0): 

### Community 18 - "Community 18"
Cohesion: 0.14
Nodes (7): AnthropicLLM, build_llm_client(), GeminiLLM, LLMClient, Shared LLM client abstraction for SentimentAgent and TradeJournal, which both ne, provider: "anthropic" | "gemini" | "auto".     "auto" prefers Anthropic (the lon, Protocol

### Community 19 - "Community 19"
Cohesion: 0.26
Nodes (10): _atr(), _bollinger(), _closes(), compute_indicators(), _ema(), _macd(), _rsi(), _score() (+2 more)

### Community 20 - "Community 20"
Cohesion: 0.22
Nodes (1): str

### Community 21 - "Community 21"
Cohesion: 0.14
Nodes (1): Tests for api/main.py's request contracts: auth gating on state-changing endpoin

### Community 22 - "Community 22"
Cohesion: 0.19
Nodes (5): _expiry_context(), _hv(), _is_event_day(), _opening_gap(), Produce a full OptionsRegime from candle data and context.         expiry_date:

### Community 23 - "Community 23"
Cohesion: 0.22
Nodes (4): Alert, _is_market_live(), _ist_now(), TelegramAlerter

### Community 24 - "Community 24"
Cohesion: 0.28
Nodes (11): _make_orchestrator(), test_cycle_increments_count(), test_cycle_populates_history(), test_cycle_recovers_from_data_error(), test_cycle_result_always_has_risk_result(), test_cycle_result_has_strategy(), test_cycle_sets_last_signal(), test_cycle_strategy_reason_populated() (+3 more)

### Community 25 - "Community 25"
Cohesion: 0.18
Nodes (7): Options Strategy Library and Selector. Defines all supported strategies as datac, Return ranked list of compatible strategies for the current regime.         Stra, Describes the option legs to trade., Full specification of a strategy., _score(), StrategyLegs, StrategySpec

### Community 26 - "Community 26"
Cohesion: 0.3
Nodes (10): _check_spread(), _correlated_crash(), _detect_gap_risk(), _detect_overextension(), gather_risk_flags(), _loss_streak(), _macro_headwind(), _near_earnings() (+2 more)

### Community 27 - "Community 27"
Cohesion: 0.2
Nodes (3): ctx(), make_candles(), make_order_book()

### Community 28 - "Community 28"
Cohesion: 0.33
Nodes (9): compute_quant_metrics(), _historical_volatility(), _hurst_exponent(), _momentum(), _prob_profit_ev(), _quant_signal(), _returns(), _rolling_sharpe() (+1 more)

### Community 29 - "Community 29"
Cohesion: 0.2
Nodes (3): Broker, BybitBroker, Bybit Broker Implementation (Crypto)     Expandable to support spot and perpetua

### Community 30 - "Community 30"
Cohesion: 0.2
Nodes (4): MarketScanner, Market-wide rotating scanner.  Builds a full tradeable universe from the scrip m, Rebuild universe from the loaded scrip master. Call after ensure_loaded()., Return the next `size` symbols in rotation, wrapping around.

### Community 31 - "Community 31"
Cohesion: 0.31
Nodes (6): accuracy(), AgentPerformanceRecord, confidence_calibration(), Learning Loop — Adaptive Agent Weight System Adjusts agent weights over time bas, _was_correct(), weighted_accuracy()

### Community 32 - "Community 32"
Cohesion: 0.32
Nodes (3): AgentWeightOptimizer, Adjusts weights based on which agents correctly predicted winning trades, Reinforcement Learning (RL) loop for tuning agent weights.     Uses a simple eps

### Community 33 - "Community 33"
Cohesion: 0.29
Nodes (7): detect_regime(), multi_timeframe_regimes(), Market Regime Detector Classifies current market state as: bull / bear / sideway, Returns one of: "bull", "bear", "sideways", "volatile"      Decision logic:, Detect regime per timeframe. Use for multi-timeframe validation., If shorter and longer timeframes agree → high conviction.     If they disagree →, regime_consensus()

### Community 34 - "Community 34"
Cohesion: 0.36
Nodes (0): 

### Community 35 - "Community 35"
Cohesion: 0.33
Nodes (3): Called after a trade closes. Updates all agents that predicted this trade., Called when a position closes (from position monitor or broker callback)., New weight ∝ weighted_accuracy × calibration_score.         Normalized to sum to

### Community 36 - "Community 36"
Cohesion: 0.38
Nodes (3): AnalyticsResult, PortfolioAnalytics, Runs a Monte Carlo simulation by bootstrapping historical trade PnLs.         Re

### Community 37 - "Community 37"
Cohesion: 0.29
Nodes (5): BaseSettings, AgentWeights, ConsensusConfig, RiskConfig, Settings

### Community 38 - "Community 38"
Cohesion: 0.33
Nodes (3): Postgres persistence — durable storage for signals, agent decisions, portfolio s, SQLAlchemy-style URLs (postgresql+asyncpg://...) aren't valid asyncpg DSNs., _to_asyncpg_dsn()

### Community 39 - "Community 39"
Cohesion: 0.33
Nodes (2): AI Trade Journal Generates human-readable post-trade analysis using an LLM (Clau, TradeJournalEntry

### Community 40 - "Community 40"
Cohesion: 0.6
Nodes (5): create_mock_context(), test_emit_hypothesis(), test_strategy_selector_pinned(), test_strategy_selector_trending(), test_strategy_selector_volatile()

### Community 41 - "Community 41"
Cohesion: 0.33
Nodes (1): Tests for api/index.py, the Vercel serverless entrypoint.  This guards specifica

### Community 42 - "Community 42"
Cohesion: 0.47
Nodes (3): create_decisions(), test_debate_skipped(), test_debate_triggered_by_split()

### Community 43 - "Community 43"
Cohesion: 0.4
Nodes (4): adjust_confidence(), apply_da_challenge(), Applies rebuttal adjustment rules to compute confidence delta.     Max confidenc, Applies Devil's Advocate cross-examination adjustments.     For each active risk

### Community 44 - "Community 44"
Cohesion: 0.67
Nodes (0): 

### Community 45 - "Community 45"
Cohesion: 0.67
Nodes (1): Tests for AlpacaBroker's guard against a missing alpaca-trade-api install.  alpa

### Community 46 - "Community 46"
Cohesion: 1.0
Nodes (2): generate_candles(), run()

### Community 47 - "Community 47"
Cohesion: 1.0
Nodes (0): 

### Community 48 - "Community 48"
Cohesion: 1.0
Nodes (0): 

### Community 49 - "Community 49"
Cohesion: 1.0
Nodes (0): 

### Community 50 - "Community 50"
Cohesion: 1.0
Nodes (0): 

### Community 51 - "Community 51"
Cohesion: 1.0
Nodes (1): Vercel serverless entrypoint.  Vercel invokes whatever ASGI `app` this file expo

### Community 52 - "Community 52"
Cohesion: 1.0
Nodes (1): Annualized historical volatility from log-returns over `period` days.         Re

### Community 53 - "Community 53"
Cohesion: 1.0
Nodes (1): Expected 1σ and 2σ moves from spot over `days_to_expiry` calendar days.

### Community 54 - "Community 54"
Cohesion: 1.0
Nodes (1): Quick expected move from ATM straddle premium (market-implied).         1σ ≈ 0.6

### Community 55 - "Community 55"
Cohesion: 1.0
Nodes (1): Return True when it's statistically favorable to sell premium (write options).

### Community 56 - "Community 56"
Cohesion: 1.0
Nodes (1): Return True when it's favorable to buy premium (long options).         Condition

### Community 57 - "Community 57"
Cohesion: 1.0
Nodes (1): Pick the safer expiry for new entries.

### Community 58 - "Community 58"
Cohesion: 1.0
Nodes (1): Next occurrence of `weekday` (0=Mon) on or after `from_date`.

### Community 59 - "Community 59"
Cohesion: 1.0
Nodes (1): Last Thursday (or expiry_weekday) of current or next month.         NSE monthly

### Community 60 - "Community 60"
Cohesion: 1.0
Nodes (0): 

### Community 61 - "Community 61"
Cohesion: 1.0
Nodes (1): EMA of accuracy — recent predictions weigh more.

### Community 62 - "Community 62"
Cohesion: 1.0
Nodes (1): How well-calibrated is the agent's confidence vs actual accuracy?

### Community 63 - "Community 63"
Cohesion: 1.0
Nodes (0): 

### Community 64 - "Community 64"
Cohesion: 1.0
Nodes (0): 

### Community 65 - "Community 65"
Cohesion: 1.0
Nodes (0): 

### Community 66 - "Community 66"
Cohesion: 1.0
Nodes (0): 

### Community 67 - "Community 67"
Cohesion: 1.0
Nodes (0): 

### Community 68 - "Community 68"
Cohesion: 1.0
Nodes (0): 

### Community 69 - "Community 69"
Cohesion: 1.0
Nodes (0): 

### Community 70 - "Community 70"
Cohesion: 1.0
Nodes (0): 

### Community 71 - "Community 71"
Cohesion: 1.0
Nodes (0): 

### Community 72 - "Community 72"
Cohesion: 1.0
Nodes (0): 

### Community 73 - "Community 73"
Cohesion: 1.0
Nodes (0): 

### Community 74 - "Community 74"
Cohesion: 1.0
Nodes (0): 

### Community 75 - "Community 75"
Cohesion: 1.0
Nodes (0): 

### Community 76 - "Community 76"
Cohesion: 1.0
Nodes (0): 

### Community 77 - "Community 77"
Cohesion: 1.0
Nodes (0): 

### Community 78 - "Community 78"
Cohesion: 1.0
Nodes (0): 

## Knowledge Gaps
- **109 isolated node(s):** `Volatility Engine for Indian Options. Computes:   - India VIX integration (from`, `Point-in-time volatility summary.`, `Rolling IV history and volatility metrics engine.      Maintains a rolling deque`, `Add a new IV observation to the rolling history.`, `IV Rank (0–100): position of current IV in its 52-week range.         0 = at 52-` (+104 more)
  These have ≤1 connection - possible missing edges or undocumented components.
- **Thin community `Community 47`** (2 nodes): `test_event_bus_round_trip()`, `test_event_bus.py`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 48`** (2 nodes): `eventsPoller.js`, `connectEvents()`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 49`** (2 nodes): `eventReducer.js`, `eventReducer()`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 50`** (2 nodes): `test_dhan_order.py`, `main()`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 51`** (2 nodes): `index.py`, `Vercel serverless entrypoint.  Vercel invokes whatever ASGI `app` this file expo`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 52`** (1 nodes): `Annualized historical volatility from log-returns over `period` days.         Re`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 53`** (1 nodes): `Expected 1σ and 2σ moves from spot over `days_to_expiry` calendar days.`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 54`** (1 nodes): `Quick expected move from ATM straddle premium (market-implied).         1σ ≈ 0.6`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 55`** (1 nodes): `Return True when it's statistically favorable to sell premium (write options).`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 56`** (1 nodes): `Return True when it's favorable to buy premium (long options).         Condition`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 57`** (1 nodes): `Pick the safer expiry for new entries.`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 58`** (1 nodes): `Next occurrence of `weekday` (0=Mon) on or after `from_date`.`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 59`** (1 nodes): `Last Thursday (or expiry_weekday) of current or next month.         NSE monthly`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 60`** (1 nodes): `__init__.py`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 61`** (1 nodes): `EMA of accuracy — recent predictions weigh more.`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 62`** (1 nodes): `How well-calibrated is the agent's confidence vs actual accuracy?`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 63`** (1 nodes): `__init__.py`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 64`** (1 nodes): `__init__.py`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 65`** (1 nodes): `__init__.py`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 66`** (1 nodes): `__init__.py`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 67`** (1 nodes): `__init__.py`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 68`** (1 nodes): `__init__.py`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 69`** (1 nodes): `__init__.py`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 70`** (1 nodes): `__init__.py`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 71`** (1 nodes): `__init__.py`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 72`** (1 nodes): `__init__.py`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 73`** (1 nodes): `__init__.py`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 74`** (1 nodes): `tailwind.config.js`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 75`** (1 nodes): `vite.config.js`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 76`** (1 nodes): `postcss.config.js`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 77`** (1 nodes): `main.jsx`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 78`** (1 nodes): `__init__.py`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `Signal` connect `Community 1` to `Community 0`, `Community 16`, `Community 2`, `Community 20`?**
  _High betweenness centrality (0.170) - this node is a cross-community bridge._
- **Why does `OHLCV` connect `Community 1` to `Community 0`, `Community 33`, `Community 2`, `Community 5`, `Community 10`, `Community 11`, `Community 22`?**
  _High betweenness centrality (0.130) - this node is a cross-community bridge._
- **Why does `OptionsAnalysisAgent` connect `Community 0` to `Community 1`, `Community 2`?**
  _High betweenness centrality (0.118) - this node is a cross-community bridge._
- **Are the 120 inferred relationships involving `Signal` (e.g. with `CycleResult` and `Orchestrator`) actually correct?**
  _`Signal` has 120 INFERRED edges - model-reasoned connections that need verification._
- **Are the 91 inferred relationships involving `TradeSignal` (e.g. with `CycleResult` and `Orchestrator`) actually correct?**
  _`TradeSignal` has 91 INFERRED edges - model-reasoned connections that need verification._
- **Are the 76 inferred relationships involving `OHLCV` (e.g. with `OptionsRegime` and `OptionsRegimeClassifier`) actually correct?**
  _`OHLCV` has 76 INFERRED edges - model-reasoned connections that need verification._
- **Are the 70 inferred relationships involving `MarketContext` (e.g. with `OptionsAnalysisAgent` and `Options Signal Generator — main orchestration class. Implements BaseAgent so it`) actually correct?**
  _`MarketContext` has 70 INFERRED edges - model-reasoned connections that need verification._