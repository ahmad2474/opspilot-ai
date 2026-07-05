from __future__ import annotations

import logging
from typing import Annotated

from agents import function_tool

from app.services import cloudwatch_service

logger = logging.getLogger("app.tools.cloudwatch")


@function_tool
def get_ec2_cpu_utilization(
    instance_id: Annotated[str, "The EC2 instance ID to check, e.g. i-0123456789abcdef0."],
    lookback_hours: Annotated[int, "How many hours back to pull CPU metrics for."] = 3,
) -> str:
    """Get CPUUtilization statistics (average and maximum, 5-minute
    resolution) for an EC2 instance over a lookback window, and whether it
    crossed the 80% threshold at any point."""
    logger.info(
        "tool_call get_ec2_cpu_utilization instance_id=%s lookback_hours=%d",
        instance_id,
        lookback_hours,
    )
    summary = cloudwatch_service.get_cpu_utilization(
        instance_id=instance_id, lookback_hours=lookback_hours
    )
    logger.info(
        "tool_result get_ec2_cpu_utilization instance_id=%s breached_80_percent=%s",
        instance_id,
        summary.breached_80_percent,
    )
    return summary.model_dump_json()
