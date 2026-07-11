-- Trading OS PostgreSQL Schema
-- Stores all signals, agent decisions, trades, and performance records

CREATE TABLE IF NOT EXISTS signals (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    request_id  UUID NOT NULL UNIQUE,
    asset       VARCHAR(20) NOT NULL,
    timeframe   VARCHAR(5) NOT NULL,
    regime      VARCHAR(20),
    final_decision BOOLEAN NOT NULL,
    action      VARCHAR(5),
    confidence  NUMERIC(5,2),
    reason      TEXT,
    strategy    VARCHAR(30),
    payload     JSONB NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_signals_asset     ON signals(asset, created_at DESC);
CREATE INDEX idx_signals_decision  ON signals(final_decision, created_at DESC);

CREATE TABLE IF NOT EXISTS agent_decisions (
    id          BIGSERIAL PRIMARY KEY,
    request_id  UUID NOT NULL REFERENCES signals(request_id) ON DELETE CASCADE,
    agent_name  VARCHAR(30) NOT NULL,
    signal      VARCHAR(5) NOT NULL,
    confidence  NUMERIC(5,2) NOT NULL,
    reasoning   TEXT,
    indicators  JSONB,
    latency_ms  NUMERIC(8,2),
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_agent_decisions_request ON agent_decisions(request_id);

CREATE TABLE IF NOT EXISTS trades (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    signal_id       UUID REFERENCES signals(request_id),
    asset           VARCHAR(20) NOT NULL,
    side            VARCHAR(5) NOT NULL,   -- 'buy' | 'sell'
    entry_price     NUMERIC(18,8),
    exit_price      NUMERIC(18,8),
    quantity        NUMERIC(18,8),
    notional_usd    NUMERIC(14,2),
    stop_loss       NUMERIC(18,8),
    take_profit     NUMERIC(18,8),
    pnl_pct         NUMERIC(8,4),
    pnl_usd         NUMERIC(14,2),
    hit_tp          BOOLEAN,
    hit_sl          BOOLEAN,
    strategy        VARCHAR(30),
    hold_duration   INTERVAL,
    opened_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    closed_at       TIMESTAMPTZ,
    status          VARCHAR(20) DEFAULT 'open',  -- open | closed | cancelled
    broker_ids      JSONB                         -- {entry: "xxx", sl: "yyy", tp: "zzz"}
);

CREATE INDEX idx_trades_asset  ON trades(asset, opened_at DESC);
CREATE INDEX idx_trades_status ON trades(status);

CREATE TABLE IF NOT EXISTS agent_performance (
    id          BIGSERIAL PRIMARY KEY,
    agent_name  VARCHAR(30) NOT NULL,
    trade_id    UUID REFERENCES trades(id),
    predicted   VARCHAR(5) NOT NULL,
    confidence  NUMERIC(5,2),
    outcome     SMALLINT,     -- 1 correct, -1 incorrect, 0 HOLD
    price_return NUMERIC(8,4),
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS portfolio_snapshots (
    id              BIGSERIAL PRIMARY KEY,
    equity          NUMERIC(14,2),
    cash            NUMERIC(14,2),
    total_exposure  NUMERIC(6,4),
    daily_pnl_pct   NUMERIC(8,4),
    open_trades     INTEGER,
    snapshot_at     TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS backtest_results (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    asset           VARCHAR(20) NOT NULL,
    timeframe       VARCHAR(5) NOT NULL,
    period_start    TIMESTAMPTZ,
    period_end      TIMESTAMPTZ,
    total_trades    INTEGER,
    win_rate        NUMERIC(5,4),
    sharpe_ratio    NUMERIC(8,4),
    sortino_ratio   NUMERIC(8,4),
    max_drawdown    NUMERIC(6,4),
    total_return    NUMERIC(8,4),
    profit_factor   NUMERIC(8,4),
    summary         JSONB,
    ran_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Materialized view: daily agent accuracy report
CREATE MATERIALIZED VIEW IF NOT EXISTS agent_accuracy_daily AS
SELECT
    agent_name,
    DATE(created_at) AS dt,
    COUNT(*) AS total_predictions,
    SUM(CASE WHEN outcome = 1 THEN 1 ELSE 0 END) AS correct,
    ROUND(AVG(confidence), 2) AS avg_confidence,
    ROUND(AVG(CASE WHEN outcome = 1 THEN 1.0 ELSE 0.0 END), 4) AS accuracy
FROM agent_performance
WHERE outcome != 0
GROUP BY agent_name, DATE(created_at)
ORDER BY dt DESC, accuracy DESC
WITH NO DATA;

CREATE UNIQUE INDEX ON agent_accuracy_daily(agent_name, dt);

CREATE TABLE IF NOT EXISTS events (
    id          BIGSERIAL PRIMARY KEY,
    event_id    UUID NOT NULL UNIQUE,
    cycle_id    VARCHAR(50) NOT NULL,
    ts          TIMESTAMPTZ NOT NULL,
    event_type  VARCHAR(50) NOT NULL,
    payload     JSONB NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_events_cycle_id ON events(cycle_id);
