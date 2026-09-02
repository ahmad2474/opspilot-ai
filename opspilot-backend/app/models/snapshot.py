"""Response model for check_snapshot_sprawl (roadmap phase 2 Section 1.3).

Findings-list shape, not IdleCheckResult -- a snapshot isn't "idle" in the
CloudWatch-window sense, it's either orphaned (source gone) or beyond a
caller-set retention threshold. Both are independent boolean-ish facts
about the same snapshot, so a single snapshot can show up as TWO findings
(one 'orphaned', one 'beyond_retention') rather than being forced into one
verdict.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class SnapshotFinding(BaseModel):
    resource_type: Literal["ebs", "rds"]
    snapshot_id: str
    finding_type: Literal["orphaned", "beyond_retention"] = Field(
        description=(
            "'orphaned' = the source EBS volume/RDS instance no longer "
            "exists -- pure waste, independent of any retention threshold. "
            "'beyond_retention' = older than (or past the Nth-most-recent "
            "cutoff for) the caller-supplied retention_days_or_count. A "
            "snapshot that is both shows up as two separate findings, "
            "never conflated into one."
        )
    )
    source_resource_id: str | None = Field(
        default=None,
        description=(
            "The source volume_id (ebs) or DB instance identifier (rds) "
            "the snapshot was taken from, even when that resource no "
            "longer exists -- AWS still reports it on the snapshot itself."
        ),
    )
    age_days: int
    size_gb: int | None = Field(
        default=None,
        description=(
            "VolumeSize (ebs) or AllocatedStorage (rds) in GB, null if AWS didn't report one."
        ),
    )
    message: str


class SnapshotSprawlReport(BaseModel):
    resource_type: Literal["ebs", "rds"]
    retention_days_or_count: int = Field(
        description=(
            "Caller-supplied threshold -- never a hardcoded default. There "
            "is no universal 'correct' retention count/age; the caller "
            "must set this explicitly every time."
        )
    )
    retention_mode: Literal["days", "count"] = Field(
        description=(
            "'days': beyond_retention = older than retention_days_or_count "
            "days. 'count': beyond_retention = falls past the "
            "retention_days_or_count most-recent snapshots for its source."
        )
    )
    findings: list[SnapshotFinding]
    total_snapshots_checked: int
    orphaned_count: int
    beyond_retention_count: int
