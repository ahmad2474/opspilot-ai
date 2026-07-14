"""Redshift cluster business logic. No boto3 calls anywhere else in the
app. Mirrors ebs_service.py's shape/style.
"""
from __future__ import annotations

from app.aws.client import get_redshift_client
from app.models.redshift import RedshiftCluster, RedshiftClusterList


def _to_summary(raw: dict) -> RedshiftCluster:
    return RedshiftCluster(
        cluster_identifier=raw["ClusterIdentifier"],
        node_type=raw.get("NodeType", "unknown"),
        status=raw.get("ClusterStatus", "unknown"),
        number_of_nodes=raw.get("NumberOfNodes", 1),
        create_time=raw.get("ClusterCreateTime"),
        vpc_security_group_ids=[
            g["VpcSecurityGroupId"]
            for g in raw.get("VpcSecurityGroups", [])
            if g.get("VpcSecurityGroupId")
        ],
        vpc_id=raw.get("VpcId"),
    )


def list_clusters(region: str | None = None) -> RedshiftClusterList:
    client = get_redshift_client(region=region)
    paginator = client.get_paginator("describe_clusters")
    clusters: list[RedshiftCluster] = []
    for page in paginator.paginate():
        for raw in page.get("Clusters", []):
            clusters.append(_to_summary(raw))
    return RedshiftClusterList(clusters=clusters, count=len(clusters))


def get_cluster(
    cluster_identifier: str, region: str | None = None
) -> RedshiftCluster | None:
    """List-then-filter, same convention every other service module uses
    for its get_*() -- see lambda_service.get_function's docstring."""
    result = list_clusters(region=region)
    for cluster in result.clusters:
        if cluster.cluster_identifier == cluster_identifier:
            return cluster
    return None
