"""Load balancer business logic. No boto3 calls anywhere else in the app.

elbv2 (ALB/NLB) is the primary target per roadmap Step 3; classic ELB is
included too since it turned out to be a small addition on top, not a
separate system -- see list_load_balancers()' second loop below.

Gateway Load Balancers (elbv2 Type="gateway") are deliberately skipped:
they're not part of the roadmap's 15 in-scope resource types and use a
different metric set (GENEVE flow counters, not RequestCount) -- a
documented gap, not silently mishandled.
"""
from __future__ import annotations

from app.aws.client import get_elb_client, get_elbv2_client
from app.models.elb import LoadBalancer, LoadBalancerList

# elbv2 Type -> our lb_type + CloudWatch namespace. "gateway" intentionally
# excluded (see module docstring).
_ELBV2_TYPE_TO_NAMESPACE = {
    "application": "AWS/ApplicationELB",
    "network": "AWS/NetworkELB",
}


def _flatten_tags(raw_tags: list[dict[str, str]] | None) -> dict[str, str]:
    if not raw_tags:
        return {}
    return {tag["Key"]: tag["Value"] for tag in raw_tags}


def list_load_balancers(region: str | None = None) -> LoadBalancerList:
    load_balancers: list[LoadBalancer] = []

    v2_client = get_elbv2_client(region=region)
    v2_paginator = v2_client.get_paginator("describe_load_balancers")
    for page in v2_paginator.paginate():
        for raw in page.get("LoadBalancers", []):
            lb_type = raw.get("Type", "application")
            if lb_type not in _ELBV2_TYPE_TO_NAMESPACE:
                continue  # gateway LBs -- see module docstring
            load_balancers.append(
                LoadBalancer(
                    name=raw["LoadBalancerName"],
                    lb_type=lb_type,
                    arn=raw.get("LoadBalancerArn"),
                    state=raw.get("State", {}).get("Code", "unknown"),
                    dns_name=raw.get("DNSName"),
                    scheme=raw.get("Scheme"),
                    created_time=raw.get("CreatedTime"),
                    security_group_ids=list(raw.get("SecurityGroups", []) or []),
                    subnet_ids=[
                        az["SubnetId"]
                        for az in raw.get("AvailabilityZones", [])
                        if az.get("SubnetId")
                    ],
                    vpc_id=raw.get("VpcId"),
                )
            )

    classic_client = get_elb_client(region=region)
    classic_paginator = classic_client.get_paginator("describe_load_balancers")
    for page in classic_paginator.paginate():
        for raw in page.get("LoadBalancerDescriptions", []):
            load_balancers.append(
                LoadBalancer(
                    name=raw["LoadBalancerName"],
                    lb_type="classic",
                    arn=None,
                    state="active",  # classic ELB has no State field
                    dns_name=raw.get("DNSName"),
                    scheme=raw.get("Scheme"),
                    created_time=raw.get("CreatedTime"),
                    security_group_ids=list(raw.get("SecurityGroups", []) or []),
                    subnet_ids=list(raw.get("Subnets", []) or []),
                    vpc_id=raw.get("VPCId"),
                )
            )

    return LoadBalancerList(load_balancers=load_balancers, count=len(load_balancers))


def get_load_balancer(name: str, region: str | None = None) -> LoadBalancer | None:
    result = list_load_balancers(region=region)
    for lb in result.load_balancers:
        if lb.name == name:
            return lb
    return None


def cloudwatch_dimension(lb: LoadBalancer) -> tuple[str, str, str]:
    """Returns (namespace, dimension_name, dimension_value) for a load
    balancer's CloudWatch metrics.

    ALB/NLB's dimension value is NOT the ARN and NOT the name -- it's a
    truncated form parsed out of the ARN, e.g. "app/my-lb/50dc6c495c0c9188"
    (everything after ".../loadbalancer/" in the full ARN). Classic ELB
    uses the plain LoadBalancerName as both name and dimension value under
    a different dimension key ("LoadBalancerName", not "LoadBalancer").
    """
    if lb.lb_type == "classic":
        return "AWS/ELB", "LoadBalancerName", lb.name

    namespace = _ELBV2_TYPE_TO_NAMESPACE[lb.lb_type]
    if not lb.arn:
        raise ValueError(f"load balancer {lb.name!r} of type {lb.lb_type!r} has no ARN")
    dimension_value = lb.arn.split("loadbalancer/", 1)[-1]
    return namespace, "LoadBalancer", dimension_value
