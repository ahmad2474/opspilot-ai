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
