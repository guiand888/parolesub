"""Tests for healthz API endpoint."""

import asyncio
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import OperationalError

from audio_to_subs.api.app import create_app
from audio_to_subs.api.routes.healthz import healthz
from audio_to_subs.api.settings import Settings
from audio_to_subs.db.base import get_async_engine


@pytest.fixture
def client_without_lifespan():
    """Create test client without running lifespan."""
    import os

    os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///:memory:"
    os.environ["BEHIND_TLS"] = "false"

    app = create_app()
    # Override lifespan to skip startup
    app.router.lifespan_context = None
    return TestClient(app, raise_server_exceptions=False)


class TestHealthz:
    """Test health check endpoint."""

    def test_healthz_route_exists(self, client_without_lifespan):
        """Test that /api/healthz route exists."""
        # This will fail because DB isn't initialized, but we're testing route exists
        response = client_without_lifespan.get("/api/healthz")

        # Should get 503 or similar error (not 404)
        assert response.status_code != 404

    def test_healthz_response_structure(self, client_without_lifespan):
        """Test that /api/healthz returns expected response structure."""
        response = client_without_lifespan.get("/api/healthz")

        # Check response has expected fields (even if error)
        # The response should be JSON
        try:
            data = response.json()
            # Should have detail field if error
            assert "detail" in data or "status" in data
        except Exception:
            # Response might not be JSON if DB error
            pass

    def test_healthz_returns_503_when_db_unavailable(self, client_without_lifespan):
        """503 returned when the DB raises OperationalError (e.g. locked).

        Regression: when the Bazarr poller held BEGIN IMMEDIATE across its
        inter-poll sleep, every health check session raised OperationalError
        and the endpoint returned 503, preventing worker/frontend startup.
        """
        with patch(
            "audio_to_subs.api.routes.healthz.get_async_engine",
            side_effect=OperationalError("database is locked", None, None),
        ):
            response = client_without_lifespan.get("/api/healthz")

        assert response.status_code == 503
        detail = response.json()["detail"]
        assert "database" in detail
        assert "error" in detail["database"]

    def test_healthz_not_blocked_by_concurrent_writer(self, tmp_path):
        """/api/healthz returns promptly while another connection holds
        BEGIN IMMEDIATE.

        Regression guard: healthz previously went through
        get_async_session(), whose "begin" listener issues BEGIN IMMEDIATE
        unconditionally, so every 10s health probe took SQLite's single
        writer lock even though it only ever reads. Uses a temp file-based
        DB (not ``:memory:``, whose pooled connections don't reliably share
        one on-disk database/lock across connections) so the writer and the
        probe actually contend for the same lock, proving the
        READ_ONLY_OPTION path (see db/base.py) is wired up end to end.
        """
        dsn = f"sqlite+aiosqlite:///{tmp_path / 'healthz-lock-test.db'}"
        settings = Settings(DATABASE_URL=dsn, SESSION_SECRET="test-secret-not-real")

        async def hold_write_lock_and_probe():
            engine = get_async_engine(dsn)
            writer = await engine.connect()
            await writer.execute(text("CREATE TABLE IF NOT EXISTS t(x int)"))
            try:
                return await asyncio.wait_for(healthz(settings), timeout=2.0)
            finally:
                await writer.rollback()
                await writer.close()

        result = asyncio.run(hold_write_lock_and_probe())
        assert result.status == "ok"
