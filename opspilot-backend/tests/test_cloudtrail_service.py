from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

from app.services import cloudtrail_service


@patch("app.services.cloudtrail_service.get_cloudtrail_client")
def test_list_events_for_resource(mock_get_client: MagicMock) -> None:
    mock_client = MagicMock()
    mock_client.lookup_events.return_value = {
        "Events": [
            {
                "EventName": "StartInstances",
                "EventTime": datetime(2026, 7, 5, tzinfo=timezone.utc),
                "Username": "Admin-OpsAI",
            },
        ]
    }
    mock_get_client.return_value = mock_client

    result = cloudtrail_service.list_events_for_resource("i-123", lookback_hours=24)

    assert result.resource_id == "i-123"
    assert len(result.events) == 1
    assert result.events[0].event_name == "StartInstances"


@patch("app.services.cloudtrail_service.get_cloudtrail_client")
def test_list_recent_management_events(mock_get_client: MagicMock) -> None:
    mock_client = MagicMock()
    mock_client.lookup_events.return_value = {
        "Events": [
            {
                "EventName": "ListClusters",
                "EventTime": datetime(2026, 7, 5, tzinfo=timezone.utc),
                "Username": None,
            },
        ]
    }
    mock_get_client.return_value = mock_client

    result = cloudtrail_service.list_recent_management_events(max_results=5)

    assert len(result.events) == 1
    assert result.events[0].event_name == "ListClusters"
    assert result.events[0].username is None
