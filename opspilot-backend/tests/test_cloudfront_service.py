from unittest.mock import MagicMock, patch

from app.services import cloudfront_service


def _fake_paginator(pages: list[dict]) -> MagicMock:
    paginator = MagicMock()
    paginator.paginate.return_value = pages
    return paginator


@patch("app.services.cloudfront_service.get_cloudfront_client")
def test_list_distributions_parses_fields(mock_get_client: MagicMock) -> None:
    mock_client = MagicMock()
    mock_client.get_paginator.return_value = _fake_paginator(
        [
            {
                "DistributionList": {
                    "Items": [
                        {
                            "Id": "E123",
                            "ARN": "arn:aws:cloudfront::123:distribution/E123",
                            "Status": "Deployed",
                            "DomainName": "d123.cloudfront.net",
                            "Enabled": True,
                        }
                    ]
                }
            }
        ]
    )
    mock_get_client.return_value = mock_client

    result = cloudfront_service.list_distributions()

    assert result.count == 1
    assert result.distributions[0].distribution_id == "E123"
    assert result.distributions[0].enabled is True


@patch("app.services.cloudfront_service.get_cloudfront_client")
def test_get_distribution_returns_none_when_not_found(mock_get_client: MagicMock) -> None:
    mock_client = MagicMock()
    mock_client.get_paginator.return_value = _fake_paginator(
        [{"DistributionList": {"Items": []}}]
    )
    mock_get_client.return_value = mock_client

    assert cloudfront_service.get_distribution("missing") is None
