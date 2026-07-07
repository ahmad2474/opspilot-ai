"""MCP server exposing OpsPilot's AWS investigation tools.

Reuses app.services directly — the third consumer of the service layer,
alongside the FastAPI dashboard routes and the Agents SDK chat tools. No
AWS logic lives here; each tool is a thin wrapper that calls a service
function and returns its result as JSON.
"""
from __future__ import annotations

import json

from mcp.server.fastmcp import FastMCP

from app.services import (
    cloudtrail_service,
    cloudwatch_service,
    dynamodb_service,
    ec2_service,
    investigation_service,
    lambda_service,
    rds_service,
    s3_service,
    sns_service,
)

mcp = FastMCP("opspilot")


@mcp.tool()
def list_ec2_instances(state_filter: str | None = None) -> str:
    """List EC2 instances in the configured AWS account/region, including
    instance type, state, availability zone, and IP addresses.

    state_filter: optional lifecycle state to filter by — pending, running,
    stopping, stopped, shutting-down, terminated. Omit to list all.
    """
    return ec2_service.list_instances(state_filter=state_filter).model_dump_json()


@mcp.tool()
def get_ec2_status_check(instance_id: str) -> str:
    """Get instance-level and system-level status checks for an EC2
    instance, plus any AWS-scheduled maintenance events. Use this to rule
    out an infrastructure-level fault as distinct from a load/CPU issue."""
    return ec2_service.get_status_check(instance_id).model_dump_json()


@mcp.tool()
def get_ec2_instance(instance_id: str) -> str:
    """Look up a single EC2 instance by ID. Returns null if not found."""
    result = ec2_service.get_instance(instance_id)
    return result.model_dump_json() if result else "null"


@mcp.tool()
def get_cpu_utilization(instance_id: str, lookback_hours: int = 3) -> str:
    """Get CloudWatch CPU utilization statistics for an EC2 instance over
    the given lookback window (hours)."""
    return cloudwatch_service.get_cpu_utilization(
        instance_id, lookback_hours=lookback_hours
    ).model_dump_json()


@mcp.tool()
def list_cloudtrail_events_for_resource(resource_id: str, lookback_hours: int = 24) -> str:
    """List CloudTrail management events referencing a specific resource ID
    over the given lookback window (hours) — correlates a perceived issue
    with something someone actually did (stop/start/reboot/modify)."""
    return cloudtrail_service.list_events_for_resource(
        resource_id, lookback_hours=lookback_hours
    ).model_dump_json()


@mcp.tool()
def list_recent_management_events(max_results: int = 5) -> str:
    """List the most recent AWS CloudTrail management events, account-wide."""
    return cloudtrail_service.list_recent_management_events(
        max_results=max_results
    ).model_dump_json()


@mcp.tool()
def list_s3_buckets() -> str:
    """List S3 buckets in the account."""
    return s3_service.list_buckets().model_dump_json()


@mcp.tool()
def list_lambda_functions() -> str:
    """List Lambda functions in the configured region."""
    return lambda_service.list_functions().model_dump_json()


@mcp.tool()
def list_dynamodb_tables() -> str:
    """List DynamoDB tables in the configured region."""
    return dynamodb_service.list_tables().model_dump_json()


@mcp.tool()
def list_sns_topics() -> str:
    """List SNS topics in the configured region."""
    return sns_service.list_topics().model_dump_json()


@mcp.tool()
def list_rds_instances() -> str:
    """List RDS instances in the configured region."""
    return rds_service.list_instances().model_dump_json()


@mcp.tool()
def find_similar_past_investigations(query: str, top_k: int = 3) -> str:
    """Search past chat investigations for ones semantically similar to the
    given query, using cosine similarity over Gemini embeddings."""
    results = investigation_service.find_similar_past_investigations(query, top_k=top_k)
    return json.dumps({"results": [r.model_dump() for r in results]})


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
