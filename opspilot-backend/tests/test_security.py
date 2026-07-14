"""Direct tests of the session-validation dependency and its wiring.

This is the backend half of "no session -> no access" (roadmap Section
3.5) — verified independently of the frontend, exactly as the roadmap
requires ("the frontend redirect is not the only protection").
"""
from __future__ import annotations

import time

import jwt
import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings, get_settings
from app.main import app
from tests.conftest import TEST_ADMIN_EMAIL, TEST_AUTH_SHARED_SECRET

client = TestClient(app)

FUTURE_EXP = int(time.time()) + 3600

# Every protected route, one from each router wired behind require_session
# in app/main.py — proves the dependency is actually applied broadly, not
# just on the two routes that happen to have their own test files.
PROTECTED_GET_ROUTES = [
    "/resources/ec2",
    "/resources/overview",
    "/investigations",
    "/mcp/tools",
]


@pytest.mark.parametrize("path", PROTECTED_GET_ROUTES)
def test_protected_routes_reject_missing_token(path: str) -> None:
    response = client.get(path)
    assert response.status_code == 401


@pytest.mark.parametrize("path", PROTECTED_GET_ROUTES)
def test_protected_routes_reject_garbage_token(path: str) -> None:
    response = client.get(path, headers={"Authorization": "Bearer not-a-real-token"})
    assert response.status_code == 401


def test_chat_route_rejects_missing_token() -> None:
    response = client.post("/chat", json={"message": "hello"})
    assert response.status_code == 401


def test_health_route_does_not_require_a_token() -> None:
    """Liveness check stays open — no bearer token needed."""
    response = client.get("/health")
    assert response.status_code == 200


def test_rejects_token_signed_with_wrong_secret() -> None:
    bad_token = jwt.encode(
        {"sub": TEST_ADMIN_EMAIL, "exp": FUTURE_EXP}, "wrong-secret", algorithm="HS256"
    )
    response = client.get("/investigations", headers={"Authorization": f"Bearer {bad_token}"})
    assert response.status_code == 401


def test_rejects_expired_token() -> None:
    expired_token = jwt.encode(
        {"sub": TEST_ADMIN_EMAIL, "exp": int(time.time()) - 60},
        TEST_AUTH_SHARED_SECRET,
        algorithm="HS256",
    )
    response = client.get(
        "/investigations", headers={"Authorization": f"Bearer {expired_token}"}
    )
    assert response.status_code == 401


def test_rejects_token_for_a_different_email_than_configured_admin() -> None:
    other_token = jwt.encode(
        {"sub": "someone-else@example.com", "exp": FUTURE_EXP},
        TEST_AUTH_SHARED_SECRET,
        algorithm="HS256",
    )
    response = client.get(
        "/investigations", headers={"Authorization": f"Bearer {other_token}"}
    )
    assert response.status_code == 401


def test_rejects_validly_signed_token_missing_exp_claim() -> None:
    """A validly-signed token that simply omits `exp` must still be
    rejected — PyJWT only checks expiry if `exp` is present at all, so
    without `options={"require": ["exp", "sub"]}` this would otherwise
    grant a token that can never expire."""
    no_exp_token = jwt.encode({"sub": TEST_ADMIN_EMAIL}, TEST_AUTH_SHARED_SECRET, algorithm="HS256")
    response = client.get(
        "/investigations", headers={"Authorization": f"Bearer {no_exp_token}"}
    )
    assert response.status_code == 401


def test_rejects_validly_signed_token_missing_sub_claim() -> None:
    no_sub_token = jwt.encode({"exp": FUTURE_EXP}, TEST_AUTH_SHARED_SECRET, algorithm="HS256")
    response = client.get(
        "/investigations", headers={"Authorization": f"Bearer {no_sub_token}"}
    )
    assert response.status_code == 401


def test_accepts_valid_token(auth_headers: dict[str, str]) -> None:
    response = client.get("/investigations", headers=auth_headers)
    assert response.status_code == 200


def test_fails_closed_when_auth_shared_secret_not_configured(
    auth_headers: dict[str, str],
) -> None:
    """If AUTH_SHARED_SECRET is unset, every request must be rejected
    (503) rather than silently accepted — a regression to fail-open here
    would open the whole API up with no token check at all."""

    def _settings_with_no_secret() -> Settings:
        return Settings(auth_shared_secret=None, admin_email=TEST_ADMIN_EMAIL)

    app.dependency_overrides[get_settings] = _settings_with_no_secret
    try:
        # Even a token that would otherwise be perfectly valid must not help.
        response = client.get("/investigations", headers=auth_headers)
    finally:
        # tests/conftest.py's autouse fixture restores the normal override
        # on the next test regardless, but leave this test's own state tidy.
        app.dependency_overrides.pop(get_settings, None)

    assert response.status_code == 503
