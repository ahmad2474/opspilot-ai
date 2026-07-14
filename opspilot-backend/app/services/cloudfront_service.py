"""CloudFront business logic. No boto3 calls anywhere else in the app.
Mirrors ebs_service.py's shape/style.

CloudFront is a global service -- get_cloudfront_client() needs no region
override (its management API has a single global endpoint), but its
CloudWatch *metrics* are us-east-1-only regardless (see
idle_service._check_idle_cloudfront, which passes region="us-east-1" into
cloudwatch_service.get_daily_datapoints -- a metrics-query concern, not a
management-API concern, so it doesn't belong in this file).
"""
from __future__ import annotations

from app.aws.client import get_cloudfront_client
from app.models.cloudfront import CloudFrontDistribution, CloudFrontDistributionList


def list_distributions(region: str | None = None) -> CloudFrontDistributionList:
    """`region` is accepted (not used) purely so scan_service/idle_service/
    cost_service can call every resource type's list_*()/get_*() with the
    same `region=region` keyword uniformly -- CloudFront's management API
    is global (see module docstring), so a distribution list is identical
    regardless of which region a scan is currently looking at. A known,
    documented consequence: a region-wide scan attributes every CloudFront
    distribution to whichever region the caller happens to be scanning,
    since there is no "home region" to filter by.
    """
    del region
    client = get_cloudfront_client()
    paginator = client.get_paginator("list_distributions")
    distributions: list[CloudFrontDistribution] = []
    for page in paginator.paginate():
        items = page.get("DistributionList", {}).get("Items", [])
        for raw in items:
            distributions.append(
                CloudFrontDistribution(
                    distribution_id=raw["Id"],
                    arn=raw.get("ARN"),
                    status=raw.get("Status", "unknown"),
                    domain_name=raw.get("DomainName"),
                    enabled=raw.get("Enabled", True),
                )
            )
    return CloudFrontDistributionList(distributions=distributions, count=len(distributions))


def get_distribution(
    distribution_id: str, region: str | None = None
) -> CloudFrontDistribution | None:
    result = list_distributions(region=region)
    for distribution in result.distributions:
        if distribution.distribution_id == distribution_id:
            return distribution
    return None
