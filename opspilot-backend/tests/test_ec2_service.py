from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

from app.services import ec2_service


def _fake_paginator(pages: list[dict]) -> MagicMock:
    paginator = MagicMock()
    paginator.paginate.return_value = pages
    return paginator


@patch("app.services.ec2_service.get_ec2_client")
def test_list_instances_parses_response(mock_get_client: MagicMock) -> None:
    mock_client = MagicMock()
    mock_client.get_paginator.return_value = _fake_paginator(
        [
            {
                "Reservations": [
                    {
                        "Instances": [
                            {
                                "InstanceId": "i-123",
                                "InstanceType": "t3.micro",
                                "State": {"Name": "running"},
                                "Placement": {"AvailabilityZone": "us-east-1d"},
                                "PublicIpAddress": "1.2.3.4",
                                "PrivateIpAddress": "10.0.0.1",
                                "LaunchTime": datetime(2026, 7, 4, 23, 8, 27, tzinfo=timezone.utc),
                                "Tags": [{"Key": "Project", "Value": "opspilot"}],
                            }
                        ]
                    }
                ]
            }
        ]
    )
    mock_get_client.return_value = mock_client

    result = ec2_service.list_instances()

    assert result.count == 1
    instance = result.instances[0]
    assert instance.instance_id == "i-123"
    assert instance.state == "running"
    assert instance.tags == {"Project": "opspilot"}


@patch("app.services.ec2_service.get_ec2_client")
def test_list_instances_empty_account(mock_get_client: MagicMock) -> None:
    mock_client = MagicMock()
    mock_client.get_paginator.return_value = _fake_paginator([{"Reservations": []}])
    mock_get_client.return_value = mock_client

    result = ec2_service.list_instances()

    assert result.count == 0
    assert result.instances == []


@patch("app.services.ec2_service.get_ec2_client")
def test_get_status_check_no_data_returns_insufficient(mock_get_client: MagicMock) -> None:
    mock_client = MagicMock()
    mock_client.describe_instance_status.return_value = {"InstanceStatuses": []}
    mock_get_client.return_value = mock_client

    result = ec2_service.get_status_check("i-missing")

    assert result.system_status == "insufficient-data"
    assert result.instance_status == "insufficient-data"


@patch("app.services.ec2_service.get_ec2_client")
def test_get_status_check_ok(mock_get_client: MagicMock) -> None:
    mock_client = MagicMock()
    mock_client.describe_instance_status.return_value = {
        "InstanceStatuses": [
            {
                "InstanceState": {"Name": "running"},
                "SystemStatus": {"Status": "ok"},
                "InstanceStatus": {"Status": "ok"},
                "Events": [],
            }
        ]
    }
    mock_get_client.return_value = mock_client

    result = ec2_service.get_status_check("i-123")

    assert result.system_status == "ok"
    assert result.instance_status == "ok"
    assert result.scheduled_events == []
