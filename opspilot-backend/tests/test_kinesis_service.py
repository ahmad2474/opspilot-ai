from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

from app.services import kinesis_service


def _fake_paginator(pages: list[dict]) -> MagicMock:
    paginator = MagicMock()
    paginator.paginate.return_value = pages
    return paginator


@patch("app.services.kinesis_service.get_kinesis_client")
def test_list_streams_fills_in_shard_count_from_describe(mock_get_client: MagicMock) -> None:
    mock_client = MagicMock()
    mock_client.get_paginator.return_value = _fake_paginator([{"StreamNames": ["stream-1"]}])
    mock_client.describe_stream_summary.return_value = {
        "StreamDescriptionSummary": {
            "StreamARN": "arn:aws:kinesis:us-east-1:123:stream/stream-1",
            "StreamStatus": "ACTIVE",
            "OpenShardCount": 4,
            "RetentionPeriodHours": 24,
            "StreamCreationTimestamp": datetime(2026, 6, 1, tzinfo=timezone.utc),
        }
    }
    mock_get_client.return_value = mock_client

    result = kinesis_service.list_streams()

    assert result.count == 1
    stream = result.streams[0]
    assert stream.stream_name == "stream-1"
    assert stream.open_shard_count == 4


@patch("app.services.kinesis_service.get_kinesis_client")
def test_get_stream_returns_none_when_not_found(mock_get_client: MagicMock) -> None:
    mock_client = MagicMock()
    mock_client.get_paginator.return_value = _fake_paginator([{"StreamNames": []}])
    mock_get_client.return_value = mock_client

    assert kinesis_service.get_stream("missing") is None
