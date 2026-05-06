"""Unit tests for auth helpers: authenticate_user, check_permissions, get_current_token."""
import asyncio
import base64
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

import app as app_module
from app import app, authenticate_user, check_permissions, get_current_token, get_db
from .conftest import make_pool, _lifespan_pool, client_for


def basic_header(username, password):
    creds = base64.b64encode(f"{username}:{password}".encode()).decode()
    return {"Authorization": f"Basic {creds}"}


# ── authenticate_user ─────────────────────────────────────────────────────────

def test_authenticate_user_posts_to_gatekeeper():
    mock_res = MagicMock()
    mock_res.json.return_value = {"token": "returned-token"}
    with patch("app.requests.post", return_value=mock_res) as mock_post:
        token = authenticate_user("user@example.com", "secret")
    mock_post.assert_called_once_with(
        f"{app_module.GATEKEEPER_URL}/login",
        json={"email": "user@example.com", "password": "secret"},
    )
    assert token == "returned-token"


# ── check_permissions ─────────────────────────────────────────────────────────

def test_check_permissions_returns_true_when_authorized():
    mock_res = MagicMock()
    mock_res.status_code = 200
    mock_res.json.return_value = {"authorized": True}
    with patch("app.requests.get", return_value=mock_res):
        result = asyncio.run(check_permissions("tok", "resource", "action"))
    assert result is True


def test_check_permissions_returns_false_when_not_authorized():
    mock_res = MagicMock()
    mock_res.status_code = 200
    mock_res.json.return_value = {"authorized": False}
    with patch("app.requests.get", return_value=mock_res):
        result = asyncio.run(check_permissions("tok", "resource", "action"))
    assert result is False


def test_check_permissions_returns_false_on_gatekeeper_error():
    mock_res = MagicMock()
    mock_res.status_code = 500
    mock_res.reason = "Internal Server Error"
    with patch("app.requests.get", return_value=mock_res):
        result = asyncio.run(check_permissions("tok", "resource", "action"))
    assert result is False


def test_check_permissions_sends_correct_payload():
    mock_res = MagicMock()
    mock_res.status_code = 200
    mock_res.json.return_value = {"authorized": True}
    with patch("app.requests.get", return_value=mock_res) as mock_get:
        asyncio.run(check_permissions("my-token", "blueprints/states/ws", "getState"))
    mock_get.assert_called_once_with(
        f"{app_module.GATEKEEPER_URL}/check_permissions",
        json={"service": "blueprints", "resource": "blueprints/states/ws", "action": "getState"},
        headers={"Authorization": "Bearer my-token"},
    )


# ── get_current_token (via HTTP) ──────────────────────────────────────────────

def test_bearer_token_is_accepted():
    pool = make_pool()
    with client_for(pool) as c:
        res = c.get("/state/ws", headers={"Authorization": "Bearer mytoken"})
    assert res.status_code == 204


def test_basic_auth_exchanges_credentials_for_token():
    pool = make_pool()
    mock_login = MagicMock()
    mock_login.json.return_value = {"token": "gk-token"}
    with patch("app.requests.post", return_value=mock_login):
        with client_for(pool) as c:
            res = c.get("/state/ws", headers=basic_header("user@x.com", "pass"))
    assert res.status_code == 204


def test_no_auth_returns_401():
    pool = make_pool()
    app.dependency_overrides[get_db] = lambda: pool
    with patch("app.asyncpg.create_pool", AsyncMock(return_value=_lifespan_pool())):
        with TestClient(app) as c:
            res = c.get("/state/ws")
    app.dependency_overrides.clear()
    assert res.status_code == 401
