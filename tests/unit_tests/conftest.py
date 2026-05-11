"""Shared fixtures for unit tests."""
import os
os.environ.setdefault("TELEMETRY_ENABLED", "false")

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import app as app_module
from app import app, get_current_token, get_db


TOKEN = "test-token"


def _make_conn(fetchrow_return=None):
    conn = MagicMock()
    conn.fetchrow = AsyncMock(return_value=fetchrow_return)
    conn.execute = AsyncMock(return_value=None)

    @asynccontextmanager
    async def transaction():
        yield

    conn.transaction = transaction
    return conn


def make_pool(fetchrow_return=None, conn=None):
    """Build a mock asyncpg Pool. Pass conn to control transaction-scoped queries."""
    _conn = conn if conn is not None else _make_conn(fetchrow_return)
    pool = MagicMock()
    pool.fetchrow = AsyncMock(return_value=fetchrow_return)
    pool.execute = AsyncMock(return_value=None)
    pool.close = AsyncMock()

    @asynccontextmanager
    async def acquire():
        yield _conn

    pool.acquire = acquire
    pool.conn = _conn  # exposed so tests can configure conn.fetchrow per-call
    return pool


def _lifespan_pool():
    """Minimal pool used only during lifespan startup (table creation)."""
    return make_pool()


def client_for(pool, authorized=True):
    """Context manager that returns a TestClient with auth + db overridden."""
    from fastapi.testclient import TestClient

    app.dependency_overrides[get_current_token] = lambda: TOKEN
    app.dependency_overrides[get_db] = lambda: pool

    perm_mock = AsyncMock(return_value=authorized)

    from contextlib import contextmanager

    @contextmanager
    def _ctx():
        with patch("app.asyncpg.create_pool", AsyncMock(return_value=_lifespan_pool())):
            with patch.object(app_module, "check_permissions", new=perm_mock):
                with TestClient(app) as c:
                    yield c
        app.dependency_overrides.clear()

    return _ctx()
