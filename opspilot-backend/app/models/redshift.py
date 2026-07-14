"""Redshift cluster models. Mirrors app/models/ebs.py's shape/style."""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class RedshiftCluster(BaseModel):
    cluster_identifier: str
    node_type: str = Field(description="e.g. dc2.large, ra3.xlplus")
    status: str = Field(description="e.g. available, creating, deleting, paused")
    number_of_nodes: int = 1
    create_time: datetime | None = None
    vpc_security_group_ids: list[str] = Field(
        default_factory=list,
        description="From VpcSecurityGroups -- roadmap 3.7 relation-shaping, no new call.",
    )
    vpc_id: str | None = Field(
        default=None,
        description="From VpcId -- None for an EC2-Classic cluster. Roadmap 3.7 "
        "relation-shaping, no new call. ClusterSubnetGroupName is a name only "
        "(not individual subnet IDs) without a separate DescribeClusterSubnetGroups "
        "call, so no 'subnet' relation is built for Redshift -- documented gap.",
    )


class RedshiftClusterList(BaseModel):
    clusters: list[RedshiftCluster]
    count: int
