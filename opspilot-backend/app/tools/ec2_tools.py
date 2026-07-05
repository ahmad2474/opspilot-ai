"""Agent-facing tools for EC2. These stay thin on purpose — all the real
logic lives in app.services.ec2_service so it can be unit-tested without
touching the LLM at all.
"""
from __future__ import annotations

import logging
from typing import Annotated

from agents import function_tool

from app.services import ec2_service

logger = logging.getLogger("app.tools.ec2")


@function_tool
def list_ec2_instances(
    state_filter: Annotated[
        str | None,
        (
            "Optional lifecycle state to filter by: pending, running, "
            "stopping, stopped, shutting-down, terminated. Omit to list all."
        ),
    ] = None,
) -> str:
    """List EC2 instances in the configured AWS account/region, including
    instance type, state, availability zone, and IP addresses."""
    logger.info("tool_call list_ec2_instances state_filter=%s", state_filter)
    result = ec2_service.list_instances(state_filter=state_filter)
    logger.info("tool_result list_ec2_instances count=%d", result.count)
    return result.model_dump_json()


@function_tool
def get_ec2_status_check(
    instance_id: Annotated[str, "The EC2 instance ID to check, e.g. i-0123456789abcdef0."],
) -> str:
    """Get instance-level and system-level status checks for an EC2
    instance, plus any AWS-scheduled maintenance events. Use this to rule
    out an infrastructure-level fault as distinct from a load/CPU issue."""
    logger.info("tool_call get_ec2_status_check instance_id=%s", instance_id)
    result = ec2_service.get_status_check(instance_id)
    logger.info(
        "tool_result get_ec2_status_check instance_id=%s system=%s instance=%s",
        instance_id,
        result.system_status,
        result.instance_status,
    )
    return result.model_dump_json()
