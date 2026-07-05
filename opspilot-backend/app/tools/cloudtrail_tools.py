from __future__ import annotations

import logging
from typing import Annotated

from agents import function_tool

from app.services import cloudtrail_service

logger = logging.getLogger("app.tools.cloudtrail")


@function_tool
def list_recent_ec2_activity(
    instance_id: Annotated[str, "The EC2 instance ID to check, e.g. i-0123456789abcdef0."],
    lookback_hours: Annotated[int, "How many hours back to look for management events."] = 24,
) -> str:
    """List recent AWS management events (stop/start/reboot/modify/etc.)
    performed on this instance. Use this to check whether a perceived
    issue correlates with something someone actually did to the instance,
    rather than a real infrastructure or load problem."""
    logger.info(
        "tool_call list_recent_ec2_activity instance_id=%s lookback_hours=%d",
        instance_id,
        lookback_hours,
    )
    result = cloudtrail_service.list_events_for_resource(
        resource_id=instance_id, lookback_hours=lookback_hours
    )
    logger.info(
        "tool_result list_recent_ec2_activity instance_id=%s event_count=%d",
        instance_id,
        len(result.events),
    )
    return result.model_dump_json()
