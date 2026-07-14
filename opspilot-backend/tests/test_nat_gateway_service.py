from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

from app.services import nat_gateway_service


def _fake_paginator(pages: list[dict]) -> MagicMock:
    paginator = MagicMock()
    paginator.paginate.return_value = pages
    return paginator


@patch("app.services.nat_gateway_service.get_ec2_client")
def test_list_nat_gateways_parses_fields(mock_get_client: MagicMock) -> None:
    mock_client = MagicMock()
    mock_client.get_paginator.return_value = _fake_paginator(
        [
            {
                "NatGateways": [
                    {
                        "NatGatewayId": "nat-123",
                        "State": "available",
                        "SubnetId": "subnet-1",
                        "VpcId": "vpc-1",
                        "ConnectivityType": "public",
                        "CreateTime": datetime(2026, 6, 1, tzinfo=timezone.utc),
                        "Tags": [{"Key": "Name", "Value": "my-nat"}],
                    }
                ]
            }
        ]
    )
    mock_get_client.return_value = mock_client

    result = nat_gateway_service.list_nat_gateways()

    assert result.count == 1
    gateway = result.nat_gateways[0]
    assert gateway.nat_gateway_id == "nat-123"
    assert gateway.state == "available"
    assert gateway.tags == {"Name": "my-nat"}


@patch("app.services.nat_gateway_service.get_ec2_client")
def test_get_nat_gateway_returns_none_when_not_found(mock_get_client: MagicMock) -> None:
    mock_client = MagicMock()
    mock_client.get_paginator.return_value = _fake_paginator([{"NatGateways": []}])
    mock_get_client.return_value = mock_client

    assert nat_gateway_service.get_nat_gateway("nat-missing") is None
