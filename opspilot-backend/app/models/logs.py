"""Response model for check_log_retention (roadmap phase 2 Section 1.3).

Findings-list shape, not IdleCheckResult -- a log group's "no retention
policy" state is a one-time configuration fact, not a CloudWatch time
series, so there is no is_idle/idle_since/idle_days to report. See the
data-schema skill's "Findings-list tools" section.
"""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class LogGroupRetentionFinding(BaseModel):
    log_group_name: str
    stored_bytes: int = Field(
        description=(
            "storedBytes from DescribeLogGroups -- the real size of data "
            "this log group is keeping forever with no expiration, not "
            "just a boolean flag."
        )
    )
    created_at: datetime | None = Field(
        default=None, description="Log group creation time, null if AWS didn't report one."
    )


class LogRetentionReport(BaseModel):
    findings: list[LogGroupRetentionFinding] = Field(
        description=(
            "One entry per log group with NO retention policy set (retentionInDays is null)."
        )
    )
    flagged_count: int = Field(
        description="len(findings) -- log groups with no retention policy."
    )
    total_log_groups_checked: int = Field(
        description="Every log group DescribeLogGroups returned, flagged or not."
    )
    total_stored_bytes_at_risk: int = Field(
        description="Sum of stored_bytes across every flagged log group -- the real size at stake."
    )
