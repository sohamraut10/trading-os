"""
Tests for api/index.py, the Vercel serverless entrypoint.

This guards specifically against the bug this file's design works around:
app.mount("/api", inner_app) does NOT run the mounted app's lifespan, so
state.db.connect() would silently never fire under Vercel — defeating the
whole persistence layer the serverless deployment depends on to resume
portfolio state across cold starts. api/index.py instead builds its own
top-level FastAPI app and attaches api.main's `lifespan` directly, then
includes api.main's `router` under an /api prefix. These tests exercise that
through the actual TestClient lifecycle (which does run lifespan, unlike a
bare app.mount() would) against a real Postgres, skipping if unavailable.
"""
import os

os.environ.setdefault("MOCK_MODE", "true")

import pytest
from fastapi.testclient import TestClient

from config.settings import settings
from core.persistence.repository import _to_asyncpg_dsn

settings.enable_live_suggestions = False

from api.index import app  # noqa: E402
from api.main import state  # noqa: E402

# state.db was already constructed (with whatever DSN settings.database_url
# held at that time) the first time api.main was imported — by any test
# module, not necessarily this one — so reassigning settings.database_url
# here would be too late. Patch the already-built Repository's DSN directly.
state.db._dsn = _to_asyncpg_dsn(
    os.environ.get("TEST_DATABASE_URL", "postgresql+asyncpg://trading:trading@localhost:5432/trading_os")
)


@pytest.fixture(autouse=True, scope="module")
def _skip_if_db_unreachable():
    with TestClient(app):
        if not state.db.connected:
            pytest.skip("Postgres not reachable at TEST_DATABASE_URL — skipping Vercel entrypoint tests")


def test_routes_are_mounted_under_api_prefix():
    with TestClient(app) as client:
        assert client.get("/api/health").status_code == 200
        assert client.get("/health").status_code == 404


def test_lifespan_actually_connects_the_database():
    # The bug this file guards against: app.mount() would leave state.db.connected
    # False here even though /api/health still returns 200 (DB writes are
    # designed to no-op quietly when disconnected) — connecting only happens
    # if TestClient's context manager actually runs api.main's lifespan.
    with TestClient(app):
        assert state.db.connected is True


def test_events_recent_reachable_under_api_prefix():
    with TestClient(app) as client:
        res = client.get("/api/events/recent?limit=5")
        assert res.status_code == 200
        assert isinstance(res.json(), list)
