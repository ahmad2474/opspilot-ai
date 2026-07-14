"""NAT Gateway business logic. No boto3 calls anywhere else in the app.

NAT Gateways are an EC2 API (describe_nat_gateways) -- reuses
get_ec2_client(), same as EBS/EIP in batch A. Mirrors ebs_service.py's
shape/style.
"""
from __future__ import annotations

from app.aws.client import get_ec2_client
from app.models.nat_gateway import NatGateway, NatGatewayList


def _flatten_tags(raw_tags: list[dict[str, str]] | None) -> dict[str, str]:
    if not raw_tags:
        return {}
    return {tag["Key"]: tag["Value"] for tag in raw_tags}


def list_nat_gateways(region: str | None = None) -> NatGatewayList:
    client = get_ec2_client(region=region)
    paginator = client.get_paginator("describe_nat_gateways")
    gateways: list[NatGateway] = []
    for page in paginator.paginate():
        for raw in page.get("NatGateways", []):
            gateways.append(
                NatGateway(
                    nat_gateway_id=raw["NatGatewayId"],
                    state=raw.get("State", "unknown"),
                    subnet_id=raw.get("SubnetId"),
                    vpc_id=raw.get("VpcId"),
                    connectivity_type=raw.get("ConnectivityType", "public"),
                    create_time=raw.get("CreateTime"),
                    tags=_flatten_tags(raw.get("Tags")),
                )
            )
    return NatGatewayList(nat_gateways=gateways, count=len(gateways))


def get_nat_gateway(nat_gateway_id: str, region: str | None = None) -> NatGateway | None:
    result = list_nat_gateways(region=region)
    for gateway in result.nat_gateways:
        if gateway.nat_gateway_id == nat_gateway_id:
            return gateway
    return None
