from __future__ import annotations

import logging

from agents import function_tool

from app.services import rds_service

logger = logging.getLogger("app.tools.rds")


@function_tool
def get_rds_status() -> str:
    """List RDS database instances in the configured AWS account, including
    each instance's status (e.g. available, stopped), engine, and instance
    class. Use this to answer questions like 'is RDS running'."""
    logger.info("tool_call get_rds_status")
    result = rds_service.list_instances()
    logger.info("tool_result get_rds_status count=%d", result.count)
    return result.model_dump_json()