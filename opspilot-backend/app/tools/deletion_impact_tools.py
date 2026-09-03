"""Agent-facing tool for deletion-impact analysis (roadmap phase 2 Section
3). Stays thin on purpose -- all the real logic lives in
app.services.deletion_impact_service so it can be unit-tested without
touching the LLM at all.
"""
from __future__ import annotations

import logging
from typing import Annotated

from agents import function_tool

from app.services import deletion_impact_service

logger = logging.getLogger("app.tools.deletion_impact")


@function_tool
def check_deletion_impact(
    resource_type: Annotated[str, "Resource type to check: 'ec2', 'rds', or 'ebs'."],
    resource_id: Annotated[
        str,
        (
            "The resource ID to check -- an EC2 instance ID (i-...), RDS DB instance "
            "identifier, or EBS volume ID (vol-...), matching resource_type."
        ),
    ],
) -> str:
    """Analyze what actually happens if a resource is deleted -- read-only,
    NEVER deletes anything itself. Returns will_be_removed,
    will_persist_and_keep_costing (with dollar figures where computable),
    behavioral_warnings, never_affected, and check_errors (unverified
    facts -- treat as unknown, never 'no'). See system instructions for
    how to report each section."""
    logger.info(
        "tool_call check_deletion_impact resource_type=%s resource_id=%s",
        resource_type,
        resource_id,
    )
    result = deletion_impact_service.check_deletion_impact(resource_type, resource_id)
    logger.info(
        "tool_result check_deletion_impact resource_id=%s will_persist=%d warnings=%d "
        "check_errors=%d",
        resource_id,
        len(result.will_persist_and_keep_costing),
        len(result.behavioral_warnings),
        len(result.check_errors),
    )
    return result.model_dump_json()
