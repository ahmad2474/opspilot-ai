from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

from app.services import cloudwatch_service


@patch("app.services.cloudwatch_service.get_cloudwatch_client")
def test_cpu_below_threshold_not_breached(mock_get_client: MagicMock) -> None:
    mock_client = MagicMock()
    mock_client.get_metric_statistics.return_value = {
        "Datapoints": [
            {
                "Timestamp": datetime(2026, 7, 5, 16, 0, tzinfo=timezone.utc),
                "Average": 0.18,
                "Maximum": 0.24,
                "Unit": "Percent",
            },
            {
                "Timestamp": datetime(2026, 7, 5, 16, 5, tzinfo=timezone.utc),
                "Average": 0.20,
                "Maximum": 0.26,
                "Unit": "Percent",
            },
        ]
    }
    mock_get_client.return_value = mock_client

    result = cloudwatch_service.get_cpu_utilization("i-123", lookback_hours=3)

    assert result.breached_80_percent is False
    assert result.max_cpu_percent == 0.26
    assert round(result.average_cpu_percent, 2) == 0.19


@patch("app.services.cloudwatch_service.get_cloudwatch_client")
def test_cpu_above_threshold_is_breached(mock_get_client: MagicMock) -> None:
    mock_client = MagicMock()
    mock_client.get_metric_statistics.return_value = {
        "Datapoints": [
            {
                "Timestamp": datetime(2026, 7, 5, 16, 0, tzinfo=timezone.utc),
                "Average": 45.0,
                "Maximum": 92.5,
                "Unit": "Percent",
            },
        ]
    }
    mock_get_client.return_value = mock_client

    result = cloudwatch_service.get_cpu_utilization("i-123")

    assert result.breached_80_percent is True
    assert result.max_cpu_percent == 92.5


@patch("app.services.cloudwatch_service.get_cloudwatch_client")
def test_cpu_no_datapoints_returns_none_not_error(mock_get_client: MagicMock) -> None:
    """A freshly started instance has no CloudWatch data yet — this must
    not crash, and must not silently report 0% (that would be a false
    'healthy' signal)."""
    mock_client = MagicMock()
    mock_client.get_metric_statistics.return_value = {"Datapoints": []}
    mock_get_client.return_value = mock_client

    result = cloudwatch_service.get_cpu_utilization("i-123")

    assert result.average_cpu_percent is None
    assert result.max_cpu_percent is None
    assert result.breached_80_percent is False
