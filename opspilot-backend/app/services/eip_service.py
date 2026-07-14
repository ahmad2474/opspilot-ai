"""Elastic IP business logic. No boto3 calls anywhere else in the app.

Mirrors ec2_service.py's shape/style. get_address() matches on
allocation_id (the modern VPC case) or falls back to public_ip (legacy
EC2-Classic), matching ElasticIp.resource_id's own fallback logic.
"""
from __future__ import annotations

from app.aws.client import get_ec2_client
from app.models.eip import ElasticIp, ElasticIpList


def _flatten_tags(raw_tags: list[dict[str, str]] | None) -> dict[str, str]:
    if not raw_tags:
        return {}
    return {tag["Key"]: tag["Value"] for tag in raw_tags}


def list_addresses(region: str | None = None) -> ElasticIpList:
    client = get_ec2_client(region=region)
    response = client.describe_addresses()
    addresses: list[ElasticIp] = []
    for raw in response.get("Addresses", []):
        addresses.append(
            ElasticIp(
                allocation_id=raw.get("AllocationId"),
                public_ip=raw["PublicIp"],
                domain=raw.get("Domain", "standard"),
                association_id=raw.get("AssociationId"),
                instance_id=raw.get("InstanceId") or None,
                network_interface_id=raw.get("NetworkInterfaceId") or None,
                tags=_flatten_tags(raw.get("Tags")),
            )
        )
    return ElasticIpList(addresses=addresses, count=len(addresses))


def get_address(resource_id: str, region: str | None = None) -> ElasticIp | None:
    result = list_addresses(region=region)
    for address in result.addresses:
        if address.resource_id == resource_id:
            return address
    return None
