from __future__ import annotations

from app.aws.client import get_rds_client
from app.models.dashboard import RdsCard, RdsInstanceSummary


def list_instances() -> RdsCard:
    client = get_rds_client()
    paginator = client.get_paginator("describe_db_instances")
    instances: list[RdsInstanceSummary] = []
    for page in paginator.paginate():
        for raw in page.get("DBInstances", []):
            instances.append(
                RdsInstanceSummary(
                    identifier=raw["DBInstanceIdentifier"],
                    engine=raw.get("Engine", "unknown"),
                    instance_class=raw.get("DBInstanceClass", "unknown"),
                    status=raw.get("DBInstanceStatus", "unknown"),
                )
            )
    return RdsCard(instances=instances, count=len(instances))
