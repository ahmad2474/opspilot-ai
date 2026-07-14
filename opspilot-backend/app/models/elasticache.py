"""ElastiCache models. Mirrors app/models/ebs.py's shape/style.

DescribeCacheClusters does not return resource tags inline (unlike EC2/EBS)
-- a separate ListTagsForResource call would be needed for tags, which this
build step deliberately skips (no tag-based feature depends on it yet, and
adding another AWS call per cluster just for tags isn't worth it for idle/
cost calc, which don't need tags at all).
"""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class ElastiCacheCluster(BaseModel):
    cache_cluster_id: str
    node_type: str = Field(description="e.g. cache.t3.micro")
    engine: str = Field(description="'redis' or 'memcached'")
    status: str = Field(description="e.g. available, creating, deleting")
    num_cache_nodes: int = 0
    create_time: datetime | None = None
    security_group_ids: list[str] = Field(
        default_factory=list,
        description="From SecurityGroups (VPC security groups) -- roadmap 3.7 "
        "relation-shaping, no new call. Legacy CacheSecurityGroups (EC2-Classic) "
        "not included -- not applicable to any VPC-only account.",
    )


class ElastiCacheClusterList(BaseModel):
    clusters: list[ElastiCacheCluster]
    count: int
