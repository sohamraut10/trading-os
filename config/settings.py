from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import BaseModel
from typing import Literal


class AgentWeights(BaseModel):
    technical: float = 0.25
    sentiment: float = 0.20
    quant: float = 0.20
    order_flow: float = 0.20
    options: float = 0.15    # options agent — votes only on index underlyings

    def normalize(self) -> dict:
        total = self.technical + self.sentiment + self.quant + self.order_flow + self.options
        return {
            "technical": self.technical / total,
            "sentiment": self.sentiment / total,
            "quant": self.quant / total,
            "order_flow": self.order_flow / total,
            "options": self.options / total,
        }


class RiskConfig(BaseModel):
    max_position_pct: float = 0.05
    max_portfolio_exposure: float = 0.40
    max_daily_drawdown: float = 0.03
    max_trade_drawdown: float = 0.02
    default_rr_ratio: float = 2.0
    max_open_trades: int = 10
    volatility_circuit_breaker_vix: float = 35.0


class ConsensusConfig(BaseModel):
    min_agents_agree: int = 2
    min_avg_confidence: float = 70.0
    min_agent_confidence: float = 60.0
    devils_advocate_veto_threshold: float = 85.0


class Settings(BaseSettings):
    # App
    app_name: str = "TradingOS"
    environment: Literal["development", "staging", "production"] = "development"
    debug: bool = False
    log_level: str = "INFO"

    # Auth — set api_auth_token to require it on state-changing endpoints
    # (trade submit/close/reset, strategy pin, manual analyze-with-execute)
    api_auth_token: str = ""
    cors_allowed_origins: str = "http://localhost:5173,http://localhost:8000,http://localhost:3000"

    # API Keys
    anthropic_api_key: str = ""
    openai_api_key: str = ""
    gemini_api_key: str = ""
    alpaca_api_key: str = ""
    alpaca_secret_key: str = ""
    alpaca_base_url: str = "https://paper-api.alpaca.markets"
    dhan_client_id: str = ""
    dhan_access_token: str = ""
    dhan_default_exchange: str = "NSE_EQ"
    # MIS = intraday (allows SL stop orders + short selling on NSE_EQ).
    # CNC = delivery (no SL trigger orders, no short selling without holdings).
    # Set to MIS so bracket orders work on both NSE and MCX.
    dhan_product_type: str = "INTRADAY"
    binance_api_key: str = ""
    binance_secret: str = ""
    polygon_api_key: str = ""
    news_api_key: str = ""
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""

    # Infrastructure (empty defaults = in-memory / disabled — safe for Vercel serverless)
    redis_url: str = ""
    database_url: str = ""
    kafka_bootstrap_servers: str = ""

    # Sub-configs
    agent_weights: AgentWeights = AgentWeights()
    risk: RiskConfig = RiskConfig()
    consensus: ConsensusConfig = ConsensusConfig()

    # LLM
    primary_llm_model: str = "claude-sonnet-4-6"
    fallback_llm_model: str = "claude-haiku-4-5-20251001"
    llm_max_tokens: int = 2048
    llm_temperature: float = 0.1           # low = deterministic analysis

    # "anthropic" | "gemini" | "auto" — auto prefers Anthropic if configured,
    # else falls back to Gemini. Used by SentimentAgent and TradeJournal.
    llm_provider: Literal["anthropic", "gemini", "auto"] = "auto"
    anthropic_model: str = "claude-haiku-4-5-20251001"
    gemini_model: str = "gemini-2.5-flash"

    # Execution
    slippage_tolerance_bps: float = 5.0    # basis points
    smart_order_min_size_usd: float = 10000.0

    # Background live-suggestions loop (api/main.py)
    # On serverless (Vercel), there is no persistent process to run this loop
    # in, so it's replaced by a Vercel Cron hitting POST /cron/tick instead.
    enable_live_suggestions: bool = True
    # Indices first — options router converts these to CE/PE.
    # MCX futures appended for commodity diversification.
    live_suggestions_assets: str = "NIFTY,BANKNIFTY,FINNIFTY,MIDCPNIFTY,SENSEX,CRUDEOIL,GOLD,SILVER,NATURALGAS"
    live_suggestions_interval_sec: float = 15.0
    # "watchlist" → scan live_suggestions_assets only
    # "full_market" → rotate through all F&O equities + MCX from the scrip master
    scan_mode: Literal["watchlist", "full_market"] = "watchlist"
    scan_batch_size: int = 20   # symbols per rotation in full_market mode
    # When True, the live-suggestions loop will fire real orders when consensus
    # produces a BUY/SELL signal. Keep False (default) until manually enabled.
    auto_execute_signals: bool = False

    # ── Options trading ───────────────────────────────────────────────────────
    # "equity"  → trade the underlying directly (default)
    # "options" → convert every signal into an options buy (CE for BUY, PE for SELL)
    trade_mode: Literal["equity", "options"] = "options"
    options_otm_strikes: int = 1        # how many strikes OTM from ATM
    options_min_days_to_expiry: int = 2 # skip expiry if fewer days remain
    options_sl_pct: float = 0.50        # close option if premium falls by this fraction
    # In options mode, premium paid = max loss (unlike equity notional).
    # 40% allocation per trade — with a small account most capital must be
    # deployable to afford even 1 lot of index options.
    # Overrides risk.max_position_pct when trade_mode="options".
    options_max_position_pct: float = 0.40
    # Minimum R:R ratio to take a trade (equity only — options enforced via TP order).
    # Trades with expected reward < min_risk_reward × risk are rejected outright.
    min_risk_reward: float = 2.0

    # ── Trade-frequency guardrails ────────────────────────────────────────────
    # Dhan charges ₹20 flat brokerage per order. High-frequency execution
    # drains capital through brokerage even when gross PnL is flat/positive.
    max_trades_per_day: int = 12           # hard cap across all assets combined
    trade_cooldown_minutes: int = 60       # min gap (minutes) between trades on same asset

    # Shared secret required on the Authorization header of POST /cron/tick.
    # Empty disables the endpoint entirely (fails closed, unlike api_auth_token).
    cron_secret: str = ""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_nested_delimiter="__",
        extra="ignore",
    )


settings = Settings()
