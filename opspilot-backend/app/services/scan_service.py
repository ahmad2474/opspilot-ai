"""Region-wide scanning (roadmap Section 3.3/3.4).

This is where the top-level "scan response" shape (region, last_updated,
resources[], totals) gets built for the first time -- everything below it
(the per-resource idle/cost blocks) is Steps 2-3's already-tested
idle_service/cost_service, unchanged in behavior, just called in a loop
across all 15 resource types for one region at a time.

Layering: this module calls services/ (idle_service, cost_service, and
each resource type's list_*()) exactly like every dashboard route and MCP
tool already does -- no boto3 here, no new AWS calls beyond what
Steps 2-3 already make per resource.

Caching: an in-process, module-level dict keyed by region, no external
cache/Redis -- reasonable at this scale (roadmap: "no background polling
... cache the last successful scan per region"). This means the cache is
per-process and resets on restart/redeploy; acceptable for a single-admin,
single-account demo-scope app (same scope decision as Section 2 of the
roadmap), and is a documented assumption worth revisiting if this is ever
run behind more than one worker process (a multi-worker deployment would
need a shared cache for the cache to mean the same thing across workers).

Debounce/cooldown: a plain (non-forced) request always serves the cache if
one exists -- reading a dict is free, no AWS calls, no cooldown needed.
Only force=True (the user's explicit "Refresh" click) *against a region
that already has a cache* is subject to the cooldown window below, since
that is the action the roadmap's debounce note is protecting against
(stop accidental over-calling of billed APIs from a UI refresh button
being clicked repeatedly) -- rejecting that request with a 429 is safe
because the caller still has a cache to fall back to. A region with no
cache yet at all is a different case: rejecting it would leave the caller
with nothing, so instead a second concurrent request for a never-cached
region blocks and waits for whichever request got there first, then
reuses its result -- never rejected, regardless of force. ScanCooldownActive
(and therefore HTTP 429 at the route layer) can only ever be raised when a
cache already exists for the region in question.
"""
from __future__ import annotations

import logging
import threading
from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from concurrent.futures import TimeoutError as FutureTimeoutError
from datetime import datetime, timezone

from app.core.config import get_settings
from app.models.cost import CostEstimate
from app.models.idle import IdleCheckResult
from app.models.scan import (
    TYPE_CODES,
    GalaxyResource,
    RelationLink,
    ResourceHealth,
    ScanResponse,
    ScanTotals,
)
from app.services import (
    api_gateway_service,
    cloudfront_service,
    cost_service,
    dynamodb_service,
    ebs_service,
    ec2_service,
    eip_service,
    elasticache_service,
    elb_service,
    idle_service,
    kinesis_service,
    lambda_service,
    nat_gateway_service,
    opensearch_service,
    rds_service,
    redshift_service,
    sagemaker_service,
)

logger = logging.getLogger("app.services.scan")

IDLE_CHECK_WINDOW_DAYS = 7

COOLDOWN_SECONDS = 45

# Bounds how many of the 15 per-type collectors (_run_scan/
# list_lite_resources, via _run_collectors_concurrently below) are allowed
# to run at once inside one region scan. Each of the 15 talks to a
# *different* AWS service API (EC2, RDS, Lambda, Redshift, ...), so this
# isn't protecting any single service's own throttling the way a
# per-service retry budget would -- there's no one API 15 threads could
# hammer at once. It's instead a general "don't open more concurrent
# boto3/HTTP connections than the wall-clock win actually needs" bound: a
# live scan of this account showed the slowest 1-2 collectors (historically
# Redshift/Kinesis, which fail slow via AWS-side opt-in checks) dominate
# total time regardless of how parallel the rest are, so going all the way
# to 15-wide buys little beyond ~6-8-wide while adding more simultaneous
# open connections/threads for no real benefit. 6 sits in the middle of
# that 5-8 range: enough to collapse the old 15x-sequential wait into
# roughly 2-3 sequential "waves," without firing every collector at AWS in
# one burst.
_SCAN_MAX_WORKERS = 6

# Safety-valve backstop only -- NOT the mechanism that decides
# success/failure for a normal (even a slow, 130s+) scan. A second
# (waiting) caller for a never-cached region blocks on the first caller's
# actual Future (see _in_flight_scans below) and gets released the moment
# that real scan finishes, however long it takes. This timeout only fires
# if the in-flight scan is genuinely wedged (e.g. an AWS call hanging
# forever with no timeout of its own) -- deliberately generous so it is
# never mistaken for, or raced against, how long a real scan takes.
_NO_CACHE_WEDGED_SCAN_TIMEOUT_SECONDS = 600

# How long the enabled-region allowlist (used to validate/reject a
# `region` argument before it ever reaches the cache/lock/AWS-call
# machinery below -- see _validate_region's docstring) is trusted before
# re-fetching via ec2:DescribeRegions. DescribeRegions itself is a free,
# unbilled EC2 API call (unlike the per-resource GetMetricStatistics/
# GetProducts calls the cooldown exists to protect), so this TTL isn't
# about cost -- it's so a cache HIT (the common case, "reading a dict is
# free") doesn't pay for a live network round trip on every single call.
# 5 minutes is short enough that a newly-enabled region shows up within a
# session, long enough to keep validation effectively free in practice.
_VALID_REGIONS_TTL_SECONDS = 300

_PRIMARY_METRIC = {
    "ec2": "cpu_percent",
    "ebs": "volume_io_ops",
    "rds": "database_connections",
    "eip": "association_state",
    "elb": "request_count",
    "lambda": "invocation_count",
    "nat_gateway": "bytes_in_out",
    "dynamodb": "consumed_capacity",
    "elasticache": "curr_connections",
    "sagemaker": "invocation_count",
    "redshift": "database_connections",
    "api_gateway": "request_count",
    "cloudfront": "request_count",
    "opensearch": "search_index_rate",
    "kinesis": "incoming_records",
}


class ScanCooldownActive(Exception):
    """Raised when a force=True rescan is rejected because one already ran
    (or is running) for this region too recently (roadmap 3.4 debounce).
    cached is the last good scan for this region, if any -- the caller
    (the /resources/scan route) serves it back with a 429 + Retry-After
    rather than silently no-op'ing.
    """

    def __init__(self, region: str, retry_after_seconds: float, cached: ScanResponse | None):
        self.region = region
        self.retry_after_seconds = max(0.0, retry_after_seconds)
        self.cached = cached
        super().__init__(
            f"scan cooldown active for region={region!r}, retry in "
            f"{self.retry_after_seconds:.0f}s"
        )


class ScanFailedNoCacheError(Exception):
    """Raised only when a region has never had a successful scan at all
    AND the current attempt also failed -- the one case roadmap 3.4 calls
    out where there is genuinely nothing to fall back to.
    """

    def __init__(self, region: str, cause: Exception):
        self.region = region
        self.cause = cause
        super().__init__(f"scan failed for region={region!r} and no prior cache exists: {cause}")


class InvalidRegionError(ValueError):
    """Raised when scan_region() is asked to scan a string that doesn't
    match any of this account's enabled regions (security: an
    unvalidated, un-normalized region string used directly as a cache/
    lock key means "us-east-1", "US-EAST-1", and "us-east-1 " each get
    treated as a brand-new never-scanned region -- every distinct string
    variant bypasses the cooldown entirely on first hit, since cooldown
    only fires for a region that already has a cache entry, and the
    cache/lock dicts grow unbounded on arbitrary input). Raised before
    any cache lookup, lock acquisition, or per-resource AWS call --
    the route layer turns this into a 400, not a 502/429.
    """

    def __init__(self, region: str, valid_regions: list[str]):
        self.region = region
        self.valid_regions = valid_regions
        super().__init__(
            f"region={region!r} is not an enabled AWS region for this account "
            f"(expected one of {valid_regions!r})"
        )


_cache: dict[str, ScanResponse] = {}
_last_scan_attempt: dict[str, datetime] = {}
_region_locks: dict[str, threading.Lock] = {}
_region_locks_guard = threading.Lock()

# Tracks the one real scan currently in flight for a never-cached region,
# keyed by region -- the fix for the design flaw where a second caller
# used to race a fixed timeout unrelated to how long the real scan took
# (see scan_region()'s no-cache branch). The first caller in atomically
# creates the Future (becoming the "winner": it performs the real scan and
# resolves the Future with the result/exception when done); every other
# concurrent caller for that same region ("losers") finds the Future
# already there and just awaits it, so they get the *same* outcome the
# winner produces, not an independent decision. Removed once the winner
# finishes so the next never-cached scan of that region starts clean.
_in_flight_scans: dict[str, Future] = {}
_in_flight_scans_guard = threading.Lock()

_valid_regions_cache: list[str] | None = None
_valid_regions_cache_at: datetime | None = None
# Dedicated (not _region_locks_guard) so a live DescribeRegions call on a
# cache miss/expiry can't block every other thread's _region_lock()
# lookup -- that guard is taken briefly (get-or-create a dict entry) on
# every scan_region() call, while this one can be held for the duration
# of a real network round trip; sharing it would serialize unrelated
# regions' scans behind a single allowlist refresh.
_valid_regions_guard = threading.Lock()


def _region_lock(region: str) -> threading.Lock:
    with _region_locks_guard:
        lock = _region_locks.get(region)
        if lock is None:
            lock = threading.Lock()
            _region_locks[region] = lock
        return lock


def _get_or_create_in_flight_future(region: str) -> tuple[Future, bool]:
    """Atomically get the in-flight Future for `region` if one already
    exists (this caller is a "loser" -- just wait on it), or create and
    register a new one (this caller is the "winner" -- it must actually
    run the scan and resolve the Future itself). Returns
    (future, is_winner).
    """
    with _in_flight_scans_guard:
        future = _in_flight_scans.get(region)
        if future is not None:
            return future, False
        future = Future()
        _in_flight_scans[region] = future
        return future, True


def get_cached_scan(region: str) -> ScanResponse | None:
    """Read-only cache lookup, no scan triggered."""
    return _cache.get(region)


def list_available_regions() -> list[str]:
    """Enabled AWS regions for the region selector (roadmap 3.3) -- thin
    passthrough to ec2_service.list_region_names()."""
    return ec2_service.list_region_names()


def _normalize_region(region: str) -> str:
    """Canonical form used for cache/lock keys and allowlist comparison --
    AWS region codes are already lowercase (e.g. "us-east-1"), so this
    just strips incidental whitespace and folds case, closing off
    "us-east-1" vs "US-EAST-1" vs "us-east-1 " each being treated as a
    distinct, never-cached region (security finding: defeats the
    cooldown and grows the cache/lock dicts unbounded on arbitrary
    input)."""
    return region.strip().lower()


def _get_valid_regions() -> list[str]:
    """The enabled-region allowlist, short-TTL-cached (see
    _VALID_REGIONS_TTL_SECONDS) so validating against it doesn't cost a
    live AWS round trip on every single scan_region() call, including
    cache hits. On a refresh failure, falls back to the last known-good
    list rather than blocking validation entirely -- same "stale beats
    blank" principle as the per-region scan cache. Only propagates the
    failure if there has never been a successful fetch at all (nothing to
    fall back to).

    Guarded by _valid_regions_guard (a dedicated lock -- see its
    definition above for why it isn't _region_locks_guard) -- with
    scan_region() now running on real threadpool threads (not just the
    single event-loop thread), multiple threads can call this
    concurrently and race the read/refresh of _valid_regions_cache/
    _valid_regions_cache_at. Low severity (DescribeRegions is free/
    unbilled, worst case without the lock is a redundant network call,
    not a cooldown/billing bypass) but a real, newly-reachable
    thread-safety gap, so it gets the same lock discipline as the rest of
    this file's module-level state.
    """
    global _valid_regions_cache, _valid_regions_cache_at
    with _valid_regions_guard:
        now = datetime.now(timezone.utc)
        if (
            _valid_regions_cache is not None
            and _valid_regions_cache_at is not None
            and (now - _valid_regions_cache_at).total_seconds() < _VALID_REGIONS_TTL_SECONDS
        ):
            return _valid_regions_cache

        try:
            fresh = [_normalize_region(r) for r in ec2_service.list_region_names()]
        except Exception:
            if _valid_regions_cache is not None:
                logger.warning(
                    "scan_region: refreshing the enabled-region allowlist failed, "
                    "validating against the last known list instead",
                    exc_info=True,
                )
                return _valid_regions_cache
            raise

        _valid_regions_cache = fresh
        _valid_regions_cache_at = now
        return fresh


def _validate_region(region: str) -> str:
    """Normalizes and validates `region` against the enabled-region
    allowlist -- called first thing in scan_region(), before any cache
    lookup, lock acquisition, or per-resource AWS call (security finding:
    an unvalidated region string must never reach the cache/lock/AWS-call
    machinery). Returns the normalized region on success; raises
    InvalidRegionError otherwise.
    """
    normalized = _normalize_region(region)
    valid_regions = _get_valid_regions()
    if normalized not in valid_regions:
        raise InvalidRegionError(region, valid_regions)
    return normalized


def scan_region(region: str, force: bool = False) -> ScanResponse:
    """Scan one region across all 15 resource types.

    force=False (plain load / tab switch): serve the cache if one exists
    -- no AWS calls, no cooldown check.

    force=True (explicit "Refresh" click) *with a cache already present*:
    subject to COOLDOWN_SECONDS since the last scan attempt (successful or
    not) for this region -- raises ScanCooldownActive if too soon, or if
    another request is already scanning this region right now, carrying
    the still-good cached payload for the caller to serve back with a
    429. Rejecting is only ever safe here because a cache exists to fall
    back to.

    No cache yet at all for this region (regardless of force): this
    request (or the very first of several racing requests) has to
    actually produce data -- there is nothing to reject back to. If
    another concurrent request is already scanning this same never-cached
    region (two tabs both loading it for the first time), this call
    blocks and waits for that scan to finish and reuses its result,
    rather than rejecting with a 429 that would leave the caller with
    nothing to show. ScanCooldownActive (and therefore HTTP 429) can only
    ever be raised when a cache already exists.

    On an AWS failure during a real scan attempt: falls back to the last
    good cache for this region (with error set, last_updated untouched)
    rather than raising -- never blank the dashboard (roadmap 3.4).
    Raises ScanFailedNoCacheError only if there is no prior cache to fall
    back to at all (including the "waited for an in-flight scan and it
    still didn't produce anything" case above).

    Raises InvalidRegionError immediately -- before any cache lookup, lock
    acquisition, or AWS call -- if `region` doesn't normalize to one of
    this account's enabled regions (security: closes off an unvalidated
    region string being used directly as a cache/lock key, which would
    let e.g. "us-east-1"/"US-EAST-1"/"us-east-1 " each bypass the
    cooldown as a "new" region and grow the cache/lock dicts unbounded).
    """
    region = _validate_region(region)

    cached = _cache.get(region)

    if not force and cached is not None:
        return cached

    if cached is None:
        # Nothing to fall back to yet -- a second concurrent caller must
        # get the *same result* the first caller (the "winner") actually
        # produces, not lose a race against a fixed timeout unrelated to
        # how long the real scan takes (roadmap 3.4: 429/failure must
        # never happen on a request with nothing else to show, and a
        # genuinely slow-but-successful scan must not be punished either).
        future, is_winner = _get_or_create_in_flight_future(region)

        if not is_winner:
            try:
                return future.result(timeout=_NO_CACHE_WEDGED_SCAN_TIMEOUT_SECONDS)
            except FutureTimeoutError:
                # The winner's scan hasn't resolved even within the
                # generous wedged-scan backstop -- one last check in case
                # it finished between the timeout firing and this line,
                # otherwise there is genuinely nothing to serve.
                still_cached = _cache.get(region)
                if still_cached is not None:
                    return still_cached
                raise ScanFailedNoCacheError(
                    region,
                    TimeoutError(
                        f"timed out after {_NO_CACHE_WEDGED_SCAN_TIMEOUT_SECONDS}s waiting "
                        "for an in-flight scan of this region to finish -- it appears wedged"
                    ),
                ) from None

        # is_winner: this call is the one that actually performs the
        # scan; every concurrent caller found above is blocked on
        # `future` and will receive whatever this resolves it with.
        try:
            result = _do_scan(region, cached=None)
        except BaseException as exc:  # noqa: BLE001 - propagate to every waiting caller, then re-raise here too
            future.set_exception(exc)
            raise
        else:
            future.set_result(result)
            return result
        finally:
            with _in_flight_scans_guard:
                _in_flight_scans.pop(region, None)

    lock = _region_lock(region)

    # From here on, cached is guaranteed not None -- force=True was
    # requested against a region that already has a cache, so rejecting
    # (429) is safe: the caller still gets data either way.
    if force:
        last_attempt = _last_scan_attempt.get(region)
        if last_attempt is not None:
            elapsed = (datetime.now(timezone.utc) - last_attempt).total_seconds()
            if elapsed < COOLDOWN_SECONDS:
                raise ScanCooldownActive(region, COOLDOWN_SECONDS - elapsed, cached)

    acquired = lock.acquire(blocking=False)
    if not acquired:
        # Another request is scanning this region right now (two rapid
        # refresh clicks racing each other) -- coalesce instead of
        # duplicating the AWS calls.
        raise ScanCooldownActive(region, COOLDOWN_SECONDS, cached)

    try:
        return _do_scan(region, cached)
    finally:
        lock.release()


def _do_scan(region: str, cached: ScanResponse | None) -> ScanResponse:
    """Runs one real scan attempt for `region`. The caller is always the
    sole party allowed to run a scan for this region at this moment --
    either it holds `_region_lock(region)` (the force=True path) or it won
    the atomic `_get_or_create_in_flight_future` race (the no-cache path).
    Falls back to `cached` (with `error` set) on failure if there is one,
    otherwise raises ScanFailedNoCacheError.
    """
    _last_scan_attempt[region] = datetime.now(timezone.utc)
    try:
        fresh = _run_scan(region)
    except Exception as exc:  # noqa: BLE001 - a scan failing is expected/handled, not a bug
        logger.warning(
            "scan_region: full scan failed for region=%s, falling back to cache",
            region,
            exc_info=True,
        )
        if cached is None:
            raise ScanFailedNoCacheError(region, exc) from exc
        return cached.model_copy(
            update={
                "error": f"Refresh failed ({exc.__class__.__name__}); showing last good data."
            }
        )
    _cache[region] = fresh
    return fresh


def scan_region_as_dict(region: str, force: bool = False) -> dict:
    """Shared response-shaping for scan_region()'s non-HTTP front doors
    (the MCP tool and the agent chat tool) -- centralized here (rather
    than each caller duplicating its own try/except translation) so the
    two can never drift on what a cooldown or failure response looks
    like, and so both automatically inherit the same region
    normalization/validation and the same "never leak a raw AWS
    exception to the caller" behavior the HTTP route gets. `region` is
    passed through as-given; normalization happens inside scan_region()
    itself (the single choke point every front door goes through).

    Shape:
      success:  the ScanResponse's own fields, spread at the top level
                (region/last_updated/resources/totals/error).
      cooldown: {"error": "...", "cached": <ScanResponse dict or None>}
                -- the still-good cached payload is included, not just a
                boolean flag, matching the HTTP route's "429 body still
                has the stale data" behavior (roadmap 3.4: two front
                doors to one backend shouldn't mean one door withholds
                data the other one gives you).
      invalid region / no-cache failure: {"error": "<safe message>"} --
      never interpolates the underlying AWS/botocore exception (that can
      embed the IAM caller ARN / account ID); the real exception is
      logged server-side instead.
    """
    try:
        result = scan_region(region, force=force)
    except InvalidRegionError as exc:
        return {"error": str(exc)}
    except ScanCooldownActive as exc:
        return {
            "error": (
                f"scan cooldown active for region {exc.region!r}, retry in "
                f"{exc.retry_after_seconds:.0f}s"
            ),
            "cached": exc.cached.model_dump(mode="json") if exc.cached is not None else None,
        }
    except ScanFailedNoCacheError as exc:
        logger.warning(
            "scan_region_as_dict: no cache to fall back to for region=%s",
            region,
            exc_info=exc.cause,
        )
        return {
            "error": f"scan failed for region {region!r} and no prior data exists yet.",
        }
    return result.model_dump(mode="json")


def _run_collectors_concurrently(
    region: str,
    type_codes: list[str] | tuple[str, ...],
    *,
    lite: bool,
    log_prefix: str,
) -> list[GalaxyResource]:
    """Runs `_COLLECTORS[type_code](region, lite=lite)` for every
    `type_code` in `type_codes` concurrently, bounded by
    `_SCAN_MAX_WORKERS` (see its comment) -- shared by both `_run_scan`
    (roadmap 3.3/3.4, `lite=False`, the CloudWatch/Pricing-inclusive full
    scan) and `list_lite_resources` (roadmap 3.8, `lite=True`, the cheap
    identity-only listing backing the `list_resources` chat/MCP tool)
    rather than duplicating the same ThreadPoolExecutor plumbing twice for
    what is otherwise an identical per-type "call a collector, catch its
    exception, log it, contribute 0 resources for that type on failure"
    shape.

    Runs on real OS threads (not asyncio) -- every collector makes
    synchronous boto3 calls under the hood, same reason the route layer
    already offloads scan_region() to FastAPI's threadpool
    (`run_in_threadpool`, see api/routes/resources.py).

    Graceful degradation is unchanged from the old sequential loop: one
    type's collector raising is caught and logged *individually*, per
    type, and contributes 0 resources for that type only -- it never fails
    or blanks the other types' results. `log_prefix` lets each caller keep
    its own distinct log message text (`"scan_region: ..."` vs.
    `"list_lite_resources: ..."`) while sharing this one implementation.

    Result order is always `type_codes`' own order, never worker-thread
    completion order -- each collector's result is stored in a dict keyed
    by `type_code` and the final list is reassembled by iterating
    `type_codes` in order, regardless of which thread finished first. Nothing
    downstream is known to depend on this (GalaxyView.tsx's `layoutResources`
    re-sorts by `(type, id)` before laying resources out, and
    resource_query_service.list_resources re-sorts by `(type, name)` too),
    but preserving deterministic order is free here and avoids introducing a
    new source of flakiness (e.g. in tests asserting on `resources[]` order)
    on top of the concurrency change itself.
    """
    results: dict[str, list[GalaxyResource]] = {}
    with ThreadPoolExecutor(max_workers=_SCAN_MAX_WORKERS) as executor:
        future_to_type = {
            executor.submit(_COLLECTORS[type_code], region, lite=lite): type_code
            for type_code in type_codes
        }
        for future in as_completed(future_to_type):
            type_code = future_to_type[future]
            try:
                results[type_code] = future.result()
            except Exception:  # noqa: BLE001 - one type's failure must not blank the whole scan/list
                logger.warning(
                    "%s: listing type=%s failed in region=%s, contributing 0 resources",
                    log_prefix,
                    type_code,
                    region,
                    exc_info=True,
                )

    resources: list[GalaxyResource] = []
    for type_code in type_codes:
        resources.extend(results.get(type_code, []))
    return resources


def _run_scan(region: str) -> ScanResponse:
    resources = _run_collectors_concurrently(
        region, TYPE_CODES, lite=False, log_prefix="scan_region"
    )

    return ScanResponse(
        region=region,
        last_updated=datetime.now(timezone.utc),
        resources=resources,
        totals=_compute_totals(resources),
        error=None,
    )


def _compute_totals(resources: list[GalaxyResource]) -> ScanTotals:
    monthly_spend = sum(r.cost.projected_monthly for r in resources if r.cost is not None)
    idle_resources = [r for r in resources if r.idle is not None and r.idle.is_idle]
    idle_monthly_waste = sum(
        r.cost.projected_monthly for r in idle_resources if r.cost is not None
    )
    return ScanTotals(
        monthly_spend=round(monthly_spend, 2),
        idle_count=len(idle_resources),
        idle_monthly_waste=round(idle_monthly_waste, 2),
    )


def _lookup_idle(type_code: str, resource_id: str, region: str) -> IdleCheckResult | None:
    try:
        return idle_service.check_idle(
            type_code, resource_id, IDLE_CHECK_WINDOW_DAYS, region=region
        )
    except Exception:  # noqa: BLE001 - one resource's idle lookup failing is a soft miss, not fatal
        logger.warning(
            "scan_region: idle check failed type=%s id=%s region=%s",
            type_code,
            resource_id,
            region,
            exc_info=True,
        )
        return None


def _lookup_cost(type_code: str, resource_id: str, region: str) -> CostEstimate | None:
    try:
        return cost_service.estimate_cost(type_code, resource_id, region=region)
    except Exception:  # noqa: BLE001 - one resource's cost lookup failing is a soft miss, not fatal
        logger.warning(
            "scan_region: cost estimate failed type=%s id=%s region=%s",
            type_code,
            resource_id,
            region,
            exc_info=True,
        )
        return None


def _relations_for(type_code: str, obj) -> list[RelationLink]:
    """Roadmap 3.7 -- shapes `GalaxyResource.relations` purely from fields
    already present on the per-type object every _collect_*()/
    get_lite_resource() call already fetched via its normal list_*()/
    get_*() service call (see the model + service edits alongside this
    function: EC2Instance.security_group_ids/subnet_id/vpc_id/
    iam_instance_profile_name/attached_volume_ids, RdsInstanceSummary/
    LoadBalancer/OpenSearchDomain/RedshiftCluster's equivalents, etc.) --
    every one of those fields is already returned by the existing
    Describe*/List* call, just not previously mapped into the model. No
    new AWS calls happen here or anywhere upstream of it.

    `label` is always one of attached | secured_by | in | routed_by |
    assumes; `kind` is either one of TYPE_CODES (cost-bearing -- e.g. an
    EC2 instance's attached EBS volume) or one of security_group | subnet
    | vpc | iam_role (infra, non-cost-bearing -- these have no `cost`
    block of their own and are rendered as bare nodes by frontend-agent's
    cluster view, not full GalaxyResources).

    Deliberately skipped (no relations built) for: dynamodb, sagemaker,
    api_gateway, cloudfront, kinesis -- none of these expose VPC/security-
    group/IAM linkage in their existing list/describe response (DynamoDB
    and Kinesis have no VPC concept at all; SageMaker's VpcConfig lives on
    the *endpoint config*, a separate DescribeEndpointConfig call this
    step doesn't make; API Gateway only has VPC links for private APIs,
    itself a separate call; CloudFront is a global edge service with no
    VPC/SG/subnet concept). A real, documented gap, not an oversight --
    revisit if a future step already ends up fetching that data for other
    reasons.
    """
    relations: list[RelationLink] = []

    if type_code == "ec2":
        for vol_id in obj.attached_volume_ids:
            relations.append(RelationLink(id=vol_id, label="attached", kind="ebs"))
        for sg_id in obj.security_group_ids:
            relations.append(RelationLink(id=sg_id, label="secured_by", kind="security_group"))
        if obj.subnet_id:
            relations.append(RelationLink(id=obj.subnet_id, label="in", kind="subnet"))
        if obj.vpc_id:
            relations.append(RelationLink(id=obj.vpc_id, label="in", kind="vpc"))
        if obj.iam_instance_profile_name:
            relations.append(
                RelationLink(id=obj.iam_instance_profile_name, label="assumes", kind="iam_role")
            )
    elif type_code == "ebs":
        for inst_id in obj.attached_instance_ids:
            relations.append(RelationLink(id=inst_id, label="attached", kind="ec2"))
    elif type_code == "rds":
        for sg_id in obj.vpc_security_group_ids:
            relations.append(RelationLink(id=sg_id, label="secured_by", kind="security_group"))
        for subnet_id in obj.subnet_ids:
            relations.append(RelationLink(id=subnet_id, label="in", kind="subnet"))
        if obj.vpc_id:
            relations.append(RelationLink(id=obj.vpc_id, label="in", kind="vpc"))
    elif type_code == "eip":
        if obj.instance_id:
            relations.append(RelationLink(id=obj.instance_id, label="attached", kind="ec2"))
    elif type_code == "elb":
        for sg_id in obj.security_group_ids:
            relations.append(RelationLink(id=sg_id, label="secured_by", kind="security_group"))
        for subnet_id in obj.subnet_ids:
            relations.append(RelationLink(id=subnet_id, label="in", kind="subnet"))
        if obj.vpc_id:
            relations.append(RelationLink(id=obj.vpc_id, label="in", kind="vpc"))
    elif type_code == "lambda":
        if obj.role_name:
            relations.append(RelationLink(id=obj.role_name, label="assumes", kind="iam_role"))
        for sg_id in obj.security_group_ids:
            relations.append(RelationLink(id=sg_id, label="secured_by", kind="security_group"))
        for subnet_id in obj.subnet_ids:
            relations.append(RelationLink(id=subnet_id, label="in", kind="subnet"))
        if obj.vpc_id:
            relations.append(RelationLink(id=obj.vpc_id, label="in", kind="vpc"))
    elif type_code == "nat_gateway":
        if obj.subnet_id:
            relations.append(RelationLink(id=obj.subnet_id, label="in", kind="subnet"))
        if obj.vpc_id:
            relations.append(RelationLink(id=obj.vpc_id, label="in", kind="vpc"))
    elif type_code == "elasticache":
        for sg_id in obj.security_group_ids:
            relations.append(RelationLink(id=sg_id, label="secured_by", kind="security_group"))
    elif type_code == "redshift":
        for sg_id in obj.vpc_security_group_ids:
            relations.append(RelationLink(id=sg_id, label="secured_by", kind="security_group"))
        if obj.vpc_id:
            relations.append(RelationLink(id=obj.vpc_id, label="in", kind="vpc"))
    elif type_code == "opensearch":
        for sg_id in obj.security_group_ids:
            relations.append(RelationLink(id=sg_id, label="secured_by", kind="security_group"))
        for subnet_id in obj.subnet_ids:
            relations.append(RelationLink(id=subnet_id, label="in", kind="subnet"))
        if obj.vpc_id:
            relations.append(RelationLink(id=obj.vpc_id, label="in", kind="vpc"))
    # dynamodb, sagemaker, api_gateway, cloudfront, kinesis: no relations
    # data available from the existing list/describe calls -- see
    # docstring above.

    return relations


def _build_resource(
    type_code: str,
    resource_id: str,
    name: str,
    region: str,
    created_at,
    status: str,
    obj=None,
    skip_idle_cost: bool = False,
) -> GalaxyResource:
    """Shared "list one, look up idle+cost, shape into GalaxyResource"
    step every per-type collector below uses. skip_idle_cost mirrors GET
    /resources/ec2's existing "only running instances get cpu/idle/cost"
    guard -- estimate_cost has no concept of instance/cluster state today,
    so pricing a non-running resource at its running on-demand rate would
    misrepresent it, and idle_service would have no CloudWatch datapoints
    to judge either (a stopped/paused resource emits no metrics at all),
    which -- left unchecked -- can get misreported as "idle" on top of
    the wrong cost. Used by every collector below whose type has a
    documented non-billing status the account can actually be in: EC2
    ("stopped" etc.), RDS ("stopped" -- up to 7 days, no compute charge),
    and Redshift ("paused" -- same). Every other type here either has no
    such pause/stop state (Lambda, DynamoDB, ElastiCache, SageMaker,
    API Gateway, CloudFront, OpenSearch, Kinesis, NAT Gateway, ELB) or is
    already priced/idle-checked from a point-in-time signal that doesn't
    assume "running" in the first place (EBS attachment state, EIP
    association state).

    `obj` -- the already-fetched per-type model instance (the same object
    _identity_from_obj() derived resource_id/name/created_at/status from)
    -- is passed through to _relations_for() (roadmap 3.7) so relations
    are shaped from data this call already has in hand, no new AWS call.
    Optional/defaults to None only because get_lite_resource() below calls
    _relations_for() directly with the object it already has, rather than
    round-tripping through this helper.
    """
    idle = None
    cost = None
    if not skip_idle_cost:
        idle = _lookup_idle(type_code, resource_id, region)
        cost = _lookup_cost(type_code, resource_id, region)

    return GalaxyResource(
        id=resource_id,
        name=name,
        type=type_code,
        region=region,
        cost=cost,
        idle=idle,
        health=ResourceHealth(
            primary_metric=_PRIMARY_METRIC[type_code],
            primary_metric_value=None,
            status=status,
        ),
        created_at=created_at,
        relations=_relations_for(type_code, obj) if obj is not None else [],
    )


def _identity_from_obj(type_code: str, obj) -> tuple[str, str, object, str]:
    """Maps one already-fetched per-type resource object -- from either a
    list_*() call's items (every _collect_* below) or a single get_*()
    lookup (get_lite_resource further down, roadmap 3.8's
    get_resource_health/get_resource_age) -- to the (id, name,
    created_at, status) tuple _build_resource()/GalaxyResource need. Both
    call paths return the exact same model type per type_code, so one
    mapping here is reused by both rather than keeping two copies that
    could silently drift out of sync on a field rename.

    `name` always falls back to the resource's own id/human identifier
    when untagged (roadmap 3.8's list_resources rule: never silently omit
    an untagged resource) -- for the types with no separate Name-taggable
    identity at all (Lambda, DynamoDB, ElastiCache, SageMaker, Redshift,
    Kinesis), the AWS-assigned name/identifier already *is* the human
    identifier, so there's nothing further to fall back from.
    """
    if type_code == "ec2":
        return obj.instance_id, obj.tags.get("Name") or obj.instance_id, obj.launch_time, obj.state
    if type_code == "ebs":
        return obj.volume_id, obj.tags.get("Name") or obj.volume_id, obj.create_time, obj.state
    if type_code == "rds":
        return obj.identifier, obj.identifier, obj.instance_create_time, obj.status
    if type_code == "eip":
        return (
            obj.resource_id,
            obj.tags.get("Name") or obj.resource_id,
            None,
            "associated" if obj.is_associated else "unassociated",
        )
    if type_code == "elb":
        return obj.name, obj.tags.get("Name") or obj.name, obj.created_time, obj.state
    if type_code == "lambda":
        return obj.name, obj.name, None, "active"
    if type_code == "nat_gateway":
        return (
            obj.nat_gateway_id,
            obj.tags.get("Name") or obj.nat_gateway_id,
            obj.create_time,
            obj.state,
        )
    if type_code == "dynamodb":
        return obj.name, obj.name, obj.creation_date_time, obj.status
    if type_code == "elasticache":
        return obj.cache_cluster_id, obj.cache_cluster_id, obj.create_time, obj.status
    if type_code == "sagemaker":
        return obj.endpoint_name, obj.endpoint_name, obj.creation_time, obj.status
    if type_code == "redshift":
        return obj.cluster_identifier, obj.cluster_identifier, obj.create_time, obj.status
    if type_code == "api_gateway":
        return obj.api_id, obj.name, obj.created_date, "active"
    if type_code == "cloudfront":
        return obj.distribution_id, obj.domain_name or obj.distribution_id, None, obj.status
    if type_code == "opensearch":
        return (
            obj.domain_name,
            obj.domain_name,
            obj.created_at,
            "active" if obj.created else "creating",
        )
    # kinesis
    return obj.stream_name, obj.stream_name, obj.creation_timestamp, obj.status


def _collect_ec2(region: str, lite: bool = False) -> list[GalaxyResource]:
    instances = ec2_service.list_instances(region=region).instances
    resources = []
    for inst in instances:
        resource_id, name, created_at, status = _identity_from_obj("ec2", inst)
        resources.append(
            _build_resource(
                "ec2",
                resource_id,
                name,
                region,
                created_at,
                status,
                obj=inst,
                skip_idle_cost=lite or (status != "running"),
            )
        )
    return resources


def _collect_ebs(region: str, lite: bool = False) -> list[GalaxyResource]:
    volumes = ebs_service.list_volumes(region=region).volumes
    resources = []
    for vol in volumes:
        resource_id, name, created_at, status = _identity_from_obj("ebs", vol)
        resources.append(
            _build_resource(
                "ebs", resource_id, name, region, created_at, status, obj=vol, skip_idle_cost=lite
            )
        )
    return resources


# RDS DBInstanceStatus values that mean "not accruing compute charges" --
# a stopped instance can sit for up to 7 days with no compute cost (AWS
# force-restarts it after that), same nuance EC2's skip_idle_cost guard
# exists for. "stopping" is included too since it is actively transitioning
# out of the billing state, not a steady-state "still running" value.
_RDS_NON_BILLING_STATUSES = {"stopped", "stopping"}


def _collect_rds(region: str, lite: bool = False) -> list[GalaxyResource]:
    instances = rds_service.list_instances(region=region).instances
    resources = []
    for inst in instances:
        resource_id, name, created_at, status = _identity_from_obj("rds", inst)
        resources.append(
            _build_resource(
                "rds",
                resource_id,
                name,
                region,
                created_at,
                status,
                obj=inst,
                skip_idle_cost=lite or (status in _RDS_NON_BILLING_STATUSES),
            )
        )
    return resources


def _collect_eip(region: str, lite: bool = False) -> list[GalaxyResource]:
    addresses = eip_service.list_addresses(region=region).addresses
    resources = []
    for addr in addresses:
        resource_id, name, created_at, status = _identity_from_obj("eip", addr)
        resources.append(
            _build_resource(
                "eip", resource_id, name, region, created_at, status, obj=addr, skip_idle_cost=lite
            )
        )
    return resources


def _collect_elb(region: str, lite: bool = False) -> list[GalaxyResource]:
    lbs = elb_service.list_load_balancers(region=region).load_balancers
    resources = []
    for lb in lbs:
        resource_id, name, created_at, status = _identity_from_obj("elb", lb)
        resources.append(
            _build_resource(
                "elb", resource_id, name, region, created_at, status, obj=lb, skip_idle_cost=lite
            )
        )
    return resources


def _collect_lambda(region: str, lite: bool = False) -> list[GalaxyResource]:
    functions = lambda_service.list_functions(region=region).functions
    resources = []
    for fn in functions:
        resource_id, name, created_at, status = _identity_from_obj("lambda", fn)
        resources.append(
            _build_resource(
                "lambda", resource_id, name, region, created_at, status, obj=fn, skip_idle_cost=lite
            )
        )
    return resources


def _collect_nat_gateway(region: str, lite: bool = False) -> list[GalaxyResource]:
    gateways = nat_gateway_service.list_nat_gateways(region=region).nat_gateways
    resources = []
    for gw in gateways:
        resource_id, name, created_at, status = _identity_from_obj("nat_gateway", gw)
        resources.append(
            _build_resource(
                "nat_gateway",
                resource_id,
                name,
                region,
                created_at,
                status,
                obj=gw,
                skip_idle_cost=lite,
            )
        )
    return resources


def _collect_dynamodb(region: str, lite: bool = False) -> list[GalaxyResource]:
    tables = dynamodb_service.list_tables(region=region).tables
    resources = []
    for table in tables:
        resource_id, name, created_at, status = _identity_from_obj("dynamodb", table)
        resources.append(
            _build_resource(
                "dynamodb", resource_id, name, region, created_at, status, skip_idle_cost=lite
            )
        )
    return resources


def _collect_elasticache(region: str, lite: bool = False) -> list[GalaxyResource]:
    clusters = elasticache_service.list_clusters(region=region).clusters
    resources = []
    for cluster in clusters:
        resource_id, name, created_at, status = _identity_from_obj("elasticache", cluster)
        resources.append(
            _build_resource(
                "elasticache",
                resource_id,
                name,
                region,
                created_at,
                status,
                obj=cluster,
                skip_idle_cost=lite,
            )
        )
    return resources


def _collect_sagemaker(region: str, lite: bool = False) -> list[GalaxyResource]:
    endpoints = sagemaker_service.list_endpoints(region=region).endpoints
    resources = []
    for ep in endpoints:
        resource_id, name, created_at, status = _identity_from_obj("sagemaker", ep)
        resources.append(
            _build_resource(
                "sagemaker", resource_id, name, region, created_at, status, skip_idle_cost=lite
            )
        )
    return resources


# Redshift ClusterStatus values that mean "not accruing compute charges" --
# a paused cluster keeps its storage but stops billing for compute, same
# nuance as RDS's "stopped" above. "pausing" is included for the same
# actively-transitioning-out-of-billing reason as RDS's "stopping".
_REDSHIFT_NON_BILLING_STATUSES = {"paused", "pausing"}


def _collect_redshift(region: str, lite: bool = False) -> list[GalaxyResource]:
    clusters = redshift_service.list_clusters(region=region).clusters
    resources = []
    for cluster in clusters:
        resource_id, name, created_at, status = _identity_from_obj("redshift", cluster)
        resources.append(
            _build_resource(
                "redshift",
                resource_id,
                name,
                region,
                created_at,
                status,
                obj=cluster,
                skip_idle_cost=lite or (status in _REDSHIFT_NON_BILLING_STATUSES),
            )
        )
    return resources


def _collect_api_gateway(region: str, lite: bool = False) -> list[GalaxyResource]:
    apis = api_gateway_service.list_apis(region=region).apis
    resources = []
    for api in apis:
        resource_id, name, created_at, status = _identity_from_obj("api_gateway", api)
        resources.append(
            _build_resource(
                "api_gateway", resource_id, name, region, created_at, status, skip_idle_cost=lite
            )
        )
    return resources


def _collect_cloudfront(region: str, lite: bool = False) -> list[GalaxyResource]:
    """CloudFront is a global service (see cloudfront_service.py's module
    docstring) -- every region's scan sees the same distribution list,
    attributed to whichever region is currently being scanned. A known,
    documented consequence of scanning one region at a time; not
    deduplicated across regions in this build step.
    """
    distributions = cloudfront_service.list_distributions(region=region).distributions
    resources = []
    for dist in distributions:
        resource_id, name, created_at, status = _identity_from_obj("cloudfront", dist)
        resources.append(
            _build_resource(
                "cloudfront", resource_id, name, region, created_at, status, skip_idle_cost=lite
            )
        )
    return resources


def _collect_opensearch(region: str, lite: bool = False) -> list[GalaxyResource]:
    domains = opensearch_service.list_domains(region=region).domains
    resources = []
    for domain in domains:
        resource_id, name, created_at, status = _identity_from_obj("opensearch", domain)
        resources.append(
            _build_resource(
                "opensearch",
                resource_id,
                name,
                region,
                created_at,
                status,
                obj=domain,
                skip_idle_cost=lite,
            )
        )
    return resources


def _collect_kinesis(region: str, lite: bool = False) -> list[GalaxyResource]:
    streams = kinesis_service.list_streams(region=region).streams
    resources = []
    for stream in streams:
        resource_id, name, created_at, status = _identity_from_obj("kinesis", stream)
        resources.append(
            _build_resource(
                "kinesis", resource_id, name, region, created_at, status, skip_idle_cost=lite
            )
        )
    return resources


_COLLECTORS = {
    "ec2": _collect_ec2,
    "ebs": _collect_ebs,
    "rds": _collect_rds,
    "eip": _collect_eip,
    "elb": _collect_elb,
    "lambda": _collect_lambda,
    "nat_gateway": _collect_nat_gateway,
    "dynamodb": _collect_dynamodb,
    "elasticache": _collect_elasticache,
    "sagemaker": _collect_sagemaker,
    "redshift": _collect_redshift,
    "api_gateway": _collect_api_gateway,
    "cloudfront": _collect_cloudfront,
    "opensearch": _collect_opensearch,
    "kinesis": _collect_kinesis,
}


# =====================================================================
# Roadmap 3.8 support -- list_resources/get_resource_health/
# get_resource_age (app/services/resource_query_service.py) need cheap
# identity+status data without paying for a CloudWatch/Pricing lookup per
# resource the way a full scan_region() does. Both functions below reuse
# the exact same per-type list_*()/get_*() service calls and
# _identity_from_obj() mapping every _collect_* above already uses -- one
# source of truth for "what is this resource's id/name/created_at/
# status", not a second copy that could drift.
# =====================================================================

_SINGLE_GETTERS = {
    "ec2": ec2_service.get_instance,
    "ebs": ebs_service.get_volume,
    "rds": rds_service.get_instance,
    "eip": eip_service.get_address,
    "elb": elb_service.get_load_balancer,
    "lambda": lambda_service.get_function,
    "nat_gateway": nat_gateway_service.get_nat_gateway,
    "dynamodb": dynamodb_service.get_table,
    "elasticache": elasticache_service.get_cluster,
    "sagemaker": sagemaker_service.get_endpoint,
    "redshift": redshift_service.get_cluster,
    "api_gateway": api_gateway_service.get_api,
    "cloudfront": cloudfront_service.get_distribution,
    "opensearch": opensearch_service.get_domain,
    "kinesis": kinesis_service.get_stream,
}


class UnsupportedResourceTypeError(ValueError):
    """Raised by list_lite_resources/get_lite_resource for a type_code
    outside the roadmap's 15 in-scope types -- same per-module pattern as
    idle_service's and cost_service's own UnsupportedResourceTypeError."""


def list_lite_resources(region: str, type_codes: list[str] | None = None) -> list[GalaxyResource]:
    """Identity+status data only (cost=None, idle=None on every entry) for
    every resource of the given types (default: all 15) in `region` --
    the fast counterpart to scan_region() that roadmap 3.8's
    list_resources uses, since a count/list question shouldn't pay for a
    CloudWatch/Pricing lookup per resource just to count or list them.

    Validates/normalizes `region` the same way scan_region() does (see
    _validate_region). Deliberately NOT cached and no cooldown -- this is
    meant to already be cheap enough (Describe*/List* calls only, same
    calls scan_region()'s own listing step makes) not to need it, unlike
    the full idle/cost-inclusive scan. One type's listing failure
    contributes 0 resources for that type rather than failing the whole
    call, same graceful-degradation precedent as _run_scan.

    Runs its (up to 15) per-type collectors concurrently via the same
    `_run_collectors_concurrently` helper `_run_scan` uses -- this sits on
    the interactive `list_resources` chat/MCP tool's response path
    (roadmap 3.8, via resource_query_service.list_resources), so a user
    asking "list my resources" for an uncached region shouldn't pay for 15
    sequential Describe*/List* round trips one after another just to get an
    answer, even though each individual call here is already cheaper than
    scan_region()'s (no CloudWatch/Pricing lookups in `lite=True` mode).
    """
    region = _validate_region(region)
    types = type_codes if type_codes is not None else list(TYPE_CODES)
    for type_code in types:
        if type_code not in _COLLECTORS:
            raise UnsupportedResourceTypeError(
                f"resource_type={type_code!r} is not supported -- only "
                f"{sorted(_COLLECTORS)!r} (the roadmap's 15 in-scope types) are tracked."
            )

    return _run_collectors_concurrently(
        region, types, lite=True, log_prefix="list_lite_resources"
    )


def get_lite_resource(
    resource_type: str, resource_id: str, region: str | None = None
) -> GalaxyResource | None:
    """Single-resource identity+status lookup (cost=None, idle=None) --
    the O(1) counterpart to list_lite_resources's "every resource of a
    type" shape, used by roadmap 3.8's get_resource_health/
    get_resource_age so they can describe one resource without listing
    every resource of its type first.

    Returns None if the resource can't be found, mirroring every
    per-type get_*() function's own "not found" convention -- never
    raises for a not-found resource_id, only for an unsupported
    resource_type. `region` is passed straight through to the underlying
    get_*() call (None means "the configured default region", same as
    every other per-type getter already used by idle_service/
    cost_service) -- no cache/lock machinery here to validate against, so
    unlike list_lite_resources this does not enforce the enabled-region
    allowlist itself.
    """
    getter = _SINGLE_GETTERS.get(resource_type)
    if getter is None:
        raise UnsupportedResourceTypeError(
            f"resource_type={resource_type!r} is not supported -- only "
            f"{sorted(_SINGLE_GETTERS)!r} (the roadmap's 15 in-scope types) are tracked."
        )
    obj = getter(resource_id, region=region)
    if obj is None:
        return None

    effective_region = region or get_settings().aws_region
    resolved_id, name, created_at, status = _identity_from_obj(resource_type, obj)
    return GalaxyResource(
        id=resolved_id,
        name=name,
        type=resource_type,
        region=effective_region,
        cost=None,
        idle=None,
        health=ResourceHealth(
            primary_metric=_PRIMARY_METRIC[resource_type],
            primary_metric_value=None,
            status=status,
        ),
        created_at=created_at,
        relations=_relations_for(resource_type, obj),
    )
