"""SageMaker endpoint business logic. No boto3 calls anywhere else in the
app. Mirrors ebs_service.py's shape/style.

Instance type/count live on the endpoint *config*, not the endpoint
itself -- ListEndpoints/DescribeEndpoint only return EndpointName/status/
timestamps, so list_endpoints() makes one extra DescribeEndpointConfig
call per endpoint to fill in instance_type/instance_count/variant_name.
This is the same "N+1 describe calls" shape dynamodb_service.list_tables()
already uses (list names, then describe each) -- SageMaker accounts
rarely have more than a handful of endpoints (they're expensive,
always-on resources), so this isn't the pagination-heavy case EC2/EBS
listing has to worry about.
"""
from __future__ import annotations

from app.aws.client import get_sagemaker_client
from app.models.sagemaker import SageMakerEndpoint, SageMakerEndpointList


def _to_summary(client, raw: dict) -> SageMakerEndpoint:
    endpoint_name = raw["EndpointName"]
    variant_name: str | None = None
    instance_type: str | None = None
    instance_count = 0

    config_name = raw.get("EndpointConfigName")
    if config_name is None:
        # list_endpoints() doesn't return EndpointConfigName -- look it up
        # via DescribeEndpoint first when called from that path.
        try:
            detail = client.describe_endpoint(EndpointName=endpoint_name)
            config_name = detail.get("EndpointConfigName")
        except client.exceptions.ClientError:
            config_name = None

    if config_name:
        try:
            config = client.describe_endpoint_config(EndpointConfigName=config_name)
            variants = config.get("ProductionVariants", [])
            if variants:
                first = variants[0]
                variant_name = first.get("VariantName")
                instance_type = first.get("InstanceType")
                instance_count = first.get("InitialInstanceCount", 0)
        except client.exceptions.ClientError:
            pass

    return SageMakerEndpoint(
        endpoint_name=endpoint_name,
        status=raw.get("EndpointStatus", "unknown"),
        creation_time=raw.get("CreationTime"),
        variant_name=variant_name,
        instance_type=instance_type,
        instance_count=instance_count,
    )


def list_endpoints(region: str | None = None) -> SageMakerEndpointList:
    client = get_sagemaker_client(region=region)
    paginator = client.get_paginator("list_endpoints")
    endpoints: list[SageMakerEndpoint] = []
    for page in paginator.paginate():
        for raw in page.get("Endpoints", []):
            endpoints.append(_to_summary(client, raw))
    return SageMakerEndpointList(endpoints=endpoints, count=len(endpoints))


def get_endpoint(
    endpoint_name: str, region: str | None = None
) -> SageMakerEndpoint | None:
    """List-then-filter, same convention every other service module uses
    for its get_*() -- see lambda_service.get_function's docstring. Costs
    one extra DescribeEndpointConfig call per *other* endpoint in the
    account versus a direct lookup, but SageMaker accounts rarely have
    more than a handful of endpoints (see module docstring)."""
    result = list_endpoints(region=region)
    for endpoint in result.endpoints:
        if endpoint.endpoint_name == endpoint_name:
            return endpoint
    return None
