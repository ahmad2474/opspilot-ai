"""Agent-facing tools for roadmap Section 3.8's chat capability & scope
contract: list_resources, get_resource_health, get_resource_age,
estimate_instance_cost. Stay thin on purpose -- all real logic lives in
app.services.resource_query_service/cost_service so it is unit-testable
without touching the LLM at all, same precedent as every other tools/*
module.
"""
from __future__ import annotations

import logging
from typing import Annotated

from agents import function_tool

from app.services import cost_service, resource_query_service

logger = logging.getLogger("app.tools.resource_query")

_TYPE_CODES_HELP = (
    "one of: ec2, ebs, rds, eip, elb, lambda, nat_gateway, dynamodb, "
    "elasticache, sagemaker, redshift, api_gateway, cloudfront, opensearch, "
    "kinesis."
)


@function_tool
def list_resources(
    region: Annotated[
        str | None,
        (
            "AWS region to list resources in, e.g. us-east-1. Omit to use the "
            "account's configured default region."
        ),
    ] = None,
    resource_type: Annotated[
        str | None,
        f"Optional filter: {_TYPE_CODES_HELP} Omit to list every tracked type.",
    ] = None,
    status: Annotated[
        str | None,
        (
            "Optional filter: exact-match lifecycle status/state (e.g. 'running', "
            "'stopped', 'available', 'associated'). Case-insensitive. Omit for all "
            "statuses."
        ),
    ] = None,
) -> str:
    """Full inventory of every resource this app tracks (or a filtered
    subset), for count/list-style questions. Each entry has its Name tag
    (raw AWS ID if untagged); grouped by type, sorted alphabetically.
    Fast -- no per-resource CloudWatch/Pricing calls. by_status is always
    populated; idle_count/not_idle_count are only populated (verified)
    when idle_data_source='cached_scan', else 'unavailable' and null --
    call scan_region first for a verified idle split.
    """
    logger.info(
        "tool_call list_resources region=%s resource_type=%s status=%s",
        region,
        resource_type,
        status,
    )
    result = resource_query_service.list_resources(
        {"region": region, "type": resource_type, "status": status}
    )
    logger.info(
        "tool_result list_resources region=%s count=%d idle_data_source=%s",
        result.region,
        result.count,
        result.idle_data_source,
    )
    return result.model_dump_json()


@function_tool
def get_resource_health(
    resource_type: Annotated[str, f"Resource type to check: {_TYPE_CODES_HELP}"],
    resource_id: Annotated[str, "The resource ID to check, e.g. an EC2 instance ID."],
    region: Annotated[
        str | None, "AWS region the resource is in. Omit for the account's default region."
    ] = None,
) -> str:
    """Status/health for one resource: lifecycle status, a short 1-day
    recent-activity check ('is this alive right now', not a longer
    idle-waste window), and for EC2 also instance/system status checks
    plus scheduled maintenance events. found=false rather than
    fabricating data if not located."""
    logger.info(
        "tool_call get_resource_health resource_type=%s resource_id=%s region=%s",
        resource_type,
        resource_id,
        region,
    )
    result = resource_query_service.get_resource_health(resource_type, resource_id, region=region)
    logger.info(
        "tool_result get_resource_health resource_id=%s found=%s status=%s",
        resource_id,
        result.found,
        result.status,
    )
    return result.model_dump_json()


@function_tool
def get_resource_age(
    resource_type: Annotated[str, f"Resource type to check: {_TYPE_CODES_HELP}"],
    resource_id: Annotated[str, "The resource ID to check."],
    region: Annotated[
        str | None, "AWS region the resource is in. Omit for the account's default region."
    ] = None,
) -> str:
    """Age of a single resource in days, from its creation/launch
    timestamp. Several types (EIP, Lambda, CloudFront -- and sometimes
    OpenSearch while a domain is still being created) expose no creation
    timestamp at all through the AWS API this app calls -- for those,
    age_is_known is false, age_days is null, and reason explains why.
    Never fabricates an age from missing data."""
    logger.info(
        "tool_call get_resource_age resource_type=%s resource_id=%s region=%s",
        resource_type,
        resource_id,
        region,
    )
    result = resource_query_service.get_resource_age(resource_type, resource_id, region=region)
    logger.info(
        "tool_result get_resource_age resource_id=%s age_is_known=%s age_days=%s",
        resource_id,
        result.age_is_known,
        result.age_days,
    )
    return result.model_dump_json()


@function_tool
def estimate_instance_cost(
    instance_type: Annotated[
        str, "EC2 instance type, e.g. m5.xlarge, t3.large, c5.2xlarge."
    ],
    region: Annotated[str, "AWS region to price against, e.g. us-east-1."],
) -> str:
    """Hypothetical EC2 on-demand cost lookup via the AWS Pricing API,
    independent of any real resource in the account -- for 'what would a
    big EC2 machine cost' style questions. Returns both hourly_rate and
    monthly_rate (hourly x ~730 hours). For a vague/ambiguous question,
    call this 2-3 times with a few concrete reference instance types
    rather than stalling to ask exactly which size the user meant."""
    logger.info(
        "tool_call estimate_instance_cost instance_type=%s region=%s", instance_type, region
    )
    result = cost_service.estimate_instance_cost(instance_type, region)
    logger.info(
        "tool_result estimate_instance_cost instance_type=%s monthly_rate=%s",
        instance_type,
        result.monthly_rate,
    )
    return result.model_dump_json()
