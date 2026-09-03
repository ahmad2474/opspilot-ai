"""MCP server exposing OpsPilot's AWS investigation tools.

Reuses app.services directly — the third consumer of the service layer,
alongside the FastAPI dashboard routes and the Agents SDK chat tools. No
AWS logic lives here; each tool is a thin wrapper that calls a service
function and returns its result as JSON.

Token auth (roadmap Section 3.6): every tool call must present a valid
MCP access token before any tool body runs or any AWS call happens — see
`_AuthenticatedFastMCP.call_tool` below for the enforcement point.
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from typing import Any

from mcp.server.fastmcp import FastMCP

from app.models.cost import DateRange
from app.services import (
    cloudtrail_service,
    cloudwatch_service,
    commitment_service,
    compute_optimizer_service,
    cost_service,
    deletion_impact_service,
    dynamodb_service,
    ec2_service,
    ecs_service,
    idle_service,
    investigation_service,
    lambda_service,
    logs_service,
    mcp_auth_service,
    rds_service,
    resource_query_service,
    s3_service,
    scan_service,
    snapshot_service,
    sns_service,
    vpc_endpoint_service,
)

logger = logging.getLogger("app.mcp.server")

# Stdio JSON-RPC has no per-request header channel (unlike an HTTP
# Authorization header), so an environment variable set in the launching
# client's config (e.g. Claude Desktop's claude_desktop_config.json "env"
# block, or this repo's own .env for local testing — app.core.config
# already calls load_dotenv() at import time, which mcp_auth_service pulls
# in transitively) is the standard credential-passing mechanism for this
# transport. Read fresh on every call (not cached at process start) so a
# revoked token takes effect immediately without restarting whatever
# spawned this process.
MCP_TOKEN_ENV_VAR = "OPSPILOT_MCP_TOKEN"


class McpAuthError(Exception):
    """Raised when a tool call arrives without a valid MCP access token.

    mcp.server.lowlevel.server.Server.call_tool's registered handler
    catches any exception raised while dispatching a tool call and turns
    it into a clean CallToolResult(isError=True, content=[...str(e)]) sent
    back to the client — the stdio connection itself is never torn down,
    but critically no tool body ever runs and no AWS call is ever made
    (roadmap 3.6: "reject immediately... no tool call or AWS role
    assumption").
    """


class _AuthenticatedFastMCP(FastMCP):
    """FastMCP subclass that gates every tool call behind a token check.

    This has to be a subclass override, not a post-construction
    monkeypatch on the constructed `mcp` instance: FastMCP.__init__ calls
    `_setup_handlers()` immediately, which registers `self.call_tool` as
    the JSON-RPC call_tool handler right away — that lookup resolves
    `self.call_tool` via the instance's actual class (normal Python MRO),
    so a subclass override defined before construction is picked up
    correctly, whereas reassigning `mcp.call_tool = ...` as an instance
    attribute *after* FastMCP("opspilot") returns would be invisible to
    the handler the lowlevel server already captured a direct reference
    to.
    """

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> Any:
        token = os.environ.get(MCP_TOKEN_ENV_VAR)
        if not mcp_auth_service.is_token_valid(token):
            logger.warning("mcp_call_rejected tool=%s reason=missing_or_invalid_token", name)
            raise McpAuthError(
                "Missing or invalid MCP access token. Set the "
                f"{MCP_TOKEN_ENV_VAR} environment variable to a token generated "
                "from Settings -> MCP Access in the OpsPilot dashboard "
                "(tokens are shown once at generation time)."
            )
        # Logged the same way the HTTP API logs each request (see
        # app/core/logging.py's RequestIdMiddleware start/end lines) —
        # this is the MCP transport's equivalent access log entry.
        logger.info("mcp_call tool=%s", name)
        return await super().call_tool(name, arguments)


mcp = _AuthenticatedFastMCP("opspilot")


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
def check_idle(resource_type: str, resource_id: str, days: int = 7) -> str:
    """Check whether a resource has been idle (near-zero utilization) over
    the given window. Idle requires *every* daily datapoint in the window
    to be below threshold, not just the average. Also reports the current
    trailing idle streak (idle_since/idle_days) and flags resources
    younger than the requested window. resource_type: one of ec2, ebs,
    rds, eip, elb, lambda, nat_gateway, dynamodb, elasticache, sagemaker,
    redshift, api_gateway, cloudfront, opensearch, kinesis (all 15
    roadmap-scoped types).
    """
    return idle_service.check_idle(resource_type, resource_id, days).model_dump_json()


@mcp.tool()
def estimate_cost(
    resource_type: str, resource_id: str, start: str | None = None, end: str | None = None
) -> str:
    """Estimate cost for a resource via the AWS Pricing API (on-demand list
    price, not actual billed cost). Returns both projected_monthly (rate x
    a full ~730-hour month) and incurred_so_far (rate x hours actually
    elapsed in the requested window, capped at the resource's own age) --
    two distinct numbers, never conflate them. start/end are optional ISO
    8601 timestamps; omit both to default to the resource's launch time
    through now (EIP/Lambda/CloudFront/OpenSearch have no launch time --
    defaults to a zero-width or trailing-7-day range instead, per type).
    resource_type: one of ec2, ebs, rds, eip, elb, lambda, nat_gateway,
    dynamodb, elasticache, sagemaker, redshift, api_gateway, cloudfront,
    opensearch, kinesis (all 15 roadmap-scoped types).
    """
    date_range = None
    if start is not None and end is not None:

        def _parse(value: str) -> datetime:
            parsed = datetime.fromisoformat(value)
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)

        date_range = DateRange(start=_parse(start), end=_parse(end))

    return cost_service.estimate_cost(resource_type, resource_id, date_range).model_dump_json()


@mcp.tool()
def list_regions() -> str:
    """List enabled AWS regions in this account (via ec2:DescribeRegions) --
    use this to find a valid `region` argument for scan_region."""
    return json.dumps({"regions": scan_service.list_available_regions()})


@mcp.tool()
def scan_region(region: str, force: bool = False) -> str:
    """Scan one AWS region across all 15 roadmap-scoped resource types
    (ec2, ebs, rds, eip, elb, lambda, nat_gateway, dynamodb, elasticache,
    sagemaker, redshift, api_gateway, cloudfront, opensearch, kinesis) --
    returns every resource found (with its idle/cost/health status) plus
    account-wide totals (monthly_spend, idle_count, idle_monthly_waste).
    This is the same aggregation the dashboard's galaxy view uses
    (GET /resources/scan) -- MCP and the dashboard are two front doors to
    the same scan_service.

    force=False (default): serves the cached scan for this region if one
    exists, no AWS calls. force=True: forces a fresh rescan, subject to a
    short anti-spam cooldown if one just ran for this region -- if that
    cooldown is active, this returns an {"error": ..., "cached": ...}
    object (the still-good cached scan, if any, alongside the notice --
    same as the dashboard's 429 response body) rather than silently
    no-op'ing, raising, or discarding the cached data. An unrecognized
    region, or a scan failure with no prior cache for this region at all,
    also returns a plain {"error": ...} object rather than fabricating
    data or leaking a raw AWS exception message.
    """
    # scan_service.scan_region_as_dict() is the single place this
    # cooldown/failure/invalid-region -> JSON translation lives, shared
    # with the chat tool (app/tools/scan_tools.py) so the two front doors
    # can't drift on what a cooldown response looks like.
    return json.dumps(scan_service.scan_region_as_dict(region, force=force))


@mcp.tool()
def list_resources(
    region: str | None = None, resource_type: str | None = None, status: str | None = None
) -> str:
    """Full inventory of every resource this app tracks (or a filtered
    subset) -- for count/list-style questions ('how many resources are
    running', 'list them'). Each entry includes its Name tag (falling
    back to its raw AWS ID if untagged) and lifecycle status; resources
    come back already grouped by type and sorted alphabetically by name
    within each group. Fast by design -- does not make a fresh
    CloudWatch/Pricing call per resource: idle_count/not_idle_count are
    only populated when idle_data_source='cached_scan' (scan_region
    already ran and is cached for this region); by_status (lifecycle
    status counts) is always populated. region: AWS region, omit for the
    account's default region. resource_type: one of ec2, ebs, rds, eip,
    elb, lambda, nat_gateway, dynamodb, elasticache, sagemaker, redshift,
    api_gateway, cloudfront, opensearch, kinesis -- omit for all 15.
    status: exact-match lifecycle status filter (e.g. running, stopped),
    case-insensitive, omit for all.
    """
    result = resource_query_service.list_resources(
        {"region": region, "type": resource_type, "status": status}
    )
    return result.model_dump_json()


@mcp.tool()
def get_resource_health(resource_type: str, resource_id: str, region: str | None = None) -> str:
    """Status/health signals for a single resource: lifecycle status, a
    short 1-day recent-activity CloudWatch check (reuses the same
    idle-detection signal as check_idle but over a short 'is this alive
    right now' window), and -- for EC2 specifically -- instance/system
    status checks with any scheduled maintenance events. Returns
    found=false rather than fabricating data if the resource cannot be
    located. resource_type: one of ec2, ebs, rds, eip, elb, lambda,
    nat_gateway, dynamodb, elasticache, sagemaker, redshift, api_gateway,
    cloudfront, opensearch, kinesis.
    """
    result = resource_query_service.get_resource_health(resource_type, resource_id, region=region)
    return result.model_dump_json()


@mcp.tool()
def get_resource_age(resource_type: str, resource_id: str, region: str | None = None) -> str:
    """Age of a single resource in days, from its creation/launch
    timestamp. EIP, Lambda, and CloudFront (and sometimes OpenSearch,
    while a domain is still being created) expose no creation timestamp
    at all -- for those, age_is_known is false, age_days is null, and
    reason explains why, rather than a fabricated age. resource_type: one
    of ec2, ebs, rds, eip, elb, lambda, nat_gateway, dynamodb,
    elasticache, sagemaker, redshift, api_gateway, cloudfront,
    opensearch, kinesis.
    """
    result = resource_query_service.get_resource_age(resource_type, resource_id, region=region)
    return result.model_dump_json()


@mcp.tool()
def estimate_instance_cost(instance_type: str, region: str) -> str:
    """Hypothetical EC2 on-demand cost lookup via the AWS Pricing API,
    independent of any real resource in the account -- for 'what would a
    big EC2 machine cost' style questions. Returns both hourly_rate and
    monthly_rate (hourly x ~730 hours)."""
    result = cost_service.estimate_instance_cost(instance_type, region)
    return result.model_dump_json()


@mcp.tool()
def check_log_retention() -> str:
    """Find CloudWatch Log groups with no retention policy set -- log data
    kept forever by default. Free/cheap DescribeLogGroups-only check, no
    CloudWatch metrics needed. Returns a findings list (one per flagged log
    group, with its real stored-byte size), not a single idle/cost verdict
    (roadmap phase 2 Section 1.3)."""
    return logs_service.check_log_retention().model_dump_json()


@mcp.tool()
def check_s3_waste(bucket: str, days: int = 7) -> str:
    """Check an S3 bucket for storage/lifecycle waste: no lifecycle policy
    at all, incomplete multipart uploads older than `days`, and versioning
    enabled with no noncurrent-version-expiration rule. Returns a findings
    list per bucket (zero to three independent flags), a structurally
    different shape from check_idle's single is_idle boolean (roadmap
    phase 2 Section 1.2). Also includes a best-effort S3-Standard-vs-
    colder-tier price-gap note. Does not flag "no recent access" objects --
    that needs S3 Storage Lens/Server Access Logging enabled to measure
    honestly."""
    return s3_service.check_s3_waste(bucket, days=days).model_dump_json()


@mcp.tool()
def list_vpc_endpoints() -> str:
    """List VPC Endpoints (Interface, Gateway, Gateway Load Balancer) in
    the configured region."""
    return vpc_endpoint_service.list_vpc_endpoints().model_dump_json()


@mcp.tool()
def check_vpc_endpoint_idle(vpc_endpoint_id: str, days: int = 7) -> str:
    """Check whether a VPC Interface Endpoint has near-zero data-plane
    traffic (BytesProcessed) over the given window -- same shape as
    check_idle. Raises for a Gateway endpoint (no hourly charge, no traffic
    metric). Roadmap phase 2 Section 1.1."""
    return vpc_endpoint_service.check_vpc_endpoint_idle(vpc_endpoint_id, days).model_dump_json()


@mcp.tool()
def estimate_vpc_endpoint_cost(vpc_endpoint_id: str) -> str:
    """Estimate on-demand cost for a VPC Interface Endpoint via the AWS
    Pricing API -- same projected_monthly/incurred_so_far shape as
    estimate_cost."""
    return vpc_endpoint_service.estimate_vpc_endpoint_cost(vpc_endpoint_id).model_dump_json()


@mcp.tool()
def check_snapshot_sprawl(
    resource_type: str, retention_days_or_count: int, retention_mode: str = "days"
) -> str:
    """Find EBS/RDS snapshot sprawl: orphaned snapshots (source volume/
    instance no longer exists) and snapshots beyond a caller-supplied
    retention threshold -- retention_days_or_count has no default, there is
    no universal 'correct' value, always ask the user for it. retention_mode:
    'days' (older than N days) or 'count' (keep the N most-recent per
    source). Returns a findings list; a snapshot can be flagged as both
    orphaned and beyond_retention at once (roadmap phase 2 Section 1.3)."""
    return snapshot_service.check_snapshot_sprawl(
        resource_type, retention_days_or_count, retention_mode=retention_mode
    ).model_dump_json()


@mcp.tool()
def list_ecs_clusters() -> str:
    """List ECS clusters in the configured region, including each cluster's
    CloudWatch Container Insights setting ('enabled'/'enhanced'/'disabled').
    Use this to find a `cluster` name for check_container_idle."""
    return ecs_service.list_clusters().model_dump_json()


@mcp.tool()
def check_container_idle(cluster: str, days: int = 7) -> str:
    """Find ECS/Fargate container waste in one cluster: running Fargate
    tasks with near-zero CPU/memory utilization, tasks allocated far more
    vCPU/memory than they use (rightsizing-flavored), and services with a
    non-zero minimum desired count but near-zero real traffic (standby
    capacity). Task-level, not cluster-level. Requires CloudWatch Container
    Insights enabled on the cluster (a one-time opt-in, same pattern as
    Redshift/Kinesis) -- if disabled, returns container_insights_enabled=false
    with a plain how-to-opt-in note instead of erroring. Findings list, not
    a single verdict (roadmap phase 2 Section 1.2)."""
    return ecs_service.check_container_idle(cluster, days).model_dump_json()


@mcp.tool()
def analyze_commitment_utilization(days: int = 30) -> str:
    """Analyze Savings Plan and Reserved Instance utilization AND coverage
    for this account (roadmap phase 2 Section 1.3) -- account-level, not
    about a single resource. UTILIZATION findings are real wasted money
    (paying for an under-used commitment); COVERAGE findings are a savings
    OPPORTUNITY, not waste in the same sense (paying on-demand for usage a
    commitment could cover, but nothing overspent). Never conflate the two.
    Calls AWS Cost Explorer's PAID commitment APIs (4 requests per call,
    small but real per-request cost -- see the response's `note`/
    `estimated_cost_explorer_api_cost_usd`)."""
    return commitment_service.analyze_commitment_utilization(days).model_dump_json()


@mcp.tool()
def get_rightsizing_recommendations(resource_type: str) -> str:
    """Get AWS Compute Optimizer's own ML-driven rightsizing recommendations
    -- NOT idle detection, catches a busy-but-oversized resource idle
    checking structurally can't. resource_type: one of 'ec2', 'ebs',
    'lambda', 'ecs' (ECS-on-Fargate). Findings are AWS-generated verdicts,
    not this app's own computed threshold. Requires a one-time
    account-level Compute Optimizer opt-in -- an un-enrolled account
    returns enrolled=false with a plain how-to-opt-in note, not an error."""
    return compute_optimizer_service.get_rightsizing_recommendations(
        resource_type
    ).model_dump_json()


@mcp.tool()
def check_deletion_impact(resource_type: str, resource_id: str) -> str:
    """Analyze what actually happens if a specific resource is deleted --
    read-only, this NEVER deletes anything itself (roadmap phase 2 Section
    3, the permanent read-only replacement for the retired write/approval
    Step 8). resource_type: 'ec2' (instance termination), 'rds' (DB
    instance deletion), or 'ebs' (standalone volume deletion). Returns a
    structured report, not prose: will_be_removed (things that actually
    disappear), will_persist_and_keep_costing (things that remain and keep
    costing, each with a real dollar figure where one is computable via
    estimate_cost), behavioral_warnings (surprising operational
    consequences -- e.g. an Auto Scaling Group replacing a terminated
    member instance, so terminating it alone won't reduce spend), and
    never_affected (independent objects like security groups/IAM roles,
    stated explicitly for completeness). check_errors lists any live check
    that could not be verified (e.g. a permission gap) -- treat those as
    unknown, never as a settled 'no'."""
    return deletion_impact_service.check_deletion_impact(
        resource_type, resource_id
    ).model_dump_json()


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
