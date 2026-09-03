from __future__ import annotations

import logging
from typing import Annotated

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


@function_tool
def check_s3_waste(
    bucket: Annotated[str, "S3 bucket name to check."],
    days: Annotated[
        int, "Age threshold in days for the incomplete-multipart-upload sub-check."
    ] = 7,
) -> str:
    """Check an S3 bucket for waste: no lifecycle policy, incomplete
    multipart uploads older than `days`, versioning with no
    noncurrent-version-expiration rule. Findings list per bucket (0-3
    independent flags, never a single boolean), plus a best-effort
    storage-class price-gap note where pricing lookup succeeds. Does NOT
    flag 'no recent access' -- that needs Storage Lens/Access Logging;
    never fabricate a last-accessed claim without one."""
    logger.info("tool_call check_s3_waste bucket=%s days=%d", bucket, days)
    result = s3_service.check_s3_waste(bucket, days=days)
    logger.info(
        "tool_result check_s3_waste bucket=%s findings=%d", bucket, len(result.findings)
    )
    return result.model_dump_json()
