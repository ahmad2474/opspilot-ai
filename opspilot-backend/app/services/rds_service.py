from __future__ import annotations

from app.aws.client import get_rds_client
from app.models.dashboard import RdsCard, RdsInstanceSummary


def list_instances(region: str | None = None) -> RdsCard:
    client = get_rds_client(region=region)
    paginator = client.get_paginator("describe_db_instances")
    instances: list[RdsInstanceSummary] = []
    for page in paginator.paginate():
        for raw in page.get("DBInstances", []):
            subnet_group = raw.get("DBSubnetGroup") or {}
            instances.append(
                RdsInstanceSummary(
                    identifier=raw["DBInstanceIdentifier"],
                    engine=raw.get("Engine", "unknown"),
                    instance_class=raw.get("DBInstanceClass", "unknown"),
                    status=raw.get("DBInstanceStatus", "unknown"),
                    instance_create_time=raw.get("InstanceCreateTime"),
                    vpc_security_group_ids=[
                        g["VpcSecurityGroupId"]
                        for g in raw.get("VpcSecurityGroups", [])
                        if g.get("VpcSecurityGroupId")
                    ],
                    subnet_ids=[
                        s["SubnetIdentifier"]
                        for s in subnet_group.get("Subnets", [])
                        if s.get("SubnetIdentifier")
                    ],
                    vpc_id=subnet_group.get("VpcId"),
                )
            )
    return RdsCard(instances=instances, count=len(instances))


def get_instance(identifier: str, region: str | None = None) -> RdsInstanceSummary | None:
    """Mirrors ec2_service.get_instance()'s shape -- idle_service/cost_service
    look up a single instance the same way regardless of resource type."""
    result = list_instances(region=region)
    for instance in result.instances:
        if instance.identifier == identifier:
            return instance
    return None
