"""NAT Gateway models. Mirrors app/models/ebs.py's shape/style.

NAT Gateways are described via the EC2 API (DescribeNatGateways), not a
separate service -- see nat_gateway_service.py's module docstring.
"""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class NatGateway(BaseModel):
    nat_gateway_id: str
    state: str = Field(description="e.g. pending, available, deleting, deleted, failed")
    subnet_id: str | None = None
    vpc_id: str | None = None
    connectivity_type: str = Field(default="public", description="'public' or 'private'")
    create_time: datetime | None = None
    tags: dict[str, str] = Field(default_factory=dict)


class NatGatewayList(BaseModel):
    nat_gateways: list[NatGateway]
    count: int
