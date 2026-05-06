"""Unit tests for Terraform HTTP backend endpoints."""
import json
from unittest.mock import AsyncMock, patch

import pytest

import app as app_module
from .conftest import make_pool, client_for

WORKSPACE = "myworkspace"
STATE = json.dumps({"version": 4, "resources": []}).encode()
LOCK = {"ID": "lock-abc", "Operation": "plan", "Who": "user@host"}
LOCK_JSON = json.dumps(LOCK)


# ── GET /state/{workspace} ────────────────────────────────────────────────────

def test_get_state_no_state_returns_204():
    pool = make_pool(fetchrow_return=None)
    with client_for(pool) as c:
        assert c.get(f"/state/{WORKSPACE}").status_code == 204


def test_get_state_returns_stored_state():
    pool = make_pool(fetchrow_return={"data": STATE})
    with client_for(pool) as c:
        res = c.get(f"/state/{WORKSPACE}")
    assert res.status_code == 200
    assert res.json() == json.loads(STATE)


def test_get_state_forbidden():
    pool = make_pool()
    with client_for(pool, authorized=False) as c:
        assert c.get(f"/state/{WORKSPACE}").status_code == 403


# ── POST /state/{workspace} ───────────────────────────────────────────────────

def test_update_state_writes_to_db():
    conn_mock = _unlocked_conn()
    pool = make_pool(conn=conn_mock)
    with client_for(pool) as c:
        res = c.post(f"/state/{WORKSPACE}", content=STATE)
    assert res.status_code == 200
    conn_mock.execute.assert_called_once()


def test_update_state_matching_lock_id_succeeds():
    conn_mock = _locked_conn(LOCK_JSON)
    pool = make_pool(conn=conn_mock)
    with client_for(pool) as c:
        res = c.post(f"/state/{WORKSPACE}?ID={LOCK['ID']}", content=STATE)
    assert res.status_code == 200


def test_update_state_wrong_lock_id_returns_409():
    conn_mock = _locked_conn(LOCK_JSON)
    pool = make_pool(conn=conn_mock)
    with client_for(pool) as c:
        res = c.post(f"/state/{WORKSPACE}?ID=wrong-id", content=STATE)
    assert res.status_code == 409
    assert res.json() == LOCK


def test_update_state_forbidden():
    pool = make_pool()
    with client_for(pool, authorized=False) as c:
        assert c.post(f"/state/{WORKSPACE}", content=STATE).status_code == 403


# ── DELETE /state/{workspace} ─────────────────────────────────────────────────

def test_delete_state_calls_db():
    pool = make_pool()
    with client_for(pool) as c:
        res = c.delete(f"/state/{WORKSPACE}")
    assert res.status_code == 200
    pool.execute.assert_called_once()


def test_delete_state_forbidden():
    pool = make_pool()
    with client_for(pool, authorized=False) as c:
        assert c.delete(f"/state/{WORKSPACE}").status_code == 403


# ── LOCK /state/{workspace} ───────────────────────────────────────────────────

def test_lock_state_inserts_lock():
    conn_mock = _unlocked_conn()
    pool = make_pool(conn=conn_mock)
    with client_for(pool) as c:
        res = c.request("LOCK", f"/state/{WORKSPACE}", content=LOCK_JSON)
    assert res.status_code == 200
    conn_mock.execute.assert_called_once()


def test_lock_state_already_locked_returns_423():
    conn_mock = _locked_conn(LOCK_JSON)
    pool = make_pool(conn=conn_mock)
    with client_for(pool) as c:
        res = c.request("LOCK", f"/state/{WORKSPACE}", content=LOCK_JSON)
    assert res.status_code == 423
    assert res.json() == LOCK


def test_lock_state_forbidden():
    pool = make_pool()
    with client_for(pool, authorized=False) as c:
        assert c.request("LOCK", f"/state/{WORKSPACE}", content=LOCK_JSON).status_code == 403


# ── UNLOCK /state/{workspace} ─────────────────────────────────────────────────

def test_unlock_state_deletes_lock():
    conn_mock = _locked_conn(LOCK_JSON)
    pool = make_pool(conn=conn_mock)
    with client_for(pool) as c:
        res = c.request("UNLOCK", f"/state/{WORKSPACE}", content=LOCK_JSON)
    assert res.status_code == 200
    conn_mock.execute.assert_called_once()


def test_unlock_state_no_lock_returns_200():
    conn_mock = _unlocked_conn()
    pool = make_pool(conn=conn_mock)
    with client_for(pool) as c:
        assert c.request("UNLOCK", f"/state/{WORKSPACE}", content=LOCK_JSON).status_code == 200


def test_unlock_state_id_mismatch_returns_409():
    conn_mock = _locked_conn(LOCK_JSON)
    pool = make_pool(conn=conn_mock)
    with client_for(pool) as c:
        res = c.request("UNLOCK", f"/state/{WORKSPACE}", content=json.dumps({**LOCK, "ID": "wrong"}))
    assert res.status_code == 409


def test_unlock_state_forbidden():
    pool = make_pool()
    with client_for(pool, authorized=False) as c:
        assert c.request("UNLOCK", f"/state/{WORKSPACE}", content=LOCK_JSON).status_code == 403


# ── Helpers ───────────────────────────────────────────────────────────────────

def _unlocked_conn():
    from contextlib import asynccontextmanager
    from unittest.mock import MagicMock
    conn = MagicMock()
    conn.fetchrow = AsyncMock(return_value=None)
    conn.execute = AsyncMock(return_value=None)

    @asynccontextmanager
    async def transaction():
        yield

    conn.transaction = transaction
    return conn


def _locked_conn(lock_json: str):
    conn = _unlocked_conn()
    conn.fetchrow = AsyncMock(return_value={"lock_data": lock_json})
    return conn
