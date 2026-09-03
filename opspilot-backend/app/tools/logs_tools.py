"""Agent-facing tool for the CloudWatch Logs retention check. Stays thin on
purpose -- all the real logic lives in app.services.logs_service so it can
be unit-tested without touching the LLM at all.
"""
from __future__ import annotations

import logging

from agents import function_tool

from app.services import logs_service

logger = logging.getLogger("app.tools.logs")


@function_tool
def check_log_retention() -> str:
    """Find CloudWatch Log groups with no retention policy set -- log data
    kept forever by default, accumulating storage cost silently until
    someone looks. One of the fastest FinOps quick wins: a pure
    configuration read (DescribeLogGroups), no CloudWatch metrics call
    needed. Returns a findings list (one entry per flagged log group, with
    its real stored-byte size), not a single idle/cost verdict."""
    logger.info("tool_call check_log_retention")
    result = logs_service.check_log_retention()
    logger.info(
        "tool_result check_log_retention flagged_count=%d total_checked=%d",
        result.flagged_count,
        result.total_log_groups_checked,
    )
    return result.model_dump_json()
