"""OpenSearch domain models. Mirrors app/models/ebs.py's shape/style.

`account_id` is parsed out of the domain's ARN
(arn:aws:es:REGION:ACCOUNT_ID:domain/NAME) rather than fetched via a
separate STS GetCallerIdentity call -- AWS/ES CloudWatch metrics require
both `ClientId` (the AWS account ID) and `DomainName` dimensions together,
and the ARN already carries the account ID for free, one call
(DescribeDomain) instead of two.
"""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class OpenSearchDomain(BaseModel):
    domain_name: str
    arn: str
    created: bool = Field(
        default=True, description="AWS's own 'has finished creating' boolean, not a timestamp."
    )
    created_at: datetime | None = Field(
        default=None,
        description=(
            "DescribeDomain does not expose a creation timestamp at all "
            "(only the 'created' boolean above) -- always None, a "
            "documented gap, same precedent as EIP/CloudFront/Lambda."
        ),
    )
    instance_type: str | None = None
    instance_count: int = 1
    security_group_ids: list[str] = Field(
        default_factory=list,
        description="From VPCOptions.SecurityGroupIds -- empty for a public (non-VPC) "
        "domain. Roadmap 3.7 relation-shaping, no new call.",
    )
    subnet_ids: list[str] = Field(
        default_factory=list,
        description="From VPCOptions.SubnetIds -- empty for a public domain. Roadmap 3.7.",
    )
    vpc_id: str | None = Field(
        default=None,
        description="From VPCOptions.VPCId -- None for a public domain. Roadmap 3.7.",
    )

    @property
    def account_id(self) -> str | None:
        # arn:aws:es:us-east-1:123456789012:domain/my-domain
        parts = self.arn.split(":")
        return parts[4] if len(parts) > 4 else None


class OpenSearchDomainList(BaseModel):
    domains: list[OpenSearchDomain]
    count: int
