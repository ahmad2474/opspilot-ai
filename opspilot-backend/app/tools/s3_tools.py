from __future__ import annotations

import logging

from agents import function_tool

from app.services import s3_service

logger = logging.getLogger("app.tools.s3")


@function_tool
def list_s3_buckets() -> str:
    """List S3 buckets in the configured AWS account. Simple lookup, no
    investigation reasoning needed."""
    logger.info("tool_call list_s3_buckets")
    result = s3_service.list_buckets()
    logger.info("tool_result list_s3_buckets count=%d", result.count)
    return result.model_dump_json()
