"""Load balancer models -- covers modern ALB/NLB (elbv2) as the primary
target, plus Classic ELB (elb) as a small addition on top per roadmap.

`lb_type` drives both the CloudWatch namespace and the Pricing API lookup
in idle_service.py/cost_service.py (application -> AWS/ApplicationELB,
network -> AWS/NetworkELB, classic -> AWS/ELB) -- see elb_service.py's
cloudwatch_dimension() for the exact mapping, including the ALB/NLB
dimension value's non-obvious truncated-ARN format.
"""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

LoadBalancerType = Literal["application", "network", "classic"]


class LoadBalancer(BaseModel):
    name: str
    lb_type: LoadBalancerType
    arn: str | None = Field(
        default=None,
        description="None for classic ELB, which has no ARN in describe_load_balancers.",
    )
    state: str = Field(
        description=(
            "e.g. active, provisioning, failed (classic ELB has no state "
            "field; 'active' assumed)"
        )
    )
    dns_name: str | None = None
    scheme: str | None = Field(default=None, description="internet-facing or internal")
    created_time: datetime | None = None
    tags: dict[str, str] = Field(default_factory=dict)

    security_group_ids: list[str] = Field(
        default_factory=list,
        description="Empty for NLB (no security groups) and any LB where none are "
        "attached. Roadmap 3.7 relation-shaping, no new call.",
    )
    subnet_ids: list[str] = Field(
        default_factory=list,
        description="Roadmap 3.7 relation-shaping, no new call.",
    )
    vpc_id: str | None = Field(
        default=None,
        description="Roadmap 3.7 relation-shaping, no new call.",
    )


class LoadBalancerList(BaseModel):
    load_balancers: list[LoadBalancer]
    count: int
