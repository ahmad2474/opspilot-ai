from unittest.mock import MagicMock, patch

from botocore.exceptions import ClientError
from fastapi.testclient import TestClient

from app.main import app
from app.models.investigation import Investigation

client = TestClient(app)


def _client_error(code: str, message: str) -> ClientError:
    return ClientError(
        {"Error": {"Code": code, "Message": message}},
        "Scan",
    )


@patch("app.api.routes.investigations.investigation_service.list_recent_investigations")
def test_list_investigations_happy_path(
    mock_list: MagicMock, auth_headers: dict[str, str]
) -> None:
    mock_list.return_value = [
        Investigation(
            id="inv-1",
            question="why is my ec2 idle?",
            trace_summary="checked cpu",
            conclusion="idle for 10 days",
            created_at="2026-07-11T00:00:00+00:00",
        )
    ]

    response = client.get("/investigations", headers=auth_headers)

    assert response.status_code == 200
    body = response.json()
    assert len(body["investigations"]) == 1
    assert body["investigations"][0]["id"] == "inv-1"
    mock_list.assert_called_once_with()


@patch("app.api.routes.investigations.investigation_service.list_recent_investigations")
def test_list_investigations_client_error_returns_sanitized_502(
    mock_list: MagicMock, auth_headers: dict[str, str], caplog
) -> None:
    """A raw AccessDeniedException (embedding the IAM caller ARN + account
    ID) must never reach the HTTP response body -- see
    app/core/aws_errors.py's module docstring."""
    raw_message = (
        "An error occurred (AccessDeniedException) when calling the Scan "
        "operation: User: arn:aws:iam::123456789012:user/opspilot-app is "
        "not authorized to perform: dynamodb:Scan on resource: "
        "arn:aws:dynamodb:us-east-1:123456789012:table/opspilot-investigations"
    )
    mock_list.side_effect = _client_error("AccessDeniedException", raw_message)

    with caplog.at_level("WARNING"):
        response = client.get("/investigations", headers=auth_headers)

    assert response.status_code == 502
    body = response.json()
    detail = body["detail"]
    assert "123456789012" not in detail
    assert "arn:aws:iam" not in detail
    assert "AccessDeniedException" not in detail
    assert "opspilot-app" not in detail

    assert any(
        "123456789012" in record.getMessage() or record.exc_info is not None
        for record in caplog.records
    )


def test_list_investigations_requires_session() -> None:
    response = client.get("/investigations")

    assert response.status_code == 401
