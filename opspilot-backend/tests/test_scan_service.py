"""Tests for scan_service (roadmap Section 3.3/3.4) -- region-wide
aggregation, per-region caching, debounce/cooldown, and graceful
degradation when one resource type (or an individual resource's idle/cost
lookup) fails.

Every 15-type list_*() call is mocked at the service-function level
(mirrors the existing precedent set by test_resources_route.py, which
mocks at the same layer rather than the boto3 client) -- scan_service's
own aggregation/caching/cooldown logic is what is under test here, not
Steps 2-3's already-tested idle_service/cost_service internals.
"""
from __future__ import annotations

import threading
import time
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

from app.models.api_gateway import ApiGatewayRestApiList
from app.models.cloudfront import CloudFrontDistributionList
from app.models.cost import CostEstimate, DateRange
from app.models.dashboard import (
    DynamoCard,
    LambdaCard,
    LambdaFunctionSummary,
    RdsCard,
    RdsInstanceSummary,
)
from app.models.ebs import EbsVolume, EbsVolumeList
from app.models.ec2 import EC2Instance, EC2InstanceList
from app.models.eip import ElasticIp, ElasticIpList
from app.models.elasticache import ElastiCacheCluster, ElastiCacheClusterList
from app.models.elb import LoadBalancer, LoadBalancerList
from app.models.idle import IdleCheckResult
from app.models.kinesis import KinesisStreamList
from app.models.nat_gateway import NatGateway, NatGatewayList
from app.models.opensearch import OpenSearchDomain, OpenSearchDomainList
from app.models.redshift import RedshiftCluster, RedshiftClusterList
from app.models.sagemaker import SageMakerEndpointList
from app.models.scan import ScanResponse, ScanTotals
from app.services import scan_service

_FAKE_ENABLED_REGIONS = ["us-east-1", "us-west-2", "eu-west-1"]


def _clear_module_state() -> None:
    scan_service._cache.clear()
    scan_service._last_scan_attempt.clear()
    scan_service._region_locks.clear()
    scan_service._in_flight_scans.clear()
    scan_service._valid_regions_cache = None
    scan_service._valid_regions_cache_at = None


@pytest.fixture(autouse=True)
def _reset_scan_service_state():
    """scan_service keeps module-level global cache/lock/cooldown/
    valid-regions state -- tests must not leak state into each other.
    Cleared before AND after every test. Also patches
    ec2_service.list_region_names() (the region allowlist scan_region()
    validates every call against) so tests never make a real AWS call and
    every test's fake regions stay in sync with this one list.
    """
    _clear_module_state()
    with patch(
        "app.services.ec2_service.list_region_names", return_value=_FAKE_ENABLED_REGIONS
    ):
        yield
    _clear_module_state()


def _empty_lists_patch():
    """Patches every one of the 15 list_*() service calls to return an
    empty result -- the baseline every test starts from, then overrides
    the specific type(s) it cares about."""
    return [
        patch(
            "app.services.ec2_service.list_instances",
            return_value=EC2InstanceList(instances=[], count=0),
        ),
        patch(
            "app.services.ebs_service.list_volumes",
            return_value=EbsVolumeList(volumes=[], count=0),
        ),
        patch(
            "app.services.rds_service.list_instances",
            return_value=RdsCard(instances=[], count=0),
        ),
        patch(
            "app.services.eip_service.list_addresses",
            return_value=ElasticIpList(addresses=[], count=0),
        ),
        patch(
            "app.services.elb_service.list_load_balancers",
            return_value=LoadBalancerList(load_balancers=[], count=0),
        ),
        patch(
            "app.services.lambda_service.list_functions",
            return_value=LambdaCard(functions=[], count=0),
        ),
        patch(
            "app.services.nat_gateway_service.list_nat_gateways",
            return_value=NatGatewayList(nat_gateways=[], count=0),
        ),
        patch(
            "app.services.dynamodb_service.list_tables",
            return_value=DynamoCard(tables=[], count=0),
        ),
        patch(
            "app.services.elasticache_service.list_clusters",
            return_value=ElastiCacheClusterList(clusters=[], count=0),
        ),
        patch(
            "app.services.sagemaker_service.list_endpoints",
            return_value=SageMakerEndpointList(endpoints=[], count=0),
        ),
        patch(
            "app.services.redshift_service.list_clusters",
            return_value=RedshiftClusterList(clusters=[], count=0),
        ),
        patch(
            "app.services.api_gateway_service.list_apis",
            return_value=ApiGatewayRestApiList(apis=[], count=0),
        ),
        patch(
            "app.services.cloudfront_service.list_distributions",
            return_value=CloudFrontDistributionList(distributions=[], count=0),
        ),
        patch(
            "app.services.opensearch_service.list_domains",
            return_value=OpenSearchDomainList(domains=[], count=0),
        ),
        patch(
            "app.services.kinesis_service.list_streams",
            return_value=KinesisStreamList(streams=[], count=0),
        ),
    ]


class _AllEmpty:
    """Context manager starting every one of the 15 list_*() patches at
    once, returning the list of live mock objects in TYPE_CODES order."""

    def __enter__(self):
        self._patches = _empty_lists_patch()
        self.mocks = [p.start() for p in self._patches]
        return self.mocks

    def __exit__(self, *exc):
        for p in self._patches:
            p.stop()


def test_scan_region_empty_account_returns_zero_totals() -> None:
    with _AllEmpty():
        result = scan_service.scan_region("us-east-1")

    assert result.region == "us-east-1"
    assert result.resources == []
    assert result.totals.monthly_spend == 0
    assert result.totals.idle_count == 0
    assert result.totals.idle_monthly_waste == 0
    assert result.error is None


def test_cache_hit_does_not_rescan() -> None:
    with _AllEmpty() as mocks:
        first = scan_service.scan_region("us-east-1")
        assert mocks[0].call_count == 1

        second = scan_service.scan_region("us-east-1")  # force=False, cache exists
        assert mocks[0].call_count == 1  # no new AWS calls
        assert second is first  # same cached object returned


def test_one_type_failing_does_not_blank_whole_scan() -> None:
    """roadmap 3.3: a permissions error on one service must not take down
    the whole scan -- it degrades to zero resources for that type only."""
    with _AllEmpty() as mocks:
        ec2_mock = mocks[0]
        ec2_mock.side_effect = RuntimeError("AccessDenied: ec2:DescribeInstances")
        with patch(
            "app.services.ebs_service.list_volumes",
            return_value=EbsVolumeList(
                volumes=[
                    EbsVolume(
                        volume_id="vol-1",
                        size_gb=8,
                        volume_type="gp3",
                        state="available",
                        availability_zone="us-east-1a",
                        create_time=datetime(2026, 1, 1, tzinfo=timezone.utc),
                        attached_instance_ids=[],
                        tags={},
                    )
                ],
                count=1,
            ),
        ):
            with patch("app.services.idle_service.check_idle") as mock_idle, patch(
                "app.services.cost_service.estimate_cost"
            ) as mock_cost:
                mock_idle.return_value = IdleCheckResult(
                    resource_id="vol-1",
                    resource_type="ebs",
                    window_days=7,
                    is_idle=True,
                    idle_since="2026-01-01",
                    idle_days=7,
                    younger_than_window=False,
                )
                mock_cost.return_value = CostEstimate(
                    resource_id="vol-1",
                    resource_type="ebs",
                    date_range=DateRange(
                        start=datetime(2026, 1, 1, tzinfo=timezone.utc),
                        end=datetime(2026, 1, 8, tzinfo=timezone.utc),
                    ),
                    method="list_price",
                    hourly_rate=0.001,
                    projected_monthly=1.0,
                    incurred_so_far=0.5,
                )
                result = scan_service.scan_region("us-east-1")

    # EC2 contributed 0 (failed), EBS still contributed its 1 volume.
    assert [r.type for r in result.resources] == ["ebs"]
    assert result.resources[0].id == "vol-1"
    assert result.totals.monthly_spend == 1.0
    assert result.totals.idle_count == 1


def test_one_resource_idle_lookup_failing_keeps_resource_with_none_idle() -> None:
    with _AllEmpty() as mocks:
        ec2_mock = mocks[0]
        ec2_mock.return_value = EC2InstanceList(
            instances=[
                EC2Instance(
                    instance_id="i-1",
                    instance_type="t3.micro",
                    state="running",
                    availability_zone="us-east-1a",
                    launch_time=datetime(2026, 1, 1, tzinfo=timezone.utc),
                    tags={},
                )
            ],
            count=1,
        )
        with patch(
            "app.services.idle_service.check_idle", side_effect=RuntimeError("throttled")
        ), patch("app.services.cost_service.estimate_cost") as mock_cost:
            mock_cost.return_value = CostEstimate(
                resource_id="i-1",
                resource_type="ec2",
                date_range=DateRange(
                    start=datetime(2026, 1, 1, tzinfo=timezone.utc),
                    end=datetime(2026, 1, 8, tzinfo=timezone.utc),
                ),
                method="list_price",
                hourly_rate=0.01,
                projected_monthly=7.3,
                incurred_so_far=1.0,
            )
            result = scan_service.scan_region("us-east-1")

    assert len(result.resources) == 1
    assert result.resources[0].idle is None
    assert result.resources[0].cost is not None
    assert result.resources[0].cost.projected_monthly == 7.3


def test_forced_refresh_too_soon_raises_cooldown_with_cached_payload() -> None:
    with _AllEmpty():
        first = scan_service.scan_region("us-east-1")

        with pytest.raises(scan_service.ScanCooldownActive) as exc_info:
            scan_service.scan_region("us-east-1", force=True)

    assert exc_info.value.cached is first
    assert exc_info.value.retry_after_seconds > 0


def test_forced_refresh_after_cooldown_elapsed_rescans() -> None:
    with _AllEmpty() as mocks:
        scan_service.scan_region("us-east-1")
        assert mocks[0].call_count == 1

        # Backdate the last attempt past the cooldown window instead of
        # sleeping 45s in a test.
        scan_service._last_scan_attempt["us-east-1"] = datetime.now(timezone.utc) - timedelta(
            seconds=scan_service.COOLDOWN_SECONDS + 1
        )

        second = scan_service.scan_region("us-east-1", force=True)

    assert mocks[0].call_count == 2
    assert second.error is None


def test_scan_failure_with_no_prior_cache_raises_clear_error() -> None:
    with patch("app.services.scan_service._run_scan", side_effect=RuntimeError("no creds")):
        with pytest.raises(scan_service.ScanFailedNoCacheError):
            scan_service.scan_region("us-east-1")


def test_stale_cache_served_on_rescan_failure_with_error_set() -> None:
    with _AllEmpty():
        first = scan_service.scan_region("us-east-1")
        original_last_updated = first.last_updated

        scan_service._last_scan_attempt["us-east-1"] = datetime.now(timezone.utc) - timedelta(
            seconds=scan_service.COOLDOWN_SECONDS + 1
        )
        # Simulate a broader failure inside the scan itself (not just one
        # type's list_*() -- that case is handled inside _run_scan and
        # would not hit this fallback path at all).
        with patch(
            "app.services.scan_service._run_scan", side_effect=RuntimeError("scan blew up")
        ):
            result = scan_service.scan_region("us-east-1", force=True)

    assert result.resources == first.resources
    assert result.last_updated == original_last_updated  # untouched
    assert result.error is not None
    assert "RuntimeError" in result.error


def test_concurrent_refresh_with_existing_cache_is_rejected_not_duplicated() -> None:
    """Once a cache exists, a concurrent force=True request racing an
    in-flight scan is safe to reject (429) -- the caller still has data.
    Simulates 'already in flight' by pre-holding the region's lock (as a
    real concurrent request would) before calling scan_region again."""
    with _AllEmpty():
        scan_service.scan_region("us-east-1")  # populate the cache first

    lock = scan_service._region_lock("us-east-1")
    lock.acquire()
    try:
        with pytest.raises(scan_service.ScanCooldownActive) as exc_info:
            scan_service.scan_region("us-east-1", force=True)
    finally:
        lock.release()

    assert exc_info.value.cached is not None


def test_no_cache_concurrent_request_waits_and_reuses_in_flight_result() -> None:
    """A region with no cache at all must never be rejected -- a second
    concurrent request (regardless of force) blocks for the in-flight
    scan (tracked via the region's Future in _in_flight_scans) to finish
    and reuses its result, rather than a 429 that would leave it with
    nothing to show."""
    future, is_winner = scan_service._get_or_create_in_flight_future("us-east-1")
    assert is_winner

    winner_result = ScanResponse(
        region="us-east-1",
        last_updated=datetime.now(timezone.utc),
        resources=[],
        totals=ScanTotals(monthly_spend=0, idle_count=0, idle_monthly_waste=0),
        error=None,
    )

    def _simulate_in_flight_scan_completing() -> None:
        time.sleep(0.1)
        scan_service._cache["us-east-1"] = winner_result
        future.set_result(winner_result)
        scan_service._in_flight_scans.pop("us-east-1", None)

    thread = threading.Thread(target=_simulate_in_flight_scan_completing)
    thread.start()
    try:
        result = scan_service.scan_region("us-east-1")  # force=False, no cache yet
    finally:
        thread.join()

    assert result is winner_result


def test_no_cache_wait_times_out_and_raises_when_winner_never_resolves(monkeypatch) -> None:
    """The wedged-scan backstop (_NO_CACHE_WEDGED_SCAN_TIMEOUT_SECONDS) is
    a last resort for a genuinely stuck winner -- if its Future is never
    resolved at all, a waiting caller eventually gives up rather than
    hanging forever."""
    monkeypatch.setattr(scan_service, "_NO_CACHE_WEDGED_SCAN_TIMEOUT_SECONDS", 0.05)
    future, is_winner = scan_service._get_or_create_in_flight_future("us-east-1")
    assert is_winner
    try:
        with pytest.raises(scan_service.ScanFailedNoCacheError):
            scan_service.scan_region("us-east-1")
    finally:
        # Clean up so the fixture teardown doesn't see a dangling Future.
        if not future.done():
            future.cancel()
        scan_service._in_flight_scans.pop("us-east-1", None)


def test_no_cache_concurrent_callers_both_get_winners_result_past_old_fixed_cap(
    monkeypatch,
) -> None:
    """Regression for the design flaw fixed in this step: previously, a
    second concurrent no-cache caller raced a *fixed*
    `_NO_CACHE_WAIT_TIMEOUT_SECONDS` (60s) that had nothing to do with how
    long the winner's real scan actually took -- a real ~130s first scan
    made the second caller raise ScanFailedNoCacheError (surfaced as a
    502) even though the first request went on to succeed shortly after.
    The fix (an in-flight Future per region) means the second caller
    blocks on the winner's *actual* completion instead, with only a very
    generous "is this genuinely wedged" backstop
    (_NO_CACHE_WEDGED_SCAN_TIMEOUT_SECONDS) as a last resort.

    This test uses two real threads, synchronized with threading.Event
    (not a literal 70s+ sleep): the winner's simulated scan is held open
    by `release_winner`, and the second caller is proven to still be
    waiting -- with no error -- well after it started, while the backstop
    is set proportionally large relative to that wait (same ratio as
    production's 600s backstop vs. a real ~130s scan), never the deciding
    factor under this normal "just slow" condition.
    """
    # Scaled-down stand-in for the new, generous wedged-scan backstop
    # (production default: 600s) -- large relative to how long the
    # simulated winner below actually takes, exactly like production's
    # 600s is large relative to a real ~130s scan. Kept small in absolute
    # terms purely so this test runs fast; what matters is the *ratio*,
    # not the literal number.
    monkeypatch.setattr(scan_service, "_NO_CACHE_WEDGED_SCAN_TIMEOUT_SECONDS", 1.5)

    winner_started = threading.Event()
    release_winner = threading.Event()
    call_count = 0

    def _slow_ec2_list(region=None):
        nonlocal call_count
        call_count += 1
        winner_started.set()
        # Held open until the test explicitly releases it -- standing in
        # for a real, still-in-progress ~130s scan.
        release_winner.wait(timeout=5)
        return EC2InstanceList(instances=[], count=0)

    patches = _empty_lists_patch()
    patches[0] = patch("app.services.ec2_service.list_instances", side_effect=_slow_ec2_list)
    started_mocks = [p.start() for p in patches]
    try:
        results: list = []
        errors: list = []

        def _first_caller() -> None:
            results.append(scan_service.scan_region("us-east-1"))

        def _second_caller() -> None:
            try:
                results.append(scan_service.scan_region("us-east-1"))
            except Exception as exc:  # noqa: BLE001 - captured for assertion, not swallowed silently
                errors.append(exc)

        first = threading.Thread(target=_first_caller)
        first.start()
        assert winner_started.wait(timeout=5)  # the winner is inside its scan now

        second = threading.Thread(target=_second_caller)
        second.start()

        # Let the second caller sit blocked while the winner is still
        # "scanning" -- if the old fixed 60s-style cap (or any regression
        # back to it) were in play here, it would already have fired well
        # before the deliberately-larger backstop above, and this would
        # already have raised.
        time.sleep(0.4)
        assert not errors

        release_winner.set()
        first.join(timeout=5)
        second.join(timeout=5)
    finally:
        for p in patches:
            p.stop()

    assert not errors
    assert call_count == 1  # only the winner actually performed the scan
    assert len(results) == 2
    assert results[0] is results[1]  # both callers received the exact same result object
    assert started_mocks[0].call_count == 1


def test_get_valid_regions_concurrent_expired_ttl_does_not_double_fetch() -> None:
    """Regression: _get_valid_regions() reads/writes the module-level
    _valid_regions_cache/_valid_regions_cache_at globals with no lock.
    Harmless while scan_region() only ever ran on the single event-loop
    thread, but scan_region() now runs in FastAPI's real OS threadpool
    (see the run_in_threadpool fix in api/routes/resources.py), so two
    threads can genuinely race an expired TTL and both fire a redundant
    list_region_names() call. Forces that race with a barrier so both
    threads see the expired cache and enter the refresh branch at the
    same instant, then asserts the now-locked implementation collapses
    both into a single underlying call."""
    stale_at = datetime.now(timezone.utc) - timedelta(
        seconds=scan_service._VALID_REGIONS_TTL_SECONDS + 1
    )
    scan_service._valid_regions_cache = _FAKE_ENABLED_REGIONS
    scan_service._valid_regions_cache_at = stale_at

    call_count = 0
    # Only synchronizes the two threads' *entry* into _get_valid_regions(),
    # not the fetch itself -- once locked, the fetch is a critical section
    # by construction, so a second barrier.wait() inside the mocked fetch
    # would deadlock (thread 2 can't reach it until thread 1, which is
    # blocked on the same barrier, releases the lock).
    entry_barrier = threading.Barrier(2)

    def _slow_list_region_names():
        nonlocal call_count
        time.sleep(0.05)  # widen the race window a real network call would have
        call_count += 1
        return _FAKE_ENABLED_REGIONS

    results: list[list[str]] = []

    def _call():
        entry_barrier.wait(timeout=5)
        results.append(scan_service._get_valid_regions())

    with patch("app.services.ec2_service.list_region_names", side_effect=_slow_list_region_names):
        t1 = threading.Thread(target=_call)
        t2 = threading.Thread(target=_call)
        t1.start()
        t2.start()
        t1.join()
        t2.join()

    assert call_count == 1
    assert results == [_FAKE_ENABLED_REGIONS, _FAKE_ENABLED_REGIONS]


def test_totals_only_count_idle_resources_projected_monthly() -> None:
    with _AllEmpty() as mocks:
        mocks[0].return_value = EC2InstanceList(
            instances=[
                EC2Instance(
                    instance_id="i-idle",
                    instance_type="t3.micro",
                    state="running",
                    availability_zone="us-east-1a",
                    launch_time=datetime(2026, 1, 1, tzinfo=timezone.utc),
                    tags={},
                ),
                EC2Instance(
                    instance_id="i-active",
                    instance_type="t3.micro",
                    state="running",
                    availability_zone="us-east-1a",
                    launch_time=datetime(2026, 1, 1, tzinfo=timezone.utc),
                    tags={},
                ),
            ],
            count=2,
        )

        def _fake_idle(resource_type, resource_id, days, region=None):
            return IdleCheckResult(
                resource_id=resource_id,
                resource_type=resource_type,
                window_days=days,
                is_idle=(resource_id == "i-idle"),
                idle_since="2026-01-01" if resource_id == "i-idle" else None,
                idle_days=7 if resource_id == "i-idle" else 0,
                younger_than_window=False,
            )

        def _fake_cost(
            resource_type, resource_id, date_range=None, method="list_price", region=None
        ):
            return CostEstimate(
                resource_id=resource_id,
                resource_type=resource_type,
                date_range=DateRange(
                    start=datetime(2026, 1, 1, tzinfo=timezone.utc),
                    end=datetime(2026, 1, 8, tzinfo=timezone.utc),
                ),
                method="list_price",
                hourly_rate=0.01,
                projected_monthly=10.0,
                incurred_so_far=1.0,
            )

        with patch("app.services.idle_service.check_idle", side_effect=_fake_idle), patch(
            "app.services.cost_service.estimate_cost", side_effect=_fake_cost
        ):
            result = scan_service.scan_region("us-east-1")

    assert result.totals.monthly_spend == 20.0
    assert result.totals.idle_count == 1
    assert result.totals.idle_monthly_waste == 10.0


def test_region_is_forwarded_to_collectors_idle_and_cost_calls() -> None:
    """scan_region("eu-west-1") must actually scan eu-west-1 -- every
    per-type list_*() call, plus idle_service.check_idle/
    cost_service.estimate_cost, must receive region="eu-west-1", not the
    process-wide default."""
    with _AllEmpty() as mocks:
        ec2_mock = mocks[0]
        ec2_mock.return_value = EC2InstanceList(
            instances=[
                EC2Instance(
                    instance_id="i-1",
                    instance_type="t3.micro",
                    state="running",
                    availability_zone="eu-west-1a",
                    launch_time=datetime(2026, 1, 1, tzinfo=timezone.utc),
                    tags={},
                )
            ],
            count=1,
        )
        with patch("app.services.idle_service.check_idle") as mock_idle, patch(
            "app.services.cost_service.estimate_cost"
        ) as mock_cost:
            mock_idle.return_value = IdleCheckResult(
                resource_id="i-1",
                resource_type="ec2",
                window_days=7,
                is_idle=False,
                idle_since=None,
                idle_days=0,
                younger_than_window=False,
            )
            mock_cost.return_value = CostEstimate(
                resource_id="i-1",
                resource_type="ec2",
                date_range=DateRange(
                    start=datetime(2026, 1, 1, tzinfo=timezone.utc),
                    end=datetime(2026, 1, 8, tzinfo=timezone.utc),
                ),
                method="list_price",
                hourly_rate=0.01,
                projected_monthly=7.3,
                incurred_so_far=1.0,
            )
            scan_service.scan_region("eu-west-1")

    ec2_mock.assert_called_once_with(region="eu-west-1")
    mock_idle.assert_called_once_with(
        "ec2", "i-1", scan_service.IDLE_CHECK_WINDOW_DAYS, region="eu-west-1"
    )
    mock_cost.assert_called_once_with("ec2", "i-1", region="eu-west-1")
    # Every OTHER type's list_*() must also have been asked for eu-west-1,
    # not silently defaulted to the process-wide configured region.
    for other_mock in mocks[1:]:
        other_mock.assert_called_once_with(region="eu-west-1")


def test_two_regions_do_not_bleed_cache_or_cooldown_state() -> None:
    with _AllEmpty() as mocks:
        us_result = scan_service.scan_region("us-east-1")
        eu_result = scan_service.scan_region("eu-west-1")

        assert us_result.region == "us-east-1"
        assert eu_result.region == "eu-west-1"
        assert scan_service.get_cached_scan("us-east-1") is us_result
        assert scan_service.get_cached_scan("eu-west-1") is eu_result

        # Both regions were just scanned -- both are independently within
        # their own cooldown window right now (proves the cooldown clock
        # isn't a single shared value that one region's activity resets
        # for every region).
        with pytest.raises(scan_service.ScanCooldownActive) as us_exc:
            scan_service.scan_region("us-east-1", force=True)
        with pytest.raises(scan_service.ScanCooldownActive) as eu_exc:
            scan_service.scan_region("eu-west-1", force=True)
        assert us_exc.value.cached.region == "us-east-1"
        assert eu_exc.value.cached.region == "eu-west-1"

        # Backdating ONLY us-east-1's last attempt lets us-east-1 refresh
        # while eu-west-1 remains cooled down -- proves the cooldown clock
        # is tracked per-region, not bled across regions.
        scan_service._last_scan_attempt["us-east-1"] = datetime.now(timezone.utc) - timedelta(
            seconds=scan_service.COOLDOWN_SECONDS + 1
        )
        us_refreshed = scan_service.scan_region("us-east-1", force=True)
        assert us_refreshed.region == "us-east-1"
        assert mocks[0].call_count == 3  # us-east-1, eu-west-1, us-east-1 (forced)

        with pytest.raises(scan_service.ScanCooldownActive):
            scan_service.scan_region("eu-west-1", force=True)


def test_ec2_skip_idle_cost_exercised_through_scan_path() -> None:
    """A stopped EC2 instance must not get idle/cost looked up at all --
    exercised through scan_region() itself, not just at a lower layer."""
    with _AllEmpty() as mocks:
        mocks[0].return_value = EC2InstanceList(
            instances=[
                EC2Instance(
                    instance_id="i-stopped",
                    instance_type="t3.micro",
                    state="stopped",
                    availability_zone="us-east-1a",
                    launch_time=datetime(2026, 1, 1, tzinfo=timezone.utc),
                    tags={},
                )
            ],
            count=1,
        )
        with patch("app.services.idle_service.check_idle") as mock_idle, patch(
            "app.services.cost_service.estimate_cost"
        ) as mock_cost:
            result = scan_service.scan_region("us-east-1")

    assert len(result.resources) == 1
    assert result.resources[0].idle is None
    assert result.resources[0].cost is None
    mock_idle.assert_not_called()
    mock_cost.assert_not_called()


# =====================================================================
# _run_collectors_concurrently -- the ThreadPoolExecutor-based
# parallelization of the 15 per-type collectors, shared by _run_scan
# (exercised through scan_region() below) and list_lite_resources.
# Exercised directly against _run_collectors_concurrently (by swapping in
# trivial fake collectors via `_COLLECTORS`) rather than through the full
# 15-real-service-mock scan path used above -- this is the function
# actually doing the concurrent dispatch/reassembly, and keeping these
# collectors trivial is what makes the timing assertion below meaningful
# (a genuine multi-thread test, not a mocked-out one: real
# ThreadPoolExecutor, real worker threads, real time.sleep()).
# =====================================================================


def _fake_collectors_calling(
    calls: list[str], calls_lock: threading.Lock, *, fail_type: str | None = None
):
    """Builds a `_COLLECTORS`-shaped dict of trivial collectors, one per
    TYPE_CODES entry, each recording its own invocation (thread-safely)
    and returning a single recognizable "resource" for its type -- except
    `fail_type`, if given, which raises instead."""

    def _make(type_code: str):
        def _collector(region, lite=False):
            with calls_lock:
                calls.append(type_code)
            if type_code == fail_type:
                raise RuntimeError(f"{type_code} boom")
            return [f"{type_code}-item"]

        return _collector

    return {type_code: _make(type_code) for type_code in scan_service.TYPE_CODES}


def test_run_collectors_concurrently_calls_every_type() -> None:
    calls: list[str] = []
    fake_collectors = _fake_collectors_calling(calls, threading.Lock())

    with patch.object(scan_service, "_COLLECTORS", fake_collectors):
        result = scan_service._run_collectors_concurrently(
            "us-east-1", scan_service.TYPE_CODES, lite=False, log_prefix="test"
        )

    assert sorted(calls) == sorted(scan_service.TYPE_CODES)
    # Result order matches TYPE_CODES order regardless of which worker
    # thread happened to finish first -- proves reassembly-by-key, not
    # completion-order appending.
    assert result == [f"{type_code}-item" for type_code in scan_service.TYPE_CODES]


def test_run_collectors_concurrently_one_failure_keeps_other_fourteen(caplog) -> None:
    calls: list[str] = []
    fake_collectors = _fake_collectors_calling(calls, threading.Lock(), fail_type="redshift")

    with patch.object(scan_service, "_COLLECTORS", fake_collectors):
        with caplog.at_level("WARNING", logger="app.services.scan"):
            result = scan_service._run_collectors_concurrently(
                "us-east-1", scan_service.TYPE_CODES, lite=False, log_prefix="scan_region"
            )

    # All 15 were still called (redshift's failure didn't stop the others
    # from being submitted/run), but only 14 contributed resources.
    assert sorted(calls) == sorted(scan_service.TYPE_CODES)
    expected = [
        f"{type_code}-item" for type_code in scan_service.TYPE_CODES if type_code != "redshift"
    ]
    assert result == expected

    # The specific failing type's warning log still fires, same message
    # shape as before parallelization.
    messages = [record.getMessage() for record in caplog.records]
    assert (
        "scan_region: listing type=redshift failed in region=us-east-1, "
        "contributing 0 resources" in messages
    )


def test_run_collectors_concurrently_runs_in_parallel_not_serially() -> None:
    """Proves genuine concurrency, not accidental serialization: 15
    collectors each sleeping 0.1s must finish in well under 15*0.1s=1.5s
    (bounded by roughly the slowest collector given _SCAN_MAX_WORKERS
    workers), not the sum of all 15."""
    sleep_seconds = 0.1

    def _make(type_code: str):
        def _collector(region, lite=False):
            time.sleep(sleep_seconds)
            return []

        return _collector

    fake_collectors = {type_code: _make(type_code) for type_code in scan_service.TYPE_CODES}

    with patch.object(scan_service, "_COLLECTORS", fake_collectors):
        start = time.monotonic()
        scan_service._run_collectors_concurrently(
            "us-east-1", scan_service.TYPE_CODES, lite=False, log_prefix="test"
        )
        elapsed = time.monotonic() - start

    assert elapsed < 0.5


def test_scan_region_still_calls_all_fifteen_collector_types() -> None:
    """End-to-end (through scan_region(), not just the lower-level
    helper): every one of the 15 real per-type list_*() service calls is
    still made for a scan after parallelization, same as the old
    sequential loop -- just no longer one after another."""
    with _AllEmpty() as mocks:
        scan_service.scan_region("us-east-1")

    for mock in mocks:
        mock.assert_called_once_with(region="us-east-1")


def test_list_lite_resources_calls_every_type() -> None:
    calls: list[str] = []
    fake_collectors = _fake_collectors_calling(calls, threading.Lock())

    with patch.object(scan_service, "_COLLECTORS", fake_collectors):
        result = scan_service.list_lite_resources("us-east-1")

    assert sorted(calls) == sorted(scan_service.TYPE_CODES)
    assert result == [f"{type_code}-item" for type_code in scan_service.TYPE_CODES]


def test_list_lite_resources_one_failure_keeps_other_fourteen(caplog) -> None:
    calls: list[str] = []
    fake_collectors = _fake_collectors_calling(calls, threading.Lock(), fail_type="kinesis")

    with patch.object(scan_service, "_COLLECTORS", fake_collectors):
        with caplog.at_level("WARNING", logger="app.services.scan"):
            result = scan_service.list_lite_resources("us-east-1")

    expected = [
        f"{type_code}-item" for type_code in scan_service.TYPE_CODES if type_code != "kinesis"
    ]
    assert result == expected

    messages = [record.getMessage() for record in caplog.records]
    assert (
        "list_lite_resources: listing type=kinesis failed in region=us-east-1, "
        "contributing 0 resources" in messages
    )


def test_list_lite_resources_runs_in_parallel_not_serially() -> None:
    sleep_seconds = 0.1

    def _make(type_code: str):
        def _collector(region, lite=False):
            time.sleep(sleep_seconds)
            return []

        return _collector

    fake_collectors = {type_code: _make(type_code) for type_code in scan_service.TYPE_CODES}

    with patch.object(scan_service, "_COLLECTORS", fake_collectors):
        start = time.monotonic()
        scan_service.list_lite_resources("us-east-1")
        elapsed = time.monotonic() - start

    assert elapsed < 0.5


# =====================================================================
# _relations_for() -- roadmap 3.7. Direct, no-AWS-call unit tests of the
# function deciding every relation's exact id/label/kind triple, one per
# covered type, asserted in the exact order _relations_for() emits them
# (not just set membership) -- this is the function a typo'd kind (the
# "ebs_volume"/"load_balancer" naming bug class RelationKind's Literal now
# also guards against at the model layer) would slip through undetected if
# only the model-level type were checked and never the actual emitted
# values.
# =====================================================================


def test_relations_for_ec2_multi_sg_and_volumes() -> None:
    instance = EC2Instance(
        instance_id="i-1",
        instance_type="t3.micro",
        state="running",
        availability_zone="us-east-1a",
        security_group_ids=["sg-1", "sg-2"],
        subnet_id="subnet-1",
        vpc_id="vpc-1",
        iam_instance_profile_name="my-profile",
        attached_volume_ids=["vol-1", "vol-2"],
    )
    relations = scan_service._relations_for("ec2", instance)
    assert [r.model_dump() for r in relations] == [
        {"id": "vol-1", "label": "attached", "kind": "ebs"},
        {"id": "vol-2", "label": "attached", "kind": "ebs"},
        {"id": "sg-1", "label": "secured_by", "kind": "security_group"},
        {"id": "sg-2", "label": "secured_by", "kind": "security_group"},
        {"id": "subnet-1", "label": "in", "kind": "subnet"},
        {"id": "vpc-1", "label": "in", "kind": "vpc"},
        {"id": "my-profile", "label": "assumes", "kind": "iam_role"},
    ]


def test_relations_for_ec2_no_linkage_is_empty() -> None:
    instance = EC2Instance(
        instance_id="i-2",
        instance_type="t3.micro",
        state="running",
        availability_zone="us-east-1a",
    )
    assert scan_service._relations_for("ec2", instance) == []


def test_relations_for_ebs_reverse_attached_to_ec2() -> None:
    volume = EbsVolume(
        volume_id="vol-1",
        size_gb=8,
        volume_type="gp3",
        state="in-use",
        availability_zone="us-east-1a",
        attached_instance_ids=["i-1"],
    )
    relations = scan_service._relations_for("ebs", volume)
    assert [r.model_dump() for r in relations] == [
        {"id": "i-1", "label": "attached", "kind": "ec2"},
    ]


def test_relations_for_rds_multi_sg_and_subnets() -> None:
    instance = RdsInstanceSummary(
        identifier="db-1",
        engine="postgres",
        instance_class="db.t3.micro",
        status="available",
        vpc_security_group_ids=["sg-1", "sg-2"],
        subnet_ids=["subnet-1", "subnet-2"],
        vpc_id="vpc-1",
    )
    relations = scan_service._relations_for("rds", instance)
    assert [r.model_dump() for r in relations] == [
        {"id": "sg-1", "label": "secured_by", "kind": "security_group"},
        {"id": "sg-2", "label": "secured_by", "kind": "security_group"},
        {"id": "subnet-1", "label": "in", "kind": "subnet"},
        {"id": "subnet-2", "label": "in", "kind": "subnet"},
        {"id": "vpc-1", "label": "in", "kind": "vpc"},
    ]


def test_relations_for_eip_attached_to_ec2() -> None:
    addr = ElasticIp(
        allocation_id="eipalloc-1",
        public_ip="1.2.3.4",
        domain="vpc",
        instance_id="i-1",
    )
    relations = scan_service._relations_for("eip", addr)
    assert [r.model_dump() for r in relations] == [
        {"id": "i-1", "label": "attached", "kind": "ec2"},
    ]


def test_relations_for_eip_unassociated_is_empty() -> None:
    addr = ElasticIp(allocation_id="eipalloc-1", public_ip="1.2.3.4", domain="vpc")
    assert scan_service._relations_for("eip", addr) == []


def test_relations_for_elb_multi_sg_and_subnets() -> None:
    lb = LoadBalancer(
        name="my-alb",
        lb_type="application",
        arn="arn:aws:elasticloadbalancing:us-east-1:123:loadbalancer/app/my-alb/abc",
        state="active",
        security_group_ids=["sg-1", "sg-2"],
        subnet_ids=["subnet-1", "subnet-2"],
        vpc_id="vpc-1",
    )
    relations = scan_service._relations_for("elb", lb)
    assert [r.model_dump() for r in relations] == [
        {"id": "sg-1", "label": "secured_by", "kind": "security_group"},
        {"id": "sg-2", "label": "secured_by", "kind": "security_group"},
        {"id": "subnet-1", "label": "in", "kind": "subnet"},
        {"id": "subnet-2", "label": "in", "kind": "subnet"},
        {"id": "vpc-1", "label": "in", "kind": "vpc"},
    ]


def test_relations_for_lambda_vpc_attached() -> None:
    fn = LambdaFunctionSummary(
        name="fn-1",
        role_name="my-role",
        security_group_ids=["sg-1"],
        subnet_ids=["subnet-1"],
        vpc_id="vpc-1",
    )
    relations = scan_service._relations_for("lambda", fn)
    assert [r.model_dump() for r in relations] == [
        {"id": "my-role", "label": "assumes", "kind": "iam_role"},
        {"id": "sg-1", "label": "secured_by", "kind": "security_group"},
        {"id": "subnet-1", "label": "in", "kind": "subnet"},
        {"id": "vpc-1", "label": "in", "kind": "vpc"},
    ]


def test_relations_for_lambda_no_vpc_attachment_edge_case() -> None:
    """The documented no-VPC-attachment edge case (roadmap 3.7): the IAM
    role relation is still present (Lambda always has a role, VPC or not),
    but no VPC/security-group/subnet relations get built for a function
    outside a VPC."""
    fn = LambdaFunctionSummary(name="fn-2", role_name="my-role-2")
    relations = scan_service._relations_for("lambda", fn)
    assert [r.model_dump() for r in relations] == [
        {"id": "my-role-2", "label": "assumes", "kind": "iam_role"},
    ]


def test_relations_for_lambda_no_role_is_empty() -> None:
    fn = LambdaFunctionSummary(name="fn-3")
    assert scan_service._relations_for("lambda", fn) == []


def test_relations_for_nat_gateway_subnet_and_vpc() -> None:
    gw = NatGateway(
        nat_gateway_id="nat-1", state="available", subnet_id="subnet-1", vpc_id="vpc-1"
    )
    relations = scan_service._relations_for("nat_gateway", gw)
    assert [r.model_dump() for r in relations] == [
        {"id": "subnet-1", "label": "in", "kind": "subnet"},
        {"id": "vpc-1", "label": "in", "kind": "vpc"},
    ]


def test_relations_for_elasticache_multi_sg() -> None:
    cluster = ElastiCacheCluster(
        cache_cluster_id="cache-1",
        node_type="cache.t3.micro",
        engine="redis",
        status="available",
        security_group_ids=["sg-1", "sg-2"],
    )
    relations = scan_service._relations_for("elasticache", cluster)
    assert [r.model_dump() for r in relations] == [
        {"id": "sg-1", "label": "secured_by", "kind": "security_group"},
        {"id": "sg-2", "label": "secured_by", "kind": "security_group"},
    ]


def test_relations_for_redshift_sg_and_vpc() -> None:
    cluster = RedshiftCluster(
        cluster_identifier="cl-1",
        node_type="dc2.large",
        status="available",
        vpc_security_group_ids=["sg-1"],
        vpc_id="vpc-1",
    )
    relations = scan_service._relations_for("redshift", cluster)
    assert [r.model_dump() for r in relations] == [
        {"id": "sg-1", "label": "secured_by", "kind": "security_group"},
        {"id": "vpc-1", "label": "in", "kind": "vpc"},
    ]


def test_relations_for_opensearch_sg_subnet_and_vpc() -> None:
    domain = OpenSearchDomain(
        domain_name="my-domain",
        arn="arn:aws:es:us-east-1:123456789012:domain/my-domain",
        security_group_ids=["sg-1"],
        subnet_ids=["subnet-1"],
        vpc_id="vpc-1",
    )
    relations = scan_service._relations_for("opensearch", domain)
    assert [r.model_dump() for r in relations] == [
        {"id": "sg-1", "label": "secured_by", "kind": "security_group"},
        {"id": "subnet-1", "label": "in", "kind": "subnet"},
        {"id": "vpc-1", "label": "in", "kind": "vpc"},
    ]


def test_relations_for_documented_gap_types_always_empty() -> None:
    """dynamodb, sagemaker, api_gateway, cloudfront, kinesis carry no VPC/
    security-group/IAM linkage in their existing list/describe response
    (see _relations_for()'s own docstring) -- always [] regardless of the
    object passed in."""
    for type_code in ("dynamodb", "sagemaker", "api_gateway", "cloudfront", "kinesis"):
        assert scan_service._relations_for(type_code, object()) == []
