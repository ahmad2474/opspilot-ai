"""Snapshot sprawl waste check (roadmap phase 2 Section 1.3) -- EBS + RDS.

check_snapshot_sprawl(resource_type, retention_days_or_count) is a
findings-list tool: two independent finding types per snapshot, never
conflated (roadmap: "two distinct findings, don't conflate them"):

- 'orphaned': the source EBS volume/RDS instance no longer exists -- pure
  waste, easy win, true regardless of any retention threshold. AWS still
  reports the original VolumeId/DBInstanceIdentifier on a snapshot even
  after its source is deleted, so orphan detection is just a set-membership
  check against the current live volumes/instances -- no extra API call
  beyond what list_volumes/list_instances already make.
- 'beyond_retention': older than (mode='days') or past the Nth-most-recent
  cutoff for its source (mode='count') for a threshold the CALLER supplies.
  There is no universal "correct" retention count/age (roadmap: "this has
  to be a parameter the user sets, not a hardcoded assumption") --
  retention_days_or_count has no default here on purpose, unlike every
  check_idle branch's `days=7` default.

A snapshot can be both -- both findings are appended independently rather
than picking one verdict per snapshot.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from typing import Literal, TypedDict

from app.aws.client import get_ec2_client, get_rds_client
from app.models.snapshot import SnapshotFinding, SnapshotSprawlReport
from app.services import ebs_service, rds_service


class UnsupportedSnapshotResourceTypeError(ValueError):
    """Raised when check_snapshot_sprawl is asked about a type other than 'ebs'/'rds'."""


class _NormalizedSnapshot(TypedDict):
    id: str
    source_id: str | None
    created: datetime | None
    size_gb: int | None


def check_snapshot_sprawl(
    resource_type: str,
    retention_days_or_count: int,
    retention_mode: Literal["days", "count"] = "days",
    region: str | None = None,
) -> SnapshotSprawlReport:
    if resource_type == "ebs":
        return _check_ebs_snapshot_sprawl(retention_days_or_count, retention_mode, region)
    if resource_type == "rds":
        return _check_rds_snapshot_sprawl(retention_days_or_count, retention_mode, region)
    raise UnsupportedSnapshotResourceTypeError(
        f"check_snapshot_sprawl for resource_type={resource_type!r} is not supported -- "
        "only 'ebs' and 'rds' snapshots are in scope for this check."
    )


def _age_days(created: datetime | None, now: datetime) -> int:
    if created is None:
        return 0
    if created.tzinfo is None:
        created = created.replace(tzinfo=timezone.utc)
    return (now - created).days


def _apply_retention(
    snapshots: list[_NormalizedSnapshot],
    retention_days_or_count: int,
    retention_mode: str,
    now: datetime,
) -> set[str]:
    """Returns the set of snapshot ids that are 'beyond_retention'."""
    if retention_mode == "days":
        return {
            s["id"] for s in snapshots if _age_days(s["created"], now) > retention_days_or_count
        }

    # mode == "count": group by source, keep the N most-recent per source,
    # flag everything past that cutoff. A snapshot with no resolvable
    # source_id is grouped under its own id (nothing else can share that
    # group), so it's judged against a group of one rather than silently
    # merged with unrelated orphans.
    by_source: dict[str, list[_NormalizedSnapshot]] = defaultdict(list)
    for s in snapshots:
        by_source[s["source_id"] or s["id"]].append(s)

    beyond: set[str] = set()
    for group in by_source.values():
        ordered = sorted(group, key=lambda s: s["created"] or now, reverse=True)
        for s in ordered[retention_days_or_count:]:
            beyond.add(s["id"])
    return beyond


def _retention_message(resource_type: str, snapshot_id: str, threshold: int, mode: str) -> str:
    if mode == "days":
        return (
            f"{resource_type.upper()} snapshot {snapshot_id} is older than the "
            f"{threshold}-day retention threshold you set."
        )
    return (
        f"{resource_type.upper()} snapshot {snapshot_id} falls past the "
        f"{threshold}-most-recent-per-source retention threshold you set."
    )


def _build_report(
    resource_type: Literal["ebs", "rds"],
    normalized: list[_NormalizedSnapshot],
    existing_source_ids: set[str],
    retention_days_or_count: int,
    retention_mode: str,
    orphan_message: str,
) -> SnapshotSprawlReport:
    now = datetime.now(timezone.utc)
    beyond_ids = _apply_retention(normalized, retention_days_or_count, retention_mode, now)

    findings: list[SnapshotFinding] = []
    orphaned_count = 0
    for s in normalized:
        is_orphaned = bool(s["source_id"]) and s["source_id"] not in existing_source_ids
        if is_orphaned:
            orphaned_count += 1
            findings.append(
                SnapshotFinding(
                    resource_type=resource_type,
                    snapshot_id=s["id"],
                    finding_type="orphaned",
                    source_resource_id=s["source_id"],
                    age_days=_age_days(s["created"], now),
                    size_gb=s["size_gb"],
                    message=orphan_message.format(source_id=s["source_id"], snapshot_id=s["id"]),
                )
            )
        if s["id"] in beyond_ids:
            findings.append(
                SnapshotFinding(
                    resource_type=resource_type,
                    snapshot_id=s["id"],
                    finding_type="beyond_retention",
                    source_resource_id=s["source_id"],
                    age_days=_age_days(s["created"], now),
                    size_gb=s["size_gb"],
                    message=_retention_message(
                        resource_type, s["id"], retention_days_or_count, retention_mode
                    ),
                )
            )

    return SnapshotSprawlReport(
        resource_type=resource_type,
        retention_days_or_count=retention_days_or_count,
        retention_mode=retention_mode,
        findings=findings,
        total_snapshots_checked=len(normalized),
        orphaned_count=orphaned_count,
        beyond_retention_count=len(beyond_ids),
    )


def _check_ebs_snapshot_sprawl(
    retention_days_or_count: int, retention_mode: str, region: str | None
) -> SnapshotSprawlReport:
    client = get_ec2_client(region=region)
    paginator = client.get_paginator("describe_snapshots")
    raw_snapshots: list[dict] = []
    for page in paginator.paginate(OwnerIds=["self"]):
        raw_snapshots.extend(page.get("Snapshots", []))

    existing_volume_ids = {v.volume_id for v in ebs_service.list_volumes(region=region).volumes}

    normalized: list[_NormalizedSnapshot] = [
        {
            "id": raw["SnapshotId"],
            "source_id": raw.get("VolumeId"),
            "created": raw.get("StartTime"),
            "size_gb": raw.get("VolumeSize"),
        }
        for raw in raw_snapshots
    ]

    return _build_report(
        "ebs",
        normalized,
        existing_volume_ids,
        retention_days_or_count,
        retention_mode,
        orphan_message=(
            "Source EBS volume {source_id} no longer exists -- snapshot "
            "{snapshot_id} is pure orphaned storage."
        ),
    )


def _check_rds_snapshot_sprawl(
    retention_days_or_count: int, retention_mode: str, region: str | None
) -> SnapshotSprawlReport:
    client = get_rds_client(region=region)
    paginator = client.get_paginator("describe_db_snapshots")
    raw_snapshots: list[dict] = []
    for page in paginator.paginate():
        raw_snapshots.extend(page.get("DBSnapshots", []))

    existing_instance_ids = {
        i.identifier for i in rds_service.list_instances(region=region).instances
    }

    normalized: list[_NormalizedSnapshot] = [
        {
            "id": raw["DBSnapshotIdentifier"],
            "source_id": raw.get("DBInstanceIdentifier"),
            "created": raw.get("SnapshotCreateTime"),
            "size_gb": raw.get("AllocatedStorage"),
        }
        for raw in raw_snapshots
    ]

    return _build_report(
        "rds",
        normalized,
        existing_instance_ids,
        retention_days_or_count,
        retention_mode,
        orphan_message=(
            "Source RDS instance {source_id} no longer exists -- snapshot "
            "{snapshot_id} is pure orphaned storage (manual snapshots are "
            "never auto-deleted with their source)."
        ),
    )
