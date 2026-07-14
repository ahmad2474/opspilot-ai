"""Elastic IP models.

Deliberately has no creation-timestamp field: the EC2 DescribeAddresses
API does not expose an EIP's allocation time at all -- unlike every other
resource type in this build step, EIP idle/cost logic has no age signal
to bound a "younger than window" check against. See idle_service.py and
cost_service.py's EIP branches for how this is handled explicitly, not
silently ignored.
"""
from __future__ import annotations

from pydantic import BaseModel, Field


class ElasticIp(BaseModel):
    allocation_id: str | None = Field(
        default=None,
        description="VPC-scoped EIP identifier. None only for legacy EC2-Classic addresses.",
    )
    public_ip: str
    domain: str = Field(description="'vpc' or 'standard' (EC2-Classic)")
    association_id: str | None = None
    instance_id: str | None = None
    network_interface_id: str | None = None
    tags: dict[str, str] = Field(default_factory=dict)

    @property
    def is_associated(self) -> bool:
        return bool(self.association_id or self.instance_id or self.network_interface_id)

    @property
    def resource_id(self) -> str:
        """Canonical ID used as check_idle/estimate_cost's resource_id --
        allocation_id when present (the modern VPC case), falling back to
        public_ip only for legacy EC2-Classic addresses that have none."""
        return self.allocation_id or self.public_ip


class ElasticIpList(BaseModel):
    addresses: list[ElasticIp]
    count: int
