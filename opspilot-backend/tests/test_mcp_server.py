"""Tests for the MCP server — verifies tool registration and that each
tool delegates to the correct service function and surfaces its result,
exercised against the real installed mcp package (not mocked)."""
from __future__ import annotations

import asyncio
import json
from unittest.mock import patch

from app.mcp import server
from app.models.cloudtrail import CloudTrailEvent, CloudTrailEventList
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
from app.models.investigation import SimilarInvestigation


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
