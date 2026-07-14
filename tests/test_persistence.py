"""
Tests for the Postgres persistence layer.

Skips automatically if no database is reachable at TEST_DATABASE_URL (or the
default local trading_os DB) — persistence is optional by design, and CI
environments without Postgres provisioned should still pass the rest of the
suite. Run infrastructure/init.sql against a local Postgres to exercise these.
"""
import os
import time
import uuid

import pytest

from core.persistence.repository import Repository, _to_asyncpg_dsn
from core.agents.base_agent import Signal
from core.agents.meta_agent import TradeSignal
from core.risk.risk_engine import PortfolioState

TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL", "postgresql+asyncpg://trading:trading@localhost:5432/trading_os"
)


def _signal(**overrides) -> TradeSignal:
    defaults = dict(
        request_id=str(uuid.uuid4()), asset="BTC/USDT", timestamp=time.time(),
        final_decision=True, action=Signal.BUY, confidence=82.0,
        agents=[{"name": "Technical", "decision": "BUY", "confidence": 80.0,
                 "reasoning": "test", "indicators": {"rsi_14": 55.0}, "warnings": [], "latency_ms": 1.2}],
        reason="test signal", regime="bull",
        suggested_position_size_pct=0.03, suggested_stop_loss_pct=0.02,
        suggested_take_profit_pct=0.04, risk_reward=2.0,
    )
    defaults.update(overrides)
    return TradeSignal(**defaults)


def _portfolio(**overrides) -> PortfolioState:
    defaults = dict(
        equity=100_000.0, cash=90_000.0, open_trades=1,
        daily_pnl_pct=0.005, max_daily_drawdown_pct=0.005,
        positions={"ETH/USDT": 10_000.0}, consecutive_losses=0,
    )
    defaults.update(overrides)
    return PortfolioState(**defaults)


@pytest.fixture
async def repo():
    r = Repository(TEST_DATABASE_URL)
    await r.connect(timeout=2.0)
    if not r.connected:
        pytest.skip("Postgres not reachable at TEST_DATABASE_URL — skipping persistence tests")
    yield r
    await r.close()


def test_asyncpg_dsn_strips_sqlalchemy_dialect():
    assert _to_asyncpg_dsn("postgresql+asyncpg://u:p@host/db") == "postgresql://u:p@host/db"
    assert _to_asyncpg_dsn("postgresql://u:p@host/db") == "postgresql://u:p@host/db"


async def test_connect_to_unreachable_db_is_a_safe_noop():
    r = Repository("postgresql+asyncpg://nouser:nopass@localhost:1/nodb")
    await r.connect(timeout=1.0)
    assert not r.connected
    # All writes should be no-ops, not exceptions
    await r.record_signal(_signal(), "1h", "swing")
    await r.record_event({"event_id": str(uuid.uuid4()), "cycle_id": "c1", "ts": time.time(), "type": "Test", "payload": {}})
    await r.snapshot_portfolio(_portfolio())


async def test_record_signal_persists_signal_and_agent_decisions(repo):
    signal = _signal()
    await repo.record_signal(signal, "1h", "swing")

    async with repo._pool.acquire() as conn:
        row = await conn.fetchrow("SELECT * FROM signals WHERE request_id = $1::uuid", signal.request_id)
        assert row is not None
        assert row["asset"] == "BTC/USDT"
        assert row["action"] == "BUY"
        assert row["strategy"] == "swing"

        agent_rows = await conn.fetch(
            "SELECT * FROM agent_decisions WHERE request_id = $1::uuid", signal.request_id
        )
        assert len(agent_rows) == 1
        assert agent_rows[0]["agent_name"] == "Technical"

    # Cleanup
    async with repo._pool.acquire() as conn:
        await conn.execute("DELETE FROM signals WHERE request_id = $1::uuid", signal.request_id)


async def test_record_signal_is_idempotent_on_conflict(repo):
    signal = _signal()
    await repo.record_signal(signal, "1h", "swing")
    await repo.record_signal(signal, "1h", "swing")  # duplicate request_id — should not raise or duplicate

    async with repo._pool.acquire() as conn:
        rows = await conn.fetch("SELECT * FROM signals WHERE request_id = $1::uuid", signal.request_id)
        assert len(rows) == 1
        await conn.execute("DELETE FROM signals WHERE request_id = $1::uuid", signal.request_id)


async def test_record_event_persists(repo):
    event_id = str(uuid.uuid4())
    event = {"event_id": event_id, "cycle_id": "cycle-1", "ts": time.time(), "type": "FinalCall", "payload": {"ok": True}}
    await repo.record_event(event)

    async with repo._pool.acquire() as conn:
        row = await conn.fetchrow("SELECT * FROM events WHERE event_id = $1::uuid", event_id)
        assert row is not None
        assert row["event_type"] == "FinalCall"
        await conn.execute("DELETE FROM events WHERE event_id = $1::uuid", event_id)


async def test_snapshot_portfolio_persists(repo):
    port = _portfolio()
    async with repo._pool.acquire() as conn:
        before = await conn.fetchval("SELECT COUNT(*) FROM portfolio_snapshots")
    await repo.snapshot_portfolio(port)
    async with repo._pool.acquire() as conn:
        after = await conn.fetchval("SELECT COUNT(*) FROM portfolio_snapshots")
    assert after == before + 1


async def test_load_latest_portfolio_snapshot_returns_most_recent(repo):
    await repo.snapshot_portfolio(_portfolio(equity=100_000.0))
    await repo.snapshot_portfolio(_portfolio(equity=222_222.0, cash=210_000.0, open_trades=4))

    latest = await repo.load_latest_portfolio_snapshot()
    assert latest is not None
    assert float(latest["equity"]) == 222_222.0
    assert float(latest["cash"]) == 210_000.0
    assert int(latest["open_trades"]) == 4


async def test_load_latest_portfolio_snapshot_none_when_empty(repo):
    async with repo._pool.acquire() as conn:
        await conn.execute("TRUNCATE portfolio_snapshots")
    latest = await repo.load_latest_portfolio_snapshot()
    assert latest is None


async def test_fetch_recent_events_orders_oldest_first_and_respects_limit(repo):
    ids = [str(uuid.uuid4()) for _ in range(3)]
    base_ts = time.time()
    for i, eid in enumerate(ids):
        await repo.record_event({
            "event_id": eid, "cycle_id": "cycle-order-test", "ts": base_ts + i,
            "type": "FinalCall", "payload": {"i": i},
        })

    events = await repo.fetch_recent_events(limit=1000)
    matching = [e for e in events if e["cycle_id"] == "cycle-order-test"]
    assert [e["event_id"] for e in matching] == ids  # oldest first

    async with repo._pool.acquire() as conn:
        await conn.execute("DELETE FROM events WHERE event_id = ANY($1::uuid[])", ids)


async def test_fetch_recent_events_after_cursor_excludes_seen(repo):
    ids = [str(uuid.uuid4()) for _ in range(3)]
    base_ts = time.time()
    for i, eid in enumerate(ids):
        await repo.record_event({
            "event_id": eid, "cycle_id": "cycle-cursor-test", "ts": base_ts + i,
            "type": "FinalCall", "payload": {"i": i},
        })

    after_first = await repo.fetch_recent_events(after=ids[0], limit=1000)
    matching = [e for e in after_first if e["cycle_id"] == "cycle-cursor-test"]
    assert [e["event_id"] for e in matching] == ids[1:]

    async with repo._pool.acquire() as conn:
        await conn.execute("DELETE FROM events WHERE event_id = ANY($1::uuid[])", ids)
