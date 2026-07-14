from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

from app.services import sagemaker_service


def _fake_paginator(pages: list[dict]) -> MagicMock:
    paginator = MagicMock()
    paginator.paginate.return_value = pages
    return paginator


@patch("app.services.sagemaker_service.get_sagemaker_client")
def test_list_endpoints_fills_in_instance_type_from_config(mock_get_client: MagicMock) -> None:
    mock_client = MagicMock()
    mock_client.get_paginator.return_value = _fake_paginator(
        [
            {
                "Endpoints": [
                    {
                        "EndpointName": "ep-1",
                        "EndpointStatus": "InService",
                        "CreationTime": datetime(2026, 6, 1, tzinfo=timezone.utc),
                    }
                ]
            }
        ]
    )
    mock_client.describe_endpoint.return_value = {"EndpointConfigName": "ep-1-config"}
    mock_client.describe_endpoint_config.return_value = {
        "ProductionVariants": [
            {"VariantName": "AllTraffic", "InstanceType": "ml.m5.large", "InitialInstanceCount": 2}
        ]
    }
    mock_get_client.return_value = mock_client

    result = sagemaker_service.list_endpoints()

    assert result.count == 1
    endpoint = result.endpoints[0]
    assert endpoint.endpoint_name == "ep-1"
    assert endpoint.variant_name == "AllTraffic"
    assert endpoint.instance_type == "ml.m5.large"
    assert endpoint.instance_count == 2


@patch("app.services.sagemaker_service.get_sagemaker_client")
def test_get_endpoint_returns_none_when_not_found(mock_get_client: MagicMock) -> None:
    mock_client = MagicMock()
    mock_client.get_paginator.return_value = _fake_paginator([{"Endpoints": []}])
    mock_get_client.return_value = mock_client

    assert sagemaker_service.get_endpoint("ep-missing") is None
