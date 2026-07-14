"""Dashboard data endpoint.

Deliberately bypasses the agent/LLM entirely — it calls ec2_service and
cloudwatch_service directly, the exact same functions app/tools wraps for
the agent. That shared source of truth is what guarantees the dashboard
and the chat answer can never disagree: there's only one place CPU data
or instance state is actually computed.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Query, Response
from starlette.concurrency import run_in_threadpool

from app.models.resources import Ec2ResourceCard, ResourcesResponse
from app.models.scan import ScanResponse
from app.services import cloudwatch_service, cost_service, ec2_service, idle_service, scan_service

logger = logging.getLogger("app.api.resources")

router = APIRouter()

# Matches the galaxy UI's "pulsing amber = idle >= 7 days" threshold
# (roadmap Section 5) -- the window check_idle is run over here.
IDLE_CHECK_WINDOW_DAYS = 7


@router.get("/resources/ec2", response_model=ResourcesResponse)
async def get_ec2_resources() -> ResourcesResponse:
    instances = ec2_service.list_instances()
    logger.info("resources_ec2 instance_count=%d", instances.count)

    cards: list[Ec2ResourceCard] = []
    for instance in instances.instances:
        cpu = None
        idle = None
        cost = None
        if instance.state == "running":
            try:
                cpu = cloudwatch_service.get_cpu_utilization(instance.instance_id)
            except Exception:  # noqa: BLE001 - cpu data is a nice-to-have, not a hard dependency
                logger.warning(
                    "cpu lookup failed for %s, omitting cpu block", instance.instance_id,
                    exc_info=True,
                )
            try:
                idle = idle_service.check_idle(
                    "ec2", instance.instance_id, IDLE_CHECK_WINDOW_DAYS
                )
            except Exception:  # noqa: BLE001 - idle data is a nice-to-have, not a hard dependency
                logger.warning(
                    "idle check failed for %s, omitting idle block", instance.instance_id,
                    exc_info=True,
                )
            try:
                cost = cost_service.estimate_cost("ec2", instance.instance_id)
            except Exception:  # noqa: BLE001 - cost data is a nice-to-have, not a hard dependency
                logger.warning(
                    "cost estimate failed for %s, omitting cost block", instance.instance_id,
                    exc_info=True,
                )
        cards.append(Ec2ResourceCard(instance=instance, cpu=cpu, idle=idle, cost=cost))

    return ResourcesResponse(ec2=cards)


@router.get("/resources/regions")
async def get_available_regions() -> dict[str, list[str]]:
    """Enabled AWS regions for the region selector (roadmap 3.3) --
    backed by `ec2:DescribeRegions`, already covered by the existing
    `ec2:Describe*` read-only grant. Not cached -- this is a cheap,
    infrequent, non-billed-per-resource call, unlike the per-type scan
    itself.
    """
    try:
        regions = scan_service.list_available_regions()
    except Exception as exc:  # noqa: BLE001 - surface a clean 502, don't leak a raw boto3 traceback
        logger.warning("get_available_regions failed", exc_info=True)
        raise HTTPException(status_code=502, detail="Failed to list AWS regions.") from exc
    return {"regions": regions}


@router.get("/resources/scan", response_model=ScanResponse)
async def scan_resources(
    response: Response,
    region: str = Query(..., description="AWS region to scan, e.g. us-east-1."),
    force: bool = Query(
        False,
        description=(
            "True = explicit user-initiated refresh; rescans this region, subject to "
            "the anti-spam cooldown. False (default) = serve the cached scan if one "
            "exists, or run a first scan if this region has never been scanned."
        ),
    ),
) -> ScanResponse:
    """Region-wide scan across all 15 roadmap resource types (roadmap
    3.3/3.4) -- the data behind the galaxy view's star field + HUD totals.

    On a rejected too-soon `force=true` rescan (roadmap 3.4 debounce):
    responds 429 with a `Retry-After` header, body = the last good cached
    scan if one exists (so the caller can keep showing data while
    surfacing "refresh already in progress / try again in Ns").

    On a real AWS failure with a good cache to fall back to: responds 200
    with the stale cached payload and a non-null `error` field -- never an
    empty result (roadmap 3.4 "never blank the dashboard").

    On a real AWS failure with no prior cache at all for this region (the
    one case roadmap 3.4 says has nothing to fall back to): responds 502
    with a generic message -- the underlying AWS exception is logged
    server-side, never echoed to the caller (an AccessDenied/
    UnauthorizedOperation message routinely embeds the full IAM caller
    ARN, including the 12-digit account ID).

    On an unrecognized `region` (doesn't match any of this account's
    enabled regions): responds 400 before any cache/lock/AWS call is made
    for it (security -- see scan_service.InvalidRegionError).

    scan_service.scan_region() is a fully synchronous function that, on a
    cache miss, makes dozens of sequential boto3 calls (Describe*/List*
    across 15 resource types, plus a CloudWatch + Pricing lookup per
    resource) -- a first scan of a region has been observed taking upwards
    of two minutes end to end against a real account. Run it in FastAPI's
    threadpool (`run_in_threadpool`) rather than calling it directly:
    calling a blocking function straight from an `async def` route freezes
    the whole single-threaded event loop for the entire scan, so every
    other concurrent request on this process (including a near-duplicate
    scan request from e.g. React 18 StrictMode's double-effect-invocation
    hitting the same region, or an unrelated request to a totally
    different route) queues up behind it instead of being served
    concurrently. This doesn't make one scan faster, it just stops one
    slow scan from blocking the entire server.
    """
    # Normalized here too (not just inside scan_service) so a malformed
    # region never even reaches the service layer with different casing
    # per caller -- the actual enforcement lives in scan_service (the one
    # choke point every front door, including MCP, goes through), this is
    # defense in depth / consistency at the edge.
    region = region.strip().lower()

    try:
        result = await run_in_threadpool(scan_service.scan_region, region, force=force)
    except scan_service.InvalidRegionError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except scan_service.ScanCooldownActive as exc:
        retry_after = str(int(exc.retry_after_seconds) + 1)
        if exc.cached is not None:
            response.headers["Retry-After"] = retry_after
            response.status_code = 429
            return exc.cached
        # Raising HTTPException makes FastAPI build a brand-new response,
        # so mutating the injected `response` object here (as the
        # exc.cached branch above does) would be silently discarded --
        # the header has to be passed on the exception itself instead.
        raise HTTPException(
            status_code=429,
            detail=(
                f"A scan for region={region!r} is already in progress or ran too "
                f"recently -- retry in {exc.retry_after_seconds:.0f}s."
            ),
            headers={"Retry-After": retry_after},
        ) from exc
    except scan_service.ScanFailedNoCacheError as exc:
        logger.warning(
            "scan_resources: no cache to fall back to for region=%s", region, exc_info=exc.cause
        )
        raise HTTPException(
            status_code=502,
            detail=f"Scan failed for region={region!r} and no prior data exists yet.",
        ) from exc

    return result
