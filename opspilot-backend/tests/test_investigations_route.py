from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

from app.main import app
from app.models.investigation import Investigation

client = TestClient(app)


@patch("app.api.routes.investigations.investigation_service.list_recent_investigations")
def test_list_investigations_returns_recent_first(mock_list: MagicMock) -> None:
    mock_list.return_value = [
        Investigation(
            id="inv-1",
            question="Is anything wrong?",
            trace_summary="Checked CPU.",
            conclusion="Nothing wrong.",
            created_at="2026-07-08T00:00:00Z",
        )
    ]

    response = client.get("/investigations")

    assert response.status_code == 200
    body = response.json()
    assert body["investigations"][0]["id"] == "inv-1"
