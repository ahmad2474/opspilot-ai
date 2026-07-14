"""Business logic for roadmap Section 3.8's chat capability tools:
list_resources, get_resource_health, get_resource_age.
(estimate_instance_cost lives in cost_service.py -- it's a direct variant
of that module's existing EC2 Pricing API helper, not new business logic
of its own.)

Layering: composes scan_service (identity/status shaping + the cheap
"lite" listing it added for this step) and idle_service (the recent-
activity CloudWatch signal get_resource_health reuses) rather than
talking to app/aws/ directly or re-deriving any per-type dimension/
threshold logic that already lives in idle_service/cost_service/
scan_service -- same "services/ calls other services/" precedent
scan_service itself already set by composing idle_service + cost_service
for the region-wide scan.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from app.core.config import get_settings
from app.models.resource_query import ResourceAgeResult, ResourceHealthResult, ResourceListResponse
from app.models.scan import TYPE_CODES
from app.services import ec2_service, idle_service, scan_service

logger = logging.getLogger("app.services.resource_query")

# Short lookback used only by get_resource_health's "is this alive right
# now" signal -- deliberately NOT the same as check_idle's caller-supplied
# `days` (that's for "has this been idle/wasteful for a while"). A 1-day
# window is enough to answer "any activity at all very recently" without
# being conflated with the longer-window idle-waste concept.
HEALTH_RECENT_ACTIVITY_WINDOW_DAYS = 1

# Resource types that expose no creation timestamp at all through any AWS
# API call this app makes for them (see idle_service.py's per-type
# docstrings: LambdaFunctionSummary, CloudFrontDistribution, and EIP's
# DescribeAddresses response all have no creation/allocation timestamp
# field whatsoever). OpenSearch is deliberately NOT in this set -- its
# domain.created_at is usually populated and only legitimately null while
# a domain is still being created, a per-resource state rather than a
# type-wide API gap, so it is reported per-resource below instead.
_NO_TIMESTAMP_TYPES = {"eip", "lambda", "cloudfront"}


class UnsupportedResourceTypeError(ValueError):
    """Raised when a resource_type outside the 15 roadmap-scoped types is
    requested -- same per-module pattern as idle_service's/cost_service's/
    scan_service's own UnsupportedResourceTypeError."""


def _require_supported_type(resource_type: str) -> None:
    if resource_type not in TYPE_CODES:
        raise UnsupportedResourceTypeError(
            f"resource_type={resource_type!r} is not supported -- only "
            f"{sorted(TYPE_CODES)!r} (the roadmap's 15 in-scope types) are tracked "
            "by this app."
        )


def _resolve_region(region: str | None) -> str:
    resolved = region or get_settings().aws_region
    return resolved.strip().lower()


def list_resources(filters: dict | None = None) -> ResourceListResponse:
    """Full inventory (or a filtered subset) for roadmap 3.8's count/list
    chat queries.

    filters (all optional):
      - "region": AWS region, e.g. "us-east-1". Defaults to the
        configured account default region -- same single-region-at-a-time
        model scan_region/the rest of this app already uses; this does
        NOT loop across every enabled region on one call.
      - "type" or "types": a single type code (str) or list of type codes
        to restrict to. Omit for all 15 tracked types.
      - "status": exact-match (case-insensitive) filter on each
        resource's lifecycle status/state (e.g. "running", "stopped",
        "available", "associated").

    Speed/architecture (roadmap 3.8: counting/listing must not pay for a
    CloudWatch/Pricing lookup per resource): if scan_region() has already
    been run and cached for the requested region, this reuses that cached
    result for free (real idle/cost data included, no new AWS calls at
    all -- reading a dict is free, same principle scan_service's own
    cache-hit path relies on). Otherwise it falls back to
    scan_service.list_lite_resources(), which only makes the cheap
    Describe*/List* calls, never CloudWatch/Pricing -- idle_count/
    not_idle_count are then null (idle_data_source="unavailable") since
    there is no real idle data to report without paying for it; by_status
    (built from cheap lifecycle status alone) is always populated
    regardless of which path was used.
    """
    filters = filters or {}
    region = _resolve_region(filters.get("region"))

    type_filter = filters.get("type") or filters.get("types")
    if type_filter is None:
        type_codes: list[str] | None = None
    elif isinstance(type_filter, str):
        type_codes = [type_filter]
    else:
        type_codes = list(type_filter)
    if type_codes:
        for type_code in type_codes:
            _require_supported_type(type_code)

    status_filter = filters.get("status")

    cached = scan_service.get_cached_scan(region)
    if cached is not None:
        resources = [r for r in cached.resources if type_codes is None or r.type in type_codes]
        idle_data_source = "cached_scan"
        cache_last_updated = cached.last_updated
    else:
        resources = scan_service.list_lite_resources(region, type_codes)
        idle_data_source = "unavailable"
        cache_last_updated = None

    if status_filter:
        resources = [
            r for r in resources if r.health.status.lower() == status_filter.strip().lower()
        ]

    # Grouped by type, alphabetical by name within each group -- roadmap
    # 3.8's list-query formatting rule, done here once so every caller
    # (dashboard, MCP, chat agent) gets it for free rather than each
    # re-sorting the same way independently.
    resources = sorted(resources, key=lambda r: (r.type, r.name.lower()))

    by_type: dict[str, int] = {}
    by_status: dict[str, int] = {}
    for r in resources:
        by_type[r.type] = by_type.get(r.type, 0) + 1
        by_status[r.health.status] = by_status.get(r.health.status, 0) + 1

    idle_count: int | None = None
    not_idle_count: int | None = None
    if idle_data_source == "cached_scan":
        known_idle = [r for r in resources if r.idle is not None]
        if resources and len(known_idle) == len(resources):
            idle_count = sum(1 for r in known_idle if r.idle.is_idle)
            not_idle_count = len(resources) - idle_count
        elif not resources:
            idle_count = 0
            not_idle_count = 0
        # else: a partial mix of known/unknown idle status among the
        # returned resources (some per-resource idle lookups failed
        # during the cached scan) -- left null rather than silently
        # undercounting/mislabeling an unknown resource as "not idle".

    return ResourceListResponse(
        region=region,
        resources=resources,
        count=len(resources),
        by_type=by_type,
        by_status=by_status,
        idle_count=idle_count,
        not_idle_count=not_idle_count,
        idle_data_source=idle_data_source,
        cache_last_updated=cache_last_updated,
    )


def get_resource_health(
    resource_type: str, resource_id: str, region: str | None = None
) -> ResourceHealthResult:
    """Status/health signals for a single resource (roadmap 3.8).

    Reuses scan_service.get_lite_resource for identity/lifecycle status,
    idle_service.check_idle over a short HEALTH_RECENT_ACTIVITY_WINDOW_DAYS
    window as a live "is this showing any activity right now" signal
    (distinct from a longer idle-waste check), and -- for EC2 only, since
    AWS exposes no equivalent status-check API for the other 14 types --
    ec2_service.get_status_check's instance/system status checks and any
    scheduled maintenance events.
    """
    _require_supported_type(resource_type)
    resolved_region = _resolve_region(region)

    lite = scan_service.get_lite_resource(resource_type, resource_id, region=resolved_region)
    if lite is None:
        return ResourceHealthResult(
            resource_id=resource_id,
            resource_type=resource_type,
            region=resolved_region,
            found=False,
        )

    ec2_status_check = None
    if resource_type == "ec2":
        try:
            ec2_status_check = ec2_service.get_status_check(resource_id)
        except Exception:  # noqa: BLE001 - a nice-to-have signal, not a hard dependency
            logger.warning(
                "get_resource_health: EC2 status check failed for %s", resource_id, exc_info=True
            )

    recent_activity_idle = None
    try:
        idle_result = idle_service.check_idle(
            resource_type,
            resource_id,
            HEALTH_RECENT_ACTIVITY_WINDOW_DAYS,
            region=resolved_region,
        )
        recent_activity_idle = idle_result.is_idle
    except Exception:  # noqa: BLE001 - a nice-to-have signal, not a hard dependency
        logger.warning(
            "get_resource_health: recent-activity check failed for %s", resource_id, exc_info=True
        )

    return ResourceHealthResult(
        resource_id=resource_id,
        resource_type=resource_type,
        region=resolved_region,
        found=True,
        name=lite.name,
        status=lite.health.status,
        primary_metric=lite.health.primary_metric,
        recent_activity_idle=recent_activity_idle,
        recent_activity_window_days=HEALTH_RECENT_ACTIVITY_WINDOW_DAYS,
        ec2_status_check=ec2_status_check,
    )


def get_resource_age(
    resource_type: str, resource_id: str, region: str | None = None
) -> ResourceAgeResult:
    """Age in days for a single resource, from its creation/launch
    timestamp (roadmap 3.8). Honest about the types with no creation
    timestamp at all (EIP, Lambda, CloudFront) or sometimes-null
    (OpenSearch, while a domain is still being created) -- age_is_known
    is False and reason explains why, rather than fabricating an age or
    silently defaulting to 0.
    """
    _require_supported_type(resource_type)
    resolved_region = _resolve_region(region)

    lite = scan_service.get_lite_resource(resource_type, resource_id, region=resolved_region)
    if lite is None:
        return ResourceAgeResult(
            resource_id=resource_id,
            resource_type=resource_type,
            region=resolved_region,
            found=False,
            age_is_known=False,
            reason="Resource not found.",
        )

    created_at = lite.created_at
    if created_at is None:
        if resource_type in _NO_TIMESTAMP_TYPES:
            reason = (
                f"{resource_type} resources expose no creation timestamp through the AWS "
                "API this app calls for this type -- age cannot be determined."
            )
        else:
            reason = (
                "No creation timestamp is currently available for this resource "
                "(e.g. an OpenSearch domain still being created)."
            )
        return ResourceAgeResult(
            resource_id=resource_id,
            resource_type=resource_type,
            region=resolved_region,
            found=True,
            name=lite.name,
            created_at=None,
            age_days=None,
            age_is_known=False,
            reason=reason,
        )

    now = datetime.now(timezone.utc)
    age_days = (now - created_at).days
    return ResourceAgeResult(
        resource_id=resource_id,
        resource_type=resource_type,
        region=resolved_region,
        found=True,
        name=lite.name,
        created_at=created_at,
        age_days=age_days,
        age_is_known=True,
        reason=None,
    )
