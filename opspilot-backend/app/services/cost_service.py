"""Cost-calculation business logic (roadmap Section 3.2).

estimate_cost(resource_type, resource_id, date_range) is parameterized by
resource_type up front, so extending across the 15 types (Section 2a) is
additive, not a rewrite. Implemented: ec2, ebs, rds, eip, elb (Step 3
batch A) plus lambda, nat_gateway, dynamodb, elasticache, sagemaker,
redshift, api_gateway, cloudfront, opensearch, kinesis (Step 3 batch B)
-- all 15 roadmap resource types.

Two methods exist per the roadmap:
- "list_price" (implemented here): AWS Pricing API on-demand rate x hours
  for instance-hour-priced types, or a documented flat per-unit constant x
  observed/extrapolated usage for usage-priced types (see "Usage-based
  types" below). Free, no cost-allocation tagging required, ignores
  reserved/savings pricing -- "good enough for demo" per roadmap 3.2.
- "billed" (stubbed): Cost Explorer's actual billed cost via cost
  allocation tags. Deliberately not built in this step -- see
  estimate_cost's NotImplementedError below.

Unifying trick used for the non-hourly-priced-at-the-source types (EBS is
priced per GB-month, EIP/NAT Gateway/Kinesis are flat per-hour surcharges
computed from a documented constant rather than a Pricing API call, and
DynamoDB-provisioned is priced per RCU/WCU-hour): every *instance/capacity-
hour* type still reports an `hourly_rate` in CostEstimate by computing an
*effective* hourly rate, so the same elapsed-hours-based incurred_so_far
logic (`_elapsed_hours`) works unchanged across all of them without a
parallel cost model per type.

Usage-based types (Lambda, DynamoDB on-demand, API Gateway, CloudFront) do
NOT have a meaningful hourly rate at all -- there is no "server" running
by the hour, cost is purely a function of request/consumed-capacity
volume. For these, `hourly_rate` is left `None` (CostEstimate's field is
Optional specifically for this reason -- see its docstring), and both
`incurred_so_far`/`projected_monthly` are computed directly from CloudWatch-
observed usage (`_sum_metric_over_window`) x a documented flat per-unit USD
constant, with `projected_monthly` extrapolating the observed daily usage
rate out to a 30-day month.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Literal

from app.aws.client import get_pricing_client
from app.core.config import get_settings
from app.models.cost import CostEstimate, DateRange, InstanceCostEstimate
from app.models.dashboard import DynamoTableSummary
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

HOURS_PER_MONTH = 730.0  # AWS's own standard approximation for "a month"

# Demo-scope assumption: instances are priced as on-demand Shared-tenancy
# Linux with no pre-installed software, since the app doesn't currently
# track AMI/OS or tenancy per instance. Revisit if Windows/BYOL/dedicated
# instances need accurate pricing.
_PRICING_OPERATING_SYSTEM = "Linux"
_PRICING_TENANCY = "Shared"
_PRICING_PREINSTALLED_SW = "NA"
_PRICING_CAPACITY_STATUS = "Used"

# Demo-scope assumption: RDS pricing is looked up as Single-AZ (ignores the
# ~2x Multi-AZ multiplier, since the app doesn't currently track
# MultiAZ per instance -- same "good enough for demo" spirit as EC2's
# Linux/Shared-tenancy note above).
_RDS_PRICING_DEPLOYMENT_OPTION = "Single-AZ"

# RDS `Engine` (DescribeDBInstances) -> Pricing API `databaseEngine` value.
# Only covers the common open-source-licensed engines with a clean
# "No license required" licenseModel; Oracle/SQL Server commercial
# licensing tiers are a documented gap (not queried -- see
# _get_rds_hourly_rate below) rather than a silently wrong price.
_RDS_ENGINE_TO_PRICING_DATABASE_ENGINE = {
    "mysql": "MySQL",
    "postgres": "PostgreSQL",
    "mariadb": "MariaDB",
    "aurora-mysql": "Aurora MySQL",
    "aurora-postgresql": "Aurora PostgreSQL",
}

# AWS has charged for unattached ("idle") Elastic IPs since Feb 2024. This
# is a documented constant rather than a Pricing API lookup -- roadmap
# instructions explicitly allow this ("can be a documented constant if the
# exact current rate isn't cleanly queryable ... rather than
# over-engineering the lookup"). Verify against the AWS EC2 pricing page
# for the account's region before relying on this for a real bill;
# $0.005/hour is the commonly published us-east-1 rate as of this build.
EIP_IDLE_HOURLY_RATE_USD = 0.005

# =====================================================================
# Step 3 batch B documented flat-constant pricing (Lambda, DynamoDB,
# NAT Gateway, API Gateway, CloudFront, Kinesis). Same "documented
# constant, not a live Pricing API lookup" precedent as
# EIP_IDLE_HOURLY_RATE_USD above -- these are all either usage-priced
# (no fixed instance-hour rate exists to look up at all) or a Pricing API
# GetProducts filter chain that would be disproportionately complex for a
# demo-scope estimate. Verify against AWS's current pricing pages for the
# account's region before relying on these for a real bill.
# =====================================================================

# Lambda (roadmap: "near-zero cost regardless ... a simple
# request+duration based estimate is fine ... your call, document it").
# us-east-1 on-demand rates as of this build.
LAMBDA_PRICE_PER_REQUEST_USD = 0.0000002  # $0.20 per 1M requests
LAMBDA_PRICE_PER_GB_SECOND_USD = 0.0000166667  # $16.6667 per 1M GB-seconds
# No per-invocation Duration metric is queried (a second CloudWatch metric
# this app doesn't otherwise pull) -- an assumed average duration stands
# in instead. Revisit with real AWS/Lambda "Duration" data if Lambda cost
# accuracy becomes load-bearing; roadmap explicitly allows a placeholder
# here since Lambda cost is "near-zero regardless."
LAMBDA_ASSUMED_AVG_DURATION_SECONDS = 0.1

# DynamoDB PROVISIONED throughput, documented flat per-unit-hour
# constants (us-east-1).
DYNAMODB_RCU_HOURLY_RATE_USD = 0.00013
DYNAMODB_WCU_HOURLY_RATE_USD = 0.00065

# DynamoDB PAY_PER_REQUEST (on-demand), documented flat per-request-unit
# constants (us-east-1). Consumed*CapacityUnits (summed over the window)
# is used directly as the request-unit count -- this is the same metric
# AWS itself derives on-demand billing from.
DYNAMODB_ON_DEMAND_RRU_PRICE_USD = 0.25 / 1_000_000  # $0.25 per million RRU
DYNAMODB_ON_DEMAND_WRU_PRICE_USD = 1.25 / 1_000_000  # $1.25 per million WRU

# NAT Gateway data-processing charge (per GB through the gateway) is
# ignored -- only the fixed per-hour charge is priced (via Pricing API,
# see _get_nat_gateway_hourly_rate), same "base rate is the dominant cost
# for a demo" precedent as ELB's LCU-ignoring note above.

# API Gateway REST APIs, first pricing tier only (ignores volume
# discounts above 333M requests/month and data transfer cost).
API_GATEWAY_PRICE_PER_REQUEST_USD = 3.50 / 1_000_000  # $3.50 per million requests

# CloudFront -- request cost only. Data transfer (GB out) is NOT priced
# here at all, despite normally being the dominant driver of a real
# CloudFront bill -- BytesDownloaded is a real CloudWatch metric this app
# could pull, but per-GB pricing is tiered by both volume *and* the
# viewer's geographic region (which this app doesn't track per
# distribution), making an honest per-GB estimate meaningfully more work
# than the other usage-based types here. Documented, known undercount --
# see _estimate_cost_cloudfront's docstring.
CLOUDFRONT_PRICE_PER_10K_REQUESTS_USD = 0.0075

# Kinesis Data Streams, provisioned (non on-demand) mode -- shard-hour
# base charge only. PUT payload unit charges (usage-based, scales with
# record volume/size) are ignored, same "dominant fixed cost, not
# perfect accuracy" spirit as NAT Gateway/ELB above.
KINESIS_SHARD_HOURLY_RATE_USD = 0.015


class UnsupportedResourceTypeError(ValueError):
    """Raised when estimate_cost is asked about a type not yet built (Step 3)."""


def _get_ec2_hourly_rate(instance_type: str, region: str) -> float:
    """On-demand USD/hour rate for an EC2 instance type in a region, via
    the AWS Pricing API's GetProducts. Filters on `regionCode` (the AWS
    region code, e.g. "us-east-1") directly rather than the Pricing API's
    old human-readable `location` name (e.g. "US East (N. Virginia)") --
    regionCode has been a valid TERM_MATCH filter for years and avoids
    needing a brittle region-code -> region-name mapping table.
    """
    client = get_pricing_client()
    response = client.get_products(
        ServiceCode="AmazonEC2",
        Filters=[
            {"Type": "TERM_MATCH", "Field": "instanceType", "Value": instance_type},
            {"Type": "TERM_MATCH", "Field": "regionCode", "Value": region},
            {"Type": "TERM_MATCH", "Field": "operatingSystem", "Value": _PRICING_OPERATING_SYSTEM},
            {"Type": "TERM_MATCH", "Field": "tenancy", "Value": _PRICING_TENANCY},
            {"Type": "TERM_MATCH", "Field": "preInstalledSw", "Value": _PRICING_PREINSTALLED_SW},
            {"Type": "TERM_MATCH", "Field": "capacitystatus", "Value": _PRICING_CAPACITY_STATUS},
        ],
        MaxResults=1,
    )

    price_list = response.get("PriceList", [])
    if not price_list:
        raise ValueError(
            f"Pricing API returned no on-demand price for instance_type={instance_type!r} "
            f"region={region!r}"
        )
    return _extract_usd_price(price_list)


def _extract_usd_price(price_list: list[str]) -> float:
    """Shared PriceList[0] -> USD-per-unit parsing, reused by every
    GetProducts caller below (EC2, RDS, EBS, ELB) so the on-demand-terms
    JSON navigation lives in exactly one place.
    """
    product = json.loads(price_list[0])
    on_demand_terms = product["terms"]["OnDemand"]
    term = next(iter(on_demand_terms.values()))
    price_dimension = next(iter(term["priceDimensions"].values()))
    return float(price_dimension["pricePerUnit"]["USD"])


def estimate_instance_cost(instance_type: str, region: str) -> InstanceCostEstimate:
    """Hypothetical EC2 on-demand cost, independent of any real resource
    in the account (roadmap 3.8 -- "how much would a big EC2 machine
    cost" style questions). Reuses _get_ec2_hourly_rate's exact Pricing
    API GetProducts call/filter chain unchanged -- the only difference
    from _estimate_cost_ec2 is that there is no real resource_id to
    resolve instance_type/region from first, they're given directly by
    the caller.
    """
    hourly_rate = _get_ec2_hourly_rate(instance_type, region)
    return InstanceCostEstimate(
        instance_type=instance_type,
        region=region,
        method="list_price",
        hourly_rate=hourly_rate,
        monthly_rate=round(hourly_rate * HOURS_PER_MONTH, 2),
    )


def _get_rds_hourly_rate(instance_class: str, engine: str, region: str) -> float:
    """On-demand USD/hour rate for an RDS instance class, via the Pricing
    API -- same pattern as _get_ec2_hourly_rate. Ignores storage/IOPS cost
    (roadmap instructions: "instance-hour cost is the dominant number for
    a demo").
    """
    pricing_engine = _RDS_ENGINE_TO_PRICING_DATABASE_ENGINE.get(engine)
    if pricing_engine is None:
        raise ValueError(
            f"RDS engine={engine!r} has no Pricing API databaseEngine mapping -- "
            "only open-source-licensed engines (mysql, postgres, mariadb, "
            "aurora-mysql, aurora-postgresql) are supported so far. "
            "Oracle/SQL Server commercial licensing tiers are a documented gap."
        )

    client = get_pricing_client()
    response = client.get_products(
        ServiceCode="AmazonRDS",
        Filters=[
            {"Type": "TERM_MATCH", "Field": "instanceType", "Value": instance_class},
            {"Type": "TERM_MATCH", "Field": "regionCode", "Value": region},
            {"Type": "TERM_MATCH", "Field": "databaseEngine", "Value": pricing_engine},
            {
                "Type": "TERM_MATCH",
                "Field": "deploymentOption",
                "Value": _RDS_PRICING_DEPLOYMENT_OPTION,
            },
            {"Type": "TERM_MATCH", "Field": "licenseModel", "Value": "No license required"},
        ],
        MaxResults=1,
    )

    price_list = response.get("PriceList", [])
    if not price_list:
        raise ValueError(
            f"Pricing API returned no on-demand price for instance_class={instance_class!r} "
            f"engine={engine!r} region={region!r}"
        )
    return _extract_usd_price(price_list)


def _get_ebs_gb_month_rate(volume_type: str, region: str) -> float:
    """USD per GB-month for an EBS volume type, via the Pricing API.
    Unlike EC2/RDS this is not an hourly rate at the source -- storage is
    billed per GB-month, per roadmap instructions ("storage cost is
    GB-month based ... not hourly").
    """
    client = get_pricing_client()
    response = client.get_products(
        ServiceCode="AmazonEC2",
        Filters=[
            {"Type": "TERM_MATCH", "Field": "volumeApiName", "Value": volume_type},
            {"Type": "TERM_MATCH", "Field": "regionCode", "Value": region},
            {"Type": "TERM_MATCH", "Field": "productFamily", "Value": "Storage"},
        ],
        MaxResults=1,
    )

    price_list = response.get("PriceList", [])
    if not price_list:
        raise ValueError(
            f"Pricing API returned no GB-month price for volume_type={volume_type!r} "
            f"region={region!r}"
        )
    return _extract_usd_price(price_list)


# elbv2 lb_type -> Pricing API productFamily. Classic ELB's productFamily
# is simply "Load Balancer" (no suffix), predating the ALB/NLB split.
_ELB_TYPE_TO_PRICING_PRODUCT_FAMILY = {
    "application": "Load Balancer-Application",
    "network": "Load Balancer-Network",
    "classic": "Load Balancer",
}


def _get_elb_hourly_rate(lb_type: str, region: str) -> float:
    """On-demand USD/hour base rate for a load balancer, via the Pricing
    API. Ignores LCU/NLCU usage-based pricing (roadmap instructions:
    "per-hour base rate is the dominant cost ... for this demo-level
    estimate") -- the base hourly charge only, not data-processing/LCU
    consumption.
    """
    product_family = _ELB_TYPE_TO_PRICING_PRODUCT_FAMILY[lb_type]
    client = get_pricing_client()
    response = client.get_products(
        ServiceCode="AWSELB",
        Filters=[
            {"Type": "TERM_MATCH", "Field": "regionCode", "Value": region},
            {"Type": "TERM_MATCH", "Field": "productFamily", "Value": product_family},
        ],
        MaxResults=1,
    )

    price_list = response.get("PriceList", [])
    if not price_list:
        raise ValueError(
            f"Pricing API returned no on-demand price for lb_type={lb_type!r} region={region!r}"
        )
    return _extract_usd_price(price_list)


def _get_nat_gateway_hourly_rate(region: str) -> float:
    """On-demand USD/hour base rate for a NAT Gateway, via the Pricing
    API. Ignores per-GB data-processing charges -- see the "NAT Gateway
    data-processing charge" note above KINESIS_SHARD_HOURLY_RATE_USD.
    """
    client = get_pricing_client()
    response = client.get_products(
        ServiceCode="AmazonEC2",
        Filters=[
            {"Type": "TERM_MATCH", "Field": "productFamily", "Value": "NAT Gateway"},
            {"Type": "TERM_MATCH", "Field": "regionCode", "Value": region},
        ],
        MaxResults=1,
    )
    price_list = response.get("PriceList", [])
    if not price_list:
        raise ValueError(
            f"Pricing API returned no on-demand price for NAT Gateway region={region!r}"
        )
    return _extract_usd_price(price_list)


def _get_elasticache_hourly_rate(node_type: str, engine: str, region: str) -> float:
    """On-demand USD/hour rate for an ElastiCache node, via the Pricing
    API. `engine` (elasticache_service's raw lowercase 'redis'/
    'memcached') is capitalized to match the Pricing API's `cacheEngine`
    attribute convention (e.g. "Redis") -- not live-verified against the
    account, same caveat as every Pricing API filter chain in this batch.
    """
    client = get_pricing_client()
    response = client.get_products(
        ServiceCode="AmazonElastiCache",
        Filters=[
            {"Type": "TERM_MATCH", "Field": "instanceType", "Value": node_type},
            {"Type": "TERM_MATCH", "Field": "regionCode", "Value": region},
            {"Type": "TERM_MATCH", "Field": "cacheEngine", "Value": engine.capitalize()},
        ],
        MaxResults=1,
    )
    price_list = response.get("PriceList", [])
    if not price_list:
        raise ValueError(
            f"Pricing API returned no on-demand price for node_type={node_type!r} "
            f"engine={engine!r} region={region!r}"
        )
    return _extract_usd_price(price_list)


def _get_sagemaker_hourly_rate(instance_type: str, region: str) -> float:
    """On-demand USD/hour rate for a SageMaker hosting instance, via the
    Pricing API. Prices the endpoint's hosting instance only -- ignores
    data processing/storage, same "instance-hour is the dominant cost"
    spirit as every other Pricing-API-priced type in this app.
    """
    client = get_pricing_client()
    response = client.get_products(
        ServiceCode="AmazonSageMaker",
        Filters=[
            {"Type": "TERM_MATCH", "Field": "instanceType", "Value": instance_type},
            {"Type": "TERM_MATCH", "Field": "regionCode", "Value": region},
        ],
        MaxResults=1,
    )
    price_list = response.get("PriceList", [])
    if not price_list:
        raise ValueError(
            f"Pricing API returned no on-demand price for instance_type={instance_type!r} "
            f"region={region!r}"
        )
    return _extract_usd_price(price_list)


def _get_redshift_hourly_rate(node_type: str, region: str) -> float:
    """On-demand USD/hour rate for a Redshift node, via the Pricing API."""
    client = get_pricing_client()
    response = client.get_products(
        ServiceCode="AmazonRedshift",
        Filters=[
            {"Type": "TERM_MATCH", "Field": "instanceType", "Value": node_type},
            {"Type": "TERM_MATCH", "Field": "regionCode", "Value": region},
        ],
        MaxResults=1,
    )
    price_list = response.get("PriceList", [])
    if not price_list:
        raise ValueError(
            f"Pricing API returned no on-demand price for node_type={node_type!r} "
            f"region={region!r}"
        )
    return _extract_usd_price(price_list)


def _get_opensearch_hourly_rate(instance_type: str, region: str) -> float:
    """On-demand USD/hour rate for an OpenSearch data node, via the
    Pricing API. ServiceCode "AmazonES" is used deliberately -- AWS kept
    this Pricing API service code even after renaming Elasticsearch
    Service to OpenSearch Service (same reason opensearch_service.py's
    CloudWatch namespace is still "AWS/ES", see idle_service.py).
    """
    client = get_pricing_client()
    response = client.get_products(
        ServiceCode="AmazonES",
        Filters=[
            {"Type": "TERM_MATCH", "Field": "instanceType", "Value": instance_type},
            {"Type": "TERM_MATCH", "Field": "regionCode", "Value": region},
        ],
        MaxResults=1,
    )
    price_list = response.get("PriceList", [])
    if not price_list:
        raise ValueError(
            f"Pricing API returned no on-demand price for instance_type={instance_type!r} "
            f"region={region!r}"
        )
    return _extract_usd_price(price_list)


def _elapsed_hours(launch_time: datetime | None, date_range: DateRange) -> float:
    """Hours actually elapsed within date_range, capped at the resource's
    own age (launch_time) if it's younger than date_range.start, and
    capped at "now" if date_range.end is in the future.
    """
    effective_start = date_range.start
    if launch_time is not None and launch_time > effective_start:
        effective_start = launch_time

    now = datetime.now(timezone.utc)
    effective_end = min(date_range.end, now)

    if effective_end <= effective_start:
        return 0.0
    return (effective_end - effective_start).total_seconds() / 3600.0


def _days_in_range(date_range: DateRange) -> int:
    """Whole-day count spanning date_range.start through now, used only to
    bound the `days`-back-from-now window get_daily_datapoints accepts
    (it has no arbitrary-start/end shape) -- _sum_metric_over_window below
    then filters the returned datapoints back down to date_range's actual
    bounds. Minimum 1 (get_daily_datapoints requires a positive window).
    """
    now = datetime.now(timezone.utc)
    span = now - date_range.start
    return max(1, int(span.total_seconds() // 86400) + 1)


def _sum_metric_over_window(
    namespace: str,
    metric_name: str,
    dimension_name: str,
    dimension_value: str,
    date_range: DateRange,
    extra_dimensions: list[tuple[str, str]] | None = None,
    region: str | None = None,
) -> float:
    """Sums a Sum-statistic CloudWatch metric's daily datapoints that fall
    within date_range -- the shared building block for every usage-based
    type's cost estimate (Lambda invocations, DynamoDB on-demand consumed
    capacity, API Gateway/CloudFront request counts). Reuses
    cloudwatch_service.get_daily_datapoints exactly like idle_service does
    (same one CloudWatch call this app makes for daily-resolution metrics
    everywhere), rather than adding a second, parallel CloudWatch-calling
    path just for cost.
    """
    now = datetime.now(timezone.utc)
    points = cloudwatch_service.get_daily_datapoints(
        namespace=namespace,
        metric_name=metric_name,
        dimension_name=dimension_name,
        dimension_value=dimension_value,
        days=_days_in_range(date_range),
        statistic="Sum",
        unit=None,
        extra_dimensions=extra_dimensions,
        region=region,
    )
    effective_end = min(date_range.end, now)
    total = 0.0
    for point in points:
        timestamp = point.timestamp
        if timestamp.tzinfo is None:
            timestamp = timestamp.replace(tzinfo=timezone.utc)
        if date_range.start <= timestamp <= effective_end and point.average is not None:
            total += point.average
    return total


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


def estimate_cost(
    resource_type: str,
    resource_id: str,
    date_range: DateRange | None = None,
    method: Literal["list_price", "billed"] = "list_price",
    region: str | None = None,
) -> CostEstimate:
    """Estimate cost for a resource.

    `region` overrides the configured default region for both the
    resource lookup and the Pricing API/CloudWatch calls used to price it
    -- needed for region-wide scanning (roadmap 3.3), where a caller
    (scan_service) asks about a resource in a region other than the
    process-wide default. None/omitted falls back to
    `get_settings().aws_region`, unchanged for every existing
    single-region caller.

    `projected_monthly` is always hourly_rate x a full month (~730 hours)
    regardless of date_range -- it drives star/bubble sizing (roadmap
    3.1a) and must never be conflated with `incurred_so_far`. For EBS this
    is naturally date_range-independent already (size x GB-month rate is a
    full month of the volume's current size by definition), which
    conveniently matches this semantic without extra work.

    `incurred_so_far` is hourly_rate x hours actually elapsed within
    date_range, capped at the resource's own age if younger.

    date_range defaults to [creation_time, now) when omitted, i.e. "cost
    incurred since this resource was created" -- except EIP, which has no
    creation timestamp available at all (see _estimate_cost_eip below).
    """
    if resource_type not in _SUPPORTED_RESOURCE_TYPES:
        raise UnsupportedResourceTypeError(
            f"estimate_cost for resource_type={resource_type!r} is not supported -- "
            f"only {sorted(_SUPPORTED_RESOURCE_TYPES)!r} (the roadmap's 15 in-scope "
            "types) are implemented."
        )

    if method == "billed":
        # TODO(Step 4+): Cost Explorer get_cost_and_usage, filtered by
        # resource ID via cost allocation tags. List price is "good enough
        # for demo" per roadmap 3.2 -- not over-investing in this yet.
        raise NotImplementedError(
            "estimate_cost method='billed' (Cost Explorer) is not implemented yet; "
            "use the default method='list_price'."
        )

    if resource_type == "ec2":
        return _estimate_cost_ec2(resource_id, date_range, region)
    if resource_type == "ebs":
        return _estimate_cost_ebs(resource_id, date_range, region)
    if resource_type == "rds":
        return _estimate_cost_rds(resource_id, date_range, region)
    if resource_type == "eip":
        return _estimate_cost_eip(resource_id, date_range, region)
    if resource_type == "elb":
        return _estimate_cost_elb(resource_id, date_range, region)
    if resource_type == "lambda":
        return _estimate_cost_lambda(resource_id, date_range, region)
    if resource_type == "nat_gateway":
        return _estimate_cost_nat_gateway(resource_id, date_range, region)
    if resource_type == "dynamodb":
        return _estimate_cost_dynamodb(resource_id, date_range, region)
    if resource_type == "elasticache":
        return _estimate_cost_elasticache(resource_id, date_range, region)
    if resource_type == "sagemaker":
        return _estimate_cost_sagemaker(resource_id, date_range, region)
    if resource_type == "redshift":
        return _estimate_cost_redshift(resource_id, date_range, region)
    if resource_type == "api_gateway":
        return _estimate_cost_api_gateway(resource_id, date_range, region)
    if resource_type == "cloudfront":
        return _estimate_cost_cloudfront(resource_id, date_range, region)
    if resource_type == "opensearch":
        return _estimate_cost_opensearch(resource_id, date_range, region)
    return _estimate_cost_kinesis(resource_id, date_range, region)


def _estimate_cost_ec2(
    resource_id: str, date_range: DateRange | None, region: str | None = None
) -> CostEstimate:
    instance = ec2_service.get_instance(resource_id, region=region)
    if instance is None:
        raise ValueError(f"EC2 instance {resource_id!r} not found")

    now = datetime.now(timezone.utc)
    if date_range is None:
        date_range = DateRange(start=instance.launch_time or now, end=now)

    region = region or get_settings().aws_region
    hourly_rate = _get_ec2_hourly_rate(instance.instance_type, region)

    projected_monthly = round(hourly_rate * HOURS_PER_MONTH, 2)
    incurred_so_far = round(hourly_rate * _elapsed_hours(instance.launch_time, date_range), 2)

    return CostEstimate(
        resource_id=resource_id,
        resource_type="ec2",
        date_range=date_range,
        method="list_price",
        hourly_rate=hourly_rate,
        projected_monthly=projected_monthly,
        incurred_so_far=incurred_so_far,
    )


def _estimate_cost_ebs(
    resource_id: str, date_range: DateRange | None, region: str | None = None
) -> CostEstimate:
    volume = ebs_service.get_volume(resource_id, region=region)
    if volume is None:
        raise ValueError(f"EBS volume {resource_id!r} not found")

    now = datetime.now(timezone.utc)
    if date_range is None:
        date_range = DateRange(start=volume.create_time or now, end=now)

    region = region or get_settings().aws_region
    gb_month_rate = _get_ebs_gb_month_rate(volume.volume_type, region)
    monthly_cost = gb_month_rate * volume.size_gb
    # Effective hourly rate -- see module docstring's "unifying trick" note
    # for why a GB-month-priced resource still reports an hourly_rate.
    hourly_rate = monthly_cost / HOURS_PER_MONTH

    projected_monthly = round(monthly_cost, 2)
    incurred_so_far = round(hourly_rate * _elapsed_hours(volume.create_time, date_range), 2)

    return CostEstimate(
        resource_id=resource_id,
        resource_type="ebs",
        date_range=date_range,
        method="list_price",
        hourly_rate=hourly_rate,
        projected_monthly=projected_monthly,
        incurred_so_far=incurred_so_far,
    )


def _estimate_cost_rds(
    resource_id: str, date_range: DateRange | None, region: str | None = None
) -> CostEstimate:
    instance = rds_service.get_instance(resource_id, region=region)
    if instance is None:
        raise ValueError(f"RDS instance {resource_id!r} not found")

    now = datetime.now(timezone.utc)
    if date_range is None:
        date_range = DateRange(start=instance.instance_create_time or now, end=now)

    region = region or get_settings().aws_region
    hourly_rate = _get_rds_hourly_rate(instance.instance_class, instance.engine, region)

    projected_monthly = round(hourly_rate * HOURS_PER_MONTH, 2)
    incurred_so_far = round(
        hourly_rate * _elapsed_hours(instance.instance_create_time, date_range), 2
    )

    return CostEstimate(
        resource_id=resource_id,
        resource_type="rds",
        date_range=date_range,
        method="list_price",
        hourly_rate=hourly_rate,
        projected_monthly=projected_monthly,
        incurred_so_far=incurred_so_far,
    )


def _estimate_cost_eip(
    resource_id: str, date_range: DateRange | None, region: str | None = None
) -> CostEstimate:
    """Flat idle surcharge for unattached EIPs (EIP_IDLE_HOURLY_RATE_USD),
    0 for associated ones -- an EIP attached to a running instance is free
    (1 per instance), only unattached ones are billed.

    date_range default: unlike every other type here, AWS's
    DescribeAddresses response exposes no allocation timestamp at all, so
    there's no "since creation" to default to. Rather than fabricate a
    lookback window, this defaults to [now, now) -- incurred_so_far is 0
    unless the caller supplies an explicit date_range. Documented
    simplification, not an oversight.
    """
    address = eip_service.get_address(resource_id, region=region)
    if address is None:
        raise ValueError(f"Elastic IP {resource_id!r} not found")

    now = datetime.now(timezone.utc)
    if date_range is None:
        date_range = DateRange(start=now, end=now)

    hourly_rate = 0.0 if address.is_associated else EIP_IDLE_HOURLY_RATE_USD

    projected_monthly = round(hourly_rate * HOURS_PER_MONTH, 2)
    incurred_so_far = round(hourly_rate * _elapsed_hours(None, date_range), 2)

    return CostEstimate(
        resource_id=resource_id,
        resource_type="eip",
        date_range=date_range,
        method="list_price",
        hourly_rate=hourly_rate,
        projected_monthly=projected_monthly,
        incurred_so_far=incurred_so_far,
    )


def _estimate_cost_elb(
    resource_id: str, date_range: DateRange | None, region: str | None = None
) -> CostEstimate:
    lb = elb_service.get_load_balancer(resource_id, region=region)
    if lb is None:
        raise ValueError(f"Load balancer {resource_id!r} not found")

    now = datetime.now(timezone.utc)
    if date_range is None:
        date_range = DateRange(start=lb.created_time or now, end=now)

    region = region or get_settings().aws_region
    hourly_rate = _get_elb_hourly_rate(lb.lb_type, region)

    projected_monthly = round(hourly_rate * HOURS_PER_MONTH, 2)
    incurred_so_far = round(hourly_rate * _elapsed_hours(lb.created_time, date_range), 2)

    return CostEstimate(
        resource_id=resource_id,
        resource_type="elb",
        date_range=date_range,
        method="list_price",
        hourly_rate=hourly_rate,
        projected_monthly=projected_monthly,
        incurred_so_far=incurred_so_far,
    )


# =====================================================================
# Step 3 batch B -- Lambda, NAT Gateway, DynamoDB, ElastiCache, SageMaker,
# Redshift, API Gateway, CloudFront, OpenSearch, Kinesis.
# =====================================================================


def _estimate_cost_lambda(
    resource_id: str, date_range: DateRange | None, region: str | None = None
) -> CostEstimate:
    """Usage-based (see module docstring) -- request count x price-per-
    request, plus an assumed-duration GB-second estimate using the
    function's real MemorySize (128 MB default if unknown) x
    LAMBDA_ASSUMED_AVG_DURATION_SECONDS. No fixed hourly_rate exists for a
    pay-per-invocation resource, so hourly_rate is left None.
    """
    function = lambda_service.get_function(resource_id, region=region)
    if function is None:
        raise ValueError(f"Lambda function {resource_id!r} not found")

    now = datetime.now(timezone.utc)
    if date_range is None:
        # No creation timestamp exists for Lambda (see LambdaFunctionSummary's
        # docstring) -- defaults to a trailing 7-day usage window rather
        # than a fabricated "since creation" default, same documented-gap
        # shape as EIP's [now, now) default above.
        date_range = DateRange(start=now - timedelta(days=7), end=now)

    invocations = _sum_metric_over_window(
        "AWS/Lambda", "Invocations", "FunctionName", resource_id, date_range, region=region
    )
    memory_gb = (function.memory_size_mb or 128) / 1024.0
    price_per_invocation = LAMBDA_PRICE_PER_REQUEST_USD + (
        memory_gb * LAMBDA_ASSUMED_AVG_DURATION_SECONDS * LAMBDA_PRICE_PER_GB_SECOND_USD
    )

    incurred_so_far = round(invocations * price_per_invocation, 2)
    hours_elapsed = max(_elapsed_hours(None, date_range), 1.0 / 24.0)
    daily_invocations = invocations / (hours_elapsed / 24.0)
    projected_monthly = round(daily_invocations * 30.0 * price_per_invocation, 2)

    return CostEstimate(
        resource_id=resource_id,
        resource_type="lambda",
        date_range=date_range,
        method="list_price",
        hourly_rate=None,
        projected_monthly=projected_monthly,
        incurred_so_far=incurred_so_far,
    )


def _estimate_cost_nat_gateway(
    resource_id: str, date_range: DateRange | None, region: str | None = None
) -> CostEstimate:
    """Instance-hour rate via the Pricing API -- ignores per-GB data
    processing charges (see the module-level note above
    KINESIS_SHARD_HOURLY_RATE_USD)."""
    gateway = nat_gateway_service.get_nat_gateway(resource_id, region=region)
    if gateway is None:
        raise ValueError(f"NAT Gateway {resource_id!r} not found")

    now = datetime.now(timezone.utc)
    if date_range is None:
        date_range = DateRange(start=gateway.create_time or now, end=now)

    region = region or get_settings().aws_region
    hourly_rate = _get_nat_gateway_hourly_rate(region)

    projected_monthly = round(hourly_rate * HOURS_PER_MONTH, 2)
    incurred_so_far = round(hourly_rate * _elapsed_hours(gateway.create_time, date_range), 2)

    return CostEstimate(
        resource_id=resource_id,
        resource_type="nat_gateway",
        date_range=date_range,
        method="list_price",
        hourly_rate=hourly_rate,
        projected_monthly=projected_monthly,
        incurred_so_far=incurred_so_far,
    )


def _estimate_cost_dynamodb(
    resource_id: str, date_range: DateRange | None, region: str | None = None
) -> CostEstimate:
    """Dispatches on billing_mode -- PROVISIONED (RCU/WCU-hour, an
    instance-hour-shaped rate) and PAY_PER_REQUEST (usage-based, priced
    from observed Consumed*CapacityUnits) are priced completely
    differently and must not be collapsed into one formula (roadmap
    instructions, Step 3 batch B DynamoDB section)."""
    table = dynamodb_service.get_table(resource_id, region=region)
    if table is None:
        raise ValueError(f"DynamoDB table {resource_id!r} not found")

    now = datetime.now(timezone.utc)
    if date_range is None:
        date_range = DateRange(start=table.creation_date_time or now, end=now)

    if table.billing_mode == "PAY_PER_REQUEST":
        return _estimate_cost_dynamodb_on_demand(table, date_range, region)
    return _estimate_cost_dynamodb_provisioned(table, date_range)


def _estimate_cost_dynamodb_provisioned(
    table: DynamoTableSummary, date_range: DateRange
) -> CostEstimate:
    hourly_rate = (
        table.read_capacity_units * DYNAMODB_RCU_HOURLY_RATE_USD
        + table.write_capacity_units * DYNAMODB_WCU_HOURLY_RATE_USD
    )
    projected_monthly = round(hourly_rate * HOURS_PER_MONTH, 2)
    incurred_so_far = round(
        hourly_rate * _elapsed_hours(table.creation_date_time, date_range), 2
    )
    return CostEstimate(
        resource_id=table.name,
        resource_type="dynamodb",
        date_range=date_range,
        method="list_price",
        hourly_rate=hourly_rate,
        projected_monthly=projected_monthly,
        incurred_so_far=incurred_so_far,
    )


def _estimate_cost_dynamodb_on_demand(
    table: DynamoTableSummary, date_range: DateRange, region: str | None = None
) -> CostEstimate:
    read_units = _sum_metric_over_window(
        "AWS/DynamoDB", "ConsumedReadCapacityUnits", "TableName", table.name, date_range,
        region=region,
    )
    write_units = _sum_metric_over_window(
        "AWS/DynamoDB", "ConsumedWriteCapacityUnits", "TableName", table.name, date_range,
        region=region,
    )
    incurred_so_far = round(
        read_units * DYNAMODB_ON_DEMAND_RRU_PRICE_USD
        + write_units * DYNAMODB_ON_DEMAND_WRU_PRICE_USD,
        2,
    )
    hours_elapsed = max(_elapsed_hours(table.creation_date_time, date_range), 1.0 / 24.0)
    daily_read = read_units / (hours_elapsed / 24.0)
    daily_write = write_units / (hours_elapsed / 24.0)
    projected_monthly = round(
        (
            daily_read * DYNAMODB_ON_DEMAND_RRU_PRICE_USD
            + daily_write * DYNAMODB_ON_DEMAND_WRU_PRICE_USD
        )
        * 30.0,
        2,
    )
    return CostEstimate(
        resource_id=table.name,
        resource_type="dynamodb",
        date_range=date_range,
        method="list_price",
        hourly_rate=None,
        projected_monthly=projected_monthly,
        incurred_so_far=incurred_so_far,
    )


def _estimate_cost_elasticache(
    resource_id: str, date_range: DateRange | None, region: str | None = None
) -> CostEstimate:
    cluster = elasticache_service.get_cluster(resource_id, region=region)
    if cluster is None:
        raise ValueError(f"ElastiCache cluster {resource_id!r} not found")

    now = datetime.now(timezone.utc)
    if date_range is None:
        date_range = DateRange(start=cluster.create_time or now, end=now)

    region = region or get_settings().aws_region
    node_hourly_rate = _get_elasticache_hourly_rate(cluster.node_type, cluster.engine, region)
    # Multiplied by node count -- a single CacheClusterId can have more
    # than one node (Memcached; Redis cluster-mode-disabled replicas), and
    # every node is billed individually.
    hourly_rate = node_hourly_rate * max(cluster.num_cache_nodes, 1)

    projected_monthly = round(hourly_rate * HOURS_PER_MONTH, 2)
    incurred_so_far = round(hourly_rate * _elapsed_hours(cluster.create_time, date_range), 2)

    return CostEstimate(
        resource_id=resource_id,
        resource_type="elasticache",
        date_range=date_range,
        method="list_price",
        hourly_rate=hourly_rate,
        projected_monthly=projected_monthly,
        incurred_so_far=incurred_so_far,
    )


def _estimate_cost_sagemaker(
    resource_id: str, date_range: DateRange | None, region: str | None = None
) -> CostEstimate:
    """Roadmap: 'often the biggest silent cost -- runs 24/7 by default.'
    Prices the endpoint's first production variant only -- see
    SageMakerEndpoint's docstring on the single-variant assumption."""
    endpoint = sagemaker_service.get_endpoint(resource_id, region=region)
    if endpoint is None:
        raise ValueError(f"SageMaker endpoint {resource_id!r} not found")
    if not endpoint.instance_type:
        raise ValueError(
            f"SageMaker endpoint {resource_id!r} has no resolvable instance_type "
            "(its endpoint config or production variant could not be determined)"
        )

    now = datetime.now(timezone.utc)
    if date_range is None:
        date_range = DateRange(start=endpoint.creation_time or now, end=now)

    region = region or get_settings().aws_region
    instance_hourly_rate = _get_sagemaker_hourly_rate(endpoint.instance_type, region)
    hourly_rate = instance_hourly_rate * max(endpoint.instance_count, 1)

    projected_monthly = round(hourly_rate * HOURS_PER_MONTH, 2)
    incurred_so_far = round(hourly_rate * _elapsed_hours(endpoint.creation_time, date_range), 2)

    return CostEstimate(
        resource_id=resource_id,
        resource_type="sagemaker",
        date_range=date_range,
        method="list_price",
        hourly_rate=hourly_rate,
        projected_monthly=projected_monthly,
        incurred_so_far=incurred_so_far,
    )


def _estimate_cost_redshift(
    resource_id: str, date_range: DateRange | None, region: str | None = None
) -> CostEstimate:
    cluster = redshift_service.get_cluster(resource_id, region=region)
    if cluster is None:
        raise ValueError(f"Redshift cluster {resource_id!r} not found")

    now = datetime.now(timezone.utc)
    if date_range is None:
        date_range = DateRange(start=cluster.create_time or now, end=now)

    region = region or get_settings().aws_region
    node_hourly_rate = _get_redshift_hourly_rate(cluster.node_type, region)
    hourly_rate = node_hourly_rate * max(cluster.number_of_nodes, 1)

    projected_monthly = round(hourly_rate * HOURS_PER_MONTH, 2)
    incurred_so_far = round(hourly_rate * _elapsed_hours(cluster.create_time, date_range), 2)

    return CostEstimate(
        resource_id=resource_id,
        resource_type="redshift",
        date_range=date_range,
        method="list_price",
        hourly_rate=hourly_rate,
        projected_monthly=projected_monthly,
        incurred_so_far=incurred_so_far,
    )


def _estimate_cost_api_gateway(
    resource_id: str, date_range: DateRange | None, region: str | None = None
) -> CostEstimate:
    """Usage-based (see module docstring). REST APIs only, dimension
    value is the API's `name` -- see api_gateway_service.py's docstring."""
    api = api_gateway_service.get_api(resource_id, region=region)
    if api is None:
        raise ValueError(f"API Gateway REST API {resource_id!r} not found")

    now = datetime.now(timezone.utc)
    if date_range is None:
        date_range = DateRange(start=api.created_date or now, end=now)

    requests = _sum_metric_over_window(
        "AWS/ApiGateway", "Count", "ApiName", api.name, date_range, region=region
    )
    incurred_so_far = round(requests * API_GATEWAY_PRICE_PER_REQUEST_USD, 2)
    hours_elapsed = max(_elapsed_hours(api.created_date, date_range), 1.0 / 24.0)
    daily_requests = requests / (hours_elapsed / 24.0)
    projected_monthly = round(daily_requests * 30.0 * API_GATEWAY_PRICE_PER_REQUEST_USD, 2)

    return CostEstimate(
        resource_id=resource_id,
        resource_type="api_gateway",
        date_range=date_range,
        method="list_price",
        hourly_rate=None,
        projected_monthly=projected_monthly,
        incurred_so_far=incurred_so_far,
    )


def _estimate_cost_cloudfront(
    resource_id: str, date_range: DateRange | None, region: str | None = None
) -> CostEstimate:
    """Usage-based (see module docstring) -- request cost only, data
    transfer (GB) is NOT priced (see CLOUDFRONT_PRICE_PER_10K_REQUESTS_USD's
    docstring for why -- a documented, known undercount). Metrics are
    pulled from us-east-1 regardless of the account's configured region
    (or whatever region a scan happens to be looking at), same as
    idle_service's CloudFront branch -- `region` is accepted only so
    scan_service can call every type uniformly; it is not forwarded to the
    metrics call below."""
    distribution = cloudfront_service.get_distribution(resource_id, region=region)
    if distribution is None:
        raise ValueError(f"CloudFront distribution {resource_id!r} not found")

    now = datetime.now(timezone.utc)
    if date_range is None:
        # No creation timestamp exists for CloudFront -- same trailing
        # 7-day default as Lambda, for the same reason.
        date_range = DateRange(start=now - timedelta(days=7), end=now)

    requests = _sum_metric_over_window(
        "AWS/CloudFront", "Requests", "DistributionId", resource_id, date_range,
        region="us-east-1",
    )
    incurred_so_far = round(
        (requests / 10_000.0) * CLOUDFRONT_PRICE_PER_10K_REQUESTS_USD, 2
    )
    hours_elapsed = max(_elapsed_hours(None, date_range), 1.0 / 24.0)
    daily_requests = requests / (hours_elapsed / 24.0)
    projected_monthly = round(
        ((daily_requests * 30.0) / 10_000.0) * CLOUDFRONT_PRICE_PER_10K_REQUESTS_USD, 2
    )

    return CostEstimate(
        resource_id=resource_id,
        resource_type="cloudfront",
        date_range=date_range,
        method="list_price",
        hourly_rate=None,
        projected_monthly=projected_monthly,
        incurred_so_far=incurred_so_far,
    )


def _estimate_cost_opensearch(
    resource_id: str, date_range: DateRange | None, region: str | None = None
) -> CostEstimate:
    domain = opensearch_service.get_domain(resource_id, region=region)
    if domain is None:
        raise ValueError(f"OpenSearch domain {resource_id!r} not found")
    if not domain.instance_type:
        raise ValueError(f"OpenSearch domain {resource_id!r} has no resolvable instance_type")

    now = datetime.now(timezone.utc)
    if date_range is None:
        # No creation timestamp exists for OpenSearch (see
        # OpenSearchDomain's docstring) -- same [now, now) default as EIP.
        date_range = DateRange(start=now, end=now)

    region = region or get_settings().aws_region
    node_hourly_rate = _get_opensearch_hourly_rate(domain.instance_type, region)
    hourly_rate = node_hourly_rate * max(domain.instance_count, 1)

    projected_monthly = round(hourly_rate * HOURS_PER_MONTH, 2)
    incurred_so_far = round(hourly_rate * _elapsed_hours(None, date_range), 2)

    return CostEstimate(
        resource_id=resource_id,
        resource_type="opensearch",
        date_range=date_range,
        method="list_price",
        hourly_rate=hourly_rate,
        projected_monthly=projected_monthly,
        incurred_so_far=incurred_so_far,
    )


def _estimate_cost_kinesis(
    resource_id: str, date_range: DateRange | None, region: str | None = None
) -> CostEstimate:
    """Shard-hour base charge only, via a documented flat constant (not
    the Pricing API -- see KINESIS_SHARD_HOURLY_RATE_USD's docstring).
    PUT payload unit charges are ignored."""
    stream = kinesis_service.get_stream(resource_id, region=region)
    if stream is None:
        raise ValueError(f"Kinesis stream {resource_id!r} not found")

    now = datetime.now(timezone.utc)
    if date_range is None:
        date_range = DateRange(start=stream.creation_timestamp or now, end=now)

    hourly_rate = KINESIS_SHARD_HOURLY_RATE_USD * max(stream.open_shard_count, 0)

    projected_monthly = round(hourly_rate * HOURS_PER_MONTH, 2)
    incurred_so_far = round(
        hourly_rate * _elapsed_hours(stream.creation_timestamp, date_range), 2
    )

    return CostEstimate(
        resource_id=resource_id,
        resource_type="kinesis",
        date_range=date_range,
        method="list_price",
        hourly_rate=hourly_rate,
        projected_monthly=projected_monthly,
        incurred_so_far=incurred_so_far,
    )
