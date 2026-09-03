"""VPC Endpoint business logic (roadmap phase 2 Section 1.1, Tier 3a).

VPC Endpoints are an EC2 API (describe_vpc_endpoints) -- reuses
get_ec2_client(), same as EBS/EIP/NAT Gateway. list_vpc_endpoints/
get_vpc_endpoint mirror nat_gateway_service.py's shape/style exactly.

Idle/cost checking is scoped to Interface endpoints only -- Gateway
endpoints (S3, DynamoDB) have no hourly charge and publish no
BytesProcessed metric, so there is no waste signal to check for them (they
are free either way). check_vpc_endpoint_idle/estimate_vpc_endpoint_cost
raise NotInterfaceEndpointError for a non-Interface endpoint rather than
silently reporting a meaningless zero.

Deliberate design call, worth recording like the other real decisions in
this codebase: idle/cost checking here is built as its own pair of
functions, NOT added as a 16th resource_type to idle_service.check_idle/
cost_service.estimate_cost's shared dispatcher. That dispatcher is a much
wider surface than this one check -- scan_service's 15-type totals
aggregation, resource_query_service's list_resources/get_resource_health,
and the chat agent's prompt all enumerate the same fixed 15 types, and
widening that list is a broader change than "add one Tier 3a check." The
exact same CloudWatch-window-idle and Pricing-API-cost *pattern* is still
reused, just via idle_service.check_idle_via_metrics/not_idle_result and
cost_service.extract_usd_price/elapsed_hours directly -- those four helpers
were promoted from private to public in this change specifically so this
module could reuse them instead of duplicating the zero-fill/trailing-
streak logic or the GetProducts JSON parsing.
"""
from __future__ import annotations

from datetime import datetime, timezone

from app.aws.client import get_ec2_client, get_pricing_client
from app.core.config import get_settings
from app.models.cost import CostEstimate, DateRange
from app.models.idle import IdleCheckResult
from app.models.vpc_endpoint import VpcEndpoint, VpcEndpointList
from app.services import cost_service, idle_service

INTERFACE_ENDPOINT_TYPE = "Interface"

# Same order of magnitude as NAT Gateway's bytes-idle threshold in
# idle_service.py (roadmap phase 2 Section 1.1: "same shape as your
# existing NAT Gateway check").
VPC_ENDPOINT_BYTES_IDLE_THRESHOLD = 5 * 1024 * 1024  # 5 MB/day, per metric


class NotInterfaceEndpointError(ValueError):
    """Raised when idle/cost checking is asked about a non-Interface VPC
    endpoint -- Gateway endpoints have no hourly charge and no
    BytesProcessed metric, so there's genuinely no waste signal to check.
    """


def _flatten_tags(raw_tags: list[dict[str, str]] | None) -> dict[str, str]:
    if not raw_tags:
        return {}
    return {tag["Key"]: tag["Value"] for tag in raw_tags}


def list_vpc_endpoints(region: str | None = None) -> VpcEndpointList:
    client = get_ec2_client(region=region)
    paginator = client.get_paginator("describe_vpc_endpoints")
    endpoints: list[VpcEndpoint] = []
    for page in paginator.paginate():
        for raw in page.get("VpcEndpoints", []):
            endpoints.append(
                VpcEndpoint(
                    vpc_endpoint_id=raw["VpcEndpointId"],
                    vpc_endpoint_type=raw.get("VpcEndpointType", "Interface"),
                    service_name=raw.get("ServiceName", ""),
                    state=raw.get("State", "unknown"),
                    vpc_id=raw.get("VpcId"),
                    subnet_ids=raw.get("SubnetIds", []) or [],
                    create_time=raw.get("CreationTimestamp"),
                    tags=_flatten_tags(raw.get("Tags")),
                )
            )
    return VpcEndpointList(vpc_endpoints=endpoints, count=len(endpoints))


def get_vpc_endpoint(vpc_endpoint_id: str, region: str | None = None) -> VpcEndpoint | None:
    result = list_vpc_endpoints(region=region)
    for endpoint in result.vpc_endpoints:
        if endpoint.vpc_endpoint_id == vpc_endpoint_id:
            return endpoint
    return None


def check_vpc_endpoint_idle(
    vpc_endpoint_id: str, days: int, region: str | None = None
) -> IdleCheckResult:
    """Section 1.1: 'Near-zero BytesProcessed/data-plane traffic over the
    window'. Reuses idle_service.check_idle_via_metrics directly -- same
    zero-fill-missing-days treatment as NAT Gateway's BytesOutToDestination/
    BytesInFromSource (BytesProcessed is the same kind of sparse "no traffic
    = no datapoint" activity metric, not a continuous gauge).
    """
    endpoint = get_vpc_endpoint(vpc_endpoint_id, region=region)
    if endpoint is None:
        return idle_service.not_idle_result(vpc_endpoint_id, "vpc_endpoint", days)
    if endpoint.vpc_endpoint_type != INTERFACE_ENDPOINT_TYPE:
        raise NotInterfaceEndpointError(
            f"VPC Endpoint {vpc_endpoint_id!r} is a {endpoint.vpc_endpoint_type!r} "
            "endpoint, not Interface -- Gateway endpoints (S3, DynamoDB) have no "
            "hourly charge and no BytesProcessed metric, so idle-checking them has "
            "no meaningful waste signal."
        )

    return idle_service.check_idle_via_metrics(
        vpc_endpoint_id,
        "vpc_endpoint",
        days,
        endpoint.create_time,
        [
            (
                "AWS/PrivateLinkEndpoints",
                "BytesProcessed",
                "VPC Endpoint Id",
                vpc_endpoint_id,
                "Sum",
                "Bytes",
                VPC_ENDPOINT_BYTES_IDLE_THRESHOLD,
            ),
        ],
        zero_fill_missing_days=True,
        region=region,
    )


def _get_vpc_endpoint_hourly_rate(region: str) -> float:
    """On-demand USD/hour base rate for an Interface VPC Endpoint, via the
    Pricing API. Ignores the per-AZ multiplier (a single endpoint can span
    multiple AZs, each billed separately) and per-GB data-processing
    charges -- same "base rate is the dominant, documented-simplified cost"
    precedent as NAT Gateway/ELB in cost_service.py. Not live-verified
    against a real GetProducts response (no AWS credentials exist in this
    build environment, see docs/BUILD_PROGRESS.md) -- confirm the
    `productFamily`/attribute values against a real call before relying on
    this for an actual bill.
    """
    client = get_pricing_client()
    response = client.get_products(
        ServiceCode="AmazonVPC",
        Filters=[
            {"Type": "TERM_MATCH", "Field": "productFamily", "Value": "VpcEndpoint"},
            {"Type": "TERM_MATCH", "Field": "regionCode", "Value": region},
        ],
        MaxResults=1,
    )
    price_list = response.get("PriceList", [])
    if not price_list:
        raise ValueError(
            f"Pricing API returned no on-demand price for VPC Endpoint region={region!r}"
        )
    return cost_service.extract_usd_price(price_list)


def estimate_vpc_endpoint_cost(
    vpc_endpoint_id: str, date_range: DateRange | None = None, region: str | None = None
) -> CostEstimate:
    endpoint = get_vpc_endpoint(vpc_endpoint_id, region=region)
    if endpoint is None:
        raise ValueError(f"VPC Endpoint {vpc_endpoint_id!r} not found")
    if endpoint.vpc_endpoint_type != INTERFACE_ENDPOINT_TYPE:
        raise NotInterfaceEndpointError(
            f"VPC Endpoint {vpc_endpoint_id!r} is a {endpoint.vpc_endpoint_type!r} "
            "endpoint, not Interface -- Gateway endpoints have no hourly charge."
        )

    now = datetime.now(timezone.utc)
    if date_range is None:
        date_range = DateRange(start=endpoint.create_time or now, end=now)

    region = region or get_settings().aws_region
    hourly_rate = _get_vpc_endpoint_hourly_rate(region)

    projected_monthly = round(hourly_rate * cost_service.HOURS_PER_MONTH, 2)
    incurred_so_far = round(
        hourly_rate * cost_service.elapsed_hours(endpoint.create_time, date_range), 2
    )

    return CostEstimate(
        resource_id=vpc_endpoint_id,
        resource_type="vpc_endpoint",
        date_range=date_range,
        method="list_price",
        hourly_rate=hourly_rate,
        projected_monthly=projected_monthly,
        incurred_so_far=incurred_so_far,
    )
