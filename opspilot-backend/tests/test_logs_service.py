from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

from app.services import logs_service


def _fake_paginator(pages: list[dict]) -> MagicMock:
    paginator = MagicMock()
    paginator.paginate.return_value = pages
    return paginator


@patch("app.services.logs_service.get_logs_client")
def test_flags_log_groups_with_no_retention_policy(mock_get_client: MagicMock) -> None:
    mock_client = MagicMock()
    mock_client.get_paginator.return_value = _fake_paginator(
        [
            {
                "logGroups": [
                    {
                        "logGroupName": "/aws/lambda/no-retention",
                        "storedBytes": 12345,
                        "creationTime": 1750000000000,
                        # no retentionInDays key at all
                    },
                    {
                        "logGroupName": "/aws/lambda/has-retention",
                        "storedBytes": 999,
                        "retentionInDays": 30,
                        "creationTime": 1750000000000,
                    },
                ]
            }
        ]
    )
    mock_get_client.return_value = mock_client

    result = logs_service.check_log_retention()

    assert result.total_log_groups_checked == 2
    assert result.flagged_count == 1
    assert result.total_stored_bytes_at_risk == 12345
    finding = result.findings[0]
    assert finding.log_group_name == "/aws/lambda/no-retention"
    assert finding.stored_bytes == 12345
    assert finding.created_at == datetime.fromtimestamp(1750000000, tz=timezone.utc)


@patch("app.services.logs_service.get_logs_client")
def test_no_findings_when_every_group_has_retention(mock_get_client: MagicMock) -> None:
    mock_client = MagicMock()
    mock_client.get_paginator.return_value = _fake_paginator(
        [{"logGroups": [{"logGroupName": "/ok", "storedBytes": 1, "retentionInDays": 7}]}]
    )
    mock_get_client.return_value = mock_client

    result = logs_service.check_log_retention()

    assert result.flagged_count == 0
    assert result.findings == []
    assert result.total_log_groups_checked == 1
    assert result.total_stored_bytes_at_risk == 0


@patch("app.services.logs_service.get_logs_client")
def test_finding_with_no_creation_time_reports_none(mock_get_client: MagicMock) -> None:
    mock_client = MagicMock()
    mock_client.get_paginator.return_value = _fake_paginator(
        [{"logGroups": [{"logGroupName": "/no-created", "storedBytes": 5}]}]
    )
    mock_get_client.return_value = mock_client

    result = logs_service.check_log_retention()

    assert result.findings[0].created_at is None
