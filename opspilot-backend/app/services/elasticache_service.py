"""ElastiCache business logic. No boto3 calls anywhere else in the app.

Mirrors ebs_service.py's shape/style. Cache *nodes* inside a replication
group each get their own CacheClusterId (e.g. "my-redis-001") -- this
lists at the cluster (node) level, matching the CloudWatch CurrConnections
metric's own dimension (CacheClusterId), not the replication-group level.
"""
from __future__ import annotations

from app.aws.client import get_elasticache_client
from app.models.elasticache import ElastiCacheCluster, ElastiCacheClusterList


def list_clusters(region: str | None = None) -> ElastiCacheClusterList:
    client = get_elasticache_client(region=region)
    paginator = client.get_paginator("describe_cache_clusters")
    clusters: list[ElastiCacheCluster] = []
    for page in paginator.paginate():
        for raw in page.get("CacheClusters", []):
            clusters.append(
                ElastiCacheCluster(
                    cache_cluster_id=raw["CacheClusterId"],
                    node_type=raw.get("CacheNodeType", "unknown"),
                    engine=raw.get("Engine", "unknown"),
                    status=raw.get("CacheClusterStatus", "unknown"),
                    num_cache_nodes=raw.get("NumCacheNodes", 0),
                    create_time=raw.get("CacheClusterCreateTime"),
                    security_group_ids=[
                        g["SecurityGroupId"]
                        for g in raw.get("SecurityGroups", [])
                        if g.get("SecurityGroupId")
                    ],
                )
            )
    return ElastiCacheClusterList(clusters=clusters, count=len(clusters))


def get_cluster(
    cache_cluster_id: str, region: str | None = None
) -> ElastiCacheCluster | None:
    result = list_clusters(region=region)
    for cluster in result.clusters:
        if cluster.cache_cluster_id == cache_cluster_id:
            return cluster
    return None
