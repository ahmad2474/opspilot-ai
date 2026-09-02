from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from app.models.cost import DateRange
from app.services import vpc_endpoint_service


def _fake_paginator(pages: list[dict]) -> MagicMock:
    paginator = MagicMock()
    paginator.paginate.return_value = pages
    return paginator


@patch("app.services.vpc_endpoint_service.get_ec2_client")
def test_list_vpc_endpoints_parses_fields(mock_get_client: MagicMock) -> None:
    mock_client = MagicMock()
    mock_client.get_paginator.return_value = _fake_paginator(
        [
            {
                "VpcEndpoints": [
                    {
                        "VpcEndpointId": "vpce-123",
                        "VpcEndpointType": "Interface",
                        "ServiceName": "com.amazonaws.us-east-1.ec2",
                        "State": "available",
                        "VpcId": "vpc-1",
                        "SubnetIds": ["subnet-1"],
                        "CreationTimestamp": datetime(2026, 6, 1, tzinfo=timezone.utc),
                        "Tags": [{"Key": "Name", "Value": "my-endpoint"}],
                    }
                ]
            }
        ]
    )
    mock_get_client.return_value = mock_client

    result = vpc_endpoint_service.list_vpc_endpoints()

    assert result.count == 1
    endpoint = result.vpc_endpoints[0]
    assert endpoint.vpc_endpoint_id == "vpce-123"
    assert endpoint.vpc_endpoint_type == "Interface"
    assert endpoint.tags == {"Name": "my-endpoint"}


@patch("app.services.vpc_endpoint_service.get_ec2_client")
def test_get_vpc_endpoint_returns_none_when_not_found(mock_get_client: MagicMock) -> None:
    mock_client = MagicMock()
    mock_client.get_paginator.return_value = _fake_paginator([{"VpcEndpoints": []}])
    mock_get_client.return_value = mock_client

    assert vpc_endpoint_service.get_vpc_endpoint("vpce-missing") is None


@patch("app.services.vpc_endpoint_service.idle_service.check_idle_via_metrics")
@patch("app.services.vpc_endpoint_service.get_vpc_endpoint")
def test_check_vpc_endpoint_idle_delegates_with_bytes_processed_metric(
    mock_get_endpoint: MagicMock, mock_check_via_metrics: MagicMock
) -> None:
    from app.models.idle import IdleCheckResult
    from app.models.vpc_endpoint import VpcEndpoint

    create_time = datetime(2026, 1, 1, tzinfo=timezone.utc)
    mock_get_endpoint.return_value = VpcEndpoint(
        vpc_endpoint_id="vpce-123",
        vpc_endpoint_type="Interface",
        service_name="com.amazonaws.us-east-1.ec2",
        state="available",
        create_time=create_time,
    )
    mock_check_via_metrics.return_value = IdleCheckResult(
        resource_id="vpce-123", resource_type="vpc_endpoint", window_days=7, is_idle=True
    )

    result = vpc_endpoint_service.check_vpc_endpoint_idle("vpce-123", 7)

    assert result.is_idle is True
    args, kwargs = mock_check_via_metrics.call_args
    assert args[0] == "vpce-123"
    assert args[1] == "vpc_endpoint"
    assert args[2] == 7
    assert args[3] == create_time
    metric_specs = args[4]
    assert metric_specs[0][0] == "AWS/PrivateLinkEndpoints"
    assert metric_specs[0][1] == "BytesProcessed"
    assert metric_specs[0][2] == "VPC Endpoint Id"
    assert kwargs["zero_fill_missing_days"] is True


@patch("app.services.vpc_endpoint_service.get_vpc_endpoint")
def test_check_vpc_endpoint_idle_not_found_delegates_to_not_idle_result(
    mock_get_endpoint: MagicMock,
) -> None:
    mock_get_endpoint.return_value = None

    result = vpc_endpoint_service.check_vpc_endpoint_idle("vpce-missing", 7)

    assert result.is_idle is False
    assert result.resource_type == "vpc_endpoint"


@patch("app.services.vpc_endpoint_service.get_vpc_endpoint")
def test_check_vpc_endpoint_idle_raises_for_gateway_endpoint(
    mock_get_endpoint: MagicMock,
) -> None:
    from app.models.vpc_endpoint import VpcEndpoint

    mock_get_endpoint.return_value = VpcEndpoint(
        vpc_endpoint_id="vpce-gw",
        vpc_endpoint_type="Gateway",
        service_name="com.amazonaws.us-east-1.s3",
        state="available",
    )

    with pytest.raises(vpc_endpoint_service.NotInterfaceEndpointError):
        vpc_endpoint_service.check_vpc_endpoint_idle("vpce-gw", 7)


@patch("app.services.vpc_endpoint_service._get_vpc_endpoint_hourly_rate")
@patch("app.services.vpc_endpoint_service.get_vpc_endpoint")
def test_estimate_vpc_endpoint_cost_computes_projected_and_incurred(
    mock_get_endpoint: MagicMock, mock_hourly_rate: MagicMock
) -> None:
    from app.models.vpc_endpoint import VpcEndpoint

    create_time = datetime(2026, 1, 1, tzinfo=timezone.utc)
    mock_get_endpoint.return_value = VpcEndpoint(
        vpc_endpoint_id="vpce-123",
        vpc_endpoint_type="Interface",
        service_name="com.amazonaws.us-east-1.ec2",
        state="available",
        create_time=create_time,
    )
    mock_hourly_rate.return_value = 0.01

    date_range = DateRange(
        start=create_time, end=datetime(2026, 1, 2, tzinfo=timezone.utc)
    )
    result = vpc_endpoint_service.estimate_vpc_endpoint_cost("vpce-123", date_range=date_range)

    assert result.resource_type == "vpc_endpoint"
    assert result.hourly_rate == 0.01
    assert result.projected_monthly == round(0.01 * 730.0, 2)
    assert result.incurred_so_far == round(0.01 * 24, 2)


@patch("app.services.vpc_endpoint_service.get_vpc_endpoint")
def test_estimate_vpc_endpoint_cost_raises_for_gateway_endpoint(
    mock_get_endpoint: MagicMock,
) -> None:
    from app.models.vpc_endpoint import VpcEndpoint

    mock_get_endpoint.return_value = VpcEndpoint(
        vpc_endpoint_id="vpce-gw",
        vpc_endpoint_type="Gateway",
        service_name="com.amazonaws.us-east-1.s3",
        state="available",
    )

    with pytest.raises(vpc_endpoint_service.NotInterfaceEndpointError):
        vpc_endpoint_service.estimate_vpc_endpoint_cost("vpce-gw")


@patch("app.services.vpc_endpoint_service.get_vpc_endpoint")
def test_estimate_vpc_endpoint_cost_raises_when_not_found(
    mock_get_endpoint: MagicMock,
) -> None:
    mock_get_endpoint.return_value = None

    with pytest.raises(ValueError):
        vpc_endpoint_service.estimate_vpc_endpoint_cost("vpce-missing")
