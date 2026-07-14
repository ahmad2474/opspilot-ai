from unittest.mock import MagicMock, patch

from app.services import audit_log_service


def _fake_paginator(pages: list[dict]) -> MagicMock:
    paginator = MagicMock()
    paginator.paginate.return_value = pages
    return paginator


@patch("app.services.audit_log_service.get_settings")
@patch("app.services.audit_log_service.get_dynamodb_client")
def test_write_entry_puts_item_with_expected_fields(
    mock_get_client: MagicMock, mock_get_settings: MagicMock
) -> None:
    mock_get_settings.return_value = MagicMock(opspilot_audit_log_table="opspilot-audit-log")
    mock_client = MagicMock()
    mock_get_client.return_value = mock_client

    entry = audit_log_service.write_entry("mcp_token_generated", actor_email="admin@example.com")

    mock_client.put_item.assert_called_once()
    call_kwargs = mock_client.put_item.call_args.kwargs
    assert call_kwargs["TableName"] == "opspilot-audit-log"
    item = call_kwargs["Item"]
    assert item["id"]["S"] == entry.id
    assert item["action"]["S"] == "mcp_token_generated"
    assert item["actor_email"]["S"] == "admin@example.com"
    assert "detail" not in item


@patch("app.services.audit_log_service.get_settings")
@patch("app.services.audit_log_service.get_dynamodb_client")
def test_write_entry_includes_detail_when_provided(
    mock_get_client: MagicMock, mock_get_settings: MagicMock
) -> None:
    mock_get_settings.return_value = MagicMock(opspilot_audit_log_table="opspilot-audit-log")
    mock_client = MagicMock()
    mock_get_client.return_value = mock_client

    audit_log_service.write_entry(
        "mcp_token_revoked", actor_email="admin@example.com", detail="manual revoke"
    )

    item = mock_client.put_item.call_args.kwargs["Item"]
    assert item["detail"]["S"] == "manual revoke"


@patch("app.services.audit_log_service.get_settings")
@patch("app.services.audit_log_service.get_dynamodb_client")
def test_list_recent_entries_sorts_newest_first_and_respects_limit(
    mock_get_client: MagicMock, mock_get_settings: MagicMock
) -> None:
    mock_get_settings.return_value = MagicMock(opspilot_audit_log_table="opspilot-audit-log")

    def _item(item_id: str, created_at: str) -> dict:
        return {
            "id": {"S": item_id},
            "action": {"S": "mcp_token_generated"},
            "actor_email": {"S": "admin@example.com"},
            "created_at": {"S": created_at},
        }

    mock_client = MagicMock()
    mock_client.get_paginator.return_value = _fake_paginator(
        [
            {
                "Items": [
                    _item("older", "2026-07-01T00:00:00Z"),
                    _item("newest", "2026-07-08T00:00:00Z"),
                    _item("middle", "2026-07-05T00:00:00Z"),
                ]
            }
        ]
    )
    mock_get_client.return_value = mock_client

    results = audit_log_service.list_recent_entries(limit=2)

    assert [r.id for r in results] == ["newest", "middle"]


@patch("app.services.audit_log_service.get_settings")
@patch("app.services.audit_log_service.get_dynamodb_client")
def test_list_recent_entries_includes_detail_when_present(
    mock_get_client: MagicMock, mock_get_settings: MagicMock
) -> None:
    mock_get_settings.return_value = MagicMock(opspilot_audit_log_table="opspilot-audit-log")

    mock_client = MagicMock()
    mock_client.get_paginator.return_value = _fake_paginator(
        [
            {
                "Items": [
                    {
                        "id": {"S": "entry-1"},
                        "action": {"S": "mcp_token_revoked"},
                        "actor_email": {"S": "admin@example.com"},
                        "created_at": {"S": "2026-07-08T00:00:00Z"},
                        "detail": {"S": "manual revoke"},
                    }
                ]
            }
        ]
    )
    mock_get_client.return_value = mock_client

    results = audit_log_service.list_recent_entries()

    assert results[0].detail == "manual revoke"
