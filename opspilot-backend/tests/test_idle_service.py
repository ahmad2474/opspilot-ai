from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

from app.models.api_gateway import ApiGatewayRestApi
from app.models.cloudfront import CloudFrontDistribution
from app.models.dashboard import DynamoTableSummary, LambdaFunctionSummary
from app.models.ebs import EbsVolume
from app.models.ec2 import EC2Instance
from app.models.eip import ElasticIp
from app.models.elasticache import ElastiCacheCluster
from app.models.elb import LoadBalancer
from app.models.kinesis import KinesisStream
from app.models.nat_gateway import NatGateway
from app.models.opensearch import OpenSearchDomain
from app.models.redshift import RedshiftCluster
from app.models.sagemaker import SageMakerEndpoint
from app.services import idle_service

NOW = datetime(2026, 7, 10, 12, 0, 0, tzinfo=timezone.utc)


def _cw_datapoint(day_offset_from_now: int, value: float, unit: str = "Percent") -> dict:
    ts = NOW - timedelta(days=day_offset_from_now)
    return {"Timestamp": ts, "Average": value, "Sum": value, "Unit": unit}


def _instance(launch_time: datetime | None) -> EC2Instance:
    return EC2Instance(
        instance_id="i-123",
        instance_type="t3.micro",
        state="running",
        availability_zone="us-east-1d",
        launch_time=launch_time,
        tags={},
    )


def _mock_cloudwatch_series(
    cpu_points: list[dict], net_in_points: list[dict], net_out_points: list[dict]
):
    """Returns a side_effect function for get_daily_datapoints that routes
    by MetricName, mirroring how idle_service calls it three times."""
    from app.models.cloudwatch import MetricDatapoint

    def _to_models(raw: list[dict], statistic: str) -> list:
        return [
            MetricDatapoint(
                timestamp=dp["Timestamp"], average=dp.get(statistic), maximum=None, unit=dp["Unit"]
            )
            for dp in raw
        ]

    def _side_effect(
        namespace,
        metric_name,
        dimension_name,
        dimension_value,
        days,
        statistic,
        unit=None,
        extra_dimensions=None,
        region=None,
    ):
        if metric_name == "CPUUtilization":
            return _to_models(cpu_points, "Average")
        if metric_name == "NetworkIn":
            return _to_models(net_in_points, "Sum")
        if metric_name == "NetworkOut":
            return _to_models(net_out_points, "Sum")
        raise AssertionError(f"unexpected metric_name {metric_name}")

    return _side_effect


@patch("app.services.idle_service.datetime")
@patch("app.services.idle_service.cloudwatch_service.get_daily_datapoints")
@patch("app.services.idle_service.ec2_service.get_instance")
def test_fully_idle_window_flags_idle(
    mock_get_instance: MagicMock, mock_daily: MagicMock, mock_datetime: MagicMock
) -> None:
    mock_datetime.now.return_value = NOW
    mock_get_instance.return_value = _instance(NOW - timedelta(days=60))

    cpu = [_cw_datapoint(i, 0.1) for i in range(9, -1, -1)]
    net_in = [_cw_datapoint(i, 100.0) for i in range(9, -1, -1)]
    net_out = [_cw_datapoint(i, 100.0) for i in range(9, -1, -1)]
    mock_daily.side_effect = _mock_cloudwatch_series(cpu, net_in, net_out)

    result = idle_service.check_idle("ec2", "i-123", days=10)

    assert result.is_idle is True
    assert result.idle_days == 10
    assert result.younger_than_window is False
    # CloudWatch-verified streak -- never an estimate.
    assert result.idle_since_is_estimated is False


@patch("app.services.idle_service.datetime")
@patch("app.services.idle_service.cloudwatch_service.get_daily_datapoints")
@patch("app.services.idle_service.ec2_service.get_instance")
def test_day_three_burst_not_flagged_idle_for_full_window(
    mock_get_instance: MagicMock, mock_daily: MagicMock, mock_datetime: MagicMock
) -> None:
    """A CPU burst partway through the window must not average out to
    'idle' for the requested window, even though the days after it are
    quiet -- this is the core roadmap 3.1 correctness rule."""
    mock_datetime.now.return_value = NOW
    mock_get_instance.return_value = _instance(NOW - timedelta(days=60))

    # 10-day window, oldest day is offset 9, most recent is offset 0.
    # Burst lands on the day 3 days before the oldest recorded day's end,
    # i.e. offset 7 (the "day-3" burst from the start of the window).
    cpu = [_cw_datapoint(i, 0.1) for i in range(9, -1, -1)]
    for dp in cpu:
        if dp["Timestamp"] == NOW - timedelta(days=7):
            dp["Average"] = 90.0
    net_in = [_cw_datapoint(i, 100.0) for i in range(9, -1, -1)]
    net_out = [_cw_datapoint(i, 100.0) for i in range(9, -1, -1)]
    mock_daily.side_effect = _mock_cloudwatch_series(cpu, net_in, net_out)

    result = idle_service.check_idle("ec2", "i-123", days=10)

    assert result.is_idle is False
    # Trailing streak should only cover the days *after* the burst.
    burst_date = (NOW - timedelta(days=7)).date()
    expected_idle_since = burst_date + timedelta(days=1)
    assert result.idle_since == expected_idle_since
    assert result.idle_days == 7


@patch("app.services.idle_service.datetime")
@patch("app.services.idle_service.cloudwatch_service.get_daily_datapoints")
@patch("app.services.idle_service.ec2_service.get_instance")
def test_idle_since_walks_back_to_day_after_break(
    mock_get_instance: MagicMock, mock_daily: MagicMock, mock_datetime: MagicMock
) -> None:
    mock_datetime.now.return_value = NOW
    mock_get_instance.return_value = _instance(NOW - timedelta(days=60))

    # offsets 4..0 idle (5 days), offset 5 is a break, older days idle too
    # but shouldn't count since the streak is broken at offset 5.
    cpu = [_cw_datapoint(i, 0.1) for i in range(9, -1, -1)]
    for dp in cpu:
        if dp["Timestamp"] == NOW - timedelta(days=5):
            dp["Average"] = 75.0
    net_in = [_cw_datapoint(i, 10.0) for i in range(9, -1, -1)]
    net_out = [_cw_datapoint(i, 10.0) for i in range(9, -1, -1)]
    mock_daily.side_effect = _mock_cloudwatch_series(cpu, net_in, net_out)

    result = idle_service.check_idle("ec2", "i-123", days=10)

    break_date = (NOW - timedelta(days=5)).date()
    assert result.idle_since == break_date + timedelta(days=1)
    assert result.idle_days == 5
    assert result.is_idle is False


@patch("app.services.idle_service.datetime")
@patch("app.services.idle_service.cloudwatch_service.get_daily_datapoints")
@patch("app.services.idle_service.ec2_service.get_instance")
def test_younger_than_window_never_reports_fabricated_longer_window(
    mock_get_instance: MagicMock, mock_daily: MagicMock, mock_datetime: MagicMock
) -> None:
    launch_time = NOW - timedelta(days=3, hours=12)
    mock_datetime.now.return_value = NOW
    mock_get_instance.return_value = _instance(launch_time)

    # Only 4 days of data exist since the instance is younger than the
    # requested 10-day window.
    cpu = [_cw_datapoint(i, 0.1) for i in range(3, -1, -1)]
    net_in = [_cw_datapoint(i, 10.0) for i in range(3, -1, -1)]
    net_out = [_cw_datapoint(i, 10.0) for i in range(3, -1, -1)]
    mock_daily.side_effect = _mock_cloudwatch_series(cpu, net_in, net_out)

    result = idle_service.check_idle("ec2", "i-123", days=10)

    assert result.younger_than_window is True
    assert result.is_idle is True
    assert result.idle_days == 4
    assert result.idle_since == launch_time.date()
    # Never fabricate a longer window than the instance has existed for.
    assert result.idle_days <= 4


@patch("app.services.idle_service.cloudwatch_service.get_daily_datapoints")
@patch("app.services.idle_service.ec2_service.get_instance")
def test_no_datapoints_does_not_fabricate_idle(
    mock_get_instance: MagicMock, mock_daily: MagicMock
) -> None:
    mock_get_instance.return_value = _instance(NOW)
    mock_daily.return_value = []

    result = idle_service.check_idle("ec2", "i-123", days=10)

    assert result.is_idle is False
    assert result.idle_since is None
    assert result.idle_days == 0


def test_unsupported_resource_type_raises() -> None:
    """'lambda' used to be the go-to unsupported example in batch A, back
    when only ec2/ebs/rds/eip/elb existed -- now that batch B adds all 10
    remaining roadmap types (including lambda), 's3' is used instead: it's
    explicitly Tier 2 / deferred (roadmap Section 2a), never one of the 15
    in-scope types, so it stays a valid 'not supported' example forever."""
    with pytest.raises(idle_service.UnsupportedResourceTypeError):
        idle_service.check_idle("s3", "my-bucket", days=7)


# =====================================================================
# EBS (roadmap Step 3, first batch)
# =====================================================================


def _volume(
    create_time: datetime | None, attached_instance_ids: list[str] | None = None
) -> EbsVolume:
    return EbsVolume(
        volume_id="vol-123",
        size_gb=100,
        volume_type="gp3",
        state="in-use" if attached_instance_ids else "available",
        availability_zone="us-east-1d",
        create_time=create_time,
        attached_instance_ids=attached_instance_ids or [],
    )


def _metric_series_side_effect(series_by_metric: dict[str, list[dict]]):
    """Generic version of _mock_cloudwatch_series above -- routes by
    metric_name, reused across EBS (2 metrics)/RDS/ELB (1 metric each)."""
    from app.models.cloudwatch import MetricDatapoint

    def _to_models(raw: list[dict], statistic: str) -> list:
        return [
            MetricDatapoint(
                timestamp=dp["Timestamp"],
                average=dp.get(statistic),
                maximum=dp.get("Maximum"),
                unit=dp["Unit"],
            )
            for dp in raw
        ]

    def _side_effect(
        namespace,
        metric_name,
        dimension_name,
        dimension_value,
        days,
        statistic,
        unit=None,
        extra_dimensions=None,
        region=None,
    ):
        raw = series_by_metric.get(metric_name)
        if raw is None:
            raise AssertionError(f"unexpected metric_name {metric_name}")
        return _to_models(raw, statistic)

    return _side_effect


def _dp(day_offset_from_now: int, value: float, unit: str = "Count") -> dict:
    ts = NOW - timedelta(days=day_offset_from_now)
    return {"Timestamp": ts, "Average": value, "Sum": value, "Maximum": value, "Unit": unit}


def _to_metric_datapoints(raw: list[dict], statistic: str = "Sum") -> list:
    """Converts _dp()'s raw dicts into real MetricDatapoint models -- used
    for single-metric batch B types (where a plain `mock_daily.return_value
    = [...]` is set directly, rather than routed through
    _metric_series_side_effect's per-metric-name dispatch)."""
    from app.models.cloudwatch import MetricDatapoint

    return [
        MetricDatapoint(
            timestamp=dp["Timestamp"],
            average=dp.get(statistic),
            maximum=dp.get("Maximum"),
            unit=dp["Unit"],
        )
        for dp in raw
    ]


@patch("app.services.idle_service.datetime")
@patch("app.services.idle_service.ebs_service.get_volume")
def test_ebs_unattached_is_instantly_idle_no_window_wait(
    mock_get_volume: MagicMock, mock_datetime: MagicMock
) -> None:
    """Roadmap: unattached EBS has no legitimate 'busy' state -- idle
    immediately, no CloudWatch window needed."""
    mock_datetime.now.return_value = NOW
    mock_get_volume.return_value = _volume(
        create_time=NOW - timedelta(days=60), attached_instance_ids=[]
    )

    result = idle_service.check_idle("ebs", "vol-123", days=7)

    assert result.is_idle is True
    assert result.younger_than_window is False
    assert result.idle_days == 7
    assert result.idle_since == (NOW - timedelta(days=7)).date()
    # No verified detach timestamp exists -- idle_days is a worst-case
    # "known idle for at least the window" assumption, not a CloudWatch-
    # verified streak, so consumers must be told it's an estimate.
    assert result.idle_since_is_estimated is True


@patch("app.services.idle_service.datetime")
@patch("app.services.idle_service.ebs_service.get_volume")
def test_ebs_unattached_younger_than_window_reports_since_creation(
    mock_get_volume: MagicMock, mock_datetime: MagicMock
) -> None:
    mock_datetime.now.return_value = NOW
    create_time = NOW - timedelta(days=2)
    mock_get_volume.return_value = _volume(create_time=create_time, attached_instance_ids=[])

    result = idle_service.check_idle("ebs", "vol-123", days=7)

    assert result.younger_than_window is True
    assert result.idle_since == create_time.date()
    # Never fabricate an idle streak longer than the volume has existed.
    assert result.idle_days <= 3
    # A real create_time-anchored signal exists here -- not an estimate.
    assert result.idle_since_is_estimated is False


@patch("app.services.idle_service.datetime")
@patch("app.services.idle_service.cloudwatch_service.get_daily_datapoints")
@patch("app.services.idle_service.ebs_service.get_volume")
def test_ebs_attached_fully_idle_window(
    mock_get_volume: MagicMock, mock_daily: MagicMock, mock_datetime: MagicMock
) -> None:
    mock_datetime.now.return_value = NOW
    mock_get_volume.return_value = _volume(
        create_time=NOW - timedelta(days=60), attached_instance_ids=["i-123"]
    )
    read_ops = [_dp(i, 0.0) for i in range(6, -1, -1)]
    write_ops = [_dp(i, 0.0) for i in range(6, -1, -1)]
    mock_daily.side_effect = _metric_series_side_effect(
        {"VolumeReadOps": read_ops, "VolumeWriteOps": write_ops}
    )

    result = idle_service.check_idle("ebs", "vol-123", days=7)

    assert result.is_idle is True
    assert result.idle_days == 7


@patch("app.services.idle_service.datetime")
@patch("app.services.idle_service.cloudwatch_service.get_daily_datapoints")
@patch("app.services.idle_service.ebs_service.get_volume")
def test_ebs_attached_burst_then_idle_not_flagged_idle_for_full_window(
    mock_get_volume: MagicMock, mock_daily: MagicMock, mock_datetime: MagicMock
) -> None:
    mock_datetime.now.return_value = NOW
    mock_get_volume.return_value = _volume(
        create_time=NOW - timedelta(days=60), attached_instance_ids=["i-123"]
    )
    read_ops = [_dp(i, 0.0) for i in range(6, -1, -1)]
    for dp in read_ops:
        if dp["Timestamp"] == NOW - timedelta(days=4):
            dp["Sum"] = 500.0
    write_ops = [_dp(i, 0.0) for i in range(6, -1, -1)]
    mock_daily.side_effect = _metric_series_side_effect(
        {"VolumeReadOps": read_ops, "VolumeWriteOps": write_ops}
    )

    result = idle_service.check_idle("ebs", "vol-123", days=7)

    assert result.is_idle is False
    burst_date = (NOW - timedelta(days=4)).date()
    assert result.idle_since == burst_date + timedelta(days=1)
    assert result.idle_days == 4


# =====================================================================
# RDS
# =====================================================================


def _rds_instance(instance_create_time: datetime | None):
    from app.models.dashboard import RdsInstanceSummary

    return RdsInstanceSummary(
        identifier="db-1",
        engine="postgres",
        instance_class="db.t3.micro",
        status="available",
        instance_create_time=instance_create_time,
    )


@patch("app.services.idle_service.datetime")
@patch("app.services.idle_service.cloudwatch_service.get_daily_datapoints")
@patch("app.services.idle_service.rds_service.get_instance")
def test_rds_fully_idle_window(
    mock_get_instance: MagicMock, mock_daily: MagicMock, mock_datetime: MagicMock
) -> None:
    mock_datetime.now.return_value = NOW
    mock_get_instance.return_value = _rds_instance(NOW - timedelta(days=60))
    connections = [_dp(i, 0.0) for i in range(6, -1, -1)]
    mock_daily.side_effect = _metric_series_side_effect({"DatabaseConnections": connections})

    result = idle_service.check_idle("rds", "db-1", days=7)

    assert result.is_idle is True
    assert result.idle_days == 7
    # CloudWatch-verified streak -- never an estimate.
    assert result.idle_since_is_estimated is False


@patch("app.services.idle_service.datetime")
@patch("app.services.idle_service.cloudwatch_service.get_daily_datapoints")
@patch("app.services.idle_service.rds_service.get_instance")
def test_rds_burst_then_idle_not_flagged_idle_for_full_window(
    mock_get_instance: MagicMock, mock_daily: MagicMock, mock_datetime: MagicMock
) -> None:
    """A brief connection spike (e.g. a single client connecting for a
    minute) must be caught by the Maximum statistic, not averaged away."""
    mock_datetime.now.return_value = NOW
    mock_get_instance.return_value = _rds_instance(NOW - timedelta(days=60))
    connections = [_dp(i, 0.0) for i in range(6, -1, -1)]
    for dp in connections:
        if dp["Timestamp"] == NOW - timedelta(days=3):
            dp["Maximum"] = 2.0
    mock_daily.side_effect = _metric_series_side_effect({"DatabaseConnections": connections})

    result = idle_service.check_idle("rds", "db-1", days=7)

    assert result.is_idle is False
    burst_date = (NOW - timedelta(days=3)).date()
    assert result.idle_since == burst_date + timedelta(days=1)
    assert result.idle_days == 3


@patch("app.services.idle_service.datetime")
@patch("app.services.idle_service.cloudwatch_service.get_daily_datapoints")
@patch("app.services.idle_service.rds_service.get_instance")
def test_rds_younger_than_window(
    mock_get_instance: MagicMock, mock_daily: MagicMock, mock_datetime: MagicMock
) -> None:
    mock_datetime.now.return_value = NOW
    launch_time = NOW - timedelta(days=2, hours=12)
    mock_get_instance.return_value = _rds_instance(launch_time)
    connections = [_dp(i, 0.0) for i in range(2, -1, -1)]
    mock_daily.side_effect = _metric_series_side_effect({"DatabaseConnections": connections})

    result = idle_service.check_idle("rds", "db-1", days=10)

    assert result.younger_than_window is True
    assert result.is_idle is True
    assert result.idle_days == 3


# =====================================================================
# EIP -- point-in-time association signal, no CloudWatch time series.
# See idle_service._instant_idle_result's docstring for the documented
# idle_since/idle_days design decision this exercises.
# =====================================================================


@patch("app.services.idle_service.datetime")
@patch("app.services.idle_service.eip_service.get_address")
def test_eip_unassociated_is_instantly_idle(
    mock_get_address: MagicMock, mock_datetime: MagicMock
) -> None:
    mock_datetime.now.return_value = NOW
    mock_get_address.return_value = ElasticIp(
        allocation_id="eipalloc-1", public_ip="1.2.3.4", domain="vpc"
    )

    result = idle_service.check_idle("eip", "eipalloc-1", days=7)

    assert result.is_idle is True
    assert result.younger_than_window is False
    # No allocation timestamp exists (see ElasticIp's docstring) -- the
    # most we can honestly claim is "idle for the requested window".
    assert result.idle_days == 7
    assert result.idle_since == (NOW - timedelta(days=7)).date()
    # EIP never has a create_time signal -- this branch is always an
    # estimate, never a CloudWatch-verified streak.
    assert result.idle_since_is_estimated is True


@patch("app.services.idle_service.eip_service.get_address")
def test_eip_associated_is_not_idle(mock_get_address: MagicMock) -> None:
    mock_get_address.return_value = ElasticIp(
        allocation_id="eipalloc-2",
        public_ip="1.2.3.4",
        domain="vpc",
        association_id="eipassoc-1",
        instance_id="i-123",
    )

    result = idle_service.check_idle("eip", "eipalloc-2", days=7)

    assert result.is_idle is False
    assert result.idle_since is None
    assert result.idle_days == 0
    assert result.idle_since_is_estimated is False


@patch("app.services.idle_service.eip_service.get_address")
def test_eip_not_found_does_not_fabricate_idle(mock_get_address: MagicMock) -> None:
    mock_get_address.return_value = None

    result = idle_service.check_idle("eip", "eipalloc-missing", days=7)

    assert result.is_idle is False
    assert result.idle_since is None


# =====================================================================
# ELB
# =====================================================================


def _load_balancer(created_time: datetime | None) -> LoadBalancer:
    return LoadBalancer(
        name="my-alb",
        lb_type="application",
        arn="arn:aws:elasticloadbalancing:us-east-1:123:loadbalancer/app/my-alb/50dc6c495c0c9188",
        state="active",
        created_time=created_time,
    )


@patch("app.services.idle_service.datetime")
@patch("app.services.idle_service.cloudwatch_service.get_daily_datapoints")
@patch("app.services.idle_service.elb_service.get_load_balancer")
def test_elb_fully_idle_window(
    mock_get_lb: MagicMock, mock_daily: MagicMock, mock_datetime: MagicMock
) -> None:
    mock_datetime.now.return_value = NOW
    mock_get_lb.return_value = _load_balancer(NOW - timedelta(days=60))
    requests = [_dp(i, 0.0) for i in range(6, -1, -1)]
    mock_daily.side_effect = _metric_series_side_effect({"RequestCount": requests})

    result = idle_service.check_idle("elb", "my-alb", days=7)

    assert result.is_idle is True
    assert result.idle_days == 7
    # Verify the CloudWatch call used the ALB's parsed dimension, not its
    # bare name or ARN.
    _, kwargs = mock_daily.call_args
    assert kwargs["namespace"] == "AWS/ApplicationELB"
    assert kwargs["dimension_name"] == "LoadBalancer"
    assert kwargs["dimension_value"] == "app/my-alb/50dc6c495c0c9188"


@patch("app.services.idle_service.datetime")
@patch("app.services.idle_service.cloudwatch_service.get_daily_datapoints")
@patch("app.services.idle_service.elb_service.get_load_balancer")
def test_elb_burst_then_idle_not_flagged_idle_for_full_window(
    mock_get_lb: MagicMock, mock_daily: MagicMock, mock_datetime: MagicMock
) -> None:
    mock_datetime.now.return_value = NOW
    mock_get_lb.return_value = _load_balancer(NOW - timedelta(days=60))
    requests = [_dp(i, 0.0) for i in range(6, -1, -1)]
    for dp in requests:
        if dp["Timestamp"] == NOW - timedelta(days=5):
            dp["Sum"] = 42.0
    mock_daily.side_effect = _metric_series_side_effect({"RequestCount": requests})

    result = idle_service.check_idle("elb", "my-alb", days=7)

    assert result.is_idle is False
    burst_date = (NOW - timedelta(days=5)).date()
    assert result.idle_since == burst_date + timedelta(days=1)
    assert result.idle_days == 5


@patch("app.services.idle_service.cloudwatch_service.get_daily_datapoints")
@patch("app.services.idle_service.elb_service.get_load_balancer")
def test_elb_not_found_does_not_fabricate_idle(
    mock_get_lb: MagicMock, mock_daily: MagicMock
) -> None:
    mock_get_lb.return_value = None

    result = idle_service.check_idle("elb", "missing-lb", days=7)

    assert result.is_idle is False
    assert result.idle_since is None
    mock_daily.assert_not_called()


# =====================================================================
# Step 3 batch B -- Lambda, NAT Gateway, DynamoDB, ElastiCache, SageMaker,
# Redshift, API Gateway, CloudFront, OpenSearch, Kinesis.
# =====================================================================


@patch("app.services.idle_service.datetime")
@patch("app.services.idle_service.cloudwatch_service.get_daily_datapoints")
@patch("app.services.idle_service.lambda_service.get_function")
def test_lambda_zero_invocations_whole_window_is_idle(
    mock_get_fn: MagicMock, mock_daily: MagicMock, mock_datetime: MagicMock
) -> None:
    """Lambda's Invocations metric is sparse -- CloudWatch returns no
    datapoint at all for a never-invoked function, not a stream of zeros.
    zero_fill_missing_days must still flag this as a genuine idle streak
    covering the full window, not 'no data, unknown'."""
    mock_datetime.now.return_value = NOW
    mock_get_fn.return_value = LambdaFunctionSummary(name="fn-1", runtime="python3.12")
    mock_daily.return_value = []

    result = idle_service.check_idle("lambda", "fn-1", days=7)

    assert result.is_idle is True
    assert result.idle_days == 7
    # "days=7" means 7 distinct calendar days including today, i.e.
    # today-6 through today -- not today-7 (see
    # _check_idle_via_metrics' zero_fill_missing_days docstring).
    assert result.idle_since == (NOW - timedelta(days=6)).date()
    assert result.idle_since_is_estimated is False


@patch("app.services.idle_service.datetime")
@patch("app.services.idle_service.cloudwatch_service.get_daily_datapoints")
@patch("app.services.idle_service.lambda_service.get_function")
def test_lambda_burst_then_idle_not_flagged_idle_for_full_window(
    mock_get_fn: MagicMock, mock_daily: MagicMock, mock_datetime: MagicMock
) -> None:
    mock_datetime.now.return_value = NOW
    mock_get_fn.return_value = LambdaFunctionSummary(name="fn-1")
    mock_daily.return_value = _to_metric_datapoints([_dp(3, 50.0)])

    result = idle_service.check_idle("lambda", "fn-1", days=7)

    assert result.is_idle is False
    burst_date = (NOW - timedelta(days=3)).date()
    assert result.idle_since == burst_date + timedelta(days=1)
    assert result.idle_days == 3


@patch("app.services.idle_service.lambda_service.get_function")
def test_lambda_not_found_does_not_fabricate_idle(mock_get_fn: MagicMock) -> None:
    mock_get_fn.return_value = None

    result = idle_service.check_idle("lambda", "missing-fn", days=7)

    assert result.is_idle is False
    assert result.idle_since is None


def _nat_gateway(create_time: datetime | None) -> NatGateway:
    return NatGateway(nat_gateway_id="nat-1", state="available", create_time=create_time)


@patch("app.services.idle_service.datetime")
@patch("app.services.idle_service.cloudwatch_service.get_daily_datapoints")
@patch("app.services.idle_service.nat_gateway_service.get_nat_gateway")
def test_nat_gateway_fully_idle_window(
    mock_get_gw: MagicMock, mock_daily: MagicMock, mock_datetime: MagicMock
) -> None:
    mock_datetime.now.return_value = NOW
    mock_get_gw.return_value = _nat_gateway(NOW - timedelta(days=60))
    mock_daily.return_value = []

    result = idle_service.check_idle("nat_gateway", "nat-1", days=7)

    assert result.is_idle is True
    assert result.idle_days == 7


@patch("app.services.idle_service.datetime")
@patch("app.services.idle_service.cloudwatch_service.get_daily_datapoints")
@patch("app.services.idle_service.nat_gateway_service.get_nat_gateway")
def test_nat_gateway_burst_then_idle(
    mock_get_gw: MagicMock, mock_daily: MagicMock, mock_datetime: MagicMock
) -> None:
    mock_datetime.now.return_value = NOW
    mock_get_gw.return_value = _nat_gateway(NOW - timedelta(days=60))
    mock_daily.side_effect = _metric_series_side_effect(
        {
            "BytesOutToDestination": [_dp(4, 10_000_000.0, unit="Bytes")],
            "BytesInFromSource": [],
        }
    )

    result = idle_service.check_idle("nat_gateway", "nat-1", days=7)

    assert result.is_idle is False
    burst_date = (NOW - timedelta(days=4)).date()
    assert result.idle_since == burst_date + timedelta(days=1)
    assert result.idle_days == 4


@patch("app.services.idle_service.datetime")
@patch("app.services.idle_service.nat_gateway_service.get_nat_gateway")
def test_nat_gateway_younger_than_window(
    mock_get_gw: MagicMock, mock_datetime: MagicMock
) -> None:
    mock_datetime.now.return_value = NOW
    create_time = NOW - timedelta(days=2, hours=12)
    mock_get_gw.return_value = _nat_gateway(create_time)

    with patch(
        "app.services.idle_service.cloudwatch_service.get_daily_datapoints", return_value=[]
    ):
        result = idle_service.check_idle("nat_gateway", "nat-1", days=10)

    assert result.younger_than_window is True
    assert result.is_idle is True
    assert result.idle_days == 3
    assert result.idle_since == create_time.date()


@patch("app.services.idle_service.nat_gateway_service.get_nat_gateway")
def test_nat_gateway_not_found_does_not_fabricate_idle(mock_get_gw: MagicMock) -> None:
    mock_get_gw.return_value = None

    result = idle_service.check_idle("nat_gateway", "missing-nat", days=7)

    assert result.is_idle is False
    assert result.idle_since is None


def _dynamo_table(
    creation_date_time: datetime | None, billing_mode: str = "PROVISIONED"
) -> DynamoTableSummary:
    return DynamoTableSummary(
        name="tbl-1",
        status="ACTIVE",
        creation_date_time=creation_date_time,
        billing_mode=billing_mode,
        read_capacity_units=5,
        write_capacity_units=5,
    )


@patch("app.services.idle_service.datetime")
@patch("app.services.idle_service.cloudwatch_service.get_daily_datapoints")
@patch("app.services.idle_service.dynamodb_service.get_table")
def test_dynamodb_fully_idle_window(
    mock_get_table: MagicMock, mock_daily: MagicMock, mock_datetime: MagicMock
) -> None:
    mock_datetime.now.return_value = NOW
    mock_get_table.return_value = _dynamo_table(NOW - timedelta(days=60))
    mock_daily.return_value = []

    result = idle_service.check_idle("dynamodb", "tbl-1", days=7)

    assert result.is_idle is True
    assert result.idle_days == 7


@patch("app.services.idle_service.datetime")
@patch("app.services.idle_service.cloudwatch_service.get_daily_datapoints")
@patch("app.services.idle_service.dynamodb_service.get_table")
def test_dynamodb_on_demand_table_burst_then_idle(
    mock_get_table: MagicMock, mock_daily: MagicMock, mock_datetime: MagicMock
) -> None:
    """The same Consumed*CapacityUnits signal applies to PAY_PER_REQUEST
    (on-demand) tables too -- see DYNAMODB_CAPACITY_IDLE_THRESHOLD's
    docstring for why."""
    mock_datetime.now.return_value = NOW
    mock_get_table.return_value = _dynamo_table(
        NOW - timedelta(days=60), billing_mode="PAY_PER_REQUEST"
    )
    mock_daily.side_effect = _metric_series_side_effect(
        {
            "ConsumedReadCapacityUnits": [],
            "ConsumedWriteCapacityUnits": [_dp(2, 20.0)],
        }
    )

    result = idle_service.check_idle("dynamodb", "tbl-1", days=7)

    assert result.is_idle is False
    burst_date = (NOW - timedelta(days=2)).date()
    assert result.idle_since == burst_date + timedelta(days=1)
    assert result.idle_days == 2


@patch("app.services.idle_service.dynamodb_service.get_table")
def test_dynamodb_not_found_does_not_fabricate_idle(mock_get_table: MagicMock) -> None:
    mock_get_table.return_value = None

    result = idle_service.check_idle("dynamodb", "missing-tbl", days=7)

    assert result.is_idle is False
    assert result.idle_since is None


def _elasticache_cluster(create_time: datetime | None) -> ElastiCacheCluster:
    return ElastiCacheCluster(
        cache_cluster_id="cache-1", node_type="cache.t3.micro", engine="redis",
        status="available", create_time=create_time,
    )


@patch("app.services.idle_service.datetime")
@patch("app.services.idle_service.cloudwatch_service.get_daily_datapoints")
@patch("app.services.idle_service.elasticache_service.get_cluster")
def test_elasticache_fully_idle_window(
    mock_get_cluster: MagicMock, mock_daily: MagicMock, mock_datetime: MagicMock
) -> None:
    mock_datetime.now.return_value = NOW
    mock_get_cluster.return_value = _elasticache_cluster(NOW - timedelta(days=60))
    mock_daily.side_effect = _metric_series_side_effect(
        {"CurrConnections": [_dp(i, 0.0) for i in range(6, -1, -1)]}
    )

    result = idle_service.check_idle("elasticache", "cache-1", days=7)

    assert result.is_idle is True
    assert result.idle_days == 7


@patch("app.services.idle_service.datetime")
@patch("app.services.idle_service.cloudwatch_service.get_daily_datapoints")
@patch("app.services.idle_service.elasticache_service.get_cluster")
def test_elasticache_burst_then_idle(
    mock_get_cluster: MagicMock, mock_daily: MagicMock, mock_datetime: MagicMock
) -> None:
    mock_datetime.now.return_value = NOW
    mock_get_cluster.return_value = _elasticache_cluster(NOW - timedelta(days=60))
    connections = [_dp(i, 0.0) for i in range(6, -1, -1)]
    for dp in connections:
        if dp["Timestamp"] == NOW - timedelta(days=5):
            dp["Maximum"] = 3.0
    mock_daily.side_effect = _metric_series_side_effect({"CurrConnections": connections})

    result = idle_service.check_idle("elasticache", "cache-1", days=7)

    assert result.is_idle is False
    burst_date = (NOW - timedelta(days=5)).date()
    assert result.idle_since == burst_date + timedelta(days=1)
    assert result.idle_days == 5


@patch("app.services.idle_service.elasticache_service.get_cluster")
def test_elasticache_not_found_does_not_fabricate_idle(mock_get_cluster: MagicMock) -> None:
    mock_get_cluster.return_value = None

    result = idle_service.check_idle("elasticache", "missing-cache", days=7)

    assert result.is_idle is False
    assert result.idle_since is None


def _sagemaker_endpoint(
    creation_time: datetime | None, variant_name: str | None = "AllTraffic"
) -> SageMakerEndpoint:
    return SageMakerEndpoint(
        endpoint_name="ep-1", status="InService", creation_time=creation_time,
        variant_name=variant_name, instance_type="ml.m5.large", instance_count=1,
    )


@patch("app.services.idle_service.datetime")
@patch("app.services.idle_service.cloudwatch_service.get_daily_datapoints")
@patch("app.services.idle_service.sagemaker_service.get_endpoint")
def test_sagemaker_zero_invocations_whole_window_is_idle(
    mock_get_ep: MagicMock, mock_daily: MagicMock, mock_datetime: MagicMock
) -> None:
    mock_datetime.now.return_value = NOW
    mock_get_ep.return_value = _sagemaker_endpoint(NOW - timedelta(days=60))
    mock_daily.return_value = []

    result = idle_service.check_idle("sagemaker", "ep-1", days=7)

    assert result.is_idle is True
    assert result.idle_days == 7
    # Both EndpointName and VariantName dimensions must be used together.
    _, kwargs = mock_daily.call_args
    assert kwargs["dimension_name"] == "EndpointName"
    assert kwargs["dimension_value"] == "ep-1"
    assert kwargs["extra_dimensions"] == [("VariantName", "AllTraffic")]


@patch("app.services.idle_service.datetime")
@patch("app.services.idle_service.cloudwatch_service.get_daily_datapoints")
@patch("app.services.idle_service.sagemaker_service.get_endpoint")
def test_sagemaker_burst_then_idle(
    mock_get_ep: MagicMock, mock_daily: MagicMock, mock_datetime: MagicMock
) -> None:
    mock_datetime.now.return_value = NOW
    mock_get_ep.return_value = _sagemaker_endpoint(NOW - timedelta(days=60))
    mock_daily.return_value = _to_metric_datapoints([_dp(2, 15.0)])

    result = idle_service.check_idle("sagemaker", "ep-1", days=7)

    assert result.is_idle is False
    burst_date = (NOW - timedelta(days=2)).date()
    assert result.idle_since == burst_date + timedelta(days=1)
    assert result.idle_days == 2


@patch("app.services.idle_service.sagemaker_service.get_endpoint")
def test_sagemaker_unknown_variant_does_not_fabricate_idle(mock_get_ep: MagicMock) -> None:
    """No production variant found -> no reliable CloudWatch dimension
    pair -- must not guess, must report 'can't verify' instead."""
    mock_get_ep.return_value = _sagemaker_endpoint(NOW, variant_name=None)

    result = idle_service.check_idle("sagemaker", "ep-1", days=7)

    assert result.is_idle is False
    assert result.idle_since is None


@patch("app.services.idle_service.sagemaker_service.get_endpoint")
def test_sagemaker_not_found_does_not_fabricate_idle(mock_get_ep: MagicMock) -> None:
    mock_get_ep.return_value = None

    result = idle_service.check_idle("sagemaker", "missing-ep", days=7)

    assert result.is_idle is False
    assert result.idle_since is None


def _redshift_cluster(create_time: datetime | None) -> RedshiftCluster:
    return RedshiftCluster(
        cluster_identifier="cl-1", node_type="dc2.large", status="available",
        create_time=create_time,
    )


@patch("app.services.idle_service.datetime")
@patch("app.services.idle_service.cloudwatch_service.get_daily_datapoints")
@patch("app.services.idle_service.redshift_service.get_cluster")
def test_redshift_fully_idle_window(
    mock_get_cluster: MagicMock, mock_daily: MagicMock, mock_datetime: MagicMock
) -> None:
    mock_datetime.now.return_value = NOW
    mock_get_cluster.return_value = _redshift_cluster(NOW - timedelta(days=60))
    mock_daily.side_effect = _metric_series_side_effect(
        {"DatabaseConnections": [_dp(i, 0.0) for i in range(6, -1, -1)]}
    )

    result = idle_service.check_idle("redshift", "cl-1", days=7)

    assert result.is_idle is True
    assert result.idle_days == 7


@patch("app.services.idle_service.datetime")
@patch("app.services.idle_service.cloudwatch_service.get_daily_datapoints")
@patch("app.services.idle_service.redshift_service.get_cluster")
def test_redshift_burst_then_idle(
    mock_get_cluster: MagicMock, mock_daily: MagicMock, mock_datetime: MagicMock
) -> None:
    mock_datetime.now.return_value = NOW
    mock_get_cluster.return_value = _redshift_cluster(NOW - timedelta(days=60))
    connections = [_dp(i, 0.0) for i in range(6, -1, -1)]
    for dp in connections:
        if dp["Timestamp"] == NOW - timedelta(days=1):
            dp["Maximum"] = 4.0
    mock_daily.side_effect = _metric_series_side_effect({"DatabaseConnections": connections})

    result = idle_service.check_idle("redshift", "cl-1", days=7)

    assert result.is_idle is False
    burst_date = (NOW - timedelta(days=1)).date()
    assert result.idle_since == burst_date + timedelta(days=1)
    assert result.idle_days == 1


@patch("app.services.idle_service.redshift_service.get_cluster")
def test_redshift_not_found_does_not_fabricate_idle(mock_get_cluster: MagicMock) -> None:
    mock_get_cluster.return_value = None

    result = idle_service.check_idle("redshift", "missing-cl", days=7)

    assert result.is_idle is False
    assert result.idle_since is None


def _rest_api(created_date: datetime | None) -> ApiGatewayRestApi:
    return ApiGatewayRestApi(api_id="abc123", name="my-api", created_date=created_date)


@patch("app.services.idle_service.datetime")
@patch("app.services.idle_service.cloudwatch_service.get_daily_datapoints")
@patch("app.services.idle_service.api_gateway_service.get_api")
def test_api_gateway_zero_requests_whole_window_is_idle(
    mock_get_api: MagicMock, mock_daily: MagicMock, mock_datetime: MagicMock
) -> None:
    mock_datetime.now.return_value = NOW
    mock_get_api.return_value = _rest_api(NOW - timedelta(days=60))
    mock_daily.return_value = []

    result = idle_service.check_idle("api_gateway", "abc123", days=7)

    assert result.is_idle is True
    assert result.idle_days == 7
    # Dimension value is the API's `name`, not its raw id.
    _, kwargs = mock_daily.call_args
    assert kwargs["dimension_name"] == "ApiName"
    assert kwargs["dimension_value"] == "my-api"


@patch("app.services.idle_service.datetime")
@patch("app.services.idle_service.cloudwatch_service.get_daily_datapoints")
@patch("app.services.idle_service.api_gateway_service.get_api")
def test_api_gateway_burst_then_idle(
    mock_get_api: MagicMock, mock_daily: MagicMock, mock_datetime: MagicMock
) -> None:
    mock_datetime.now.return_value = NOW
    mock_get_api.return_value = _rest_api(NOW - timedelta(days=60))
    mock_daily.return_value = _to_metric_datapoints([_dp(6, 200.0)])

    result = idle_service.check_idle("api_gateway", "abc123", days=7)

    assert result.is_idle is False
    burst_date = (NOW - timedelta(days=6)).date()
    assert result.idle_since == burst_date + timedelta(days=1)
    assert result.idle_days == 6


@patch("app.services.idle_service.api_gateway_service.get_api")
def test_api_gateway_not_found_does_not_fabricate_idle(mock_get_api: MagicMock) -> None:
    mock_get_api.return_value = None

    result = idle_service.check_idle("api_gateway", "missing-api", days=7)

    assert result.is_idle is False
    assert result.idle_since is None


def _distribution() -> CloudFrontDistribution:
    return CloudFrontDistribution(distribution_id="E123", status="Deployed", enabled=True)


@patch("app.services.idle_service.datetime")
@patch("app.services.idle_service.cloudwatch_service.get_daily_datapoints")
@patch("app.services.idle_service.cloudfront_service.get_distribution")
def test_cloudfront_zero_requests_whole_window_is_idle(
    mock_get_dist: MagicMock, mock_daily: MagicMock, mock_datetime: MagicMock
) -> None:
    mock_datetime.now.return_value = NOW
    mock_get_dist.return_value = _distribution()
    mock_daily.return_value = []

    result = idle_service.check_idle("cloudfront", "E123", days=7)

    assert result.is_idle is True
    assert result.idle_days == 7
    # No creation timestamp exists for CloudFront -- never younger-than-window.
    assert result.younger_than_window is False
    # Metrics must be pinned to us-east-1 regardless of the account's region.
    _, kwargs = mock_daily.call_args
    assert kwargs["region"] == "us-east-1"


@patch("app.services.idle_service.datetime")
@patch("app.services.idle_service.cloudwatch_service.get_daily_datapoints")
@patch("app.services.idle_service.cloudfront_service.get_distribution")
def test_cloudfront_burst_then_idle(
    mock_get_dist: MagicMock, mock_daily: MagicMock, mock_datetime: MagicMock
) -> None:
    mock_datetime.now.return_value = NOW
    mock_get_dist.return_value = _distribution()
    mock_daily.return_value = _to_metric_datapoints([_dp(4, 500.0)])

    result = idle_service.check_idle("cloudfront", "E123", days=7)

    assert result.is_idle is False
    burst_date = (NOW - timedelta(days=4)).date()
    assert result.idle_since == burst_date + timedelta(days=1)
    assert result.idle_days == 4


@patch("app.services.idle_service.cloudfront_service.get_distribution")
def test_cloudfront_not_found_does_not_fabricate_idle(mock_get_dist: MagicMock) -> None:
    mock_get_dist.return_value = None

    result = idle_service.check_idle("cloudfront", "missing-dist", days=7)

    assert result.is_idle is False
    assert result.idle_since is None


def _opensearch_domain(account_id: str | None = "123456789012") -> OpenSearchDomain:
    if account_id:
        arn = f"arn:aws:es:us-east-1:{account_id}:domain/my-domain"
    else:
        arn = "arn:aws:es:us-east-1:domain/my-domain"
    return OpenSearchDomain(domain_name="my-domain", arn=arn, instance_type="r6g.large.search")


@patch("app.services.idle_service.datetime")
@patch("app.services.idle_service.cloudwatch_service.get_daily_datapoints")
@patch("app.services.idle_service.opensearch_service.get_domain")
def test_opensearch_fully_idle_window(
    mock_get_domain: MagicMock, mock_daily: MagicMock, mock_datetime: MagicMock
) -> None:
    mock_datetime.now.return_value = NOW
    mock_get_domain.return_value = _opensearch_domain()
    mock_daily.side_effect = _metric_series_side_effect(
        {
            "SearchRate": [_dp(i, 0.0) for i in range(6, -1, -1)],
            "IndexingRate": [_dp(i, 0.0) for i in range(6, -1, -1)],
        }
    )

    result = idle_service.check_idle("opensearch", "my-domain", days=7)

    assert result.is_idle is True
    assert result.idle_days == 7
    _, kwargs = mock_daily.call_args
    assert kwargs["extra_dimensions"] == [("ClientId", "123456789012")]


@patch("app.services.idle_service.datetime")
@patch("app.services.idle_service.cloudwatch_service.get_daily_datapoints")
@patch("app.services.idle_service.opensearch_service.get_domain")
def test_opensearch_burst_then_idle(
    mock_get_domain: MagicMock, mock_daily: MagicMock, mock_datetime: MagicMock
) -> None:
    mock_datetime.now.return_value = NOW
    mock_get_domain.return_value = _opensearch_domain()
    search = [_dp(i, 0.0) for i in range(6, -1, -1)]
    for dp in search:
        if dp["Timestamp"] == NOW - timedelta(days=3):
            dp["Average"] = 12.0
    index_rate = [_dp(i, 0.0) for i in range(6, -1, -1)]
    mock_daily.side_effect = _metric_series_side_effect(
        {"SearchRate": search, "IndexingRate": index_rate}
    )

    result = idle_service.check_idle("opensearch", "my-domain", days=7)

    assert result.is_idle is False
    burst_date = (NOW - timedelta(days=3)).date()
    assert result.idle_since == burst_date + timedelta(days=1)
    assert result.idle_days == 3


@patch("app.services.idle_service.opensearch_service.get_domain")
def test_opensearch_not_found_does_not_fabricate_idle(mock_get_domain: MagicMock) -> None:
    mock_get_domain.return_value = None

    result = idle_service.check_idle("opensearch", "missing-domain", days=7)

    assert result.is_idle is False
    assert result.idle_since is None


def _kinesis_stream(creation_timestamp: datetime | None) -> KinesisStream:
    return KinesisStream(
        stream_name="stream-1", status="ACTIVE", open_shard_count=2,
        creation_timestamp=creation_timestamp,
    )


@patch("app.services.idle_service.datetime")
@patch("app.services.idle_service.cloudwatch_service.get_daily_datapoints")
@patch("app.services.idle_service.kinesis_service.get_stream")
def test_kinesis_zero_records_whole_window_is_idle(
    mock_get_stream: MagicMock, mock_daily: MagicMock, mock_datetime: MagicMock
) -> None:
    mock_datetime.now.return_value = NOW
    mock_get_stream.return_value = _kinesis_stream(NOW - timedelta(days=60))
    mock_daily.return_value = []

    result = idle_service.check_idle("kinesis", "stream-1", days=7)

    assert result.is_idle is True
    assert result.idle_days == 7


@patch("app.services.idle_service.datetime")
@patch("app.services.idle_service.cloudwatch_service.get_daily_datapoints")
@patch("app.services.idle_service.kinesis_service.get_stream")
def test_kinesis_burst_then_idle(
    mock_get_stream: MagicMock, mock_daily: MagicMock, mock_datetime: MagicMock
) -> None:
    mock_datetime.now.return_value = NOW
    mock_get_stream.return_value = _kinesis_stream(NOW - timedelta(days=60))
    mock_daily.return_value = _to_metric_datapoints([_dp(5, 1000.0)])

    result = idle_service.check_idle("kinesis", "stream-1", days=7)

    assert result.is_idle is False
    burst_date = (NOW - timedelta(days=5)).date()
    assert result.idle_since == burst_date + timedelta(days=1)
    assert result.idle_days == 5


@patch("app.services.idle_service.datetime")
@patch("app.services.idle_service.kinesis_service.get_stream")
def test_kinesis_younger_than_window(
    mock_get_stream: MagicMock, mock_datetime: MagicMock
) -> None:
    mock_datetime.now.return_value = NOW
    create_time = NOW - timedelta(days=3, hours=12)
    mock_get_stream.return_value = _kinesis_stream(create_time)

    with patch(
        "app.services.idle_service.cloudwatch_service.get_daily_datapoints", return_value=[]
    ):
        result = idle_service.check_idle("kinesis", "stream-1", days=10)

    assert result.younger_than_window is True
    assert result.is_idle is True
    assert result.idle_days == 4
    assert result.idle_since == create_time.date()


@patch("app.services.idle_service.kinesis_service.get_stream")
def test_kinesis_not_found_does_not_fabricate_idle(mock_get_stream: MagicMock) -> None:
    mock_get_stream.return_value = None

    result = idle_service.check_idle("kinesis", "missing-stream", days=7)

    assert result.is_idle is False
    assert result.idle_since is None
