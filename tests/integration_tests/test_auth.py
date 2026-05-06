"""Integration tests for authentication flows."""
import requests

GATEKEEPER_URL = "http://localhost:8080"

EMAIL = "auth_integration_test@example.com"
PASSWORD = "auth_integration_pass"


def test_signup_and_login_returns_token():
    requests.post(
        f"{GATEKEEPER_URL}/signup",
        json={"email": EMAIL, "username": "auth_integration_tester", "password": PASSWORD},
    )
    res = requests.post(
        f"{GATEKEEPER_URL}/login",
        json={"email": EMAIL, "password": PASSWORD},
    )
    assert res.status_code == 200
    assert "token" in res.json()


def test_login_wrong_password_fails():
    res = requests.post(
        f"{GATEKEEPER_URL}/login",
        json={"email": EMAIL, "password": "wrong-password"},
    )
    assert res.status_code != 200
