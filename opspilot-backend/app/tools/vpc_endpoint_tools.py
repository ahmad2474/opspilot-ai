"""Agent-facing tools for VPC Interface Endpoint waste checking. Stay thin
on purpose -- all the real logic lives in app.services.vpc_endpoint_service
so it can be unit-tested without touching the LLM at all.
"""
from __future__ import annotations

import logging
from typing import Annotated

from agents import function_tool

from app.services import vpc_endpoint_service

logger = logging.getLogger("app.tools.vpc_endpoint")


@function_tool
def list_vpc_endpoints() -> str:
    """List VPC Endpoints (Interface, Gateway, and Gateway Load Balancer)
    in the configured region. Use this to find a vpc_endpoint_id for
    check_vpc_endpoint_idle/estimate_vpc_endpoint_cost -- only Interface
    endpoints have a meaningful idle/cost signal; Gateway endpoints (S3,
    DynamoDB) have no hourly charge."""
    logger.info("tool_call list_vpc_endpoints")
    result = vpc_endpoint_service.list_vpc_endpoints()
    logger.info("tool_result list_vpc_endpoints count=%d", result.count)
    return result.model_dump_json()


@function_tool
def check_vpc_endpoint_idle(
    vpc_endpoint_id: Annotated[str, "VPC Interface Endpoint ID, e.g. vpce-0123456789abcdef0."],
    days: Annotated[int, "How many days back to check for idleness."] = 7,
) -> str:
    """Check whether a VPC Interface Endpoint has near-zero data-plane
    traffic (BytesProcessed) over the given window -- same shape as
    check_idle (is_idle/idle_since/idle_days/younger_than_window). Only
    valid for Interface endpoints; raises for Gateway endpoints, which have
    no hourly charge and no traffic metric to check."""
    logger.info(
        "tool_call check_vpc_endpoint_idle vpc_endpoint_id=%s days=%d", vpc_endpoint_id, days
    )
    result = vpc_endpoint_service.check_vpc_endpoint_idle(vpc_endpoint_id, days)
    logger.info(
        "tool_result check_vpc_endpoint_idle vpc_endpoint_id=%s is_idle=%s",
        vpc_endpoint_id,
        result.is_idle,
    )
    return result.model_dump_json()


@function_tool
def estimate_vpc_endpoint_cost(
    vpc_endpoint_id: Annotated[str, "VPC Interface Endpoint ID."],
) -> str:
    """Estimate on-demand cost for a VPC Interface Endpoint via the AWS
    Pricing API -- same projected_monthly/incurred_so_far shape as
    estimate_cost. Ignores the per-AZ multiplier and per-GB data-processing
    charge (base hourly rate only, a documented simplification)."""
    logger.info("tool_call estimate_vpc_endpoint_cost vpc_endpoint_id=%s", vpc_endpoint_id)
    result = vpc_endpoint_service.estimate_vpc_endpoint_cost(vpc_endpoint_id)
    return result.model_dump_json()
