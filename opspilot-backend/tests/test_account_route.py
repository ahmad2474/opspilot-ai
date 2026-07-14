"""Tests for GET /aws/account (roadmap Section 5's Settings tab
"Connected account" section -- see app/api/routes/aws_account.py's module
docstring). account_service.get_connected_account is mocked at the
service layer here, mirroring test_mcp_auth_route.py's convention; its own
STS-calling behavior is exercised directly in test_account_service.py.
"""
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

from app.main import app
from app.models.account import AccountIdentity

client = TestClient(app)


@patch("app.api.routes.aws_account.account_service.get_connected_account")
def test_get_aws_account_returns_account_id_and_region_only(
    mock_get: MagicMock, auth_headers: dict[str, str]
) -> None:
    mock_get.return_value = AccountIdentity(account_id="123456789012", region="us-east-1")

    response = client.get("/aws/account", headers=auth_headers)

    assert response.status_code == 200
    body = response.json()
    assert body == {"account_id": "123456789012", "region": "us-east-1"}
    assert set(body.keys()) == {"account_id", "region"}


def test_get_aws_account_requires_session() -> None:
    response = client.get("/aws/account")

    assert response.status_code == 401
