"""OpenSearch domain business logic. No boto3 calls anywhere else in the
app. Mirrors ebs_service.py's shape/style.

list_domain_names() doesn't return enough detail for idle/cost use (no
ARN, no instance type/count) -- describe_domain() is always the second
call, one per domain name, same N+1 shape as dynamodb_service/
sagemaker_service. ListDomainNames itself has no pagination token (AWS
caps a single account/region at 100 domains, well under any page size),
so no paginator is used here.
"""
from __future__ import annotations

from app.aws.client import get_opensearch_client
from app.models.opensearch import OpenSearchDomain, OpenSearchDomainList


def list_domains(region: str | None = None) -> OpenSearchDomainList:
    client = get_opensearch_client(region=region)
    names_response = client.list_domain_names()
    domain_names = [d["DomainName"] for d in names_response.get("DomainNames", [])]

    domains: list[OpenSearchDomain] = []
    for name in domain_names:
        detail = client.describe_domain(DomainName=name)["DomainStatus"]
        cluster_config = detail.get("ClusterConfig", {})
        vpc_options = detail.get("VPCOptions") or {}
        domains.append(
            OpenSearchDomain(
                domain_name=name,
                arn=detail["ARN"],
                created=detail.get("Created", True),
                instance_type=cluster_config.get("InstanceType"),
                instance_count=cluster_config.get("InstanceCount", 1),
                security_group_ids=list(vpc_options.get("SecurityGroupIds", []) or []),
                subnet_ids=list(vpc_options.get("SubnetIds", []) or []),
                vpc_id=vpc_options.get("VPCId") or None,
            )
        )
    return OpenSearchDomainList(domains=domains, count=len(domains))


def get_domain(domain_name: str, region: str | None = None) -> OpenSearchDomain | None:
    result = list_domains(region=region)
    for domain in result.domains:
        if domain.domain_name == domain_name:
            return domain
    return None
