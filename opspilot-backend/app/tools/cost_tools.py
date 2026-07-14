"""Agent-facing tool for cost estimation. Stays thin on purpose -- all the
real logic lives in app.services.cost_service so it can be unit-tested
without touching the LLM at all.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Annotated

from agents import function_tool

from app.models.cost import DateRange
from app.services import cost_service

logger = logging.getLogger("app.tools.cost")


def _parse_iso(value: str | None) -> datetime | None:
    if value is None:
        return None
    parsed = datetime.fromisoformat(value)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


@function_tool
def estimate_cost(
    resource_type: Annotated[
        str,
        (
            "Resource type to estimate cost for: one of the 15 roadmap-scoped "
            "types -- 'ec2', 'ebs', 'rds', 'eip', 'elb', 'lambda', "
            "'nat_gateway', 'dynamodb', 'elasticache', 'sagemaker', 'redshift', "
            "'api_gateway', 'cloudfront', 'opensearch', 'kinesis'."
        ),
    ],
    resource_id: Annotated[
        str, "The resource ID to estimate cost for, e.g. an EC2 instance ID."
    ],
    start: Annotated[
        str | None,
        (
            "ISO 8601 start of the window incurred_so_far is computed over. "
            "Omit to default to the resource's launch time."
        ),
    ] = None,
    end: Annotated[
        str | None, "ISO 8601 end of the window. Omit to default to now."
    ] = None,
) -> str:
    """Estimate cost for a resource via the AWS Pricing API (on-demand list
    price -- not actual billed cost). Returns both projected_monthly (rate
    x a full ~730-hour month -- what drives star/bubble sizing) and
    incurred_so_far (rate x hours actually elapsed in the requested
    window, capped at the resource's own age) -- these are two distinct
    numbers and must never be conflated with each other."""
    logger.info(
        "tool_call estimate_cost resource_type=%s resource_id=%s start=%s end=%s",
        resource_type,
        resource_id,
        start,
        end,
    )
    parsed_start = _parse_iso(start)
    parsed_end = _parse_iso(end)
    # Only build an explicit date_range when both ends are given -- a
    # partial override (e.g. just `end`) would otherwise silently default
    # the missing side to "now", producing a bogus near-zero window instead
    # of the intended "since launch" default.
    date_range = None
    if parsed_start is not None and parsed_end is not None:
        date_range = DateRange(start=parsed_start, end=parsed_end)

    result = cost_service.estimate_cost(resource_type, resource_id, date_range)
    logger.info(
        "tool_result estimate_cost resource_id=%s projected_monthly=%s incurred_so_far=%s",
        resource_id,
        result.projected_monthly,
        result.incurred_so_far,
    )
    return result.model_dump_json()
