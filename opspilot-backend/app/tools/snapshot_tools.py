"""Agent-facing tool for the EBS/RDS snapshot sprawl check. Stays thin on
purpose -- all the real logic lives in app.services.snapshot_service so it
can be unit-tested without touching the LLM at all.
"""
from __future__ import annotations

import logging
from typing import Annotated, Literal

from agents import function_tool

from app.services import snapshot_service

logger = logging.getLogger("app.tools.snapshot")


@function_tool
def check_snapshot_sprawl(
    resource_type: Annotated[str, "Snapshot type to check: 'ebs' or 'rds'."],
    retention_days_or_count: Annotated[
        int,
        (
            "Retention threshold YOU MUST ASK THE USER FOR -- there is no "
            "universal 'correct' value, never assume one. Meaning depends "
            "on retention_mode."
        ),
    ],
    retention_mode: Annotated[
        Literal["days", "count"],
        (
            "'days': flag snapshots older than retention_days_or_count days. "
            "'count': keep the retention_days_or_count most-recent snapshots "
            "per source volume/instance, flag the rest."
        ),
    ] = "days",
) -> str:
    """Find EBS/RDS snapshot sprawl: orphaned snapshots (source volume/
    instance no longer exists -- pure waste, easy win) and snapshots beyond
    a caller-supplied retention threshold. Returns a findings list, not a
    single boolean -- a snapshot can appear under both finding types at
    once (orphaned AND beyond retention)."""
    logger.info(
        "tool_call check_snapshot_sprawl resource_type=%s retention=%s mode=%s",
        resource_type,
        retention_days_or_count,
        retention_mode,
    )
    result = snapshot_service.check_snapshot_sprawl(
        resource_type, retention_days_or_count, retention_mode=retention_mode
    )
    logger.info(
        "tool_result check_snapshot_sprawl orphaned=%d beyond_retention=%d",
        result.orphaned_count,
        result.beyond_retention_count,
    )
    return result.model_dump_json()
