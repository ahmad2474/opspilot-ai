from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

from app.services import rds_service


def _fake_paginator(pages: list[dict]) -> MagicMock:
    paginator = MagicMock()
    paginator.paginate.return_value = pages
    return paginator


@patch("app.services.rds_service.get_rds_client")
def test_list_instances_captures_create_time(mock_get_client: MagicMock) -> None:
    mock_client = MagicMock()
    mock_client.get_paginator.return_value = _fake_paginator(
        [
            {
                "DBInstances": [
                    {
                        "DBInstanceIdentifier": "db-1",
                        "Engine": "postgres",
                        "DBInstanceClass": "db.t3.micro",
                        "DBInstanceStatus": "available",
                        "InstanceCreateTime": datetime(2026, 6, 1, tzinfo=timezone.utc),
                    }
                ]
            }
        ]
    )
    mock_get_client.return_value = mock_client

    result = rds_service.list_instances()

    assert result.count == 1
    assert result.instances[0].instance_create_time == datetime(2026, 6, 1, tzinfo=timezone.utc)


@patch("app.services.rds_service.get_rds_client")
def test_list_instances_parses_relation_fields(mock_get_client: MagicMock) -> None:
    """Roadmap 3.7 -- vpc_security_group_ids from VpcSecurityGroups,
    subnet_ids/vpc_id from DBSubnetGroup."""
    mock_client = MagicMock()
    mock_client.get_paginator.return_value = _fake_paginator(
        [
            {
                "DBInstances": [
                    {
                        "DBInstanceIdentifier": "db-2",
                        "Engine": "postgres",
                        "DBInstanceClass": "db.t3.micro",
                        "DBInstanceStatus": "available",
                        "VpcSecurityGroups": [
                            {"VpcSecurityGroupId": "sg-1"},
                            {"VpcSecurityGroupId": "sg-2"},
                        ],
                        "DBSubnetGroup": {
                            "VpcId": "vpc-1",
                            "Subnets": [
                                {"SubnetIdentifier": "subnet-1"},
                                {"SubnetIdentifier": "subnet-2"},
                            ],
                        },
                    }
                ]
            }
        ]
    )
    mock_get_client.return_value = mock_client

    instance = rds_service.list_instances().instances[0]

    assert instance.vpc_security_group_ids == ["sg-1", "sg-2"]
    assert instance.subnet_ids == ["subnet-1", "subnet-2"]
    assert instance.vpc_id == "vpc-1"


@patch("app.services.rds_service.get_rds_client")
def test_list_instances_relation_fields_default_when_absent(mock_get_client: MagicMock) -> None:
    mock_client = MagicMock()
    mock_client.get_paginator.return_value = _fake_paginator(
        [
            {
                "DBInstances": [
                    {
                        "DBInstanceIdentifier": "db-3",
                        "Engine": "postgres",
                        "DBInstanceClass": "db.t3.micro",
                        "DBInstanceStatus": "available",
                    }
                ]
            }
        ]
    )
    mock_get_client.return_value = mock_client

    instance = rds_service.list_instances().instances[0]

    assert instance.vpc_security_group_ids == []
    assert instance.subnet_ids == []
    assert instance.vpc_id is None


@patch("app.services.rds_service.get_rds_client")
def test_get_instance_returns_none_when_not_found(mock_get_client: MagicMock) -> None:
    mock_client = MagicMock()
    mock_client.get_paginator.return_value = _fake_paginator([{"DBInstances": []}])
    mock_get_client.return_value = mock_client

    assert rds_service.get_instance("db-missing") is None


@patch("app.services.rds_service.get_rds_client")
def test_get_instance_matches_by_identifier(mock_get_client: MagicMock) -> None:
    mock_client = MagicMock()
    mock_client.get_paginator.return_value = _fake_paginator(
        [
            {
                "DBInstances": [
                    {
                        "DBInstanceIdentifier": "db-1",
                        "Engine": "mysql",
                        "DBInstanceClass": "db.t3.micro",
                        "DBInstanceStatus": "available",
                    }
                ]
            }
        ]
    )
    mock_get_client.return_value = mock_client

    instance = rds_service.get_instance("db-1")
    assert instance is not None
    assert instance.engine == "mysql"
