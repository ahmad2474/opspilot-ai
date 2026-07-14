from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

from app.services import elasticache_service


def _fake_paginator(pages: list[dict]) -> MagicMock:
    paginator = MagicMock()
    paginator.paginate.return_value = pages
    return paginator


@patch("app.services.elasticache_service.get_elasticache_client")
def test_list_clusters_parses_fields(mock_get_client: MagicMock) -> None:
    mock_client = MagicMock()
    mock_client.get_paginator.return_value = _fake_paginator(
        [
            {
                "CacheClusters": [
                    {
                        "CacheClusterId": "cache-123",
                        "CacheNodeType": "cache.t3.micro",
                        "Engine": "redis",
                        "CacheClusterStatus": "available",
                        "NumCacheNodes": 1,
                        "CacheClusterCreateTime": datetime(2026, 6, 1, tzinfo=timezone.utc),
                    }
                ]
            }
        ]
    )
    mock_get_client.return_value = mock_client

    result = elasticache_service.list_clusters()

    assert result.count == 1
    assert result.clusters[0].cache_cluster_id == "cache-123"
    assert result.clusters[0].engine == "redis"


@patch("app.services.elasticache_service.get_elasticache_client")
def test_list_clusters_parses_security_group_ids(mock_get_client: MagicMock) -> None:
    """Roadmap 3.7 -- security_group_ids from SecurityGroups (VPC security
    groups), including the multi-security-group case."""
    mock_client = MagicMock()
    mock_client.get_paginator.return_value = _fake_paginator(
        [
            {
                "CacheClusters": [
                    {
                        "CacheClusterId": "cache-456",
                        "CacheNodeType": "cache.t3.micro",
                        "Engine": "redis",
                        "CacheClusterStatus": "available",
                        "NumCacheNodes": 1,
                        "SecurityGroups": [
                            {"SecurityGroupId": "sg-1"},
                            {"SecurityGroupId": "sg-2"},
                        ],
                    }
                ]
            }
        ]
    )
    mock_get_client.return_value = mock_client

    cluster = elasticache_service.list_clusters().clusters[0]

    assert cluster.security_group_ids == ["sg-1", "sg-2"]


@patch("app.services.elasticache_service.get_elasticache_client")
def test_list_clusters_security_group_ids_default_empty(mock_get_client: MagicMock) -> None:
    mock_client = MagicMock()
    mock_client.get_paginator.return_value = _fake_paginator(
        [
            {
                "CacheClusters": [
                    {
                        "CacheClusterId": "cache-789",
                        "CacheNodeType": "cache.t3.micro",
                        "Engine": "redis",
                        "CacheClusterStatus": "available",
                        "NumCacheNodes": 1,
                    }
                ]
            }
        ]
    )
    mock_get_client.return_value = mock_client

    cluster = elasticache_service.list_clusters().clusters[0]

    assert cluster.security_group_ids == []


@patch("app.services.elasticache_service.get_elasticache_client")
def test_get_cluster_returns_none_when_not_found(mock_get_client: MagicMock) -> None:
    mock_client = MagicMock()
    mock_client.get_paginator.return_value = _fake_paginator([{"CacheClusters": []}])
    mock_get_client.return_value = mock_client

    assert elasticache_service.get_cluster("cache-missing") is None
