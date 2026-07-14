"""Tests for POST /auth/login-audit (roadmap Section 4 / Step 7).

This route is deliberately reachable with no session bearer token (see its
module docstring in app/api/routes/auth_events.py for why) -- these tests
cover its actual gate instead: the HMAC signature over {action, email, ts},
plus the DynamoDB-failure-doesn't-block pattern mirrored from
test_mcp_auth_route.py.
"""
from __future__ import annotations

import time
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

from app.core.security import sign_login_event
from app.main import app
from tests.conftest import TEST_AUTH_SHARED_SECRET

client = TestClient(app)


def _signed_payload(
    action: str,
    email: str,
    ts: int | None = None,
    secret: str = TEST_AUTH_SHARED_SECRET,
) -> dict:
    ts = ts if ts is not None else int(time.time())
    return {
        "action": action,
        "email": email,
        "ts": ts,
        "signature": sign_login_event(action, email, ts, secret),
    }


@patch("app.api.routes.auth_events.audit_log_service.write_entry")
def test_login_success_with_valid_signature_writes_audit_entry(mock_audit: MagicMock) -> None:
    payload = _signed_payload("login_success", "admin@example.com")

    response = client.post("/auth/login-audit", json=payload)

    assert response.status_code == 200
    assert response.json()["recorded"] is True
    mock_audit.assert_called_once_with("login_success", actor_email="admin@example.com")


@patch("app.api.routes.auth_events.audit_log_service.write_entry")
def test_login_failed_with_valid_signature_writes_audit_entry_for_attempted_email(
    mock_audit: MagicMock,
) -> None:
    """Recorded even on failure -- and against the *attempted* email, not
    the configured admin email, since "someone tried logging in as X and
    failed" is the useful signal for a single-admin app."""
    payload = _signed_payload("login_failed", "attacker@example.com")

    response = client.post("/auth/login-audit", json=payload)

    assert response.status_code == 200
    assert response.json()["recorded"] is True
    mock_audit.assert_called_once_with("login_failed", actor_email="attacker@example.com")


@patch("app.api.routes.auth_events.audit_log_service.write_entry")
def test_rejects_signature_signed_with_wrong_secret(mock_audit: MagicMock) -> None:
    payload = _signed_payload("login_success", "admin@example.com", secret="wrong-secret")

    response = client.post("/auth/login-audit", json=payload)

    assert response.status_code == 401
    mock_audit.assert_not_called()


@patch("app.api.routes.auth_events.audit_log_service.write_entry")
def test_rejects_tampered_signature(mock_audit: MagicMock) -> None:
    payload = _signed_payload("login_success", "admin@example.com")
    payload["email"] = "someone-else@example.com"  # signature no longer matches this body

    response = client.post("/auth/login-audit", json=payload)

    assert response.status_code == 401
    mock_audit.assert_not_called()


@patch("app.api.routes.auth_events.audit_log_service.write_entry")
def test_rejects_stale_timestamp(mock_audit: MagicMock) -> None:
    stale_ts = int(time.time()) - 3600  # well outside the freshness window
    payload = _signed_payload("login_success", "admin@example.com", ts=stale_ts)

    response = client.post("/auth/login-audit", json=payload)

    assert response.status_code == 401
    mock_audit.assert_not_called()


@patch("app.api.routes.auth_events.audit_log_service.write_entry")
def test_rejects_future_timestamp_outside_freshness_window(mock_audit: MagicMock) -> None:
    future_ts = int(time.time()) + 3600
    payload = _signed_payload("login_success", "admin@example.com", ts=future_ts)

    response = client.post("/auth/login-audit", json=payload)

    assert response.status_code == 401
    mock_audit.assert_not_called()


@patch("app.api.routes.auth_events.audit_log_service.write_entry")
def test_login_audit_recorded_even_when_dynamodb_write_fails(mock_audit: MagicMock) -> None:
    """A transient DynamoDB failure on the audit write must not surface as
    an error to the caller (NextAuth's authorize()) -- same non-blocking
    pattern as app/api/routes/mcp_auth.py's token generate/revoke writes."""
    mock_audit.side_effect = Exception("dynamodb unavailable")
    payload = _signed_payload("login_failed", "admin@example.com")

    response = client.post("/auth/login-audit", json=payload)

    assert response.status_code == 200
    assert response.json()["recorded"] is True
    mock_audit.assert_called_once_with("login_failed", actor_email="admin@example.com")


@patch("app.api.routes.auth_events.audit_log_service.write_entry")
def test_login_audit_does_not_require_a_session_bearer_token(mock_audit: MagicMock) -> None:
    """This route is the one intentional exception to "every route requires
    a session" -- it's called before any session JWT exists. Confirms it's
    reachable with no Authorization header at all, gated only by the
    signature."""
    payload = _signed_payload("login_success", "admin@example.com")

    response = client.post("/auth/login-audit", json=payload)

    assert response.status_code == 200
    mock_audit.assert_called_once_with("login_success", actor_email="admin@example.com")


def test_rejects_missing_signature_field() -> None:
    response = client.post(
        "/auth/login-audit",
        json={"action": "login_success", "email": "admin@example.com", "ts": int(time.time())},
    )
    assert response.status_code == 422


def test_fails_closed_when_auth_shared_secret_not_configured() -> None:
    from app.core.config import Settings, get_settings

    payload = _signed_payload("login_success", "admin@example.com")

    def _settings_with_no_secret() -> Settings:
        return Settings(auth_shared_secret=None, admin_email="admin@example.com")

    app.dependency_overrides[get_settings] = _settings_with_no_secret
    try:
        response = client.post("/auth/login-audit", json=payload)
    finally:
        app.dependency_overrides.pop(get_settings, None)

    assert response.status_code == 503
