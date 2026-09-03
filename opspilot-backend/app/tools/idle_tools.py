"""Agent-facing tool for idle detection. Stays thin on purpose -- all the
real logic lives in app.services.idle_service so it can be unit-tested
without touching the LLM at all.
"""
from __future__ import annotations

import logging
from typing import Annotated

from agents import function_tool

from app.services import idle_service

logger = logging.getLogger("app.tools.idle")


@function_tool
def check_idle(
    resource_type: Annotated[
        str,
        (
            "One of: ec2, ebs, rds, eip, elb, lambda, nat_gateway, dynamodb, "
            "elasticache, sagemaker, redshift, api_gateway, cloudfront, "
            "opensearch, kinesis."
        ),
    ],
    resource_id: Annotated[str, "The resource ID to check, e.g. i-0123456789abcdef0."],
    days: Annotated[int, "How many days back to check for idleness."] = 7,
) -> str:
    """Check whether a resource has been idle over the window -- requires
    *every* daily datapoint below threshold, not just the average (a burst
    on one day disqualifies the window). Reports the trailing idle streak
    (idle_since/idle_days), flagging resources younger than the window so
    'idle since' is never longer than the resource has actually existed."""
    logger.info(
        "tool_call check_idle resource_type=%s resource_id=%s days=%d",
        resource_type,
        resource_id,
        days,
    )
    result = idle_service.check_idle(resource_type, resource_id, days)
    logger.info(
        "tool_result check_idle resource_id=%s is_idle=%s idle_days=%d",
        resource_id,
        result.is_idle,
        result.idle_days,
    )
    return result.model_dump_json()
