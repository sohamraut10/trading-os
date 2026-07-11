"""
Tests for api/main.py's request contracts: auth gating on state-changing
endpoints, CORS allow-listing, and the mock-mode analyze/trade flow.

MOCK_MODE must be set before api.main is imported (AppState reads it directly
from os.environ at construction time), and settings are overridden in-place
before import so these tests never touch a real Postgres/live-suggestions loop.
"""
import os

os.environ.setdefault("MOCK_MODE", "true")

from fastapi.testclient import TestClient

from config.settings import settings

settings.api_auth_token = "test-secret"
settings.enable_live_suggestions = False
settings.database_url = "postgresql+asyncpg://nouser:nopass@localhost:1/nodb"

from api.main import app, state  # noqa: E402  (must follow the settings overrides above)


def test_health_ok():
    with TestClient(app) as client:
        res = client.get("/health")
        assert res.status_code == 200
        assert res.json()["status"] == "ok"


def test_analyze_returns_signal_in_mock_mode():
    with TestClient(app) as client:
        res = client.post("/analyze", json={"asset": "BTCUSDT", "timeframe": "1h", "candle_limit": 100})
        assert res.status_code == 200
        body = res.json()
        assert body["asset"] == "BTCUSDT"
        assert "final_decision" in body


def test_analyze_with_execute_requires_api_key():
    with TestClient(app) as client:
        res = client.post(
            "/analyze",
            json={"asset": "BTCUSDT", "timeframe": "1h", "candle_limit": 100, "execute_if_signal": True},
        )
        assert res.status_code == 401


def test_trade_submit_requires_api_key():
    with TestClient(app) as client:
        res = client.post("/trade/submit", json={"asset": "BTCUSDT", "side": "buy", "quantity": 0.01})
        assert res.status_code == 401


def test_trade_submit_rejects_wrong_api_key():
    with TestClient(app) as client:
        res = client.post(
            "/trade/submit",
            json={"asset": "BTCUSDT", "side": "buy", "quantity": 0.01},
            headers={"X-API-Key": "wrong"},
        )
        assert res.status_code == 401


def test_trade_submit_succeeds_with_api_key_and_runs_risk_check():
    with TestClient(app) as client:
        res = client.post(
            "/trade/submit",
            json={"asset": "BTCUSDT", "side": "buy", "quantity": 0.01},
            headers={"X-API-Key": "test-secret"},
        )
        assert res.status_code == 200
        body = res.json()
        assert body["status"] == "filled"
        assert "risk_check" in body


def test_trade_submit_rejected_by_risk_engine_on_circuit_breaker():
    with TestClient(app) as client:
        original = state.portfolio.consecutive_losses
        state.portfolio.consecutive_losses = 5  # hard circuit breaker threshold
        try:
            res = client.post(
                "/trade/submit",
                json={"asset": "BTCUSDT", "side": "buy", "quantity": 0.01},
                headers={"X-API-Key": "test-secret"},
            )
            assert res.status_code == 400
            assert "rejections" in res.json()["detail"]
        finally:
            state.portfolio.consecutive_losses = original


def test_strategy_select_requires_api_key():
    with TestClient(app) as client:
        res = client.post("/strategy/select", json={"strategy": "swing"})
        assert res.status_code == 401


def test_portfolio_reset_requires_api_key():
    with TestClient(app) as client:
        res = client.post("/portfolio/reset")
        assert res.status_code == 401


def test_cors_allows_configured_origin_only():
    with TestClient(app) as client:
        allowed = client.options(
            "/trade/submit",
            headers={"Origin": "http://localhost:5173", "Access-Control-Request-Method": "POST"},
        )
        assert allowed.headers.get("access-control-allow-origin") == "http://localhost:5173"

        disallowed = client.options(
            "/trade/submit",
            headers={"Origin": "http://evil.example", "Access-Control-Request-Method": "POST"},
        )
        assert "access-control-allow-origin" not in disallowed.headers
