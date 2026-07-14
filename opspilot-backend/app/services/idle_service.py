"""Idle-detection business logic (roadmap Section 3.1).

check_idle(resource_type, resource_id, days) is parameterized by
resource_type up front, so extending across the 15 types (Section 2a) is
additive (add a branch + a per-type CloudWatch signal), never a rewrite of
the tool/model/response shape (roadmap: "this tool is parameterized per
type, not rewritten per type"). Implemented: ec2, ebs, rds, eip, elb
(Step 3 batch A) plus lambda, nat_gateway, dynamodb, elasticache,
sagemaker, redshift, api_gateway, cloudfront, opensearch, kinesis (Step 3
batch B) -- all 15 roadmap resource types.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from app.models.idle import IdleCheckResult
from app.services import (
    api_gateway_service,
    cloudfront_service,
    cloudwatch_service,
    dynamodb_service,
    ebs_service,
    ec2_service,
    eip_service,
    elasticache_service,
    elb_service,
    kinesis_service,
    lambda_service,
    nat_gateway_service,
    opensearch_service,
    rds_service,
    redshift_service,
    sagemaker_service,
)

# --- EC2 idle thresholds (Section 2a: "CPUUtilization ~= 0% AND
# NetworkIn/NetworkOut ~= 0 for every day in window") ----------------------
# "~= 0" is never literally exact-zero in practice (background OS chatter,
# health-check pings) -- these are demo-scope thresholds, not derived from
# real traffic analysis. Flagging as an assumption worth revisiting once
# real account data is available.
CPU_IDLE_THRESHOLD_PERCENT = 2.0
NETWORK_IDLE_THRESHOLD_BYTES = 5 * 1024 * 1024  # 5 MB/day, summed in + out

# --- EBS idle threshold (Section 2a: "attached with VolumeReadOps/
# VolumeWriteOps ~= 0") -- demo-scope threshold, same rationale as above.
EBS_IO_IDLE_THRESHOLD_OPS = 1.0  # ops/day, per metric (read, write)

# --- RDS idle threshold (Section 2a: "DatabaseConnections = 0 for every
# day"). Checked via the Maximum statistic, not Average -- a brief
# connection spike could average down near zero over a full day but still
# represent real, non-idle usage; Maximum catches "any connection at all
# that day" the way a literal "=0" reading requires.
DB_CONNECTIONS_IDLE_THRESHOLD = 1.0

# --- ELB idle threshold (Section 2a: "RequestCount = 0 for every day").
# Checked via Sum (RequestCount is a count metric, not a percentage).
REQUEST_COUNT_IDLE_THRESHOLD = 1.0

# =====================================================================
# Step 3 batch B thresholds (Lambda, NAT Gateway, DynamoDB, ElastiCache,
# SageMaker, Redshift, API Gateway, CloudFront, OpenSearch, Kinesis).
# Same "demo-scope, not derived from real traffic analysis" caveat as the
# batch A thresholds above applies to every constant below.
# =====================================================================

# Lambda (Section 2a: "Invocation count = 0 over window"). AWS/Lambda's
# Invocations metric is a sparse "activity" metric -- CloudWatch publishes
# no datapoint at all for a period with zero invocations, rather than a
# datapoint valued 0 (unlike EC2 CPUUtilization/EBS Volume*Ops/RDS
# DatabaseConnections, which are always-on gauges published every period
# regardless of value). See _check_idle_via_metrics' zero_fill_missing_days
# parameter below for how this is handled -- every branch in this batch
# that shares this "sparse count metric" shape (Lambda, NAT Gateway,
# DynamoDB, SageMaker, API Gateway, CloudFront, Kinesis) passes
# zero_fill_missing_days=True; the three that are continuous gauges like
# batch A (ElastiCache CurrConnections, Redshift DatabaseConnections,
# OpenSearch SearchRate/IndexingRate) do not.
LAMBDA_INVOCATIONS_IDLE_THRESHOLD = 1.0

# NAT Gateway (Section 2a: "BytesOutToDestination/BytesInFromSource ~= 0").
# Same order of magnitude as EC2's NETWORK_IDLE_THRESHOLD_BYTES.
NAT_GATEWAY_BYTES_IDLE_THRESHOLD = 5 * 1024 * 1024  # 5 MB/day, per metric

# DynamoDB (Section 2a: "Consumed read/write capacity ~= 0"). Applies to
# both PROVISIONED and PAY_PER_REQUEST (on-demand) tables -- on-demand
# tables still publish ConsumedReadCapacityUnits/ConsumedWriteCapacityUnits
# (translated from actual request volume into capacity-unit terms), they
# just have no *Provisioned*CapacityUnits metric to compare against. There
# is no meaningful "provisioned but unused" signal for on-demand tables the
# way there is for EC2/RDS, but "consumed capacity ~= 0" still correctly
# identifies "nobody is reading or writing this table," which is the
# actual waste signal worth surfacing regardless of billing mode.
DYNAMODB_CAPACITY_IDLE_THRESHOLD = 1.0

# ElastiCache (Section 2a: "CurrConnections ~= 0"). Checked via Maximum,
# same "catch any connection at all that day" rationale as RDS
# DatabaseConnections above -- CurrConnections is a continuous gauge
# (always published), not a sparse count metric.
ELASTICACHE_CONNECTIONS_IDLE_THRESHOLD = 1.0

# SageMaker (Section 2a: "Invocation count = 0 -- often the biggest silent
# cost, runs 24/7 by default").
SAGEMAKER_INVOCATIONS_IDLE_THRESHOLD = 1.0

# Redshift (Section 2a: "DatabaseConnections ~= 0"). Same Maximum
# rationale as RDS -- continuous gauge, not sparse.
REDSHIFT_CONNECTIONS_IDLE_THRESHOLD = 1.0

# API Gateway (Section 2a: "Count (requests) = 0"). REST APIs only -- see
# api_gateway_service.py's module docstring for why.
API_GATEWAY_REQUEST_IDLE_THRESHOLD = 1.0

# CloudFront (Section 2a: "Requests = 0"). Metrics only publish to
# us-east-1 regardless of the account's configured region -- see
# _check_idle_cloudfront below.
CLOUDFRONT_REQUEST_IDLE_THRESHOLD = 1.0

# OpenSearch (Section 2a: "Search/index rate ~= 0"). AWS/ES's SearchRate/
# IndexingRate are continuous domain-health gauges (the domain is always
# running and always reports a rate, even 0), not sparse count metrics --
# same "no zero_fill needed" bucket as ElastiCache/Redshift above.
OPENSEARCH_RATE_IDLE_THRESHOLD = 1.0

# Kinesis (Section 2a: "IncomingRecords ~= 0").
KINESIS_INCOMING_RECORDS_IDLE_THRESHOLD = 1.0


class UnsupportedResourceTypeError(ValueError):
    """Raised when check_idle is asked about a type not yet built."""


_SUPPORTED_RESOURCE_TYPES = {
    "ec2",
    "ebs",
    "rds",
    "eip",
    "elb",
    "lambda",
    "nat_gateway",
    "dynamodb",
    "elasticache",
    "sagemaker",
    "redshift",
    "api_gateway",
    "cloudfront",
    "opensearch",
    "kinesis",
}


def check_idle(
    resource_type: str, resource_id: str, days: int, region: str | None = None
) -> IdleCheckResult:
    """`region` overrides the configured default region -- needed for
    region-wide scanning (roadmap 3.3), where a caller (scan_service) asks
    about a resource in a region other than the process-wide default.
    None/omitted behaves exactly as before (the configured default
    region), unchanged for every existing single-region caller.
    """
    if resource_type not in _SUPPORTED_RESOURCE_TYPES:
        raise UnsupportedResourceTypeError(
            f"check_idle for resource_type={resource_type!r} is not supported -- "
            f"only {sorted(_SUPPORTED_RESOURCE_TYPES)!r} (the roadmap's 15 in-scope "
            "types) are implemented."
        )
    if resource_type == "ec2":
        return _check_idle_ec2(resource_id, days, region)
    if resource_type == "ebs":
        return _check_idle_ebs(resource_id, days, region)
    if resource_type == "rds":
        return _check_idle_rds(resource_id, days, region)
    if resource_type == "eip":
        return _check_idle_eip(resource_id, days, region)
    if resource_type == "elb":
        return _check_idle_elb(resource_id, days, region)
    if resource_type == "lambda":
        return _check_idle_lambda(resource_id, days, region)
    if resource_type == "nat_gateway":
        return _check_idle_nat_gateway(resource_id, days, region)
    if resource_type == "dynamodb":
        return _check_idle_dynamodb(resource_id, days, region)
    if resource_type == "elasticache":
        return _check_idle_elasticache(resource_id, days, region)
    if resource_type == "sagemaker":
        return _check_idle_sagemaker(resource_id, days, region)
    if resource_type == "redshift":
        return _check_idle_redshift(resource_id, days, region)
    if resource_type == "api_gateway":
        return _check_idle_api_gateway(resource_id, days, region)
    if resource_type == "cloudfront":
        return _check_idle_cloudfront(resource_id, days, region)
    if resource_type == "opensearch":
        return _check_idle_opensearch(resource_id, days, region)
    return _check_idle_kinesis(resource_id, days, region)


def _to_utc_date(value: datetime) -> date:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).date()


def _bucket_by_day(datapoints: list) -> dict[date, float]:
    """Last datapoint wins per day (there should only be one per day at
    Period=86400, but sorted-by-timestamp makes 'last wins' deterministic
    if CloudWatch ever returns more than one for a partial/boundary day)."""
    by_day: dict[date, float] = {}
    for dp in datapoints:
        if dp.average is None:
            continue
        by_day[_to_utc_date(dp.timestamp)] = dp.average
    return by_day


def _trailing_idle_streak(all_days: list[date], is_day_idle) -> tuple[date | None, int]:
    """Walk backward from the most recent day to the first day that breaks
    the idle condition, +1 (roadmap 3.1's exact 'idle since' definition).
    Returns (None, 0) if the most recent day itself isn't idle -- there is
    no current streak.
    """
    if not all_days or not is_day_idle(all_days[-1]):
        return None, 0

    streak_start = all_days[-1]
    for day in reversed(all_days):
        if is_day_idle(day):
            streak_start = day
        else:
            break

    idle_days = (all_days[-1] - streak_start).days + 1
    return streak_start, idle_days


def _not_idle_result(resource_id: str, resource_type: str, days: int) -> IdleCheckResult:
    """Conservative 'no verdict' result -- used both when a resource can't
    be found at all (mirrors EC2's own 'no datapoints' leniency: never
    fabricate an idle verdict from missing data) and when a resource is
    confirmed *not* idle by a point-in-time signal that has no time series
    to derive idle_since/idle_days from (e.g. an associated EIP)."""
    return IdleCheckResult(
        resource_id=resource_id,
        resource_type=resource_type,
        window_days=days,
        is_idle=False,
        idle_since=None,
        idle_days=0,
        younger_than_window=False,
        idle_since_is_estimated=False,
    )


def _instant_idle_result(
    resource_id: str, resource_type: str, days: int, create_time: datetime | None
) -> IdleCheckResult:
    """Shared design for resource types whose idle signal is a point-in-time
    boolean rather than a CloudWatch time series (EBS unattached, EIP
    unassociated) -- there is no "first day it broke a threshold" the way
    there is for a metric-driven check, so idle_since/idle_days can't be
    derived by walking datapoints backward the way _trailing_idle_streak
    does.

    Design decision (documented per roadmap instructions, since this is a
    genuinely different shape of idle signal, not just a simpler one):
    - If the resource is younger than the requested window (create_time
      available and recent, e.g. EBS), report "idle since creation" --
      same younger-than-window rule as every other type, never a
      fabricated longer window.
    - Otherwise, report idle_since = the start of the requested window and
      idle_days = the full window. We deliberately do NOT claim the
      resource has been idle for longer than we were asked to check --
      unlike a CloudWatch time series, a boolean attachment/association
      state has no historical record we can walk backward through (AWS
      doesn't expose "date last detached/disassociated"), so "idle since
      window start" is the most we can honestly assert, never "idle since
      it was created" when create_time is unknown or far in the past.
    - EIP has no create_time at all (DescribeAddresses exposes no
      allocation timestamp) -- create_time is always None for EIP calls
      into this helper, so it always falls into the "idle since window
      start, idle_days = days" branch.

    idle_since_is_estimated (roadmap Section 3.1/data-schema follow-up):
    the "idle since window start, idle_days = days" branch is a worst-case
    assumption ("known idle for at least the requested window"), not a
    verified streak -- an EIP disassociated 5 minutes ago and one
    genuinely idle a full week both report idle_days=days here, and a
    consumer has no way to tell them apart without this flag. Set True
    only in that branch; the younger-than-window branch has a real
    create_time-anchored signal and is not an estimate.
    """
    now = datetime.now(timezone.utc)
    window_start = now - timedelta(days=days)
    younger_than_window = bool(create_time is not None and create_time > window_start)

    if younger_than_window:
        idle_since = create_time.date()  # type: ignore[union-attr]
        idle_days = (now.date() - idle_since).days + 1
        idle_since_is_estimated = False
    else:
        idle_since = window_start.date()
        idle_days = days
        idle_since_is_estimated = True

    return IdleCheckResult(
        resource_id=resource_id,
        resource_type=resource_type,
        window_days=days,
        is_idle=True,
        idle_since=idle_since,
        idle_days=idle_days,
        younger_than_window=younger_than_window,
        idle_since_is_estimated=idle_since_is_estimated,
    )


def _check_idle_via_metrics(
    resource_id: str,
    resource_type: str,
    days: int,
    create_time: datetime | None,
    metric_specs: list[tuple[str, str, str, str, str, str | None, float]],
    extra_dimensions: list[tuple[str, str]] | None = None,
    region: str | None = None,
    zero_fill_missing_days: bool = False,
) -> IdleCheckResult:
    """Generic CloudWatch-metric-window idle check, shared by every
    metric-driven type in both Step 3 batches (EBS-attached, RDS, ELB from
    batch A; Lambda, NAT Gateway, DynamoDB, ElastiCache, SageMaker,
    Redshift, API Gateway, CloudFront, OpenSearch, Kinesis from batch B)
    -- and EC2's own two-metric variant could be rewritten on top of this
    too, but that working code is left untouched here rather than risking
    a regression for no functional gain.

    metric_specs: list of (namespace, metric_name, dimension_name,
    dimension_value, statistic, unit, threshold). A day is idle only if
    every metric's value that day is below its own threshold. A metric
    with no datapoint for a given day defaults to 0.0 (treated as idle for
    that metric that day) -- consistent with how EC2's NetworkIn/NetworkOut
    already treat a missing day (no bytes recorded = no traffic).

    extra_dimensions: applied to every metric_specs entry's CloudWatch
    call -- needed for the handful of metrics CloudWatch requires more
    than one dimension for (SageMaker Invocations needs EndpointName +
    VariantName; OpenSearch SearchRate/IndexingRate need DomainName +
    ClientId).

    region: overrides the CloudWatch client's region for every call in
    this function -- needed for CloudFront, whose metrics only ever
    publish to us-east-1.

    zero_fill_missing_days: when False (batch A's original behavior,
    unchanged), `all_days` is the union of days that had *any* datapoint
    from *any* metric -- a day with literally no datapoint from any metric
    is silently excluded from consideration rather than counted as idle.
    That's fine for batch A's metrics (RDS DatabaseConnections, EBS
    Volume*Ops, ELB RequestCount), which are continuous gauges AWS
    publishes every period regardless of value.

    It is NOT fine for batch B's sparse "activity" metrics (Lambda
    Invocations, NAT Gateway Bytes*, DynamoDB Consumed*CapacityUnits,
    SageMaker Invocations, API Gateway Count, CloudFront Requests, Kinesis
    IncomingRecords) -- CloudWatch publishes literally no datapoint for a
    period with zero activity on these, rather than a datapoint valued 0.
    Excluding those genuinely-idle days from `all_days` would silently
    drop them from the idle streak instead of counting them, undercounting
    idle_days and potentially misses is_idle entirely for a resource with
    zero activity the whole window (no datapoints anywhere -> falls into
    the "no data, don't fabricate idle" branch below, which is *wrong*
    here: a Lambda function that has genuinely never been invoked in the
    window really is idle, not "unknown"). When True, `all_days` is
    instead every calendar day from max(window_start, create_time) through
    today, so a day with no datapoint on any metric still gets evaluated
    (and defaults to idle via the same `by_day.get(day, 0.0)` fallback used
    for the non-zero-fill case) instead of being skipped.
    """
    now = datetime.now(timezone.utc)

    by_metric: list[tuple[dict[date, float], float]] = []
    for spec in metric_specs:
        namespace, metric_name, dimension_name, dimension_value, statistic, unit, threshold = spec
        points = cloudwatch_service.get_daily_datapoints(
            namespace=namespace,
            metric_name=metric_name,
            dimension_name=dimension_name,
            dimension_value=dimension_value,
            days=days,
            statistic=statistic,
            unit=unit,
            extra_dimensions=extra_dimensions,
            region=region,
        )
        by_metric.append((_bucket_by_day(points), threshold))

    younger_than_window = bool(
        create_time is not None and create_time > now - timedelta(days=days)
    )

    if zero_fill_missing_days:
        # `days - 1`, not `days`: a "days=7" window means 7 distinct
        # calendar days including today (today, today-1, ..., today-6) --
        # matching the CloudWatch-observed-datapoint case elsewhere in this
        # function/EC2's own dedicated check, both of which naturally span
        # exactly `days` calendar days when real data exists for each one.
        window_start_date = now.date() - timedelta(days=days - 1)
        if create_time is not None:
            window_start_date = max(window_start_date, _to_utc_date(create_time))
        today = now.date()
        all_days: list[date] = []
        cursor = window_start_date
        while cursor <= today:
            all_days.append(cursor)
            cursor += timedelta(days=1)
    else:
        all_days = (
            sorted(set().union(*[set(d) for d, _ in by_metric])) if by_metric else []
        )

    if not all_days:
        return IdleCheckResult(
            resource_id=resource_id,
            resource_type=resource_type,
            window_days=days,
            is_idle=False,
            idle_since=None,
            idle_days=0,
            younger_than_window=younger_than_window,
            idle_since_is_estimated=False,
        )

    def _day_is_idle(day: date) -> bool:
        return all(by_day.get(day, 0.0) < threshold for by_day, threshold in by_metric)

    is_idle = all(_day_is_idle(d) for d in all_days)
    idle_since, idle_days = _trailing_idle_streak(all_days, _day_is_idle)

    return IdleCheckResult(
        resource_id=resource_id,
        resource_type=resource_type,
        window_days=days,
        is_idle=is_idle,
        idle_since=idle_since,
        idle_days=idle_days,
        younger_than_window=younger_than_window,
        idle_since_is_estimated=False,
    )


def _check_idle_ec2(instance_id: str, days: int, region: str | None = None) -> IdleCheckResult:
    instance = ec2_service.get_instance(instance_id, region=region)
    launch_time = instance.launch_time if instance else None

    cpu_points = cloudwatch_service.get_daily_datapoints(
        namespace="AWS/EC2",
        metric_name="CPUUtilization",
        dimension_name="InstanceId",
        dimension_value=instance_id,
        days=days,
        statistic="Average",
        unit="Percent",
        region=region,
    )
    net_in_points = cloudwatch_service.get_daily_datapoints(
        namespace="AWS/EC2",
        metric_name="NetworkIn",
        dimension_name="InstanceId",
        dimension_value=instance_id,
        days=days,
        statistic="Sum",
        unit="Bytes",
        region=region,
    )
    net_out_points = cloudwatch_service.get_daily_datapoints(
        namespace="AWS/EC2",
        metric_name="NetworkOut",
        dimension_name="InstanceId",
        dimension_value=instance_id,
        days=days,
        statistic="Sum",
        unit="Bytes",
        region=region,
    )

    cpu_by_day = _bucket_by_day(cpu_points)
    net_in_by_day = _bucket_by_day(net_in_points)
    net_out_by_day = _bucket_by_day(net_out_points)

    # Every day judged independently, not blended -- a day-3 burst must
    # never be averaged away by idle days around it (roadmap 3.1).
    all_days = sorted(set(cpu_by_day) | set(net_in_by_day) | set(net_out_by_day))

    younger_than_window = bool(
        launch_time is not None
        and launch_time > datetime.now(timezone.utc) - timedelta(days=days)
    )

    if not all_days:
        # No CloudWatch data at all (freshly launched instance, nothing
        # published yet). Don't fabricate an idle verdict from nothing.
        return IdleCheckResult(
            resource_id=instance_id,
            resource_type="ec2",
            window_days=days,
            is_idle=False,
            idle_since=None,
            idle_days=0,
            younger_than_window=younger_than_window,
        )

    def _day_is_idle(day: date) -> bool:
        cpu_val = cpu_by_day.get(day)
        net_in_val = net_in_by_day.get(day, 0.0)
        net_out_val = net_out_by_day.get(day, 0.0)
        cpu_ok = cpu_val is None or cpu_val < CPU_IDLE_THRESHOLD_PERCENT
        net_ok = (
            net_in_val < NETWORK_IDLE_THRESHOLD_BYTES
            and net_out_val < NETWORK_IDLE_THRESHOLD_BYTES
        )
        return cpu_ok and net_ok

    is_idle = all(_day_is_idle(d) for d in all_days)
    idle_since, idle_days = _trailing_idle_streak(all_days, _day_is_idle)

    return IdleCheckResult(
        resource_id=instance_id,
        resource_type="ec2",
        window_days=days,
        is_idle=is_idle,
        idle_since=idle_since,
        idle_days=idle_days,
        younger_than_window=younger_than_window,
    )


def _check_idle_ebs(volume_id: str, days: int, region: str | None = None) -> IdleCheckResult:
    """Section 2a: 'Not attached, or attached with VolumeReadOps/
    VolumeWriteOps ~= 0'. Unattached is checked first and short-circuits --
    an unattached volume has no legitimate "busy" state to wait out a
    CloudWatch window for (roadmap instructions, Step 3 EBS section)."""
    volume = ebs_service.get_volume(volume_id, region=region)
    create_time = volume.create_time if volume else None

    if volume is not None and not volume.is_attached:
        return _instant_idle_result(volume_id, "ebs", days, create_time)

    return _check_idle_via_metrics(
        volume_id,
        "ebs",
        days,
        create_time,
        [
            (
                "AWS/EBS", "VolumeReadOps", "VolumeId", volume_id,
                "Sum", "Count", EBS_IO_IDLE_THRESHOLD_OPS,
            ),
            (
                "AWS/EBS", "VolumeWriteOps", "VolumeId", volume_id,
                "Sum", "Count", EBS_IO_IDLE_THRESHOLD_OPS,
            ),
        ],
        region=region,
    )


def _check_idle_rds(instance_id: str, days: int, region: str | None = None) -> IdleCheckResult:
    """Section 2a: 'DatabaseConnections = 0 for every day in window'."""
    instance = rds_service.get_instance(instance_id, region=region)
    create_time = instance.instance_create_time if instance else None

    return _check_idle_via_metrics(
        instance_id,
        "rds",
        days,
        create_time,
        [
            (
                "AWS/RDS",
                "DatabaseConnections",
                "DBInstanceIdentifier",
                instance_id,
                "Maximum",
                None,
                DB_CONNECTIONS_IDLE_THRESHOLD,
            ),
        ],
        region=region,
    )


def _check_idle_eip(resource_id: str, days: int, region: str | None = None) -> IdleCheckResult:
    """Section 2a: 'Not associated with a running instance/ENI'.

    See _instant_idle_result's docstring for the idle_since/idle_days
    design decision -- EIP association is a point-in-time boolean with no
    CloudWatch time series and no exposed allocation timestamp, a
    genuinely different shape of idle signal than every other type here.
    """
    address = eip_service.get_address(resource_id, region=region)
    if address is None:
        # Can't verify association state at all -- never fabricate an idle
        # verdict from missing data (mirrors EC2's "no datapoints" case).
        return _not_idle_result(resource_id, "eip", days)
    if not address.is_associated:
        return _instant_idle_result(resource_id, "eip", days, None)
    # Associated -- never idle, full stop, per the Section 2a signal. No
    # time series exists either way, so idle_since/idle_days stay empty.
    return _not_idle_result(resource_id, "eip", days)


def _check_idle_elb(name: str, days: int, region: str | None = None) -> IdleCheckResult:
    """Section 2a: 'RequestCount = 0 for every day in window'."""
    lb = elb_service.get_load_balancer(name, region=region)
    if lb is None:
        # Unlike EC2/EBS/RDS (where the CloudWatch dimension value equals
        # the resource_id itself), an ALB/NLB's dimension value must be
        # parsed from its ARN -- with no describe_load_balancers match
        # there's no ARN to parse, so there's no CloudWatch call to make.
        return _not_idle_result(name, "elb", days)

    namespace, dimension_name, dimension_value = elb_service.cloudwatch_dimension(lb)
    return _check_idle_via_metrics(
        name,
        "elb",
        days,
        lb.created_time,
        [
            (
                namespace, "RequestCount", dimension_name, dimension_value,
                "Sum", "Count", REQUEST_COUNT_IDLE_THRESHOLD,
            ),
        ],
        region=region,
    )


# =====================================================================
# Step 3 batch B -- Lambda, NAT Gateway, DynamoDB, ElastiCache, SageMaker,
# Redshift, API Gateway, CloudFront, OpenSearch, Kinesis.
# =====================================================================


def _check_idle_lambda(
    function_name: str, days: int, region: str | None = None
) -> IdleCheckResult:
    """Section 2a: 'Invocation count = 0 over window'. Lambda's API
    exposes no creation timestamp at all (see LambdaFunctionSummary's
    docstring) -- create_time is always None, so younger_than_window is
    always False for this type, same documented gap as EIP."""
    function = lambda_service.get_function(function_name, region=region)
    if function is None:
        return _not_idle_result(function_name, "lambda", days)

    return _check_idle_via_metrics(
        function_name,
        "lambda",
        days,
        None,
        [
            (
                "AWS/Lambda", "Invocations", "FunctionName", function_name,
                "Sum", "Count", LAMBDA_INVOCATIONS_IDLE_THRESHOLD,
            ),
        ],
        zero_fill_missing_days=True,
        region=region,
    )


def _check_idle_nat_gateway(
    nat_gateway_id: str, days: int, region: str | None = None
) -> IdleCheckResult:
    """Section 2a: 'BytesOutToDestination/BytesInFromSource ~= 0'."""
    gateway = nat_gateway_service.get_nat_gateway(nat_gateway_id, region=region)
    if gateway is None:
        return _not_idle_result(nat_gateway_id, "nat_gateway", days)

    return _check_idle_via_metrics(
        nat_gateway_id,
        "nat_gateway",
        days,
        gateway.create_time,
        [
            (
                "AWS/NATGateway", "BytesOutToDestination", "NatGatewayId", nat_gateway_id,
                "Sum", "Bytes", NAT_GATEWAY_BYTES_IDLE_THRESHOLD,
            ),
            (
                "AWS/NATGateway", "BytesInFromSource", "NatGatewayId", nat_gateway_id,
                "Sum", "Bytes", NAT_GATEWAY_BYTES_IDLE_THRESHOLD,
            ),
        ],
        zero_fill_missing_days=True,
        region=region,
    )


def _check_idle_dynamodb(
    table_name: str, days: int, region: str | None = None
) -> IdleCheckResult:
    """Section 2a: 'Consumed read/write capacity ~= 0 (provisioned
    tables)'. Applies to on-demand (PAY_PER_REQUEST) tables too -- see
    DYNAMODB_CAPACITY_IDLE_THRESHOLD's docstring for why the same
    Consumed*CapacityUnits signal is valid regardless of billing mode."""
    table = dynamodb_service.get_table(table_name, region=region)
    if table is None:
        return _not_idle_result(table_name, "dynamodb", days)

    return _check_idle_via_metrics(
        table_name,
        "dynamodb",
        days,
        table.creation_date_time,
        [
            (
                "AWS/DynamoDB", "ConsumedReadCapacityUnits", "TableName", table_name,
                "Sum", "Count", DYNAMODB_CAPACITY_IDLE_THRESHOLD,
            ),
            (
                "AWS/DynamoDB", "ConsumedWriteCapacityUnits", "TableName", table_name,
                "Sum", "Count", DYNAMODB_CAPACITY_IDLE_THRESHOLD,
            ),
        ],
        zero_fill_missing_days=True,
        region=region,
    )


def _check_idle_elasticache(
    cache_cluster_id: str, days: int, region: str | None = None
) -> IdleCheckResult:
    """Section 2a: 'CurrConnections ~= 0'."""
    cluster = elasticache_service.get_cluster(cache_cluster_id, region=region)
    if cluster is None:
        return _not_idle_result(cache_cluster_id, "elasticache", days)

    return _check_idle_via_metrics(
        cache_cluster_id,
        "elasticache",
        days,
        cluster.create_time,
        [
            (
                "AWS/ElastiCache", "CurrConnections", "CacheClusterId", cache_cluster_id,
                "Maximum", "Count", ELASTICACHE_CONNECTIONS_IDLE_THRESHOLD,
            ),
        ],
        region=region,
    )


def _check_idle_sagemaker(
    endpoint_name: str, days: int, region: str | None = None
) -> IdleCheckResult:
    """Section 2a: 'Invocation count = 0 -- often the biggest silent cost,
    runs 24/7 by default'. AWS/SageMaker's Invocations metric requires
    both EndpointName AND VariantName dimensions together -- if the
    endpoint's production variant can't be determined (see
    SageMakerEndpoint's docstring on the single-variant assumption), there
    is no reliable dimension pair to query, so this falls back to
    'can't verify' rather than guessing at a variant name."""
    endpoint = sagemaker_service.get_endpoint(endpoint_name, region=region)
    if endpoint is None:
        return _not_idle_result(endpoint_name, "sagemaker", days)
    if not endpoint.variant_name:
        return _not_idle_result(endpoint_name, "sagemaker", days)

    return _check_idle_via_metrics(
        endpoint_name,
        "sagemaker",
        days,
        endpoint.creation_time,
        [
            (
                "AWS/SageMaker", "Invocations", "EndpointName", endpoint_name,
                "Sum", "Count", SAGEMAKER_INVOCATIONS_IDLE_THRESHOLD,
            ),
        ],
        extra_dimensions=[("VariantName", endpoint.variant_name)],
        zero_fill_missing_days=True,
        region=region,
    )


def _check_idle_redshift(
    cluster_identifier: str, days: int, region: str | None = None
) -> IdleCheckResult:
    """Section 2a: 'DatabaseConnections ~= 0'."""
    cluster = redshift_service.get_cluster(cluster_identifier, region=region)
    if cluster is None:
        return _not_idle_result(cluster_identifier, "redshift", days)

    return _check_idle_via_metrics(
        cluster_identifier,
        "redshift",
        days,
        cluster.create_time,
        [
            (
                "AWS/Redshift", "DatabaseConnections", "ClusterIdentifier", cluster_identifier,
                "Maximum", None, REDSHIFT_CONNECTIONS_IDLE_THRESHOLD,
            ),
        ],
        region=region,
    )


def _check_idle_api_gateway(
    resource_id: str, days: int, region: str | None = None
) -> IdleCheckResult:
    """Section 2a: 'Count (requests) = 0'. REST APIs only -- see
    api_gateway_service.py's module docstring. Dimension value is the
    API's `name`, not its `id` -- see ApiGatewayRestApi's docstring."""
    api = api_gateway_service.get_api(resource_id, region=region)
    if api is None:
        return _not_idle_result(resource_id, "api_gateway", days)

    return _check_idle_via_metrics(
        resource_id,
        "api_gateway",
        days,
        api.created_date,
        [
            (
                "AWS/ApiGateway", "Count", "ApiName", api.name,
                "Sum", "Count", API_GATEWAY_REQUEST_IDLE_THRESHOLD,
            ),
        ],
        zero_fill_missing_days=True,
        region=region,
    )


def _check_idle_cloudfront(
    distribution_id: str, days: int, region: str | None = None
) -> IdleCheckResult:
    """Section 2a: 'Requests = 0'. CloudFront metrics only publish to
    us-east-1 regardless of the account's configured region -- region is
    pinned explicitly here rather than relying on whatever the account
    happens to be configured for, or whatever region a scan happens to be
    looking at (the `region` param is accepted for calling-convention
    uniformity with every other type but deliberately NOT forwarded to the
    metrics call below). No creation timestamp is available (see
    CloudFrontDistribution's docstring) -- younger_than_window is always
    False for this type, same documented gap as EIP/Lambda."""
    distribution = cloudfront_service.get_distribution(distribution_id, region=region)
    if distribution is None:
        return _not_idle_result(distribution_id, "cloudfront", days)

    return _check_idle_via_metrics(
        distribution_id,
        "cloudfront",
        days,
        None,
        [
            (
                "AWS/CloudFront", "Requests", "DistributionId", distribution_id,
                "Sum", "Count", CLOUDFRONT_REQUEST_IDLE_THRESHOLD,
            ),
        ],
        region="us-east-1",
        zero_fill_missing_days=True,
    )


def _check_idle_opensearch(
    domain_name: str, days: int, region: str | None = None
) -> IdleCheckResult:
    """Section 2a: 'Search/index rate ~= 0'. AWS/ES's SearchRate/
    IndexingRate metrics require both DomainName AND ClientId (the AWS
    account ID) dimensions together -- see OpenSearchDomain.account_id's
    docstring for how that's derived from the domain's ARN without an
    extra STS call."""
    domain = opensearch_service.get_domain(domain_name, region=region)
    if domain is None:
        return _not_idle_result(domain_name, "opensearch", days)
    account_id = domain.account_id
    if not account_id:
        return _not_idle_result(domain_name, "opensearch", days)

    return _check_idle_via_metrics(
        domain_name,
        "opensearch",
        days,
        domain.created_at,
        [
            (
                "AWS/ES", "SearchRate", "DomainName", domain_name,
                "Average", None, OPENSEARCH_RATE_IDLE_THRESHOLD,
            ),
            (
                "AWS/ES", "IndexingRate", "DomainName", domain_name,
                "Average", None, OPENSEARCH_RATE_IDLE_THRESHOLD,
            ),
        ],
        extra_dimensions=[("ClientId", account_id)],
        region=region,
    )


def _check_idle_kinesis(
    stream_name: str, days: int, region: str | None = None
) -> IdleCheckResult:
    """Section 2a: 'IncomingRecords ~= 0'."""
    stream = kinesis_service.get_stream(stream_name, region=region)
    if stream is None:
        return _not_idle_result(stream_name, "kinesis", days)

    return _check_idle_via_metrics(
        stream_name,
        "kinesis",
        days,
        stream.creation_timestamp,
        [
            (
                "AWS/Kinesis", "IncomingRecords", "StreamName", stream_name,
                "Sum", "Count", KINESIS_INCOMING_RECORDS_IDLE_THRESHOLD,
            ),
        ],
        zero_fill_missing_days=True,
        region=region,
    )
