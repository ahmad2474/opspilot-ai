from __future__ import annotations

import logging

from agents import function_tool

from app.services import lambda_service

logger = logging.getLogger("app.tools.lambda")


@function_tool
def list_lambda_functions() -> str:
    """List Lambda functions in the configured AWS account. Simple
    lookup, no investigation reasoning needed."""
    logger.info("tool_call list_lambda_functions")
    result = lambda_service.list_functions()
    logger.info("tool_result list_lambda_functions count=%d", result.count)
    return result.model_dump_json()
