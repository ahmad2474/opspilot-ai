"""Tests for resource_query_service (roadmap Section 3.8's chat tools'
business logic: list_resources, get_resource_health, get_resource_age).

Mocked at the same service-function boundary test_scan_service.py already
uses for scan_service's own composition of other services/ modules --
resource_query_service composes scan_service/idle_service/ec2_service, it
makes no boto3 calls of its own, so there's nothing lower to mock here.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

from app.models.cost import CostEstimate
from app.models.ec2 import EC2StatusCheck
from app.models.idle import IdleCheckResult
from app.models.scan import GalaxyResource, ResourceHealth, ScanResponse, ScanTotals
from app.services import resource_query_service

NOW = datetime(2026, 7, 11, 12, 0, 0, tzinfo=timezone.utc)


def _resource(
    id: str,
    type: str,
    name: str | None = None,
    region: str = "us-east-1",
    status: str = "running",
    idle: IdleCheckResult | None = None,
    cost: CostEstimate | None = None,
    created_at: datetime | None = None,
) -> GalaxyResource:
    return GalaxyResource(
        id=id,
        name=name or id,
        type=type,
        region=region,
        cost=cost,
        idle=idle,
        health=ResourceHealth(
            primary_metric="cpu_percent", primary_metric_value=None, status=status
        ),
        created_at=created_at,
        relations=[],
    )


def _idle_result(resource_id: str, resource_type: str, is_idle: bool) -> IdleCheckResult:
    return IdleCheckResult(
        resource_id=resource_id,
        resource_type=resource_type,
        window_days=7,
        is_idle=is_idle,
        idle_days=7 if is_idle else 0,
    )


def _scan_response(region: str, resources: list) -> ScanResponse:
    return ScanResponse(
        region=region,
        last_updated=NOW,
        resources=resources,
        totals=ScanTotals(monthly_spend=0.0, idle_count=0, idle_monthly_waste=0.0),
        error=None,
    )


# =====================================================================
# list_resources
# =====================================================================


@patch("app.services.resource_query_service.scan_service.get_cached_scan")
def test_list_resources_defaults_to_configured_region(mock_get_cached: MagicMock) -> None:
    mock_get_cached.return_value = None
    with patch(
        "app.services.resource_query_service.scan_service.list_lite_resources", return_value=[]
    ) as mock_lite:
        result = resource_query_service.list_resources()

    assert result.region == "us-east-1"
    mock_get_cached.assert_called_once_with("us-east-1")
    mock_lite.assert_called_once_with("us-east-1", None)


@patch("app.services.resource_query_service.scan_service.get_cached_scan")
def test_list_resources_region_filter_is_normalized(mock_get_cached: MagicMock) -> None:
    mock_get_cached.return_value = None
    with patch(
        "app.services.resource_query_service.scan_service.list_lite_resources", return_value=[]
    ) as mock_lite:
        result = resource_query_service.list_resources({"region": " US-WEST-2 "})

    assert result.region == "us-west-2"
    mock_lite.assert_called_once_with("us-west-2", None)


def test_list_resources_unsupported_type_raises() -> None:
    with pytest.raises(resource_query_service.UnsupportedResourceTypeError):
        resource_query_service.list_resources({"type": "s3"})


@patch("app.services.resource_query_service.scan_service.list_lite_resources")
@patch("app.services.resource_query_service.scan_service.get_cached_scan")
def test_list_resources_type_filter_accepts_single_str_or_list(
    mock_get_cached: MagicMock, mock_lite: MagicMock
) -> None:
    mock_get_cached.return_value = None
    mock_lite.return_value = []

    resource_query_service.list_resources({"type": "ec2"})
    mock_lite.assert_called_with("us-east-1", ["ec2"])

    resource_query_service.list_resources({"types": ["ec2", "ebs"]})
    mock_lite.assert_called_with("us-east-1", ["ec2", "ebs"])


@patch("app.services.resource_query_service.scan_service.get_cached_scan")
def test_list_resources_status_filter_is_case_insensitive(mock_get_cached: MagicMock) -> None:
    resources = [
        _resource("i-1", "ec2", status="running"),
        _resource("i-2", "ec2", status="stopped"),
    ]
    mock_get_cached.return_value = _scan_response("us-east-1", resources)

    result = resource_query_service.list_resources({"status": "RUNNING"})

    assert result.count == 1
    assert result.resources[0].id == "i-1"


@patch("app.services.resource_query_service.scan_service.get_cached_scan")
def test_list_resources_sorted_by_type_then_name(mock_get_cached: MagicMock) -> None:
    resources = [
        _resource("i-2", "ec2", name="zebra"),
        _resource("vol-1", "ebs", name="alpha-vol"),
        _resource("i-1", "ec2", name="apple"),
    ]
    mock_get_cached.return_value = _scan_response("us-east-1", resources)

    result = resource_query_service.list_resources()

    assert [(r.type, r.name) for r in result.resources] == [
        ("ebs", "alpha-vol"),
        ("ec2", "apple"),
        ("ec2", "zebra"),
    ]


@patch("app.services.resource_query_service.scan_service.get_cached_scan")
def test_list_resources_empty_result_set(mock_get_cached: MagicMock) -> None:
    mock_get_cached.return_value = _scan_response("us-east-1", [])

    result = resource_query_service.list_resources()

    assert result.count == 0
    assert result.resources == []
    assert result.by_type == {}
    assert result.by_status == {}
    assert result.idle_count == 0
    assert result.not_idle_count == 0
    assert result.idle_data_source == "cached_scan"


@patch("app.services.resource_query_service.scan_service.get_cached_scan")
def test_list_resources_cached_scan_populates_idle_counts(mock_get_cached: MagicMock) -> None:
    resources = [
        _resource("i-1", "ec2", idle=_idle_result("i-1", "ec2", True)),
        _resource("i-2", "ec2", idle=_idle_result("i-2", "ec2", False)),
    ]
    mock_get_cached.return_value = _scan_response("us-east-1", resources)

    result = resource_query_service.list_resources()

    assert result.idle_data_source == "cached_scan"
    assert result.idle_count == 1
    assert result.not_idle_count == 1
    assert result.cache_last_updated == NOW


@patch("app.services.resource_query_service.scan_service.list_lite_resources")
@patch("app.services.resource_query_service.scan_service.get_cached_scan")
def test_list_resources_no_cache_falls_back_to_lite_listing_with_unavailable_idle(
    mock_get_cached: MagicMock, mock_lite: MagicMock
) -> None:
    mock_get_cached.return_value = None
    mock_lite.return_value = [_resource("i-1", "ec2")]

    result = resource_query_service.list_resources()

    assert result.idle_data_source == "unavailable"
    assert result.idle_count is None
    assert result.not_idle_count is None
    assert result.cache_last_updated is None
    assert result.count == 1


@patch("app.services.resource_query_service.scan_service.get_cached_scan")
def test_list_resources_partial_known_idle_mix_returns_null_not_undercounted(
    mock_get_cached: MagicMock,
) -> None:
    resources = [
        _resource("i-1", "ec2", idle=_idle_result("i-1", "ec2", True)),
        _resource("i-2", "ec2", idle=None),
    ]
    mock_get_cached.return_value = _scan_response("us-east-1", resources)

    result = resource_query_service.list_resources()

    assert result.idle_count is None
    assert result.not_idle_count is None
    assert result.count == 2


# =====================================================================
# get_resource_health
# =====================================================================


@patch("app.services.resource_query_service.scan_service.get_lite_resource")
def test_get_resource_health_not_found(mock_get_lite: MagicMock) -> None:
    mock_get_lite.return_value = None

    result = resource_query_service.get_resource_health("ec2", "i-missing")

    assert result.found is False
    assert result.name is None
    assert result.status is None


@patch("app.services.resource_query_service.idle_service.check_idle")
@patch("app.services.resource_query_service.ec2_service.get_status_check")
@patch("app.services.resource_query_service.scan_service.get_lite_resource")
def test_get_resource_health_ec2_status_check_success(
    mock_get_lite: MagicMock, mock_status_check: MagicMock, mock_check_idle: MagicMock
) -> None:
    mock_get_lite.return_value = _resource("i-1", "ec2", status="running")
    mock_status_check.return_value = EC2StatusCheck(
        instance_id="i-1", instance_state="running", system_status="ok", instance_status="ok"
    )
    mock_check_idle.return_value = _idle_result("i-1", "ec2", False)

    result = resource_query_service.get_resource_health("ec2", "i-1")

    assert result.found is True
    assert result.ec2_status_check is not None
    assert result.ec2_status_check.system_status == "ok"
    assert result.recent_activity_idle is False


@patch("app.services.resource_query_service.idle_service.check_idle")
@patch("app.services.resource_query_service.ec2_service.get_status_check")
@patch("app.services.resource_query_service.scan_service.get_lite_resource")
def test_get_resource_health_ec2_status_check_failure_is_non_fatal(
    mock_get_lite: MagicMock, mock_status_check: MagicMock, mock_check_idle: MagicMock
) -> None:
    mock_get_lite.return_value = _resource("i-1", "ec2", status="running")
    mock_status_check.side_effect = RuntimeError("boom")
    mock_check_idle.return_value = _idle_result("i-1", "ec2", False)

    result = resource_query_service.get_resource_health("ec2", "i-1")

    assert result.found is True
    assert result.ec2_status_check is None
    assert result.recent_activity_idle is False


@patch("app.services.resource_query_service.idle_service.check_idle")
@patch("app.services.resource_query_service.ec2_service.get_status_check")
@patch("app.services.resource_query_service.scan_service.get_lite_resource")
def test_get_resource_health_recent_activity_failure_is_non_fatal(
    mock_get_lite: MagicMock, mock_status_check: MagicMock, mock_check_idle: MagicMock
) -> None:
    mock_get_lite.return_value = _resource("i-1", "ec2", status="running")
    mock_status_check.return_value = EC2StatusCheck(
        instance_id="i-1", instance_state="running", system_status="ok", instance_status="ok"
    )
    mock_check_idle.side_effect = RuntimeError("cloudwatch throttled")

    result = resource_query_service.get_resource_health("ec2", "i-1")

    assert result.found is True
    assert result.recent_activity_idle is None
    assert result.ec2_status_check is not None


@patch("app.services.resource_query_service.idle_service.check_idle")
@patch("app.services.resource_query_service.ec2_service.get_status_check")
@patch("app.services.resource_query_service.scan_service.get_lite_resource")
def test_get_resource_health_non_ec2_types_get_no_status_check(
    mock_get_lite: MagicMock, mock_status_check: MagicMock, mock_check_idle: MagicMock
) -> None:
    mock_get_lite.return_value = _resource("db-1", "rds", status="available")
    mock_check_idle.return_value = _idle_result("db-1", "rds", False)

    result = resource_query_service.get_resource_health("rds", "db-1")

    assert result.ec2_status_check is None
    mock_status_check.assert_not_called()


# =====================================================================
# get_resource_age
# =====================================================================


@patch("app.services.resource_query_service.scan_service.get_lite_resource")
def test_get_resource_age_not_found(mock_get_lite: MagicMock) -> None:
    mock_get_lite.return_value = None

    result = resource_query_service.get_resource_age("ec2", "i-missing")

    assert result.found is False
    assert result.age_is_known is False
    assert result.reason == "Resource not found."


@pytest.mark.parametrize("resource_type", sorted(resource_query_service._NO_TIMESTAMP_TYPES))
@patch("app.services.resource_query_service.scan_service.get_lite_resource")
def test_get_resource_age_no_timestamp_types_report_unknown_with_reason(
    mock_get_lite: MagicMock, resource_type: str
) -> None:
    mock_get_lite.return_value = _resource("res-1", resource_type, created_at=None)

    result = resource_query_service.get_resource_age(resource_type, "res-1")

    assert result.found is True
    assert result.age_is_known is False
    assert result.age_days is None
    assert resource_type in result.reason


@patch("app.services.resource_query_service.scan_service.get_lite_resource")
def test_get_resource_age_opensearch_per_resource_null_case(mock_get_lite: MagicMock) -> None:
    mock_get_lite.return_value = _resource("domain-1", "opensearch", created_at=None)

    result = resource_query_service.get_resource_age("opensearch", "domain-1")

    assert result.age_is_known is False
    assert result.age_days is None
    assert "opensearch" not in result.reason
    assert "still being created" in result.reason


@patch("app.services.resource_query_service.datetime")
@patch("app.services.resource_query_service.scan_service.get_lite_resource")
def test_get_resource_age_normal_computation_path(
    mock_get_lite: MagicMock, mock_datetime: MagicMock
) -> None:
    mock_datetime.now.return_value = NOW
    created_at = NOW - timedelta(days=42)
    mock_get_lite.return_value = _resource("i-1", "ec2", created_at=created_at)

    result = resource_query_service.get_resource_age("ec2", "i-1")

    assert result.found is True
    assert result.age_is_known is True
    assert result.created_at == created_at
    assert result.age_days == 42
    assert result.reason is None
