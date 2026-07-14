"""CloudFront distribution models.

Deliberately has no creation-timestamp field: neither ListDistributions
nor GetDistribution expose a distribution's creation time, only
LastModifiedTime (which changes on every config update, e.g. adding a
cache behavior years after creation) -- using it as a creation-time proxy
would be actively misleading (a years-old distribution edited yesterday
would falsely report as "younger than window"), so it's intentionally
left unset, same documented-gap precedent as ElasticIp (see
app/models/eip.py) and Lambda (see lambda_service.py's get_function).
"""
from __future__ import annotations

from pydantic import BaseModel, Field


class CloudFrontDistribution(BaseModel):
    distribution_id: str
    arn: str | None = None
    status: str = Field(description="e.g. Deployed, InProgress")
    domain_name: str | None = None
    enabled: bool = True


class CloudFrontDistributionList(BaseModel):
    distributions: list[CloudFrontDistribution]
    count: int
