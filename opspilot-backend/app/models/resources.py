from __future__ import annotations

from pydantic import BaseModel

from app.models.cloudwatch import CpuUtilizationSummary
from app.models.ec2 import EC2Instance


class Ec2ResourceCard(BaseModel):
    instance: EC2Instance
    cpu: CpuUtilizationSummary | None = None


class ResourcesResponse(BaseModel):
    ec2: list[Ec2ResourceCard]
