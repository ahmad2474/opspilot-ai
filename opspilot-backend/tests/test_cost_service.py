import json
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

from app.models.api_gateway import ApiGatewayRestApi
from app.models.cloudfront import CloudFrontDistribution
from app.models.cost import DateRange
from app.models.dashboard import DynamoTableSummary, LambdaFunctionSummary, RdsInstanceSummary
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
from app.services import cost_service

NOW = datetime(2026, 7, 10, 12, 0, 0, tzinfo=timezone.utc)


def _pricing_response(hourly_usd: str) -> dict:
    product = {
        "terms": {
            "OnDemand": {
                "SKU.OFFER_TERM": {
                    "priceDimensions": {
                        "SKU.OFFER_TERM.RATE": {
                            "pricePerUnit": {"USD": hourly_usd},
                            "unit": "Hrs",
                        }
                    }
                }
            }
        }
    }
    return {"PriceList": [json.dumps(product)]}


def _instance(launch_time: datetime | None, instance_type: str = "t3.micro") -> EC2Instance:
    return EC2Instance(
        instance_id="i-123",
        instance_type=instance_type,
        state="running",
        availability_zone="us-east-1d",
        launch_time=launch_time,
        tags={},
    )


@patch("app.services.cost_service.datetime")
@patch("app.services.cost_service.get_pricing_client")
@patch("app.services.cost_service.ec2_service.get_instance")
def test_projected_monthly_and_incurred_so_far_are_distinct(
    mock_get_instance: MagicMock, mock_get_pricing: MagicMock, mock_datetime: MagicMock
) -> None:
    """A large instance created 2 hours ago must show a real
    projected_monthly figure independent of the tiny incurred_so_far --
    the two must never collapse into one number (roadmap 3.1a)."""
    mock_datetime.now.return_value = NOW
    launch_time = NOW - timedelta(hours=2)
    mock_get_instance.return_value = _instance(launch_time, instance_type="m5.24xlarge")

    mock_client = MagicMock()
    mock_client.get_products.return_value = _pricing_response("4.8000000000")
    mock_get_pricing.return_value = mock_client

    result = cost_service.estimate_cost("ec2", "i-123")

    assert result.method == "list_price"
    assert result.hourly_rate == 4.8
    # projected_monthly = rate x 730h, independent of how new the instance is.
    assert result.projected_monthly == pytest.approx(4.8 * 730.0, rel=1e-6)
    # incurred_so_far = rate x ~2 elapsed hours only.
    assert result.incurred_so_far == pytest.approx(4.8 * 2.0, rel=1e-2)
    assert result.incurred_so_far < result.projected_monthly


@patch("app.services.cost_service.datetime")
@patch("app.services.cost_service.get_pricing_client")
@patch("app.services.cost_service.ec2_service.get_instance")
def test_incurred_so_far_capped_at_instance_age(
    mock_get_instance: MagicMock, mock_get_pricing: MagicMock, mock_datetime: MagicMock
) -> None:
    """A date_range starting before the instance existed must not credit
    it with cost from before it was created."""
    mock_datetime.now.return_value = NOW
    launch_time = NOW - timedelta(hours=5)
    mock_get_instance.return_value = _instance(launch_time)

    mock_client = MagicMock()
    mock_client.get_products.return_value = _pricing_response("0.0500000000")
    mock_get_pricing.return_value = mock_client

    # Requested window starts 30 days before launch.
    date_range = DateRange(start=NOW - timedelta(days=30), end=NOW)
    result = cost_service.estimate_cost("ec2", "i-123", date_range)

    # Only ~5 hours (since launch), not 30 days' worth.
    assert result.incurred_so_far == pytest.approx(0.05 * 5.0, rel=1e-2)


@patch("app.services.cost_service.datetime")
@patch("app.services.cost_service.get_pricing_client")
@patch("app.services.cost_service.ec2_service.get_instance")
def test_incurred_so_far_capped_at_now_when_date_range_end_is_future(
    mock_get_instance: MagicMock, mock_get_pricing: MagicMock, mock_datetime: MagicMock
) -> None:
    """A date_range extending past "now" must not credit the instance with
    cost for hours that haven't happened yet."""
    mock_datetime.now.return_value = NOW
    launch_time = NOW - timedelta(days=30)
    mock_get_instance.return_value = _instance(launch_time)

    mock_client = MagicMock()
    mock_client.get_products.return_value = _pricing_response("0.1000000000")
    mock_get_pricing.return_value = mock_client

    # Window starts 10 hours ago but extends 5 days into the future.
    date_range = DateRange(start=NOW - timedelta(hours=10), end=NOW + timedelta(days=5))
    result = cost_service.estimate_cost("ec2", "i-123", date_range)

    # Only the ~10 hours through "now" count, not the extra 5 days.
    assert result.incurred_so_far == pytest.approx(0.1 * 10.0, rel=1e-2)


@patch("app.services.cost_service.get_pricing_client")
@patch("app.services.cost_service.ec2_service.get_instance")
def test_instance_not_found_raises(
    mock_get_instance: MagicMock, mock_get_pricing: MagicMock
) -> None:
    mock_get_instance.return_value = None

    with pytest.raises(ValueError, match="not found"):
        cost_service.estimate_cost("ec2", "i-missing")


def test_unsupported_resource_type_raises() -> None:
    """'lambda' used to be the go-to unsupported example in batch A -- now
    that batch B adds it (plus the other 9 remaining roadmap types), 's3'
    is used instead: it's explicitly Tier 2 / deferred (roadmap Section
    2a), never one of the 15 in-scope types, so it stays valid forever."""
    with pytest.raises(cost_service.UnsupportedResourceTypeError):
        cost_service.estimate_cost("s3", "my-bucket")


@patch("app.services.cost_service.ec2_service.get_instance")
def test_billed_method_not_implemented(mock_get_instance: MagicMock) -> None:
    mock_get_instance.return_value = _instance(NOW)
    with pytest.raises(NotImplementedError):
        cost_service.estimate_cost("ec2", "i-123", method="billed")


@patch("app.services.cost_service.get_pricing_client")
@patch("app.services.cost_service.ec2_service.get_instance")
def test_no_price_found_raises_clear_error(
    mock_get_instance: MagicMock, mock_get_pricing: MagicMock
) -> None:
    mock_get_instance.return_value = _instance(NOW)
    mock_client = MagicMock()
    mock_client.get_products.return_value = {"PriceList": []}
    mock_get_pricing.return_value = mock_client

    with pytest.raises(ValueError, match="no on-demand price"):
        cost_service.estimate_cost("ec2", "i-123")


# =====================================================================
# EBS -- GB-month priced, not hourly. projected_monthly is naturally
# date_range-independent (roadmap instructions).
# =====================================================================


def _volume(
    create_time: datetime | None, size_gb: int = 100, volume_type: str = "gp3"
) -> EbsVolume:
    return EbsVolume(
        volume_id="vol-123",
        size_gb=size_gb,
        volume_type=volume_type,
        state="in-use",
        availability_zone="us-east-1d",
        create_time=create_time,
        attached_instance_ids=["i-123"],
    )


@patch("app.services.cost_service.datetime")
@patch("app.services.cost_service.get_pricing_client")
@patch("app.services.cost_service.ebs_service.get_volume")
def test_ebs_projected_monthly_is_size_times_gb_month_rate(
    mock_get_volume: MagicMock, mock_get_pricing: MagicMock, mock_datetime: MagicMock
) -> None:
    mock_datetime.now.return_value = NOW
    mock_get_volume.return_value = _volume(NOW - timedelta(hours=2), size_gb=100, volume_type="gp3")

    mock_client = MagicMock()
    mock_client.get_products.return_value = _pricing_response("0.0800000000")  # $/GB-month
    mock_get_pricing.return_value = mock_client

    result = cost_service.estimate_cost("ebs", "vol-123")

    assert result.resource_type == "ebs"
    # 100 GB x $0.08/GB-month = $8.00/month, independent of date_range.
    assert result.projected_monthly == pytest.approx(8.0, rel=1e-6)
    # ~2 hours elapsed at the equivalent hourly rate (8.0 / 730) -- both
    # sides rounded to cents, so an absolute (not relative) tolerance is
    # correct at this small a dollar amount.
    assert result.incurred_so_far == pytest.approx((8.0 / 730.0) * 2.0, abs=0.01)
    assert result.incurred_so_far < result.projected_monthly


@patch("app.services.cost_service.get_pricing_client")
@patch("app.services.cost_service.ebs_service.get_volume")
def test_ebs_volume_not_found_raises(
    mock_get_volume: MagicMock, mock_get_pricing: MagicMock
) -> None:
    mock_get_volume.return_value = None
    with pytest.raises(ValueError, match="not found"):
        cost_service.estimate_cost("ebs", "vol-missing")


# =====================================================================
# RDS -- instance-hour rate, same shape as EC2.
# =====================================================================


def _rds_instance(
    instance_create_time: datetime | None, engine: str = "postgres"
) -> RdsInstanceSummary:
    return RdsInstanceSummary(
        identifier="db-1",
        engine=engine,
        instance_class="db.t3.micro",
        status="available",
        instance_create_time=instance_create_time,
    )


@patch("app.services.cost_service.datetime")
@patch("app.services.cost_service.get_pricing_client")
@patch("app.services.cost_service.rds_service.get_instance")
def test_rds_projected_vs_incurred_are_distinct(
    mock_get_instance: MagicMock, mock_get_pricing: MagicMock, mock_datetime: MagicMock
) -> None:
    mock_datetime.now.return_value = NOW
    mock_get_instance.return_value = _rds_instance(NOW - timedelta(hours=3))

    mock_client = MagicMock()
    mock_client.get_products.return_value = _pricing_response("0.2000000000")
    mock_get_pricing.return_value = mock_client

    result = cost_service.estimate_cost("rds", "db-1")

    assert result.resource_type == "rds"
    assert result.hourly_rate == 0.2
    assert result.projected_monthly == pytest.approx(0.2 * 730.0, rel=1e-6)
    assert result.incurred_so_far == pytest.approx(0.2 * 3.0, rel=1e-2)
    assert result.incurred_so_far < result.projected_monthly


@patch("app.services.cost_service.rds_service.get_instance")
def test_rds_unmapped_engine_raises_clear_error(mock_get_instance: MagicMock) -> None:
    mock_get_instance.return_value = _rds_instance(NOW, engine="oracle-ee")
    with pytest.raises(ValueError, match="Pricing API databaseEngine mapping"):
        cost_service.estimate_cost("rds", "db-1")


@patch("app.services.cost_service.get_pricing_client")
@patch("app.services.cost_service.rds_service.get_instance")
def test_rds_instance_not_found_raises(
    mock_get_instance: MagicMock, mock_get_pricing: MagicMock
) -> None:
    mock_get_instance.return_value = None
    with pytest.raises(ValueError, match="not found"):
        cost_service.estimate_cost("rds", "db-missing")


# =====================================================================
# EIP -- flat idle surcharge, only charged when unattached. No allocation
# timestamp exists at all (see cost_service._estimate_cost_eip docstring),
# so date_range defaults to [now, now) rather than "since creation".
# =====================================================================


@patch("app.services.cost_service.datetime")
@patch("app.services.cost_service.eip_service.get_address")
def test_eip_unassociated_charges_flat_idle_rate(
    mock_get_address: MagicMock, mock_datetime: MagicMock
) -> None:
    mock_datetime.now.return_value = NOW
    mock_get_address.return_value = ElasticIp(
        allocation_id="eipalloc-1", public_ip="1.2.3.4", domain="vpc"
    )

    result = cost_service.estimate_cost("eip", "eipalloc-1")

    assert result.hourly_rate == cost_service.EIP_IDLE_HOURLY_RATE_USD
    assert result.projected_monthly == pytest.approx(
        cost_service.EIP_IDLE_HOURLY_RATE_USD * 730.0, rel=1e-6
    )
    # No allocation timestamp available -- default date_range is [now, now),
    # so incurred_so_far is 0 unless the caller supplies an explicit window.
    assert result.incurred_so_far == 0.0


@patch("app.services.cost_service.datetime")
@patch("app.services.cost_service.eip_service.get_address")
def test_eip_unassociated_with_explicit_date_range_prorates(
    mock_get_address: MagicMock, mock_datetime: MagicMock
) -> None:
    mock_datetime.now.return_value = NOW
    mock_get_address.return_value = ElasticIp(
        allocation_id="eipalloc-1", public_ip="1.2.3.4", domain="vpc"
    )

    date_range = DateRange(start=NOW - timedelta(hours=10), end=NOW)
    result = cost_service.estimate_cost("eip", "eipalloc-1", date_range)

    assert result.incurred_so_far == pytest.approx(
        cost_service.EIP_IDLE_HOURLY_RATE_USD * 10.0, rel=1e-6
    )


@patch("app.services.cost_service.eip_service.get_address")
def test_eip_associated_is_free(mock_get_address: MagicMock) -> None:
    mock_get_address.return_value = ElasticIp(
        allocation_id="eipalloc-2",
        public_ip="1.2.3.4",
        domain="vpc",
        association_id="eipassoc-1",
        instance_id="i-123",
    )

    result = cost_service.estimate_cost("eip", "eipalloc-2")

    assert result.hourly_rate == 0.0
    assert result.projected_monthly == 0.0


@patch("app.services.cost_service.eip_service.get_address")
def test_eip_not_found_raises(mock_get_address: MagicMock) -> None:
    mock_get_address.return_value = None
    with pytest.raises(ValueError, match="not found"):
        cost_service.estimate_cost("eip", "eipalloc-missing")


# =====================================================================
# ELB -- per-hour base rate only (LCU/NLCU usage ignored, per roadmap).
# =====================================================================


def _load_balancer(created_time: datetime | None) -> LoadBalancer:
    return LoadBalancer(
        name="my-alb",
        lb_type="application",
        arn="arn:aws:elasticloadbalancing:us-east-1:123:loadbalancer/app/my-alb/50dc6c495c0c9188",
        state="active",
        created_time=created_time,
    )


@patch("app.services.cost_service.datetime")
@patch("app.services.cost_service.get_pricing_client")
@patch("app.services.cost_service.elb_service.get_load_balancer")
def test_elb_projected_vs_incurred_are_distinct(
    mock_get_lb: MagicMock, mock_get_pricing: MagicMock, mock_datetime: MagicMock
) -> None:
    mock_datetime.now.return_value = NOW
    mock_get_lb.return_value = _load_balancer(NOW - timedelta(hours=1))

    mock_client = MagicMock()
    mock_client.get_products.return_value = _pricing_response("0.0225000000")
    mock_get_pricing.return_value = mock_client

    result = cost_service.estimate_cost("elb", "my-alb")

    assert result.resource_type == "elb"
    assert result.hourly_rate == 0.0225
    assert result.projected_monthly == pytest.approx(0.0225 * 730.0, abs=0.01)
    assert result.incurred_so_far == pytest.approx(0.0225 * 1.0, abs=0.01)
    assert result.incurred_so_far < result.projected_monthly

    # Pricing API queried with the ALB-specific productFamily.
    call_kwargs = mock_client.get_products.call_args.kwargs
    assert call_kwargs["ServiceCode"] == "AWSELB"
    filters = {f["Field"]: f["Value"] for f in call_kwargs["Filters"]}
    assert filters["productFamily"] == "Load Balancer-Application"


@patch("app.services.cost_service.get_pricing_client")
@patch("app.services.cost_service.elb_service.get_load_balancer")
def test_elb_not_found_raises(
    mock_get_lb: MagicMock, mock_get_pricing: MagicMock
) -> None:
    mock_get_lb.return_value = None
    with pytest.raises(ValueError, match="not found"):
        cost_service.estimate_cost("elb", "missing-lb")


# =====================================================================
# Step 3 batch B -- Lambda, NAT Gateway, DynamoDB, ElastiCache, SageMaker,
# Redshift, API Gateway, CloudFront, OpenSearch, Kinesis.
# =====================================================================


def _metric_datapoints(values_by_day_offset: dict[int, float]) -> list:
    """Real MetricDatapoint models timestamped `offset` days before NOW --
    used to mock cost_service.cloudwatch_service.get_daily_datapoints for
    the usage-based types (Lambda, DynamoDB on-demand, API Gateway,
    CloudFront)."""
    from app.models.cloudwatch import MetricDatapoint

    return [
        MetricDatapoint(
            timestamp=NOW - timedelta(days=offset), average=value, maximum=None, unit="Count"
        )
        for offset, value in values_by_day_offset.items()
    ]


# --- Lambda -----------------------------------------------------------


@patch("app.services.cost_service.datetime")
@patch("app.services.cost_service.cloudwatch_service.get_daily_datapoints")
@patch("app.services.cost_service.lambda_service.get_function")
def test_lambda_usage_based_projected_vs_incurred_are_distinct(
    mock_get_fn: MagicMock, mock_daily: MagicMock, mock_datetime: MagicMock
) -> None:
    mock_datetime.now.return_value = NOW
    mock_get_fn.return_value = LambdaFunctionSummary(name="fn-1", memory_size_mb=256)
    mock_daily.return_value = _metric_datapoints({0: 500_000, 3: 500_000})

    result = cost_service.estimate_cost("lambda", "fn-1")

    assert result.resource_type == "lambda"
    # No fixed hourly rate exists for a pay-per-invocation resource.
    assert result.hourly_rate is None
    assert result.incurred_so_far > 0
    # 7-day observed window extrapolated to a 30-day month must be larger.
    assert result.projected_monthly > result.incurred_so_far
    # Default date_range: no creation timestamp exists for Lambda, so a
    # trailing 7-day window is used instead of a fabricated "since
    # creation" default.
    assert (result.date_range.end - result.date_range.start) == timedelta(days=7)


@patch("app.services.cost_service.lambda_service.get_function")
def test_lambda_not_found_raises(mock_get_fn: MagicMock) -> None:
    mock_get_fn.return_value = None
    with pytest.raises(ValueError, match="not found"):
        cost_service.estimate_cost("lambda", "missing-fn")


# --- NAT Gateway --------------------------------------------------------


def _nat_gateway(create_time: datetime | None) -> NatGateway:
    return NatGateway(nat_gateway_id="nat-1", state="available", create_time=create_time)


@patch("app.services.cost_service.datetime")
@patch("app.services.cost_service.get_pricing_client")
@patch("app.services.cost_service.nat_gateway_service.get_nat_gateway")
def test_nat_gateway_projected_vs_incurred_are_distinct(
    mock_get_gw: MagicMock, mock_get_pricing: MagicMock, mock_datetime: MagicMock
) -> None:
    mock_datetime.now.return_value = NOW
    mock_get_gw.return_value = _nat_gateway(NOW - timedelta(hours=4))
    mock_client = MagicMock()
    mock_client.get_products.return_value = _pricing_response("0.0450000000")
    mock_get_pricing.return_value = mock_client

    result = cost_service.estimate_cost("nat_gateway", "nat-1")

    assert result.hourly_rate == 0.045
    assert result.projected_monthly == pytest.approx(0.045 * 730.0, rel=1e-6)
    assert result.incurred_so_far == pytest.approx(0.045 * 4.0, rel=1e-2)
    assert result.incurred_so_far < result.projected_monthly


@patch("app.services.cost_service.get_pricing_client")
@patch("app.services.cost_service.nat_gateway_service.get_nat_gateway")
def test_nat_gateway_not_found_raises(
    mock_get_gw: MagicMock, mock_get_pricing: MagicMock
) -> None:
    mock_get_gw.return_value = None
    with pytest.raises(ValueError, match="not found"):
        cost_service.estimate_cost("nat_gateway", "missing-nat")


# --- DynamoDB -- provisioned (RCU/WCU-hour) vs on-demand (usage-based) --


def _dynamo_table(
    creation_date_time: datetime | None,
    billing_mode: str = "PROVISIONED",
    read_capacity_units: int = 10,
    write_capacity_units: int = 5,
) -> DynamoTableSummary:
    return DynamoTableSummary(
        name="tbl-1",
        status="ACTIVE",
        creation_date_time=creation_date_time,
        billing_mode=billing_mode,
        read_capacity_units=read_capacity_units,
        write_capacity_units=write_capacity_units,
    )


@patch("app.services.cost_service.datetime")
@patch("app.services.cost_service.dynamodb_service.get_table")
def test_dynamodb_provisioned_priced_from_rcu_wcu(
    mock_get_table: MagicMock, mock_datetime: MagicMock
) -> None:
    mock_datetime.now.return_value = NOW
    mock_get_table.return_value = _dynamo_table(
        NOW - timedelta(hours=2), read_capacity_units=10, write_capacity_units=5
    )

    result = cost_service.estimate_cost("dynamodb", "tbl-1")

    expected_hourly = (
        10 * cost_service.DYNAMODB_RCU_HOURLY_RATE_USD
        + 5 * cost_service.DYNAMODB_WCU_HOURLY_RATE_USD
    )
    assert result.hourly_rate == pytest.approx(expected_hourly, rel=1e-9)
    # projected_monthly is rounded to cents server-side and expected_hourly
    # x 730 isn't a clean 2-decimal number -- absolute, not relative,
    # tolerance is correct here (same rationale as the EBS GB-month test).
    assert result.projected_monthly == pytest.approx(expected_hourly * 730.0, abs=0.01)
    assert result.incurred_so_far == pytest.approx(expected_hourly * 2.0, abs=0.01)
    assert result.incurred_so_far < result.projected_monthly


@patch("app.services.cost_service.datetime")
@patch("app.services.cost_service.cloudwatch_service.get_daily_datapoints")
@patch("app.services.cost_service.dynamodb_service.get_table")
def test_dynamodb_on_demand_is_usage_based_not_capacity_hour(
    mock_get_table: MagicMock, mock_daily: MagicMock, mock_datetime: MagicMock
) -> None:
    """PAY_PER_REQUEST tables must not be priced as if RCU/WCU were
    reserved -- their read/write_capacity_units are 0, but cost still
    comes from observed Consumed*CapacityUnits, not a $0 hourly rate."""
    mock_datetime.now.return_value = NOW
    mock_get_table.return_value = _dynamo_table(
        NOW - timedelta(days=60),
        billing_mode="PAY_PER_REQUEST",
        read_capacity_units=0,
        write_capacity_units=0,
    )
    mock_daily.return_value = _metric_datapoints({0: 1_000_000})

    result = cost_service.estimate_cost("dynamodb", "tbl-1")

    assert result.hourly_rate is None
    assert result.incurred_so_far > 0


@patch("app.services.cost_service.dynamodb_service.get_table")
def test_dynamodb_not_found_raises(mock_get_table: MagicMock) -> None:
    mock_get_table.return_value = None
    with pytest.raises(ValueError, match="not found"):
        cost_service.estimate_cost("dynamodb", "missing-tbl")


# --- ElastiCache ---------------------------------------------------------


def _elasticache_cluster(
    create_time: datetime | None, num_cache_nodes: int = 1
) -> ElastiCacheCluster:
    return ElastiCacheCluster(
        cache_cluster_id="cache-1", node_type="cache.t3.micro", engine="redis",
        status="available", num_cache_nodes=num_cache_nodes, create_time=create_time,
    )


@patch("app.services.cost_service.datetime")
@patch("app.services.cost_service.get_pricing_client")
@patch("app.services.cost_service.elasticache_service.get_cluster")
def test_elasticache_multi_node_hourly_rate_is_per_node(
    mock_get_cluster: MagicMock, mock_get_pricing: MagicMock, mock_datetime: MagicMock
) -> None:
    mock_datetime.now.return_value = NOW
    mock_get_cluster.return_value = _elasticache_cluster(
        NOW - timedelta(hours=3), num_cache_nodes=3
    )
    mock_client = MagicMock()
    mock_client.get_products.return_value = _pricing_response("0.0170000000")
    mock_get_pricing.return_value = mock_client

    result = cost_service.estimate_cost("elasticache", "cache-1")

    assert result.hourly_rate == pytest.approx(0.017 * 3, rel=1e-6)
    assert result.projected_monthly == pytest.approx(0.017 * 3 * 730.0, rel=1e-6)
    assert result.incurred_so_far == pytest.approx(0.017 * 3 * 3.0, abs=0.01)


@patch("app.services.cost_service.get_pricing_client")
@patch("app.services.cost_service.elasticache_service.get_cluster")
def test_elasticache_not_found_raises(
    mock_get_cluster: MagicMock, mock_get_pricing: MagicMock
) -> None:
    mock_get_cluster.return_value = None
    with pytest.raises(ValueError, match="not found"):
        cost_service.estimate_cost("elasticache", "missing-cache")


# --- SageMaker -------------------------------------------------------------


def _sagemaker_endpoint(
    creation_time: datetime | None,
    instance_type: str | None = "ml.m5.large",
    instance_count: int = 1,
) -> SageMakerEndpoint:
    return SageMakerEndpoint(
        endpoint_name="ep-1", status="InService", creation_time=creation_time,
        variant_name="AllTraffic", instance_type=instance_type, instance_count=instance_count,
    )


@patch("app.services.cost_service.datetime")
@patch("app.services.cost_service.get_pricing_client")
@patch("app.services.cost_service.sagemaker_service.get_endpoint")
def test_sagemaker_projected_vs_incurred_are_distinct(
    mock_get_ep: MagicMock, mock_get_pricing: MagicMock, mock_datetime: MagicMock
) -> None:
    """Roadmap: SageMaker endpoints run 24/7 by default and are 'often the
    biggest silent cost' -- a 2-hour-old endpoint must still show a full
    projected_monthly figure, not just its tiny incurred_so_far."""
    mock_datetime.now.return_value = NOW
    mock_get_ep.return_value = _sagemaker_endpoint(NOW - timedelta(hours=2))
    mock_client = MagicMock()
    mock_client.get_products.return_value = _pricing_response("0.1150000000")
    mock_get_pricing.return_value = mock_client

    result = cost_service.estimate_cost("sagemaker", "ep-1")

    assert result.hourly_rate == pytest.approx(0.115, rel=1e-6)
    assert result.projected_monthly == pytest.approx(0.115 * 730.0, rel=1e-6)
    assert result.incurred_so_far == pytest.approx(0.115 * 2.0, abs=0.01)
    assert result.incurred_so_far < result.projected_monthly


@patch("app.services.cost_service.sagemaker_service.get_endpoint")
def test_sagemaker_unresolvable_instance_type_raises(mock_get_ep: MagicMock) -> None:
    mock_get_ep.return_value = _sagemaker_endpoint(NOW, instance_type=None)
    with pytest.raises(ValueError, match="instance_type"):
        cost_service.estimate_cost("sagemaker", "ep-1")


@patch("app.services.cost_service.sagemaker_service.get_endpoint")
def test_sagemaker_not_found_raises(mock_get_ep: MagicMock) -> None:
    mock_get_ep.return_value = None
    with pytest.raises(ValueError, match="not found"):
        cost_service.estimate_cost("sagemaker", "missing-ep")


# --- Redshift --------------------------------------------------------------


def _redshift_cluster(create_time: datetime | None, number_of_nodes: int = 1) -> RedshiftCluster:
    return RedshiftCluster(
        cluster_identifier="cl-1", node_type="dc2.large", status="available",
        number_of_nodes=number_of_nodes, create_time=create_time,
    )


@patch("app.services.cost_service.datetime")
@patch("app.services.cost_service.get_pricing_client")
@patch("app.services.cost_service.redshift_service.get_cluster")
def test_redshift_multi_node_hourly_rate_is_per_node(
    mock_get_cluster: MagicMock, mock_get_pricing: MagicMock, mock_datetime: MagicMock
) -> None:
    mock_datetime.now.return_value = NOW
    mock_get_cluster.return_value = _redshift_cluster(NOW - timedelta(hours=5), number_of_nodes=2)
    mock_client = MagicMock()
    mock_client.get_products.return_value = _pricing_response("0.2500000000")
    mock_get_pricing.return_value = mock_client

    result = cost_service.estimate_cost("redshift", "cl-1")

    assert result.hourly_rate == pytest.approx(0.25 * 2, rel=1e-6)
    assert result.projected_monthly == pytest.approx(0.25 * 2 * 730.0, rel=1e-6)
    assert result.incurred_so_far == pytest.approx(0.25 * 2 * 5.0, abs=0.01)


@patch("app.services.cost_service.get_pricing_client")
@patch("app.services.cost_service.redshift_service.get_cluster")
def test_redshift_not_found_raises(
    mock_get_cluster: MagicMock, mock_get_pricing: MagicMock
) -> None:
    mock_get_cluster.return_value = None
    with pytest.raises(ValueError, match="not found"):
        cost_service.estimate_cost("redshift", "missing-cl")


# --- API Gateway (usage-based) ----------------------------------------


def _rest_api(created_date: datetime | None) -> ApiGatewayRestApi:
    return ApiGatewayRestApi(api_id="abc123", name="my-api", created_date=created_date)


@patch("app.services.cost_service.datetime")
@patch("app.services.cost_service.cloudwatch_service.get_daily_datapoints")
@patch("app.services.cost_service.api_gateway_service.get_api")
def test_api_gateway_usage_based_projected_vs_incurred(
    mock_get_api: MagicMock, mock_daily: MagicMock, mock_datetime: MagicMock
) -> None:
    mock_datetime.now.return_value = NOW
    mock_get_api.return_value = _rest_api(NOW - timedelta(days=3))
    mock_daily.return_value = _metric_datapoints({0: 2_000_000, 1: 2_000_000})

    result = cost_service.estimate_cost("api_gateway", "abc123")

    assert result.hourly_rate is None
    assert result.incurred_so_far == pytest.approx(
        4_000_000 * cost_service.API_GATEWAY_PRICE_PER_REQUEST_USD, rel=1e-6
    )
    assert result.projected_monthly > result.incurred_so_far


@patch("app.services.cost_service.api_gateway_service.get_api")
def test_api_gateway_not_found_raises(mock_get_api: MagicMock) -> None:
    mock_get_api.return_value = None
    with pytest.raises(ValueError, match="not found"):
        cost_service.estimate_cost("api_gateway", "missing-api")


# --- CloudFront (usage-based, us-east-1-pinned metrics) ------------------


def _distribution() -> CloudFrontDistribution:
    return CloudFrontDistribution(distribution_id="E123", status="Deployed", enabled=True)


@patch("app.services.cost_service.datetime")
@patch("app.services.cost_service.cloudwatch_service.get_daily_datapoints")
@patch("app.services.cost_service.cloudfront_service.get_distribution")
def test_cloudfront_usage_based_and_region_pinned(
    mock_get_dist: MagicMock, mock_daily: MagicMock, mock_datetime: MagicMock
) -> None:
    mock_datetime.now.return_value = NOW
    mock_get_dist.return_value = _distribution()
    mock_daily.return_value = _metric_datapoints({0: 500_000})

    result = cost_service.estimate_cost("cloudfront", "E123")

    assert result.hourly_rate is None
    # abs, not rel, tolerance: incurred_so_far is rounded to cents
    # server-side and the raw expected value isn't a clean 2-decimal
    # number (50 x $0.0075 = $0.375) -- same rationale as the EBS
    # GB-month test.
    assert result.incurred_so_far == pytest.approx(
        (500_000 / 10_000.0) * cost_service.CLOUDFRONT_PRICE_PER_10K_REQUESTS_USD, abs=0.01
    )
    _, kwargs = mock_daily.call_args
    assert kwargs["region"] == "us-east-1"


@patch("app.services.cost_service.cloudfront_service.get_distribution")
def test_cloudfront_not_found_raises(mock_get_dist: MagicMock) -> None:
    mock_get_dist.return_value = None
    with pytest.raises(ValueError, match="not found"):
        cost_service.estimate_cost("cloudfront", "missing-dist")


# --- OpenSearch --------------------------------------------------------


def _opensearch_domain(
    instance_type: str | None = "r6g.large.search", instance_count: int = 1
) -> OpenSearchDomain:
    return OpenSearchDomain(
        domain_name="my-domain",
        arn="arn:aws:es:us-east-1:123456789012:domain/my-domain",
        instance_type=instance_type,
        instance_count=instance_count,
    )


@patch("app.services.cost_service.datetime")
@patch("app.services.cost_service.get_pricing_client")
@patch("app.services.cost_service.opensearch_service.get_domain")
def test_opensearch_multi_node_hourly_rate_is_per_node(
    mock_get_domain: MagicMock, mock_get_pricing: MagicMock, mock_datetime: MagicMock
) -> None:
    mock_datetime.now.return_value = NOW
    mock_get_domain.return_value = _opensearch_domain(instance_count=3)
    mock_client = MagicMock()
    mock_client.get_products.return_value = _pricing_response("0.3350000000")
    mock_get_pricing.return_value = mock_client

    result = cost_service.estimate_cost("opensearch", "my-domain")

    assert result.hourly_rate == pytest.approx(0.335 * 3, rel=1e-6)
    assert result.projected_monthly == pytest.approx(0.335 * 3 * 730.0, rel=1e-6)
    # No creation timestamp exists for OpenSearch -- default date_range is
    # [now, now), same documented-gap shape as EIP.
    assert result.incurred_so_far == 0.0


@patch("app.services.cost_service.opensearch_service.get_domain")
def test_opensearch_unresolvable_instance_type_raises(mock_get_domain: MagicMock) -> None:
    mock_get_domain.return_value = _opensearch_domain(instance_type=None)
    with pytest.raises(ValueError, match="instance_type"):
        cost_service.estimate_cost("opensearch", "my-domain")


@patch("app.services.cost_service.opensearch_service.get_domain")
def test_opensearch_not_found_raises(mock_get_domain: MagicMock) -> None:
    mock_get_domain.return_value = None
    with pytest.raises(ValueError, match="not found"):
        cost_service.estimate_cost("opensearch", "missing-domain")


# --- Kinesis -------------------------------------------------------------


def _kinesis_stream(
    creation_timestamp: datetime | None, open_shard_count: int = 2
) -> KinesisStream:
    return KinesisStream(
        stream_name="stream-1", status="ACTIVE", open_shard_count=open_shard_count,
        creation_timestamp=creation_timestamp,
    )


@patch("app.services.cost_service.datetime")
@patch("app.services.cost_service.kinesis_service.get_stream")
def test_kinesis_shard_hour_rate_scales_with_shard_count(
    mock_get_stream: MagicMock, mock_datetime: MagicMock
) -> None:
    mock_datetime.now.return_value = NOW
    mock_get_stream.return_value = _kinesis_stream(NOW - timedelta(hours=6), open_shard_count=4)

    result = cost_service.estimate_cost("kinesis", "stream-1")

    expected_hourly = cost_service.KINESIS_SHARD_HOURLY_RATE_USD * 4
    assert result.hourly_rate == pytest.approx(expected_hourly, rel=1e-9)
    assert result.projected_monthly == pytest.approx(expected_hourly * 730.0, rel=1e-6)
    assert result.incurred_so_far == pytest.approx(expected_hourly * 6.0, abs=0.01)


@patch("app.services.cost_service.kinesis_service.get_stream")
def test_kinesis_not_found_raises(mock_get_stream: MagicMock) -> None:
    mock_get_stream.return_value = None
    with pytest.raises(ValueError, match="not found"):
        cost_service.estimate_cost("kinesis", "missing-stream")


# =====================================================================
# estimate_instance_cost -- roadmap 3.8's "what would a big EC2 cost"
# chat tool. Independent of any real resource in the account, so it only
# needs the Pricing API mocked, no get_instance() lookup at all.
# =====================================================================


@patch("app.services.cost_service.get_pricing_client")
def test_estimate_instance_cost_returns_hourly_and_monthly_rate(
    mock_get_pricing: MagicMock,
) -> None:
    mock_client = MagicMock()
    mock_client.get_products.return_value = _pricing_response("1.5000000000")
    mock_get_pricing.return_value = mock_client

    result = cost_service.estimate_instance_cost("m5.24xlarge", "us-east-1")

    assert result.instance_type == "m5.24xlarge"
    assert result.region == "us-east-1"
    assert result.method == "list_price"
    assert result.hourly_rate == 1.5
    assert result.monthly_rate == pytest.approx(1.5 * 730.0, rel=1e-6)


@patch("app.services.cost_service.get_pricing_client")
def test_estimate_instance_cost_no_match_raises_clear_error(
    mock_get_pricing: MagicMock,
) -> None:
    mock_client = MagicMock()
    mock_client.get_products.return_value = {"PriceList": []}
    mock_get_pricing.return_value = mock_client

    with pytest.raises(ValueError, match="no on-demand price"):
        cost_service.estimate_instance_cost("made.up-type", "us-east-1")


@patch("app.services.cost_service.get_pricing_client")
def test_estimate_instance_cost_is_region_parameterized(
    mock_get_pricing: MagicMock,
) -> None:
    """Same instance_type in two different regions must query the Pricing
    API with each region's own regionCode filter -- not a hardcoded
    default region."""
    mock_client = MagicMock()
    mock_client.get_products.return_value = _pricing_response("0.0400000000")
    mock_get_pricing.return_value = mock_client

    result = cost_service.estimate_instance_cost("t3.micro", "eu-west-1")

    assert result.region == "eu-west-1"
    call_kwargs = mock_client.get_products.call_args.kwargs
    filters = {f["Field"]: f["Value"] for f in call_kwargs["Filters"]}
    assert filters["regionCode"] == "eu-west-1"
