from unittest.mock import MagicMock, patch

from botocore.exceptions import ClientError
from fastapi.testclient import TestClient

from app.main import app
from app.services.mcp_auth_service import McpTokenStatusResult

client = TestClient(app)


def _client_error(code: str, message: str) -> ClientError:
    return ClientError(
        {"Error": {"Code": code, "Message": message}},
        "GetItem",
    )


# Shared across the three "primary AWS call fails" tests below -- a
# realistic AccessDeniedException embedding the IAM caller ARN + account
# ID, the exact class of string that must never reach an HTTP response
# body (see app/core/aws_errors.py's module docstring).
_RAW_ACCESS_DENIED_MESSAGE = (
    "An error occurred (AccessDeniedException) when calling the GetItem "
    "operation: User: arn:aws:iam::123456789012:user/opspilot-app is not "
    "authorized to perform: dynamodb:GetItem on resource: "
    "arn:aws:dynamodb:us-east-1:123456789012:table/opspilot-mcp-tokens"
)


def _assert_sanitized_502(response, caplog) -> None:
    assert response.status_code == 502
    detail = response.json()["detail"]
    assert "123456789012" not in detail
    assert "arn:aws:iam" not in detail
    assert "AccessDeniedException" not in detail
    assert "opspilot-app" not in detail
    assert any(
        "123456789012" in record.getMessage() or record.exc_info is not None
        for record in caplog.records
    )


@patch("app.api.routes.mcp_auth.audit_log_service.write_entry")
@patch("app.api.routes.mcp_auth.mcp_auth_service.generate_token")
def test_generate_token_returns_plaintext_once_and_writes_audit_entry(
    mock_generate: MagicMock, mock_audit: MagicMock, auth_headers: dict[str, str]
) -> None:
    mock_generate.return_value = ("plaintext-token-value", "2026-07-11T00:00:00+00:00")

    response = client.post("/mcp/token/generate", headers=auth_headers)

    assert response.status_code == 200
    body = response.json()
    assert body["token"] == "plaintext-token-value"
    assert body["created_at"] == "2026-07-11T00:00:00+00:00"
    warning = body["warning"].lower()
    assert "shown once" in warning or "not be shown again" in warning

    mock_audit.assert_called_once_with("mcp_token_generated", actor_email="admin@example.com")


@patch("app.api.routes.mcp_auth.audit_log_service.write_entry")
@patch("app.api.routes.mcp_auth.mcp_auth_service.generate_token")
def test_generate_token_still_returns_token_when_audit_write_fails(
    mock_generate: MagicMock, mock_audit: MagicMock, auth_headers: dict[str, str]
) -> None:
    """generate_token() already persisted the new token before the audit
    write runs -- a transient DynamoDB failure on the audit table must not
    turn an already-successful token rotation into a lost one-time secret.
    """
    mock_generate.return_value = ("plaintext-token-value", "2026-07-11T00:00:00+00:00")
    mock_audit.side_effect = Exception("dynamodb unavailable")

    response = client.post("/mcp/token/generate", headers=auth_headers)

    assert response.status_code == 200
    body = response.json()
    assert body["token"] == "plaintext-token-value"
    assert body["created_at"] == "2026-07-11T00:00:00+00:00"
    mock_audit.assert_called_once_with("mcp_token_generated", actor_email="admin@example.com")


@patch("app.api.routes.mcp_auth.audit_log_service.write_entry")
@patch("app.api.routes.mcp_auth.mcp_auth_service.generate_token")
def test_generate_token_client_error_returns_sanitized_502_and_skips_audit_write(
    mock_generate: MagicMock, mock_audit: MagicMock, auth_headers: dict[str, str], caplog
) -> None:
    """A DynamoDB failure on the primary token mutation itself (not the
    best-effort audit write below it) must degrade to a clean 502, never a
    raw 500 with an ARN/account-ID-bearing traceback."""
    mock_generate.side_effect = _client_error("AccessDeniedException", _RAW_ACCESS_DENIED_MESSAGE)

    with caplog.at_level("WARNING"):
        response = client.post("/mcp/token/generate", headers=auth_headers)

    _assert_sanitized_502(response, caplog)
    mock_audit.assert_not_called()


def test_generate_token_requires_session() -> None:
    response = client.post("/mcp/token/generate")

    assert response.status_code == 401


@patch("app.api.routes.mcp_auth.audit_log_service.write_entry")
@patch("app.api.routes.mcp_auth.mcp_auth_service.revoke_token")
def test_revoke_token_writes_audit_entry_when_something_was_revoked(
    mock_revoke: MagicMock, mock_audit: MagicMock, auth_headers: dict[str, str]
) -> None:
    mock_revoke.return_value = True

    response = client.post("/mcp/token/revoke", headers=auth_headers)

    assert response.status_code == 200
    assert response.json()["revoked"] is True
    mock_audit.assert_called_once_with("mcp_token_revoked", actor_email="admin@example.com")


@patch("app.api.routes.mcp_auth.audit_log_service.write_entry")
@patch("app.api.routes.mcp_auth.mcp_auth_service.revoke_token")
def test_revoke_token_still_succeeds_when_audit_write_fails(
    mock_revoke: MagicMock, mock_audit: MagicMock, auth_headers: dict[str, str]
) -> None:
    """revoke_token() already succeeded before the audit write runs -- a
    transient DynamoDB failure on the audit table must not turn an
    already-successful revoke into a 500 (or worse, mask itself as the
    404 a retry would hit once revoke_token() correctly reports False).
    """
    mock_revoke.return_value = True
    mock_audit.side_effect = Exception("dynamodb unavailable")

    response = client.post("/mcp/token/revoke", headers=auth_headers)

    assert response.status_code == 200
    assert response.json()["revoked"] is True
    mock_audit.assert_called_once_with("mcp_token_revoked", actor_email="admin@example.com")


@patch("app.api.routes.mcp_auth.audit_log_service.write_entry")
@patch("app.api.routes.mcp_auth.mcp_auth_service.revoke_token")
def test_revoke_token_404_when_nothing_to_revoke_and_no_audit_entry_written(
    mock_revoke: MagicMock, mock_audit: MagicMock, auth_headers: dict[str, str]
) -> None:
    mock_revoke.return_value = False

    response = client.post("/mcp/token/revoke", headers=auth_headers)

    assert response.status_code == 404
    mock_audit.assert_not_called()


@patch("app.api.routes.mcp_auth.audit_log_service.write_entry")
@patch("app.api.routes.mcp_auth.mcp_auth_service.revoke_token")
def test_revoke_token_client_error_returns_sanitized_502_and_skips_audit_write(
    mock_revoke: MagicMock, mock_audit: MagicMock, auth_headers: dict[str, str], caplog
) -> None:
    """A DynamoDB failure on the primary token mutation itself (not the
    best-effort audit write below it) must degrade to a clean 502, never a
    raw 500 with an ARN/account-ID-bearing traceback."""
    mock_revoke.side_effect = _client_error("AccessDeniedException", _RAW_ACCESS_DENIED_MESSAGE)

    with caplog.at_level("WARNING"):
        response = client.post("/mcp/token/revoke", headers=auth_headers)

    _assert_sanitized_502(response, caplog)
    mock_audit.assert_not_called()


def test_revoke_token_requires_session() -> None:
    response = client.post("/mcp/token/revoke")

    assert response.status_code == 401


@patch("app.api.routes.mcp_auth.mcp_auth_service.get_status")
def test_get_token_status_no_active_token(
    mock_status: MagicMock, auth_headers: dict[str, str]
) -> None:
    mock_status.return_value = McpTokenStatusResult(
        has_active_token=False, created_at=None, revoked_at=None
    )

    response = client.get("/mcp/token/status", headers=auth_headers)

    assert response.status_code == 200
    body = response.json()
    assert body["has_active_token"] is False
    assert "token" not in body or body.get("token") is None


@patch("app.api.routes.mcp_auth.mcp_auth_service.get_status")
def test_get_token_status_active_token_never_exposes_token_value(
    mock_status: MagicMock, auth_headers: dict[str, str]
) -> None:
    mock_status.return_value = McpTokenStatusResult(
        has_active_token=True, created_at="2026-07-11T00:00:00+00:00", revoked_at=None
    )

    response = client.get("/mcp/token/status", headers=auth_headers)

    assert response.status_code == 200
    body = response.json()
    assert body["has_active_token"] is True
    assert body["created_at"] == "2026-07-11T00:00:00+00:00"
    assert set(body.keys()) == {"has_active_token", "created_at", "revoked_at"}


@patch("app.api.routes.mcp_auth.mcp_auth_service.get_status")
def test_get_token_status_client_error_returns_sanitized_502(
    mock_status: MagicMock, auth_headers: dict[str, str], caplog
) -> None:
    """The bug live-reproduced against the real AWS account: an unguarded
    DynamoDB GetItem AccessDeniedException must not 500 with a raw
    traceback embedding the IAM caller ARN + account ID."""
    mock_status.side_effect = _client_error("AccessDeniedException", _RAW_ACCESS_DENIED_MESSAGE)

    with caplog.at_level("WARNING"):
        response = client.get("/mcp/token/status", headers=auth_headers)

    _assert_sanitized_502(response, caplog)


def test_get_token_status_requires_session() -> None:
    response = client.get("/mcp/token/status")

    assert response.status_code == 401
