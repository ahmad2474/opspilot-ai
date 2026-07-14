from __future__ import annotations

from pydantic import BaseModel

from app.models.cloudwatch import CpuUtilizationSummary
from app.models.cost import CostEstimate
from app.models.ec2 import EC2Instance
from app.models.idle import IdleCheckResult


class Ec2ResourceCard(BaseModel):
    instance: EC2Instance
    cpu: CpuUtilizationSummary | None = None
    idle: IdleCheckResult | None = None
    cost: CostEstimate | None = None


class ResourcesResponse(BaseModel):
    ec2: list[Ec2ResourceCard]
