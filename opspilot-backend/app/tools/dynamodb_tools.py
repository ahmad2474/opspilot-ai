from __future__ import annotations

import logging

from agents import function_tool

from app.services import dynamodb_service

logger = logging.getLogger("app.tools.dynamodb")


@function_tool
def list_dynamodb_tables() -> str:
    """List DynamoDB tables in the configured AWS account, including each
    table's status and item count. Simple lookup, no investigation
    reasoning needed."""
    logger.info("tool_call list_dynamodb_tables")
    result = dynamodb_service.list_tables()
    logger.info("tool_result list_dynamodb_tables count=%d", result.count)
    return result.model_dump_json()
