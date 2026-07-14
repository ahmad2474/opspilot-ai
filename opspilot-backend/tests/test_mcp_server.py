"""Tests for the MCP server — verifies tool registration and that each
tool delegates to the correct service function and surfaces its result,
exercised against the real installed mcp package (not mocked)."""
from __future__ import annotations

import asyncio
import json
from unittest.mock import patch

import pytest

from app.mcp import server
from app.models.cloudtrail import CloudTrailEvent, CloudTrailEventList
from app.models.cost import CostEstimate, DateRange
from app.models.dashboard import (
    CloudTrailCard,
    DynamoCard,
    DynamoTableSummary,
    LambdaCard,
    LambdaFunctionSummary,
    RdsCard,
    RdsInstanceSummary,
    S3BucketSummary,
    S3Card,
    SnsCard,
    SnsTopicSummary,
)
from app.models.ec2 import EC2Instance, EC2InstanceList, EC2StatusCheck
from app.models.idle import IdleCheckResult
from app.models.investigation import SimilarInvestigation


@pytest.fixture(autouse=True)
def _valid_mcp_token(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every tool-dispatch test below is about the wrapped tool's own
    behavior, not the roadmap 3.6 auth gate itself (see
    test_call_tool_* below for that) — so make every call here look like
    it arrived with a valid, non-revoked token, matching how these tests
    behaved before the auth gate existed."""
    monkeypatch.setattr(server.mcp_auth_service, "is_token_valid", lambda token: True)


def _call_tool(name: str, arguments: dict) -> dict | None:
    """Invoke a registered MCP tool and return its structured result."""
    _unstructured, structured = asyncio.run(server.mcp.call_tool(name, arguments))
    return json.loads(structured["result"])


def test_all_tools_are_registered() -> None:
    tools = asyncio.run(server.mcp.list_tools())
    names = {t.name for t in tools}
    assert names == {
        "list_ec2_instances",
        "get_ec2_status_check",
        "get_ec2_instance",
        "get_cpu_utilization",
        "list_cloudtrail_events_for_resource",
        "list_recent_management_events",
        "list_s3_buckets",
        "list_lambda_functions",
        "list_dynamodb_tables",
        "list_sns_topics",
        "list_rds_instances",
        "check_idle",
        "estimate_cost",
        "list_regions",
        "scan_region",
        "list_resources",
        "get_resource_health",
        "get_resource_age",
        "estimate_instance_cost",
        "find_similar_past_investigations",
    }


@patch("app.mcp.server.ec2_service.list_instances")
def test_list_ec2_instances(mock_list: object) -> None:
    mock_list.return_value = EC2InstanceList(
        instances=[
            EC2Instance(
                instance_id="i-123",
                instance_type="t3.micro",
                state="running",
                availability_zone="us-east-1d",
                public_ip=None,
                private_ip="10.0.0.1",
                launch_time=None,
                tags={},
            )
        ],
        count=1,
    )

    result = _call_tool("list_ec2_instances", {"state_filter": "running"})

    mock_list.assert_called_once_with(state_filter="running")
    assert result["count"] == 1
    assert result["instances"][0]["instance_id"] == "i-123"


@patch("app.mcp.server.ec2_service.get_status_check")
def test_get_ec2_status_check(mock_status: object) -> None:
    mock_status.return_value = EC2StatusCheck(
        instance_id="i-123",
        instance_state="running",
        system_status="ok",
        instance_status="ok",
    )

    result = _call_tool("get_ec2_status_check", {"instance_id": "i-123"})

    mock_status.assert_called_once_with("i-123")
    assert result["system_status"] == "ok"


@patch("app.mcp.server.ec2_service.get_instance")
def test_get_ec2_instance_not_found_returns_null(mock_get: object) -> None:
    mock_get.return_value = None

    result = _call_tool("get_ec2_instance", {"instance_id": "i-missing"})

    assert result is None


@patch("app.mcp.server.cloudwatch_service.get_cpu_utilization")
def test_get_cpu_utilization(mock_cpu: object) -> None:
    from app.models.cloudwatch import CpuUtilizationSummary

    mock_cpu.return_value = CpuUtilizationSummary(
        instance_id="i-123",
        lookback_hours=3,
        datapoints=[],
        average_cpu_percent=None,
        max_cpu_percent=None,
        breached_80_percent=False,
    )

    result = _call_tool("get_cpu_utilization", {"instance_id": "i-123"})

    mock_cpu.assert_called_once_with("i-123", lookback_hours=3)
    assert result["instance_id"] == "i-123"
    assert result["breached_80_percent"] is False


@patch("app.mcp.server.cloudtrail_service.list_events_for_resource")
def test_list_cloudtrail_events_for_resource(mock_events: object) -> None:
    mock_events.return_value = CloudTrailEventList(
        resource_id="i-123",
        lookback_hours=24,
        events=[
            CloudTrailEvent(
                event_name="StopInstances", event_time="2026-07-01T00:00:00Z", username="ahmad"
            )
        ],
    )

    result = _call_tool("list_cloudtrail_events_for_resource", {"resource_id": "i-123"})

    mock_events.assert_called_once_with("i-123", lookback_hours=24)
    assert result["events"][0]["event_name"] == "StopInstances"


@patch("app.mcp.server.cloudtrail_service.list_recent_management_events")
def test_list_recent_management_events(mock_events: object) -> None:
    mock_events.return_value = CloudTrailCard(events=[])

    result = _call_tool("list_recent_management_events", {"max_results": 5})

    mock_events.assert_called_once_with(max_results=5)
    assert result["events"] == []


@patch("app.mcp.server.s3_service.list_buckets")
def test_list_s3_buckets(mock_buckets: object) -> None:
    mock_buckets.return_value = S3Card(
        buckets=[S3BucketSummary(name="opspilot-demo", creation_date=None)], count=1
    )

    result = _call_tool("list_s3_buckets", {})

    assert result["count"] == 1
    assert result["buckets"][0]["name"] == "opspilot-demo"


@patch("app.mcp.server.lambda_service.list_functions")
def test_list_lambda_functions(mock_functions: object) -> None:
    mock_functions.return_value = LambdaCard(
        functions=[
            LambdaFunctionSummary(
                name="opspilot-function", runtime="python3.14", last_modified=None
            )
        ],
        count=1,
    )

    result = _call_tool("list_lambda_functions", {})

    assert result["functions"][0]["name"] == "opspilot-function"


@patch("app.mcp.server.dynamodb_service.list_tables")
def test_list_dynamodb_tables(mock_tables: object) -> None:
    mock_tables.return_value = DynamoCard(
        tables=[DynamoTableSummary(name="opspilot-investigations", status="ACTIVE", item_count=0)],
        count=1,
    )

    result = _call_tool("list_dynamodb_tables", {})

    assert result["tables"][0]["name"] == "opspilot-investigations"


@patch("app.mcp.server.sns_service.list_topics")
def test_list_sns_topics(mock_topics: object) -> None:
    mock_topics.return_value = SnsCard(
        topics=[
            SnsTopicSummary(
                topic_arn="arn:aws:sns:us-east-1:123:opspilot-alerts", name="opspilot-alerts"
            )
        ],
        count=1,
    )

    result = _call_tool("list_sns_topics", {})

    assert result["topics"][0]["name"] == "opspilot-alerts"


@patch("app.mcp.server.rds_service.list_instances")
def test_list_rds_instances(mock_instances: object) -> None:
    mock_instances.return_value = RdsCard(
        instances=[
            RdsInstanceSummary(
                identifier="opspilot-db",
                engine="mysql",
                instance_class="db.t4g.micro",
                status="stopped",
            )
        ],
        count=1,
    )

    result = _call_tool("list_rds_instances", {})

    assert result["instances"][0]["identifier"] == "opspilot-db"


@patch("app.mcp.server.idle_service.check_idle")
def test_check_idle(mock_check_idle: object) -> None:
    mock_check_idle.return_value = IdleCheckResult(
        resource_id="i-123",
        resource_type="ec2",
        window_days=7,
        is_idle=True,
        idle_since="2026-07-03",
        idle_days=7,
        younger_than_window=False,
    )

    result = _call_tool("check_idle", {"resource_type": "ec2", "resource_id": "i-123", "days": 7})

    mock_check_idle.assert_called_once_with("ec2", "i-123", 7)
    assert result["is_idle"] is True
    assert result["idle_days"] == 7


@patch("app.mcp.server.cost_service.estimate_cost")
def test_estimate_cost_defaults_date_range_when_omitted(mock_estimate: object) -> None:
    mock_estimate.return_value = CostEstimate(
        resource_id="i-123",
        resource_type="ec2",
        date_range=DateRange(start="2026-07-01T00:00:00Z", end="2026-07-10T00:00:00Z"),
        method="list_price",
        hourly_rate=0.0104,
        projected_monthly=7.59,
        incurred_so_far=2.25,
    )

    result = _call_tool("estimate_cost", {"resource_type": "ec2", "resource_id": "i-123"})

    mock_estimate.assert_called_once_with("ec2", "i-123", None)
    assert result["method"] == "list_price"
    assert result["projected_monthly"] == 7.59
    assert result["incurred_so_far"] == 2.25
    assert result["projected_monthly"] != result["incurred_so_far"]


@patch("app.mcp.server.scan_service.list_available_regions")
def test_list_regions(mock_regions: object) -> None:
    mock_regions.return_value = ["us-east-1", "us-west-2"]

    result = _call_tool("list_regions", {})

    assert result["regions"] == ["us-east-1", "us-west-2"]


@patch("app.mcp.server.scan_service.scan_region")
def test_scan_region(mock_scan: object) -> None:
    from app.models.scan import ScanResponse, ScanTotals

    mock_scan.return_value = ScanResponse(
        region="us-east-1",
        last_updated="2026-07-10T09:15:00Z",
        resources=[],
        totals=ScanTotals(monthly_spend=0, idle_count=0, idle_monthly_waste=0),
        error=None,
    )

    result = _call_tool("scan_region", {"region": "us-east-1"})

    mock_scan.assert_called_once_with("us-east-1", force=False)
    assert result["region"] == "us-east-1"
    assert result["resources"] == []


@patch("app.mcp.server.scan_service.scan_region")
def test_scan_region_cooldown_with_no_cache_returns_error_object(mock_scan: object) -> None:
    from app.services import scan_service as scan_service_module

    mock_scan.side_effect = scan_service_module.ScanCooldownActive("us-east-1", 10.0, None)

    result = _call_tool("scan_region", {"region": "us-east-1", "force": True})

    assert "error" in result
    assert result["cached"] is None


@patch("app.mcp.server.scan_service.scan_region")
def test_scan_region_cooldown_with_cache_still_returns_cached_data(mock_scan: object) -> None:
    """MCP must not discard the still-good cached payload during a
    cooldown -- the dashboard's 429 response includes it, and the two
    front doors must not disagree on this."""
    from app.models.scan import ScanResponse, ScanTotals
    from app.services import scan_service as scan_service_module

    cached = ScanResponse(
        region="us-east-1",
        last_updated="2026-07-10T09:15:00Z",
        resources=[],
        totals=ScanTotals(monthly_spend=42.0, idle_count=1, idle_monthly_waste=10.0),
        error=None,
    )
    mock_scan.side_effect = scan_service_module.ScanCooldownActive("us-east-1", 10.0, cached)

    result = _call_tool("scan_region", {"region": "us-east-1", "force": True})

    assert "error" in result
    assert result["cached"]["totals"]["monthly_spend"] == 42.0


@patch("app.mcp.server.scan_service.scan_region")
def test_scan_region_invalid_region_returns_error_object(mock_scan: object) -> None:
    from app.services import scan_service as scan_service_module

    mock_scan.side_effect = scan_service_module.InvalidRegionError(
        "not-a-region", ["us-east-1", "us-west-2"]
    )

    result = _call_tool("scan_region", {"region": "not-a-region"})

    assert "error" in result
    assert "not-a-region" in result["error"]


@patch("app.mcp.server.scan_service.scan_region")
def test_scan_region_no_cache_failure_does_not_leak_exception_detail(mock_scan: object) -> None:
    from app.services import scan_service as scan_service_module

    mock_scan.side_effect = scan_service_module.ScanFailedNoCacheError(
        "us-east-1", RuntimeError("AccessDenied for arn:aws:iam::123456789012:user/ahmad")
    )

    result = _call_tool("scan_region", {"region": "us-east-1"})

    assert "error" in result
    assert "123456789012" not in result["error"]
    assert "AccessDenied" not in result["error"]


@patch("app.mcp.server.investigation_service.find_similar_past_investigations")
def test_find_similar_past_investigations(mock_find: object) -> None:
    mock_find.return_value = [
        SimilarInvestigation(
            id="inv-1",
            question="Is anything wrong with my instance?",
            trace_summary="Checked CPU, checked status checks.",
            conclusion="Nothing wrong.",
            created_at="2026-07-01T00:00:00Z",
            similarity=0.92,
        )
    ]

    result = _call_tool("find_similar_past_investigations", {"query": "instance issue", "top_k": 3})

    mock_find.assert_called_once_with("instance issue", top_k=3)
    assert result["results"][0]["id"] == "inv-1"


# --- Roadmap 3.6: token auth gate on every tool call ------------------------
# These deliberately bypass the module-level _valid_mcp_token autouse
# fixture per-test (re-patching within the test body) since they're testing
# the gate itself, not a specific tool's behavior.


def test_call_tool_rejects_missing_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(server.MCP_TOKEN_ENV_VAR, raising=False)
    monkeypatch.setattr(server.mcp_auth_service, "is_token_valid", lambda token: False)

    with patch("app.mcp.server.ec2_service.list_instances") as mock_list:
        with pytest.raises(server.McpAuthError):
            asyncio.run(server.mcp.call_tool("list_ec2_instances", {}))
        mock_list.assert_not_called()


def test_call_tool_rejects_invalid_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(server.MCP_TOKEN_ENV_VAR, "wrong-token")
    monkeypatch.setattr(server.mcp_auth_service, "is_token_valid", lambda token: False)

    with patch("app.mcp.server.ec2_service.list_instances") as mock_list:
        with pytest.raises(server.McpAuthError):
            asyncio.run(server.mcp.call_tool("list_ec2_instances", {}))
        mock_list.assert_not_called()


def test_call_tool_passes_env_var_token_to_validator(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(server.MCP_TOKEN_ENV_VAR, "the-configured-token")
    seen: list[str | None] = []

    def _fake_is_valid(token: str | None) -> bool:
        seen.append(token)
        return True

    monkeypatch.setattr(server.mcp_auth_service, "is_token_valid", _fake_is_valid)

    with patch("app.mcp.server.scan_service.list_available_regions", return_value=["us-east-1"]):
        asyncio.run(server.mcp.call_tool("list_regions", {}))

    assert seen == ["the-configured-token"]


def test_call_tool_allows_valid_token_through_to_the_tool(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(server.MCP_TOKEN_ENV_VAR, "a-valid-token")
    monkeypatch.setattr(server.mcp_auth_service, "is_token_valid", lambda token: True)

    with patch(
        "app.mcp.server.scan_service.list_available_regions", return_value=["us-east-1"]
    ) as mock_regions:
        result = _call_tool("list_regions", {})

    mock_regions.assert_called_once()
    assert result["regions"] == ["us-east-1"]
