"""Integration tests for Terraform HTTP backend endpoints."""
import base64
import json
import uuid

import pytest
import requests

BLUEPRINTS_URL = "http://localhost:8081"
GATEKEEPER_URL = "http://localhost:8080"

EMAIL = "tf_backend_test@example.com"
PASSWORD = "tf_backend_pass"


@pytest.fixture(scope="module")
def token():
    requests.post(
        f"{GATEKEEPER_URL}/signup",
        json={"email": EMAIL, "username": "tf_backend_tester", "password": PASSWORD},
    )
    res = requests.post(
        f"{GATEKEEPER_URL}/login",
        json={"email": EMAIL, "password": PASSWORD},
    )
    return res.json()["token"]


@pytest.fixture
def workspace():
    return f"ws-{uuid.uuid4().hex[:8]}"


@pytest.fixture
def bearer(token):
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def basic_auth():
    creds = base64.b64encode(f"{EMAIL}:{PASSWORD}".encode()).decode()
    return {"Authorization": f"Basic {creds}"}


def lock_body():
    return json.dumps({
        "ID": str(uuid.uuid4()),
        "Operation": "OperationTypePlan",
        "Who": "tester@host",
        "Info": "",
        "Version": "1.5.0",
        "Created": "2024-01-01T00:00:00.000000000Z",
        "Path": "",
    })


STATE = json.dumps({
    "version": 4,
    "terraform_version": "1.5.0",
    "serial": 1,
    "lineage": str(uuid.uuid4()),
    "outputs": {},
    "resources": [],
})


def test_unauthenticated_request_returns_401(workspace):
    res = requests.get(f"{BLUEPRINTS_URL}/state/{workspace}")
    assert res.status_code == 401


def test_get_empty_state_returns_204(bearer, workspace):
    res = requests.get(f"{BLUEPRINTS_URL}/state/{workspace}", headers=bearer)
    assert res.status_code == 204


def test_state_lifecycle(bearer, workspace):
    # no state yet
    res = requests.get(f"{BLUEPRINTS_URL}/state/{workspace}", headers=bearer)
    assert res.status_code == 204

    # store state
    res = requests.post(f"{BLUEPRINTS_URL}/state/{workspace}", headers=bearer, data=STATE)
    assert res.status_code == 200

    # retrieve it
    res = requests.get(f"{BLUEPRINTS_URL}/state/{workspace}", headers=bearer)
    assert res.status_code == 200
    assert res.json()["version"] == 4

    # delete it
    res = requests.delete(f"{BLUEPRINTS_URL}/state/{workspace}", headers=bearer)
    assert res.status_code == 200

    # gone again
    res = requests.get(f"{BLUEPRINTS_URL}/state/{workspace}", headers=bearer)
    assert res.status_code == 204


def test_lock_unlock_cycle(bearer, workspace):
    lock = lock_body()
    lock_data = json.loads(lock)

    res = requests.request("LOCK", f"{BLUEPRINTS_URL}/state/{workspace}", headers=bearer, data=lock)
    assert res.status_code == 200

    # second lock attempt returns 423 with lock info
    res = requests.request("LOCK", f"{BLUEPRINTS_URL}/state/{workspace}", headers=bearer, data=lock)
    assert res.status_code == 423
    assert res.json()["ID"] == lock_data["ID"]

    res = requests.request("UNLOCK", f"{BLUEPRINTS_URL}/state/{workspace}", headers=bearer, data=lock)
    assert res.status_code == 200

    # can lock again after unlock
    res = requests.request("LOCK", f"{BLUEPRINTS_URL}/state/{workspace}", headers=bearer, data=lock)
    assert res.status_code == 200

    requests.request("UNLOCK", f"{BLUEPRINTS_URL}/state/{workspace}", headers=bearer, data=lock)


def test_update_state_rejected_with_wrong_lock_id(bearer, workspace):
    lock = lock_body()

    requests.request("LOCK", f"{BLUEPRINTS_URL}/state/{workspace}", headers=bearer, data=lock)

    res = requests.post(
        f"{BLUEPRINTS_URL}/state/{workspace}?ID=wrong-id",
        headers=bearer,
        data=STATE,
    )
    assert res.status_code == 409

    requests.request("UNLOCK", f"{BLUEPRINTS_URL}/state/{workspace}", headers=bearer, data=lock)


def test_update_state_accepted_with_correct_lock_id(bearer, workspace):
    lock = lock_body()
    lock_data = json.loads(lock)

    requests.request("LOCK", f"{BLUEPRINTS_URL}/state/{workspace}", headers=bearer, data=lock)

    res = requests.post(
        f"{BLUEPRINTS_URL}/state/{workspace}?ID={lock_data['ID']}",
        headers=bearer,
        data=STATE,
    )
    assert res.status_code == 200

    requests.request("UNLOCK", f"{BLUEPRINTS_URL}/state/{workspace}", headers=bearer, data=lock)


def test_basic_auth_accepted(basic_auth, workspace):
    res = requests.get(f"{BLUEPRINTS_URL}/state/{workspace}", headers=basic_auth)
    assert res.status_code == 204


def test_unlock_without_lock_returns_200(bearer, workspace):
    res = requests.request("UNLOCK", f"{BLUEPRINTS_URL}/state/{workspace}", headers=bearer, data=lock_body())
    assert res.status_code == 200


def test_delete_nonexistent_state_returns_200(bearer, workspace):
    res = requests.delete(f"{BLUEPRINTS_URL}/state/{workspace}", headers=bearer)
    assert res.status_code == 200
