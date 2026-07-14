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


@patch("app.services.cloudwatch_service.get_cloudwatch_client")
def test_get_daily_datapoints_builds_correct_request(mock_get_client: MagicMock) -> None:
    """This is the exact CloudWatch call idle_service depends on for
    CPUUtilization/NetworkIn/NetworkOut today, and every Step 3 resource
    type will reuse -- assert the request shape (Dimensions, Period,
    Statistics) directly rather than only through a mocked-out caller."""
    mock_client = MagicMock()
    mock_client.get_metric_statistics.return_value = {"Datapoints": []}
    mock_get_client.return_value = mock_client

    cloudwatch_service.get_daily_datapoints(
        namespace="AWS/EC2",
        metric_name="NetworkIn",
        dimension_name="InstanceId",
        dimension_value="i-123",
        days=10,
        statistic="Sum",
        unit="Bytes",
    )

    mock_client.get_metric_statistics.assert_called_once()
    _, kwargs = mock_client.get_metric_statistics.call_args
    assert kwargs["Namespace"] == "AWS/EC2"
    assert kwargs["MetricName"] == "NetworkIn"
    assert kwargs["Dimensions"] == [{"Name": "InstanceId", "Value": "i-123"}]
    assert kwargs["Period"] == cloudwatch_service.DAILY_PERIOD_SECONDS
    assert kwargs["Period"] == 86400
    assert kwargs["Statistics"] == ["Sum"]
    assert kwargs["Unit"] == "Bytes"


@patch("app.services.cloudwatch_service.get_cloudwatch_client")
def test_get_daily_datapoints_maps_sum_statistic_correctly(mock_get_client: MagicMock) -> None:
    """A Sum-statistic response (e.g. NetworkIn) must land in `.average`
    (the field idle_service reads regardless of which statistic was
    requested), not silently dropped because the key isn't literally
    'Average'."""
    mock_client = MagicMock()
    mock_client.get_metric_statistics.return_value = {
        "Datapoints": [
            {
                "Timestamp": datetime(2026, 7, 5, 0, 0, tzinfo=timezone.utc),
                "Sum": 123456.0,
                "Unit": "Bytes",
            },
            {
                "Timestamp": datetime(2026, 7, 4, 0, 0, tzinfo=timezone.utc),
                "Sum": 0.0,
                "Unit": "Bytes",
            },
        ]
    }
    mock_get_client.return_value = mock_client

    result = cloudwatch_service.get_daily_datapoints(
        namespace="AWS/EC2",
        metric_name="NetworkIn",
        dimension_name="InstanceId",
        dimension_value="i-123",
        days=7,
        statistic="Sum",
        unit="Bytes",
    )

    assert len(result) == 2
    # Sorted ascending by timestamp -- July 4th first, then July 5th.
    assert result[0].timestamp == datetime(2026, 7, 4, 0, 0, tzinfo=timezone.utc)
    assert result[0].average == 0.0
    assert result[1].timestamp == datetime(2026, 7, 5, 0, 0, tzinfo=timezone.utc)
    assert result[1].average == 123456.0
    assert result[1].unit == "Bytes"


@patch("app.services.cloudwatch_service.get_cloudwatch_client")
def test_get_daily_datapoints_appends_extra_dimensions_in_order(
    mock_get_client: MagicMock,
) -> None:
    """SageMaker's Invocations metric requires EndpointName + VariantName
    together, and OpenSearch's SearchRate/IndexingRate require DomainName +
    ClientId together -- `extra_dimensions` must be appended after the
    primary dimension, in the order given, as real separate Dimensions
    entries (not merged/dropped), or those CloudWatch calls silently
    return no data instead of raising."""
    mock_client = MagicMock()
    mock_client.get_metric_statistics.return_value = {"Datapoints": []}
    mock_get_client.return_value = mock_client

    cloudwatch_service.get_daily_datapoints(
        namespace="AWS/SageMaker",
        metric_name="Invocations",
        dimension_name="EndpointName",
        dimension_value="ep-1",
        days=7,
        statistic="Sum",
        unit="Count",
        extra_dimensions=[("VariantName", "AllTraffic")],
    )

    _, kwargs = mock_client.get_metric_statistics.call_args
    assert kwargs["Dimensions"] == [
        {"Name": "EndpointName", "Value": "ep-1"},
        {"Name": "VariantName", "Value": "AllTraffic"},
    ]


@patch("app.services.cloudwatch_service.get_cloudwatch_client")
def test_get_daily_datapoints_forwards_region_to_client_factory(
    mock_get_client: MagicMock,
) -> None:
    """CloudFront's Requests metric only ever publishes to us-east-1,
    regardless of the account's configured region -- `region` must reach
    get_cloudwatch_client, not just be accepted and silently dropped."""
    mock_client = MagicMock()
    mock_client.get_metric_statistics.return_value = {"Datapoints": []}
    mock_get_client.return_value = mock_client

    cloudwatch_service.get_daily_datapoints(
        namespace="AWS/CloudFront",
        metric_name="Requests",
        dimension_name="DistributionId",
        dimension_value="E123",
        days=7,
        statistic="Sum",
        unit="Count",
        region="us-east-1",
    )

    mock_get_client.assert_called_once_with(region="us-east-1")
