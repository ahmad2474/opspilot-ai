from unittest.mock import MagicMock, patch

import pytest

from app.services import compute_optimizer_service


def _ec2_recommendation(finding: str = "Overprovisioned") -> dict:
    return {
        "instanceArn": "arn:aws:ec2:us-east-1:123:instance/i-abc",
        "currentInstanceType": "m5.2xlarge",
        "finding": finding,
        "lookBackPeriodInDays": 14.0,
        "recommendationOptions": [
            {
                "instanceType": "m5.large",
                "rank": 1,
                "savingsOpportunity": {
                    "estimatedMonthlySavings": {"value": 45.0, "currency": "USD"}
                },
            },
            {
                "instanceType": "m5.xlarge",
                "rank": 2,
                "savingsOpportunity": {
                    "estimatedMonthlySavings": {"value": 20.0, "currency": "USD"}
                },
            },
        ],
    }


@patch("app.services.compute_optimizer_service.get_compute_optimizer_client")
def test_ec2_recommendations_skips_optimized_and_uses_top_ranked_option(
    mock_get_client: MagicMock,
) -> None:
    client = MagicMock()
    client.get_ec2_instance_recommendations.return_value = {
        "instanceRecommendations": [
            _ec2_recommendation(),
            _ec2_recommendation(finding="Optimized"),
        ],
    }
    mock_get_client.return_value = client

    result = compute_optimizer_service.get_rightsizing_recommendations("ec2")

    assert result.enrolled is True
    assert result.total_checked == 2
    assert len(result.findings) == 1
    finding = result.findings[0]
    assert finding.finding == "Overprovisioned"
    assert finding.recommended_configuration == "m5.large"
    assert finding.estimated_monthly_savings_usd == 45.0


@patch("app.services.compute_optimizer_service.get_compute_optimizer_client")
def test_ec2_recommendations_paginate_via_next_token(mock_get_client: MagicMock) -> None:
    client = MagicMock()
    client.get_ec2_instance_recommendations.side_effect = [
        {"instanceRecommendations": [_ec2_recommendation()], "nextToken": "page-2"},
        {"instanceRecommendations": [_ec2_recommendation()]},
    ]
    mock_get_client.return_value = client

    result = compute_optimizer_service.get_rightsizing_recommendations("ec2")

    assert result.total_checked == 2
    assert client.get_ec2_instance_recommendations.call_count == 2
    second_call_kwargs = client.get_ec2_instance_recommendations.call_args_list[1].kwargs
    assert second_call_kwargs["nextToken"] == "page-2"


@patch("app.services.compute_optimizer_service.get_compute_optimizer_client")
def test_ebs_recommendations_formats_configuration(mock_get_client: MagicMock) -> None:
    client = MagicMock()
    client.get_ebs_volume_recommendations.return_value = {
        "volumeRecommendations": [
            {
                "volumeArn": "arn:aws:ec2:us-east-1:123:volume/vol-abc",
                "finding": "NotOptimized",
                "currentConfiguration": {"volumeType": "gp2", "volumeSize": 100},
                "volumeRecommendationOptions": [
                    {
                        "configuration": {"volumeType": "gp3", "volumeSize": 100},
                        "rank": 1,
                        "savingsOpportunity": {
                            "estimatedMonthlySavings": {"value": 3.5, "currency": "USD"}
                        },
                    }
                ],
            }
        ]
    }
    mock_get_client.return_value = client

    result = compute_optimizer_service.get_rightsizing_recommendations("ebs")

    assert result.findings[0].current_configuration == "gp2 100GiB"
    assert result.findings[0].recommended_configuration == "gp3 100GiB"


@patch("app.services.compute_optimizer_service.get_compute_optimizer_client")
def test_lambda_recommendations(mock_get_client: MagicMock) -> None:
    client = MagicMock()
    client.get_lambda_function_recommendations.return_value = {
        "lambdaFunctionRecommendations": [
            {
                "functionArn": "arn:aws:lambda:us-east-1:123:function:my-fn",
                "finding": "NotOptimized",
                "currentMemorySize": 1024,
                "lookbackPeriodInDays": 14.0,
                "memorySizeRecommendationOptions": [
                    {
                        "memorySize": 512,
                        "rank": 1,
                        "savingsOpportunity": {
                            "estimatedMonthlySavings": {"value": 8.0, "currency": "USD"}
                        },
                    }
                ],
            }
        ]
    }
    mock_get_client.return_value = client

    result = compute_optimizer_service.get_rightsizing_recommendations("lambda")

    assert result.findings[0].current_configuration == "1024MB"
    assert result.findings[0].recommended_configuration == "512MB"


@patch("app.services.compute_optimizer_service.get_compute_optimizer_client")
def test_ecs_recommendations(mock_get_client: MagicMock) -> None:
    client = MagicMock()
    client.get_ecs_service_recommendations.return_value = {
        "ecsServiceRecommendations": [
            {
                "serviceArn": "arn:aws:ecs:us-east-1:123:service/my-cluster/my-service",
                "finding": "Overprovisioned",
                "currentServiceConfiguration": {"cpu": 1024, "memory": 2048},
                "lookbackPeriodInDays": 14.0,
                "serviceRecommendationOptions": [
                    {
                        "cpu": 512,
                        "memory": 1024,
                        "rank": 1,
                        "savingsOpportunity": {
                            "estimatedMonthlySavings": {"value": 15.0, "currency": "USD"}
                        },
                    }
                ],
            }
        ]
    }
    mock_get_client.return_value = client

    result = compute_optimizer_service.get_rightsizing_recommendations("ecs")

    assert result.findings[0].current_configuration == "1024 CPU units / 2048MiB"
    assert result.findings[0].recommended_configuration == "512 CPU units / 1024MiB"


@patch("app.services.compute_optimizer_service.get_compute_optimizer_client")
def test_not_enrolled_returns_graceful_result(mock_get_client: MagicMock) -> None:
    client = MagicMock()

    class _FakeExceptions:
        class OptInRequiredException(Exception):
            pass

    client.exceptions = _FakeExceptions
    client.get_ec2_instance_recommendations.side_effect = _FakeExceptions.OptInRequiredException(
        "not enrolled"
    )
    mock_get_client.return_value = client

    result = compute_optimizer_service.get_rightsizing_recommendations("ec2")

    assert result.enrolled is False
    assert result.findings == []
    assert result.total_checked == 0
    assert "Compute Optimizer" in result.note


def test_unsupported_resource_type_raises() -> None:
    with pytest.raises(compute_optimizer_service.UnsupportedRightsizingResourceTypeError):
        compute_optimizer_service.get_rightsizing_recommendations("rds")


@patch("app.services.compute_optimizer_service.get_compute_optimizer_client")
def test_all_resources_optimized_reports_note_not_findings(mock_get_client: MagicMock) -> None:
    client = MagicMock()
    client.get_ec2_instance_recommendations.return_value = {
        "instanceRecommendations": [_ec2_recommendation(finding="Optimized")],
    }
    mock_get_client.return_value = client

    result = compute_optimizer_service.get_rightsizing_recommendations("ec2")

    assert result.findings == []
    assert result.total_checked == 1
    assert "Optimized" in result.note
