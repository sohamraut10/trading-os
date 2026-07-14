"""
Postgres persistence — durable storage for signals, agent decisions, portfolio
snapshots, and pipeline events, matching the schema in infrastructure/init.sql.

Entirely optional: if the database is unreachable at startup, connect() logs a
warning and every write becomes a no-op. Nothing else in the system depends on
persistence succeeding — signals are still generated, trades still execute,
the API still serves requests. This only adds durability across restarts.
"""
import json
import logging

try:
    import asyncpg
    _ASYNCPG_AVAILABLE = True
except ImportError:
    _ASYNCPG_AVAILABLE = False

from core.agents.meta_agent import TradeSignal
from core.risk.risk_engine import PortfolioState

log = logging.getLogger("trading_os.persistence")


def _to_asyncpg_dsn(database_url: str) -> str:
    """SQLAlchemy-style URLs (postgresql+asyncpg://...) aren't valid asyncpg DSNs."""
    return database_url.replace("postgresql+asyncpg://", "postgresql://")


class Repository:
    def __init__(self, database_url: str):
        self._dsn = _to_asyncpg_dsn(database_url)
        self._pool = None

    @property
    def connected(self) -> bool:
        return self._pool is not None

    async def connect(self, timeout: float = 5.0) -> None:
        if not _ASYNCPG_AVAILABLE:
            log.warning("asyncpg not installed — running without DB persistence")
            return
        try:
            self._pool = await asyncpg.create_pool(
                self._dsn, min_size=1, max_size=5, timeout=timeout, command_timeout=timeout,
            )
            log.info("Connected to Postgres for persistence")
        except Exception as e:
            log.warning("Database unavailable (%s) — running without DB persistence", e)
            self._pool = None

    async def close(self) -> None:
        if self._pool:
            await self._pool.close()
            self._pool = None

    async def record_signal(self, signal: TradeSignal, timeframe: str, strategy: str) -> None:
        if not self._pool:
            return
        try:
            async with self._pool.acquire() as conn:
                async with conn.transaction():
                    await conn.execute(
                        """
                        INSERT INTO signals (request_id, asset, timeframe, regime, final_decision,
                                              action, confidence, reason, strategy, payload)
                        VALUES ($1::uuid, $2, $3, $4, $5, $6, $7, $8, $9, $10::jsonb)
                        ON CONFLICT (request_id) DO NOTHING
                        """,
                        signal.request_id, signal.asset, timeframe, signal.regime,
                        signal.final_decision, signal.action.value if signal.action else None,
                        signal.confidence, signal.reason, strategy, json.dumps(signal.to_dict()),
                    )
                    for agent in signal.agents:
                        await conn.execute(
                            """
                            INSERT INTO agent_decisions (request_id, agent_name, signal, confidence,
                                                          reasoning, indicators, latency_ms)
                            VALUES ($1::uuid, $2, $3, $4, $5, $6::jsonb, $7)
                            """,
                            signal.request_id, agent.get("name"), agent.get("decision"),
                            agent.get("confidence", 0.0), agent.get("reasoning", ""),
                            json.dumps(agent.get("indicators", {})), agent.get("latency_ms", 0.0),
                        )
        except Exception:
            log.exception("Failed to persist signal %s", signal.request_id)

    async def record_event(self, event: dict) -> None:
        if not self._pool:
            return
        try:
            async with self._pool.acquire() as conn:
                await conn.execute(
                    """
                    INSERT INTO events (event_id, cycle_id, ts, event_type, payload)
                    VALUES ($1::uuid, $2, to_timestamp($3), $4, $5::jsonb)
                    ON CONFLICT (event_id) DO NOTHING
                    """,
                    event["event_id"], event["cycle_id"], event["ts"], event["type"],
                    json.dumps(event["payload"]),
                )
        except Exception:
            log.exception("Failed to persist event %s", event.get("event_id"))

    async def snapshot_portfolio(self, portfolio: PortfolioState) -> None:
        if not self._pool:
            return
        try:
            async with self._pool.acquire() as conn:
                await conn.execute(
                    """
                    INSERT INTO portfolio_snapshots (equity, cash, total_exposure, daily_pnl_pct, open_trades)
                    VALUES ($1, $2, $3, $4, $5)
                    """,
                    portfolio.equity, portfolio.cash, portfolio.total_exposure_pct,
                    portfolio.daily_pnl_pct, portfolio.open_trades,
                )
        except Exception:
            log.exception("Failed to persist portfolio snapshot")

    async def load_latest_portfolio_snapshot(self) -> dict | None:
        """
        Restores equity/cash/daily_pnl_pct/open_trades from the most recent
        snapshot. Needed on serverless: each cold start gets a fresh in-memory
        PortfolioState, so without this every cold start would silently reset
        to the hardcoded starting equity instead of resuming.
        """
        if not self._pool:
            return None
        try:
            async with self._pool.acquire() as conn:
                row = await conn.fetchrow(
                    "SELECT equity, cash, daily_pnl_pct, open_trades "
                    "FROM portfolio_snapshots ORDER BY snapshot_at DESC LIMIT 1"
                )
                return dict(row) if row else None
        except Exception:
            log.exception("Failed to load latest portfolio snapshot")
            return None

    async def fetch_recent_events(self, after: str | None = None, limit: int = 200) -> list[dict]:
        """
        Returns events newer than the given event_id cursor (or the most
        recent `limit` if no cursor given), oldest first — the polling
        replacement for the WebSocket event stream on deployments (like
        Vercel) that can't hold a persistent connection open.
        """
        if not self._pool:
            return []
        try:
            async with self._pool.acquire() as conn:
                if after:
                    cursor_ts = await conn.fetchval(
                        "SELECT created_at FROM events WHERE event_id = $1::uuid", after
                    )
                    if cursor_ts is None:
                        rows = await conn.fetch(
                            "SELECT * FROM (SELECT * FROM events ORDER BY created_at DESC LIMIT $1) t ORDER BY created_at ASC",
                            limit,
                        )
                    else:
                        rows = await conn.fetch(
                            "SELECT * FROM events WHERE created_at > $1 ORDER BY created_at ASC LIMIT $2",
                            cursor_ts, limit,
                        )
                else:
                    rows = await conn.fetch(
                        "SELECT * FROM (SELECT * FROM events ORDER BY created_at DESC LIMIT $1) t ORDER BY created_at ASC",
                        limit,
                    )
                return [
                    {
                        "event_id": str(r["event_id"]),
                        "cycle_id": r["cycle_id"],
                        "ts": r["ts"].timestamp(),
                        "type": r["event_type"],
                        "payload": json.loads(r["payload"]),
                    }
                    for r in rows
                ]
        except Exception:
            log.exception("Failed to fetch recent events")
            return []
