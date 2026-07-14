from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

from app.services import ebs_service


def _fake_paginator(pages: list[dict]) -> MagicMock:
    paginator = MagicMock()
    paginator.paginate.return_value = pages
    return paginator


@patch("app.services.ebs_service.get_ec2_client")
def test_list_volumes_parses_attached_volume(mock_get_client: MagicMock) -> None:
    mock_client = MagicMock()
    mock_client.get_paginator.return_value = _fake_paginator(
        [
            {
                "Volumes": [
                    {
                        "VolumeId": "vol-123",
                        "Size": 100,
                        "VolumeType": "gp3",
                        "State": "in-use",
                        "AvailabilityZone": "us-east-1d",
                        "CreateTime": datetime(2026, 6, 1, tzinfo=timezone.utc),
                        "Attachments": [{"InstanceId": "i-123"}],
                        "Tags": [{"Key": "Name", "Value": "data-vol"}],
                    }
                ]
            }
        ]
    )
    mock_get_client.return_value = mock_client

    result = ebs_service.list_volumes()

    assert result.count == 1
    volume = result.volumes[0]
    assert volume.volume_id == "vol-123"
    assert volume.is_attached is True
    assert volume.attached_instance_ids == ["i-123"]
    assert volume.tags == {"Name": "data-vol"}


@patch("app.services.ebs_service.get_ec2_client")
def test_list_volumes_parses_unattached_volume(mock_get_client: MagicMock) -> None:
    mock_client = MagicMock()
    mock_client.get_paginator.return_value = _fake_paginator(
        [
            {
                "Volumes": [
                    {
                        "VolumeId": "vol-456",
                        "Size": 50,
                        "VolumeType": "gp2",
                        "State": "available",
                        "AvailabilityZone": "us-east-1d",
                        "CreateTime": datetime(2026, 6, 1, tzinfo=timezone.utc),
                        "Attachments": [],
                    }
                ]
            }
        ]
    )
    mock_get_client.return_value = mock_client

    result = ebs_service.list_volumes()

    assert result.volumes[0].is_attached is False
    assert result.volumes[0].attached_instance_ids == []


@patch("app.services.ebs_service.get_ec2_client")
def test_get_volume_returns_none_when_not_found(mock_get_client: MagicMock) -> None:
    mock_client = MagicMock()
    mock_client.get_paginator.return_value = _fake_paginator([{"Volumes": []}])
    mock_get_client.return_value = mock_client

    assert ebs_service.get_volume("vol-missing") is None
