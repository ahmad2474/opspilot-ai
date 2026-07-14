from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

from app.services import api_gateway_service


def _fake_paginator(pages: list[dict]) -> MagicMock:
    paginator = MagicMock()
    paginator.paginate.return_value = pages
    return paginator


@patch("app.services.api_gateway_service.get_apigateway_client")
def test_list_apis_parses_fields(mock_get_client: MagicMock) -> None:
    mock_client = MagicMock()
    mock_client.get_paginator.return_value = _fake_paginator(
        [
            {
                "items": [
                    {
                        "id": "abc123",
                        "name": "my-api",
                        "createdDate": datetime(2026, 6, 1, tzinfo=timezone.utc),
                    }
                ]
            }
        ]
    )
    mock_get_client.return_value = mock_client

    result = api_gateway_service.list_apis()

    assert result.count == 1
    assert result.apis[0].api_id == "abc123"
    assert result.apis[0].name == "my-api"


@patch("app.services.api_gateway_service.get_apigateway_client")
def test_get_api_matches_by_id_or_name(mock_get_client: MagicMock) -> None:
    mock_client = MagicMock()
    mock_client.get_paginator.return_value = _fake_paginator(
        [{"items": [{"id": "abc123", "name": "my-api"}]}]
    )
    mock_get_client.return_value = mock_client

    assert api_gateway_service.get_api("abc123") is not None
    assert api_gateway_service.get_api("my-api") is not None
    assert api_gateway_service.get_api("missing") is None
