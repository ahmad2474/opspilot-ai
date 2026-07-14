"""CloudWatch business logic — paired with ec2_service for the deep
investigation flow (Phase 3), but the basic CPU lookup is needed as early
as Phase 1's exit criterion.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.aws.client import get_cloudwatch_client
from app.models.cloudwatch import CpuUtilizationSummary, MetricDatapoint

CPU_ALERT_THRESHOLD_PERCENT = 80.0

# 300s (5-minute) period matches EC2 *basic* monitoring, which is what
# free-tier / zero-spend instances use — do not lower this to 60s, that
# implies detailed monitoring and CloudWatch won't have the data anyway.
METRIC_PERIOD_SECONDS = 300

# One-day period, used by idle detection (Section 3.1) — deliberately a
# separate constant/call from METRIC_PERIOD_SECONDS above: idle checking
# needs one datapoint per calendar day so a burst on one day can never be
# averaged away by the 5-minute-resolution interactive CPU lookup.
DAILY_PERIOD_SECONDS = 86400


def get_cpu_utilization(instance_id: str, lookback_hours: int = 3) -> CpuUtilizationSummary:
    client = get_cloudwatch_client()
    end_time = datetime.now(timezone.utc)
    start_time = end_time - timedelta(hours=lookback_hours)

    response = client.get_metric_statistics(
        Namespace="AWS/EC2",
        MetricName="CPUUtilization",
        Dimensions=[{"Name": "InstanceId", "Value": instance_id}],
        StartTime=start_time,
        EndTime=end_time,
        Period=METRIC_PERIOD_SECONDS,
        Statistics=["Average", "Maximum"],
        Unit="Percent",
    )

    raw_points = sorted(response.get("Datapoints", []), key=lambda dp: dp["Timestamp"])
    datapoints = [
        MetricDatapoint(
            timestamp=dp["Timestamp"],
            average=dp.get("Average"),
            maximum=dp.get("Maximum"),
            unit=dp.get("Unit", "Percent"),
        )
        for dp in raw_points
    ]

    averages = [dp.average for dp in datapoints if dp.average is not None]
    maxima = [dp.maximum for dp in datapoints if dp.maximum is not None]

    avg_cpu = sum(averages) / len(averages) if averages else None
    max_cpu = max(maxima) if maxima else None

    return CpuUtilizationSummary(
        instance_id=instance_id,
        lookback_hours=lookback_hours,
        datapoints=datapoints,
        average_cpu_percent=avg_cpu,
        max_cpu_percent=max_cpu,
        breached_80_percent=bool(max_cpu and max_cpu > CPU_ALERT_THRESHOLD_PERCENT),
    )


def get_daily_datapoints(
    namespace: str,
    metric_name: str,
    dimension_name: str,
    dimension_value: str,
    days: int,
    statistic: str = "Average",
    unit: str | None = None,
    extra_dimensions: list[tuple[str, str]] | None = None,
    region: str | None = None,
) -> list[MetricDatapoint]:
    """One datapoint per calendar day (Period=86400) for idle detection.

    Generic across namespace/metric/dimension on purpose — this is the one
    daily-resolution CloudWatch call idle_service uses for EC2
    (CPUUtilization/NetworkIn/NetworkOut, dimension InstanceId) and reuses
    for every other resource type (e.g. RDS DatabaseConnections keyed by
    DBInstanceIdentifier) without needing a new function per type.

    `extra_dimensions`: additional (name, value) pairs appended after the
    primary dimension, for the handful of metrics CloudWatch requires more
    than one dimension for (SageMaker's Invocations needs EndpointName +
    VariantName together; OpenSearch's SearchRate/IndexingRate need
    DomainName + ClientId together). None/omitted for every metric that
    only needs one dimension, which is most of them.

    `region`: overrides the client's region for this one call — needed for
    CloudFront, whose metrics only ever publish to us-east-1 regardless of
    the account's configured region (see get_cloudwatch_client's docstring
    in app/aws/client.py). None/omitted uses the normal configured region.

    Note: `.average` on the returned MetricDatapoint holds whichever
    `statistic` was requested (e.g. Sum for a byte-count metric like
    NetworkIn), not literally an arithmetic average — reusing the existing
    model field rather than adding a parallel one for a single extra label.
    """
    client = get_cloudwatch_client(region=region)
    end_time = datetime.now(timezone.utc)
    start_time = end_time - timedelta(days=days)

    dimensions = [{"Name": dimension_name, "Value": dimension_value}]
    if extra_dimensions:
        dimensions.extend({"Name": name, "Value": value} for name, value in extra_dimensions)

    kwargs: dict[str, object] = dict(
        Namespace=namespace,
        MetricName=metric_name,
        Dimensions=dimensions,
        StartTime=start_time,
        EndTime=end_time,
        Period=DAILY_PERIOD_SECONDS,
        Statistics=[statistic],
    )
    if unit:
        kwargs["Unit"] = unit

    response = client.get_metric_statistics(**kwargs)
    raw_points = sorted(response.get("Datapoints", []), key=lambda dp: dp["Timestamp"])

    return [
        MetricDatapoint(
            timestamp=dp["Timestamp"],
            average=dp.get(statistic),
            maximum=dp.get("Maximum"),
            unit=dp.get("Unit", unit or "None"),
        )
        for dp in raw_points
    ]
