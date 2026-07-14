from unittest.mock import MagicMock, patch

from botocore.exceptions import ClientError
from fastapi.testclient import TestClient

from app.main import app
from app.models.audit_log import AuditLogEntry

client = TestClient(app)


def _client_error(code: str, message: str) -> ClientError:
    return ClientError(
        {"Error": {"Code": code, "Message": message}},
        "GetItem",
    )


@patch("app.api.routes.audit_log.audit_log_service.list_recent_entries")
def test_get_audit_log_happy_path(
    mock_list: MagicMock, auth_headers: dict[str, str]
) -> None:
    mock_list.return_value = [
        AuditLogEntry(
            id="entry-1",
            action="mcp_token_generated",
            actor_email="admin@example.com",
            created_at="2026-07-11T00:00:00+00:00",
            detail=None,
        )
    ]

    response = client.get("/audit-log", headers=auth_headers)

    assert response.status_code == 200
    body = response.json()
    assert len(body["entries"]) == 1
    assert body["entries"][0]["id"] == "entry-1"
    mock_list.assert_called_once_with(limit=50)


@patch("app.api.routes.audit_log.audit_log_service.list_recent_entries")
def test_get_audit_log_client_error_returns_sanitized_502(
    mock_list: MagicMock, auth_headers: dict[str, str], caplog
) -> None:
    """A raw AccessDeniedException (embedding the IAM caller ARN + account
    ID) must never reach the HTTP response body -- see
    app/core/aws_errors.py's module docstring."""
    raw_message = (
        "An error occurred (AccessDeniedException) when calling the Scan "
        "operation: User: arn:aws:iam::476141958109:user/opspilot-app is "
        "not authorized to perform: dynamodb:Scan on resource: "
        "arn:aws:dynamodb:us-east-1:476141958109:table/opspilot-audit-log"
    )
    mock_list.side_effect = _client_error("AccessDeniedException", raw_message)

    with caplog.at_level("WARNING"):
        response = client.get("/audit-log", headers=auth_headers)

    assert response.status_code == 502
    body = response.json()
    detail = body["detail"]
    assert "476141958109" not in detail
    assert "arn:aws:iam" not in detail
    assert "AccessDeniedException" not in detail
    assert "opspilot-app" not in detail

    # The real exception was logged server-side (never returned to the caller).
    assert any(
        "476141958109" in record.getMessage() or record.exc_info is not None
        for record in caplog.records
    )


def test_get_audit_log_requires_session() -> None:
    response = client.get("/audit-log")

    assert response.status_code == 401
