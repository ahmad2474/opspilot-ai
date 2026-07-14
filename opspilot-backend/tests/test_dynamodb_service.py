from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

from app.services import dynamodb_service


def _fake_paginator(pages: list[dict]) -> MagicMock:
    paginator = MagicMock()
    paginator.paginate.return_value = pages
    return paginator


@patch("app.services.dynamodb_service.get_dynamodb_client")
def test_list_tables_parses_provisioned_table(mock_get_client: MagicMock) -> None:
    mock_client = MagicMock()
    mock_client.get_paginator.return_value = _fake_paginator([{"TableNames": ["tbl-1"]}])
    mock_client.describe_table.return_value = {
        "Table": {
            "TableStatus": "ACTIVE",
            "ItemCount": 10,
            "CreationDateTime": datetime(2026, 6, 1, tzinfo=timezone.utc),
            "ProvisionedThroughput": {"ReadCapacityUnits": 5, "WriteCapacityUnits": 5},
        }
    }
    mock_get_client.return_value = mock_client

    result = dynamodb_service.list_tables()

    assert result.count == 1
    table = result.tables[0]
    assert table.billing_mode == "PROVISIONED"
    assert table.read_capacity_units == 5
    assert table.write_capacity_units == 5


@patch("app.services.dynamodb_service.get_dynamodb_client")
def test_list_tables_parses_on_demand_table(mock_get_client: MagicMock) -> None:
    mock_client = MagicMock()
    mock_client.get_paginator.return_value = _fake_paginator([{"TableNames": ["tbl-2"]}])
    mock_client.describe_table.return_value = {
        "Table": {
            "TableStatus": "ACTIVE",
            "BillingModeSummary": {"BillingMode": "PAY_PER_REQUEST"},
            "ProvisionedThroughput": {"ReadCapacityUnits": 0, "WriteCapacityUnits": 0},
        }
    }
    mock_get_client.return_value = mock_client

    result = dynamodb_service.list_tables()

    assert result.tables[0].billing_mode == "PAY_PER_REQUEST"
    assert result.tables[0].read_capacity_units == 0


@patch("app.services.dynamodb_service.get_dynamodb_client")
def test_get_table_returns_none_when_not_found(mock_get_client: MagicMock) -> None:
    mock_client = MagicMock()
    mock_client.get_paginator.return_value = _fake_paginator([{"TableNames": []}])
    mock_get_client.return_value = mock_client

    assert dynamodb_service.get_table("missing") is None
