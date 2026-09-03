"""Tier 3 waste-check endpoints (roadmap phase 2 Section 1, "Batch A" +
"Batch B").

Batch A: four checks that don't fit check_idle/estimate_cost's
per-resource-ID shape at all (log retention, S3 lifecycle/multipart/
versioning, snapshot sprawl -- all findings-list tools, see the
data-schema skill), or that were deliberately kept as standalone functions
rather than folded into the existing 15-type resource_type dispatcher
(VPC Interface Endpoints -- see vpc_endpoint_service.py's module docstring
for why).

Batch B (roadmap phase 2 Section 1.2/1.3): ECS container idle/rightsizing
(per-cluster, looped over every cluster the same way /waste/s3 loops over
every bucket), Savings Plan/Reserved Instance commitment analysis
(genuinely account-level, no loop), and Compute Optimizer rightsizing
(per resource_type, required query param -- same "no universal default"
shape as /waste/snapshots' retention_days_or_count).

Each route is a thin wrapper over app/services, same "dashboard is just
another caller of services/" pattern as every other route file in this app.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Query

from app.models.commitment import CommitmentAnalysisReport
from app.models.compute_optimizer import RightsizingReport
from app.models.ecs import EcsContainerIdleReport
from app.models.logs import LogRetentionReport
from app.models.s3_waste import S3WasteReport
from app.models.snapshot import SnapshotSprawlReport
from app.models.vpc_endpoint import VpcEndpointWasteEntry, VpcEndpointWasteReport
from app.services import (
    commitment_service,
    compute_optimizer_service,
    ecs_service,
    logs_service,
    s3_service,
    snapshot_service,
    vpc_endpoint_service,
)

logger = logging.getLogger("app.api.waste")

router = APIRouter()


@router.get("/waste/log-retention", response_model=LogRetentionReport)
async def get_log_retention() -> LogRetentionReport:
    """CloudWatch Log groups with no retention policy set (roadmap phase 2
    Section 1.3) -- a free/cheap DescribeLogGroups-only check."""
    return logs_service.check_log_retention()


@router.get("/waste/s3", response_model=list[S3WasteReport])
async def get_s3_waste(
    days: int = Query(
        7, description="Age threshold (days) for the incomplete-multipart-upload sub-check."
    ),
) -> list[S3WasteReport]:
    """S3 lifecycle/multipart/versioning waste findings for every bucket in
    the account (roadmap phase 2 Section 1.2). One bucket's lookup failing
    is logged and skipped rather than blanking the rest of the response --
    same graceful-degradation principle as scan_region's per-resource
    nullable cost/idle fields."""
    buckets = s3_service.list_buckets().buckets
    reports: list[S3WasteReport] = []
    for bucket in buckets:
        try:
            reports.append(s3_service.check_s3_waste(bucket.name, days=days))
        except Exception:  # noqa: BLE001 - one bucket failing must not blank the rest
            logger.warning("s3 waste check failed for bucket=%s", bucket.name, exc_info=True)
    return reports


@router.get("/waste/snapshots", response_model=SnapshotSprawlReport)
async def get_snapshot_sprawl(
    resource_type: str = Query(..., description="'ebs' or 'rds'."),
    retention_days_or_count: int = Query(
        ..., description="Caller-set retention threshold -- required, no default."
    ),
    retention_mode: str = Query("days", description="'days' or 'count'."),
) -> SnapshotSprawlReport:
    """EBS/RDS snapshot sprawl (roadmap phase 2 Section 1.3) -- orphaned
    snapshots and snapshots beyond the caller-supplied retention threshold.
    retention_days_or_count has no default here on purpose: there is no
    universal 'correct' retention count/age."""
    try:
        return snapshot_service.check_snapshot_sprawl(
            resource_type, retention_days_or_count, retention_mode=retention_mode
        )
    except snapshot_service.UnsupportedSnapshotResourceTypeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/waste/vpc-endpoints", response_model=VpcEndpointWasteReport)
async def get_vpc_endpoint_waste(
    days: int = Query(7, description="Idle-window size in days."),
) -> VpcEndpointWasteReport:
    """Idle/cost status for every VPC Interface Endpoint in the configured
    region (roadmap phase 2 Section 1.1). Gateway/GatewayLoadBalancer
    endpoints are skipped -- they have no hourly charge, so there's no
    waste signal to check. One endpoint's idle/cost lookup failing is
    logged and nulled, not dropped from the response -- same nullable-per-
    resource pattern as /resources/ec2."""
    endpoints = vpc_endpoint_service.list_vpc_endpoints().vpc_endpoints
    entries: list[VpcEndpointWasteEntry] = []
    for endpoint in endpoints:
        if endpoint.vpc_endpoint_type != vpc_endpoint_service.INTERFACE_ENDPOINT_TYPE:
            continue
        idle = None
        cost = None
        try:
            idle = vpc_endpoint_service.check_vpc_endpoint_idle(endpoint.vpc_endpoint_id, days)
        except Exception:  # noqa: BLE001 - idle data is a nice-to-have, not a hard dependency
            logger.warning(
                "vpc endpoint idle check failed for %s", endpoint.vpc_endpoint_id, exc_info=True
            )
        try:
            cost = vpc_endpoint_service.estimate_vpc_endpoint_cost(endpoint.vpc_endpoint_id)
        except Exception:  # noqa: BLE001 - cost data is a nice-to-have, not a hard dependency
            logger.warning(
                "vpc endpoint cost estimate failed for %s",
                endpoint.vpc_endpoint_id,
                exc_info=True,
            )
        entries.append(VpcEndpointWasteEntry(endpoint=endpoint, idle=idle, cost=cost))

    return VpcEndpointWasteReport(entries=entries, count=len(entries))


@router.get("/waste/ecs-container-idle", response_model=list[EcsContainerIdleReport])
async def get_ecs_container_idle(
    days: int = Query(7, description="Idle-window size in days."),
) -> list[EcsContainerIdleReport]:
    """ECS/Fargate container waste for every cluster in the configured
    region (roadmap phase 2 Section 1.2) -- task-level idle/rightsizing
    findings plus service-level standby-capacity findings. One cluster's
    lookup failing is logged and skipped rather than blanking the rest of
    the response -- same graceful-degradation principle as /waste/s3."""
    clusters = ecs_service.list_clusters().clusters
    reports: list[EcsContainerIdleReport] = []
    for cluster in clusters:
        try:
            reports.append(ecs_service.check_container_idle(cluster.cluster_name, days))
        except Exception:  # noqa: BLE001 - one cluster failing must not blank the rest
            logger.warning(
                "ecs container idle check failed for cluster=%s", cluster.cluster_name,
                exc_info=True,
            )
    return reports


@router.get("/waste/commitment-utilization", response_model=CommitmentAnalysisReport)
async def get_commitment_utilization(
    days: int = Query(30, description="Lookback window in days for the Cost Explorer query."),
) -> CommitmentAnalysisReport:
    """Savings Plan / Reserved Instance utilization and coverage analysis
    (roadmap phase 2 Section 1.3) -- account-level, not per-resource. Calls
    AWS Cost Explorer's PAID commitment APIs (see the response's
    `note`/`estimated_cost_explorer_api_cost_usd`), unlike every other
    /waste/* route, which uses the free Pricing/Describe*/List* APIs."""
    try:
        return commitment_service.analyze_commitment_utilization(days)
    except Exception as exc:  # noqa: BLE001 - surface a clean 502, don't leak a raw boto3 traceback
        logger.warning("get_commitment_utilization failed", exc_info=True)
        raise HTTPException(
            status_code=502, detail="Failed to analyze commitment utilization."
        ) from exc


@router.get("/waste/rightsizing", response_model=RightsizingReport)
async def get_rightsizing(
    resource_type: str = Query(..., description="'ec2', 'ebs', 'lambda', or 'ecs'."),
) -> RightsizingReport:
    """AWS Compute Optimizer rightsizing recommendations (roadmap phase 2
    Section 1.3) -- AWS's own ML-driven over/under-provisioning signal, not
    idle detection. Requires a one-time account-level Compute Optimizer
    opt-in; an un-enrolled account gets enrolled=false with a plain
    how-to-opt-in note, not an error."""
    try:
        return compute_optimizer_service.get_rightsizing_recommendations(resource_type)
    except compute_optimizer_service.UnsupportedRightsizingResourceTypeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001 - surface a clean 502, don't leak a raw boto3 traceback
        logger.warning(
            "get_rightsizing failed for resource_type=%s", resource_type, exc_info=True
        )
        raise HTTPException(
            status_code=502, detail="Failed to get rightsizing recommendations."
        ) from exc
