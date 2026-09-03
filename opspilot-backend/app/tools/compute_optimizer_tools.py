"""Agent-facing tool for AWS Compute Optimizer rightsizing recommendations.
Stays thin on purpose -- all the real logic lives in
app.services.compute_optimizer_service so it can be unit-tested without
touching the LLM at all.
"""
from __future__ import annotations

import logging
from typing import Annotated

from agents import function_tool

from app.services import compute_optimizer_service

logger = logging.getLogger("app.tools.compute_optimizer")


@function_tool
def get_rightsizing_recommendations(
    resource_type: Annotated[str, "One of: 'ec2', 'ebs', 'lambda', 'ecs' (ECS-on-Fargate)."],
) -> str:
    """AWS Compute Optimizer's own ML-driven rightsizing verdicts (NOT idle
    detection -- catches a busy-but-oversized resource idle-checking
    can't). Findings are AWS-generated (Overprovisioned/Underprovisioned/
    NotOptimized) -- present as AWS's recommendation, not this app's own.
    Requires account-level opt-in; if not enrolled, returns
    enrolled=false with a how-to-opt-in note instead of erroring."""
    logger.info("tool_call get_rightsizing_recommendations resource_type=%s", resource_type)
    result = compute_optimizer_service.get_rightsizing_recommendations(resource_type)
    logger.info(
        "tool_result get_rightsizing_recommendations resource_type=%s enrolled=%s findings=%d",
        resource_type,
        result.enrolled,
        len(result.findings),
    )
    return result.model_dump_json()
