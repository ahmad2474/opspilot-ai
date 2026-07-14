from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

from app.services import redshift_service


def _fake_paginator(pages: list[dict]) -> MagicMock:
    paginator = MagicMock()
    paginator.paginate.return_value = pages
    return paginator


@patch("app.services.redshift_service.get_redshift_client")
def test_list_clusters_parses_fields(mock_get_client: MagicMock) -> None:
    mock_client = MagicMock()
    mock_client.get_paginator.return_value = _fake_paginator(
        [
            {
                "Clusters": [
                    {
                        "ClusterIdentifier": "cl-1",
                        "NodeType": "dc2.large",
                        "ClusterStatus": "available",
                        "NumberOfNodes": 2,
                        "ClusterCreateTime": datetime(2026, 6, 1, tzinfo=timezone.utc),
                    }
                ]
            }
        ]
    )
    mock_get_client.return_value = mock_client

    result = redshift_service.list_clusters()

    assert result.count == 1
    assert result.clusters[0].cluster_identifier == "cl-1"
    assert result.clusters[0].number_of_nodes == 2


@patch("app.services.redshift_service.get_redshift_client")
def test_list_clusters_parses_relation_fields(mock_get_client: MagicMock) -> None:
    """Roadmap 3.7 -- vpc_security_group_ids from VpcSecurityGroups
    (multi-security-group case), vpc_id from VpcId."""
    mock_client = MagicMock()
    mock_client.get_paginator.return_value = _fake_paginator(
        [
            {
                "Clusters": [
                    {
                        "ClusterIdentifier": "cl-2",
                        "NodeType": "dc2.large",
                        "ClusterStatus": "available",
                        "NumberOfNodes": 2,
                        "VpcSecurityGroups": [
                            {"VpcSecurityGroupId": "sg-1"},
                            {"VpcSecurityGroupId": "sg-2"},
                        ],
                        "VpcId": "vpc-1",
                    }
                ]
            }
        ]
    )
    mock_get_client.return_value = mock_client

    cluster = redshift_service.list_clusters().clusters[0]

    assert cluster.vpc_security_group_ids == ["sg-1", "sg-2"]
    assert cluster.vpc_id == "vpc-1"


@patch("app.services.redshift_service.get_redshift_client")
def test_list_clusters_relation_fields_default_when_absent(mock_get_client: MagicMock) -> None:
    mock_client = MagicMock()
    mock_client.get_paginator.return_value = _fake_paginator(
        [
            {
                "Clusters": [
                    {
                        "ClusterIdentifier": "cl-3",
                        "NodeType": "dc2.large",
                        "ClusterStatus": "available",
                        "NumberOfNodes": 1,
                    }
                ]
            }
        ]
    )
    mock_get_client.return_value = mock_client

    cluster = redshift_service.list_clusters().clusters[0]

    assert cluster.vpc_security_group_ids == []
    assert cluster.vpc_id is None


@patch("app.services.redshift_service.get_redshift_client")
def test_get_cluster_returns_none_when_not_found(mock_get_client: MagicMock) -> None:
    mock_client = MagicMock()
    mock_client.get_paginator.return_value = _fake_paginator([{"Clusters": []}])
    mock_get_client.return_value = mock_client

    assert redshift_service.get_cluster("cl-missing") is None
