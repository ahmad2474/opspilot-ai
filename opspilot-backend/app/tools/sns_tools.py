from __future__ import annotations

import logging

from agents import function_tool

from app.services import sns_service

logger = logging.getLogger("app.tools.sns")


@function_tool
def list_sns_topics() -> str:
    """List SNS topics in the configured AWS account. Simple lookup, no
    investigation reasoning needed."""
    logger.info("tool_call list_sns_topics")
    result = sns_service.list_topics()
    logger.info("tool_result list_sns_topics count=%d", result.count)
    return result.model_dump_json()
