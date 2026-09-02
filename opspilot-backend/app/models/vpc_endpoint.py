"""VPC Endpoint models (roadmap phase 2 Section 1.1, Tier 3a).

Mirrors app/models/nat_gateway.py's shape/style -- VPC Endpoints are
described via the EC2 API (DescribeVpcEndpoints), same client as NAT
Gateway/EBS/EIP/EC2. See vpc_endpoint_service.py's module docstring for why
idle/cost checking is deliberately NOT folded into IdleCheckResult/
CostEstimate's shared 15-type resource_type dispatcher, even though it
reuses those exact response models.
"""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from app.models.cost import CostEstimate
from app.models.idle import IdleCheckResult


class VpcEndpoint(BaseModel):
    vpc_endpoint_id: str
    vpc_endpoint_type: str = Field(
        description="'Interface', 'Gateway', or 'GatewayLoadBalancer'."
    )
    service_name: str
    state: str = Field(description="e.g. pending, available, deleting, deleted, failed")
    vpc_id: str | None = None
    subnet_ids: list[str] = Field(default_factory=list)
    create_time: datetime | None = None
    tags: dict[str, str] = Field(default_factory=dict)


class VpcEndpointList(BaseModel):
    vpc_endpoints: list[VpcEndpoint]
    count: int


class VpcEndpointWasteEntry(BaseModel):
    """One row of the /waste/vpc-endpoints dashboard route -- pairs an
    Interface endpoint with its idle/cost lookup, nullable at the
    per-resource level (same graceful-degradation rule as GalaxyResource's
    cost/idle fields in the data-schema skill) so one endpoint's lookup
    failing never blanks the rest.
    """

    endpoint: VpcEndpoint
    idle: IdleCheckResult | None = None
    cost: CostEstimate | None = None


class VpcEndpointWasteReport(BaseModel):
    entries: list[VpcEndpointWasteEntry]
    count: int
