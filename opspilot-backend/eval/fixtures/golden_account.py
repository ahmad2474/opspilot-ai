"""Deterministic, versioned "golden" AWS accounts (roadmap phase 2
Section 2.1).

Built on `moto`, not `unittest.mock` -- a genuinely new mocking pattern
for this repo (every existing test under `tests/` mocks a
`get_*_client()` factory function directly, per that layer's own
convention). This module exists specifically so the rest of `eval/` never
has to touch moto/freezegun details directly.

Why moto here and not the boto3-client-mock convention `tests/` uses:
the whole point of the eval harness (roadmap 2.0-2.2) is generating a
provably-correct oracle by calling the *real* `services/` functions
against *real*-shaped AWS responses, then separately letting the chat
agent investigate the *same* backing data through its own tool calls.
Hand-mocking a `boto3` client's return value only ever produces the one
response shape the test author already decided on -- it can't be probed
two independent ways (oracle vs. agent) the way a real (fake) AWS backend
can, since both paths hit the same moto-simulated account state.

Verified empirically, not assumed (see docs/opspilot-ai-roadmap-phase2.md
"Additional real context" note): a `with mock_aws():` block correctly
intercepts every `get_*_client()` factory in app/aws/client.py, including
EC2, EBS (same EC2 client), EIP (same EC2 client), RDS, CloudWatch (both
`put_metric_data` and `get_metric_statistics` with `Period=86400` daily
aggregation), and DynamoDB -- despite `_session()` being `@lru_cache`d and
client construction going through `_client_creation_lock`. moto patches at
the botocore request layer, not at session/client-construction time, so
neither of those pre-existing patterns interferes.

`freezegun.freeze_time` is used to backdate `LaunchTime`/`CreateTime`/
`InstanceCreateTime` -- moto stamps these with the real wall-clock time at
creation and does not accept a caller-supplied timestamp for them, so
"an EC2 instance launched 30 days ago" is only achievable by actually
creating it while time is frozen 30 days in the past.

**A genuine gap, found by running this, not by reading moto's docs**: the
AWS Pricing API (`pricing`, used by `cost_service.py`'s
`_get_ec2_hourly_rate`/`extract_usd_price` for every instance-hour-priced
resource type) is not in moto's supported-service list at all
(`moto.backends.list_of_moto_modules()` has no `"pricing"` entry).
`mock_aws()` still intercepts the call rather than leaking it to the real
network (moto patches botocore's transport layer globally, for every
service, not per an allowlist -- verified by calling `get_products`
inside a live `mock_aws()` block and observing a clean, local
`ClientError("Not yet implemented")`, never a real HTTP request), so
there is no risk of real AWS spend/traffic. But it does mean every
Pricing-API-dependent cost figure comes back as a soft "lookup failed"
miss (scan_service.py's own documented per-resource graceful-degradation
path, roadmap 1.4's "cost being None means this lookup failed, not zero
cost") rather than a real number -- which would make every dollar-amount
eval case in this harness untestable for exactly the resource types that
most need list_price coverage (EC2/RDS/EBS). `fake_pricing_client` below
closes that gap the same way `tests/` already mocks any one boto3 call
this repo doesn't want hitting a real (or moto-simulated) backend: patch
`cost_service.get_pricing_client` directly, at the exact import site
(`app.services.cost_service.get_pricing_client`, mirroring every existing
`@patch("app.services.X.get_Y_client")` in tests/), returning a fixed,
real-shaped `GetProducts` response. This is a deliberate stand-in for one
specific AWS API moto doesn't implement, not a workaround for a moto bug
-- everything else in this fixture is real moto.
"""
from __future__ import annotations

import json
import os
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Iterator
from unittest.mock import MagicMock, patch

from freezegun import freeze_time
from moto import mock_aws

from app.aws.client import get_cloudwatch_client, get_ec2_client, get_rds_client

REGION = "us-east-1"

# Demo-scope idle/active thresholds this fixture is built to straddle --
# see app/services/idle_service.py for the real threshold constants these
# values are deliberately placed well on either side of, so the fixture
# never sits ambiguously close to a threshold and isn't a source of flake.
_IDLE_CPU_PERCENT = 0.5
_ACTIVE_CPU_PERCENT = 55.0
_IDLE_NETWORK_BYTES = 1024.0  # well under NETWORK_IDLE_THRESHOLD_BYTES (5MB)
_ACTIVE_NETWORK_BYTES = 50 * 1024 * 1024.0  # well over it


def moto_test_env() -> dict[str, str]:
    """Fake AWS credentials -- required so boto3 never falls through to a
    real credential chain (e.g. an operator's own ~/.aws/credentials) even
    before moto gets a chance to intercept the call. Standard moto
    convention, not this app's own credential handling (app/aws/client.py
    deliberately never touches AWS_ACCESS_KEY_ID/SECRET itself either way
    -- see its module docstring)."""
    return {
        "AWS_ACCESS_KEY_ID": "eval-harness-testing",
        "AWS_SECRET_ACCESS_KEY": "eval-harness-testing",
        "AWS_SECURITY_TOKEN": "eval-harness-testing",
        "AWS_SESSION_TOKEN": "eval-harness-testing",
        "AWS_DEFAULT_REGION": REGION,
        "AWS_REGION": REGION,
    }


@contextmanager
def moto_env() -> Iterator[None]:
    """Sets the fake credential env vars for the duration of the block,
    restoring whatever was there before on exit -- so a developer running
    this locally with real AWS credentials in their shell never has those
    leak into (or get shadowed oddly around) a moto-mocked test."""
    previous = {key: os.environ.get(key) for key in moto_test_env()}
    os.environ.update(moto_test_env())
    try:
        yield
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


# Real, published on-demand rate for t3.micro Linux Shared in us-east-1
# at time of writing -- used as the fixed stand-in rate every
# fake_pricing_client() call returns, regardless of what instance type/
# resource type actually asked (see module docstring for why a stand-in
# is needed at all). Not asserted against directly by any eval case's
# *exact* dollar figure (the roadmap's own tolerance_number_match exists
# precisely because Pricing API values drift) -- just needs to be a real,
# plausible, non-zero rate so cost-related checks have something genuine
# to compare against.
_FAKE_HOURLY_RATE_USD = 0.0104


def _fake_price_list_json(usd_price_per_unit: float) -> str:
    """One `PriceList[0]` entry shaped exactly the way
    `cost_service.extract_usd_price` parses it
    (`terms.OnDemand.<any key>.priceDimensions.<any key>.pricePerUnit.USD`)
    -- the SKU/rate-code keys are fake but their *shape* (arbitrary string
    keys wrapping the object `extract_usd_price` actually reads) matches
    the real Price List API's own JSON exactly.
    """
    price_dimension = {"pricePerUnit": {"USD": str(usd_price_per_unit)}}
    price_dimensions = {"FAKE.SKU.OFFER.PRICE": price_dimension}
    offer = {"FAKE.SKU.OFFER": {"priceDimensions": price_dimensions}}
    return json.dumps({"terms": {"OnDemand": offer}})


@contextmanager
def fake_pricing_client(usd_price_per_unit: float = _FAKE_HOURLY_RATE_USD) -> Iterator[MagicMock]:
    """Stands in for the AWS Pricing API, which moto does not implement
    (see this module's docstring for the empirical verification) --
    patches `cost_service.get_pricing_client` directly, the exact
    "mock the boto3 client factory at its import site" convention every
    test under tests/ already uses (e.g.
    `@patch("app.services.ec2_service.get_ec2_client")`), just applied
    here as a context manager instead of a decorator since it needs to
    stay active for the same `with` block moto's `mock_aws()` does.

    Returns the same fixed on-demand rate for every `get_products` call
    regardless of the actual filters passed -- deliberately not
    per-instance-type-accurate (this fixture doesn't need EC2 vs. RDS vs.
    EBS to have realistically *different* rates, only for every rate to
    be a real, non-null number an eval case's tolerance_number_match
    check can compare against).
    """
    fake_client = MagicMock()
    fake_client.get_products.return_value = {
        "PriceList": [_fake_price_list_json(usd_price_per_unit)]
    }
    with patch("app.services.cost_service.get_pricing_client", return_value=fake_client):
        yield fake_client


def _seed_daily_metric(
    namespace: str,
    metric_name: str,
    dimension_name: str,
    dimension_value: str,
    days: int,
    value: float,
    unit: str = "Count",
) -> None:
    """Puts one datapoint per calendar day for the last `days` days
    (including today), matching exactly the daily-resolution shape
    `cloudwatch_service.get_daily_datapoints` queries for (Period=86400).

    `unit` MUST match the `Unit` that `get_daily_datapoints` requests for
    this metric (e.g. "Percent" for CPUUtilization, "Bytes" for
    NetworkIn/Out) -- verified empirically that CloudWatch's (and moto's)
    GetMetricStatistics silently returns zero datapoints when the stored
    Unit doesn't match the requested one, exactly like the real API. This
    isn't a documented moto quirk, it's real CloudWatch semantics --
    caught by actually running this fixture and inspecting the oracle's
    output rather than assuming the seed would "just work."
    """
    cw = get_cloudwatch_client()
    now = datetime.now(timezone.utc)
    for day_offset in range(days):
        # A couple of hours before "now" each day so no two puts land in
        # the same UTC calendar bucket regardless of what time of day the
        # harness happens to run at.
        timestamp = now - timedelta(days=day_offset, hours=2)
        cw.put_metric_data(
            Namespace=namespace,
            MetricData=[
                {
                    "MetricName": metric_name,
                    "Dimensions": [{"Name": dimension_name, "Value": dimension_value}],
                    "Timestamp": timestamp,
                    "Value": value,
                    "Unit": unit,
                }
            ],
        )


@dataclass(frozen=True)
class GoldenAccountV1:
    """Resource identifiers for `golden_account_v1` -- every eval case
    references resources by these fields rather than hardcoding IDs
    moto generates randomly per run.
    """

    region: str
    idle_ec2_instance_id: str
    active_ec2_instance_id: str
    unattached_ebs_volume_id: str
    unattached_ebs_create_time: datetime
    unassociated_eip_allocation_id: str
    idle_rds_identifier: str
    # Window (days) every idle-related eval case in this fixture's family
    # is written against -- centralized here so a case's YAML and this
    # fixture's actual seeded history can never silently drift apart.
    idle_window_days: int = 7
    # How many days of real history were seeded for the *idle* EC2/RDS
    # resources -- deliberately more than idle_window_days so "is idle for
    # the full requested window" is unambiguous, not a boundary case.
    metric_history_days: int = 10
    # The unattached EBS volume's real age -- deliberately younger than
    # idle_window_days, to exercise the "resource younger than the
    # requested window" edge case (see eval/cases/young_resource_edge_case.yaml).
    young_resource_age_days: int = 5


def build_golden_account_v1(*, injected_name_tag: str | None = None) -> GoldenAccountV1:
    """Provisions golden_account_v1 against the currently-active
    `mock_aws()` context (the caller owns entering/exiting that context --
    see the `golden_account_v1` pytest fixture in eval/conftest.py).

    `injected_name_tag`: when set, the idle EC2 instance's Name tag is
    replaced with this value instead of a plain "opspilot-idle-ec2" --
    used by golden_account_injection_v1 (roadmap 2.3's tag_injection.yaml)
    to plant an embedded-instruction Name tag on an otherwise-identical
    idle resource. Every other resource is unaffected.
    """
    ec2 = get_ec2_client()
    now = datetime.now(timezone.utc)

    # --- EC2: one idle, one active, both "launched" well outside every
    # window this fixture's cases use, so idle status is driven purely by
    # the seeded CloudWatch history, never by younger-than-window logic.
    launch_time_ago = now - timedelta(days=30)
    with freeze_time(launch_time_ago):
        idle_name = injected_name_tag or "opspilot-idle-ec2"
        idle_resp = ec2.run_instances(
            ImageId="ami-0abcdef1234567890",
            MinCount=1,
            MaxCount=1,
            InstanceType="t3.micro",
            TagSpecifications=[
                {"ResourceType": "instance", "Tags": [{"Key": "Name", "Value": idle_name}]}
            ],
        )
        idle_ec2_id = idle_resp["Instances"][0]["InstanceId"]

        active_resp = ec2.run_instances(
            ImageId="ami-0abcdef1234567890",
            MinCount=1,
            MaxCount=1,
            InstanceType="t3.micro",
            TagSpecifications=[
                {
                    "ResourceType": "instance",
                    "Tags": [{"Key": "Name", "Value": "opspilot-active-ec2"}],
                }
            ],
        )
        active_ec2_id = active_resp["Instances"][0]["InstanceId"]

    history_days = GoldenAccountV1.metric_history_days
    _seed_daily_metric(
        "AWS/EC2", "CPUUtilization", "InstanceId", idle_ec2_id, history_days,
        _IDLE_CPU_PERCENT, unit="Percent",
    )
    _seed_daily_metric(
        "AWS/EC2", "NetworkIn", "InstanceId", idle_ec2_id, history_days,
        _IDLE_NETWORK_BYTES, unit="Bytes",
    )
    _seed_daily_metric(
        "AWS/EC2", "NetworkOut", "InstanceId", idle_ec2_id, history_days,
        _IDLE_NETWORK_BYTES, unit="Bytes",
    )

    _seed_daily_metric(
        "AWS/EC2", "CPUUtilization", "InstanceId", active_ec2_id, history_days,
        _ACTIVE_CPU_PERCENT, unit="Percent",
    )
    _seed_daily_metric(
        "AWS/EC2", "NetworkIn", "InstanceId", active_ec2_id, history_days,
        _ACTIVE_NETWORK_BYTES, unit="Bytes",
    )
    _seed_daily_metric(
        "AWS/EC2", "NetworkOut", "InstanceId", active_ec2_id, history_days,
        _ACTIVE_NETWORK_BYTES, unit="Bytes",
    )

    # --- EBS: unattached, young (5 days old) -- the "resource younger
    # than the requested idle window" edge case. Unattached short-circuits
    # idle_service._check_idle_ebs straight to _instant_idle_result, which
    # is where younger_than_window/idle_since_is_estimated actually get
    # computed -- no CloudWatch history needed for this one.
    young_age_days = GoldenAccountV1.young_resource_age_days
    ebs_create_time = now - timedelta(days=young_age_days)
    with freeze_time(ebs_create_time):
        vol_resp = ec2.create_volume(AvailabilityZone=f"{REGION}a", Size=8, VolumeType="gp3")
        ebs_volume_id = vol_resp["VolumeId"]

    # --- EIP: unassociated. No creation timestamp exists for this type at
    # all (see app/models/eip.py's docstring) -- this is the fixture that
    # genuinely exercises idle_since_is_estimated=True, a real field only
    # the point-in-time-signal types (EIP, unattached-EBS-without-a-
    # younger-than-window create_time) ever set (see
    # app/models/idle.py's IdleCheckResult.idle_since_is_estimated
    # docstring) -- verified against the actual code, not assumed from
    # the phase-2 doc's own illustrative RDS example (see eval/cases/
    # young_resource_edge_case.yaml's module note for why this fixture
    # deliberately diverges from that doc's specific resource-type choice).
    eip_resp = ec2.allocate_address(Domain="vpc")
    eip_allocation_id = eip_resp["AllocationId"]

    # --- RDS: idle (DatabaseConnections=0 for the full window), created
    # well outside every window this fixture's cases use.
    rds = get_rds_client()
    rds_launch_ago = now - timedelta(days=20)
    idle_rds_id = "opspilot-idle-rds"
    with freeze_time(rds_launch_ago):
        rds.create_db_instance(
            DBInstanceIdentifier=idle_rds_id,
            DBInstanceClass="db.t3.micro",
            Engine="postgres",
            MasterUsername="opspilot",
            MasterUserPassword="eval-harness-testing-password",
            AllocatedStorage=20,
        )
    _seed_daily_metric(
        "AWS/RDS", "DatabaseConnections", "DBInstanceIdentifier", idle_rds_id, history_days, 0.0
    )

    return GoldenAccountV1(
        region=REGION,
        idle_ec2_instance_id=idle_ec2_id,
        active_ec2_instance_id=active_ec2_id,
        unattached_ebs_volume_id=ebs_volume_id,
        unattached_ebs_create_time=ebs_create_time,
        unassociated_eip_allocation_id=eip_allocation_id,
        idle_rds_identifier=idle_rds_id,
    )


__all__ = [
    "REGION",
    "GoldenAccountV1",
    "build_golden_account_v1",
    "moto_env",
    "mock_aws",
    "fake_pricing_client",
]
