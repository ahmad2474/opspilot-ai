from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

from app.models.dashboard import RdsCard
from app.models.ebs import EbsVolume, EbsVolumeList
from app.services import snapshot_service

NOW = datetime(2026, 9, 1, tzinfo=timezone.utc)


def _fake_paginator(pages: list[dict]) -> MagicMock:
    paginator = MagicMock()
    paginator.paginate.return_value = pages
    return paginator


def _volume(volume_id: str) -> EbsVolume:
    return EbsVolume(
        volume_id=volume_id,
        size_gb=10,
        volume_type="gp3",
        state="in-use",
        availability_zone="us-east-1a",
        create_time=NOW - timedelta(days=200),
        attached_instance_ids=[],
        tags={},
    )


@patch("app.services.snapshot_service.datetime")
@patch("app.services.snapshot_service.ebs_service.list_volumes")
@patch("app.services.snapshot_service.get_ec2_client")
def test_ebs_orphaned_snapshot_flagged(
    mock_get_client: MagicMock, mock_list_volumes: MagicMock, mock_datetime: MagicMock
) -> None:
    mock_datetime.now.return_value = NOW
    mock_client = MagicMock()
    mock_client.get_paginator.return_value = _fake_paginator(
        [
            {
                "Snapshots": [
                    {
                        "SnapshotId": "snap-orphan",
                        "VolumeId": "vol-deleted",
                        "StartTime": NOW - timedelta(days=10),
                        "VolumeSize": 20,
                    }
                ]
            }
        ]
    )
    mock_get_client.return_value = mock_client
    mock_list_volumes.return_value = EbsVolumeList(volumes=[], count=0)

    result = snapshot_service.check_snapshot_sprawl("ebs", 30, retention_mode="days")

    assert result.orphaned_count == 1
    assert result.beyond_retention_count == 0
    finding = result.findings[0]
    assert finding.finding_type == "orphaned"
    assert finding.source_resource_id == "vol-deleted"
    assert finding.size_gb == 20


@patch("app.services.snapshot_service.datetime")
@patch("app.services.snapshot_service.ebs_service.list_volumes")
@patch("app.services.snapshot_service.get_ec2_client")
def test_ebs_snapshot_beyond_days_retention_flagged(
    mock_get_client: MagicMock, mock_list_volumes: MagicMock, mock_datetime: MagicMock
) -> None:
    mock_datetime.now.return_value = NOW
    mock_client = MagicMock()
    mock_client.get_paginator.return_value = _fake_paginator(
        [
            {
                "Snapshots": [
                    {
                        "SnapshotId": "snap-old",
                        "VolumeId": "vol-1",
                        "StartTime": NOW - timedelta(days=100),
                        "VolumeSize": 5,
                    },
                    {
                        "SnapshotId": "snap-recent",
                        "VolumeId": "vol-1",
                        "StartTime": NOW - timedelta(days=1),
                        "VolumeSize": 5,
                    },
                ]
            }
        ]
    )
    mock_get_client.return_value = mock_client
    mock_list_volumes.return_value = EbsVolumeList(volumes=[_volume("vol-1")], count=1)

    result = snapshot_service.check_snapshot_sprawl("ebs", 30, retention_mode="days")

    assert result.orphaned_count == 0
    assert result.beyond_retention_count == 1
    beyond = [f for f in result.findings if f.finding_type == "beyond_retention"]
    assert beyond[0].snapshot_id == "snap-old"


@patch("app.services.snapshot_service.datetime")
@patch("app.services.snapshot_service.ebs_service.list_volumes")
@patch("app.services.snapshot_service.get_ec2_client")
def test_ebs_snapshot_count_retention_keeps_n_most_recent(
    mock_get_client: MagicMock, mock_list_volumes: MagicMock, mock_datetime: MagicMock
) -> None:
    mock_datetime.now.return_value = NOW
    snapshots = [
        {
            "SnapshotId": f"snap-{i}",
            "VolumeId": "vol-1",
            "StartTime": NOW - timedelta(days=i),
            "VolumeSize": 5,
        }
        for i in range(5)
    ]
    mock_client = MagicMock()
    mock_client.get_paginator.return_value = _fake_paginator([{"Snapshots": snapshots}])
    mock_get_client.return_value = mock_client
    mock_list_volumes.return_value = EbsVolumeList(volumes=[_volume("vol-1")], count=1)

    result = snapshot_service.check_snapshot_sprawl("ebs", 2, retention_mode="count")

    # Keep the 2 most recent (snap-0, snap-1), flag the rest (snap-2..4).
    assert result.beyond_retention_count == 3
    flagged_ids = {f.snapshot_id for f in result.findings if f.finding_type == "beyond_retention"}
    assert flagged_ids == {"snap-2", "snap-3", "snap-4"}


@patch("app.services.snapshot_service.datetime")
@patch("app.services.snapshot_service.rds_service.list_instances")
@patch("app.services.snapshot_service.get_rds_client")
def test_rds_orphaned_snapshot_flagged(
    mock_get_client: MagicMock, mock_list_instances: MagicMock, mock_datetime: MagicMock
) -> None:
    mock_datetime.now.return_value = NOW
    mock_client = MagicMock()
    mock_client.get_paginator.return_value = _fake_paginator(
        [
            {
                "DBSnapshots": [
                    {
                        "DBSnapshotIdentifier": "rds-snap-orphan",
                        "DBInstanceIdentifier": "db-deleted",
                        "SnapshotCreateTime": NOW - timedelta(days=5),
                        "AllocatedStorage": 100,
                    }
                ]
            }
        ]
    )
    mock_get_client.return_value = mock_client
    mock_list_instances.return_value = RdsCard(instances=[], count=0)

    result = snapshot_service.check_snapshot_sprawl("rds", 30)

    assert result.orphaned_count == 1
    assert result.findings[0].resource_type == "rds"
    assert result.findings[0].source_resource_id == "db-deleted"


def test_unsupported_resource_type_raises() -> None:
    with pytest.raises(snapshot_service.UnsupportedSnapshotResourceTypeError):
        snapshot_service.check_snapshot_sprawl("ec2", 30)
