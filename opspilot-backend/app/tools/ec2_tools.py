"""Agent-facing tools for EC2. These stay thin on purpose — all the real
logic lives in app.services.ec2_service so it can be unit-tested without
touching the LLM at all.
"""
from __future__ import annotations

from typing import Annotated

from agents import function_tool

from app.services import ec2_service


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
    result = ec2_service.list_instances(state_filter=state_filter)
    return result.model_dump_json()
