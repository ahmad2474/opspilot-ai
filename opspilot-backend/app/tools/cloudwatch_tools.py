from __future__ import annotations

from typing import Annotated

from agents import function_tool

from app.services import cloudwatch_service


@function_tool
def get_ec2_cpu_utilization(
    instance_id: Annotated[str, "The EC2 instance ID to check, e.g. i-0123456789abcdef0."],
    lookback_hours: Annotated[int, "How many hours back to pull CPU metrics for."] = 3,
) -> str:
    """Get CPUUtilization statistics (average and maximum, 5-minute
    resolution) for an EC2 instance over a lookback window, and whether it
    crossed the 80% threshold at any point."""
    summary = cloudwatch_service.get_cpu_utilization(
        instance_id=instance_id, lookback_hours=lookback_hours
    )
    return summary.model_dump_json()
