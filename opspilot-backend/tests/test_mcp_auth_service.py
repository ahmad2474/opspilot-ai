from unittest.mock import MagicMock, patch

import bcrypt

from app.services import mcp_auth_service


@patch("app.services.mcp_auth_service.get_settings")
@patch("app.services.mcp_auth_service.get_dynamodb_client")
def test_generate_token_returns_plaintext_and_stores_only_hash(
    mock_get_client: MagicMock, mock_get_settings: MagicMock
) -> None:
    mock_get_settings.return_value = MagicMock(opspilot_mcp_tokens_table="opspilot-mcp-tokens")
    mock_client = MagicMock()
    mock_get_client.return_value = mock_client

    plaintext, created_at = mcp_auth_service.generate_token()

    assert plaintext
    assert created_at

    mock_client.put_item.assert_called_once()
    call_kwargs = mock_client.put_item.call_args.kwargs
    assert call_kwargs["TableName"] == "opspilot-mcp-tokens"
    item = call_kwargs["Item"]
    assert item["id"]["S"] == "current"
    assert item["revoked"]["BOOL"] is False
    stored_hash = item["token_hash"]["S"]
    assert stored_hash != plaintext
    assert bcrypt.checkpw(plaintext.encode("utf-8"), stored_hash.encode("utf-8"))


@patch("app.services.mcp_auth_service.get_settings")
@patch("app.services.mcp_auth_service.get_dynamodb_client")
def test_generate_token_is_random_each_call(
    mock_get_client: MagicMock, mock_get_settings: MagicMock
) -> None:
    mock_get_settings.return_value = MagicMock(opspilot_mcp_tokens_table="opspilot-mcp-tokens")
    mock_get_client.return_value = MagicMock()

    token_a, _ = mcp_auth_service.generate_token()
    token_b, _ = mcp_auth_service.generate_token()

    assert token_a != token_b


@patch("app.services.mcp_auth_service.get_settings")
@patch("app.services.mcp_auth_service.get_dynamodb_client")
def test_revoke_token_returns_false_when_nothing_generated_yet(
    mock_get_client: MagicMock, mock_get_settings: MagicMock
) -> None:
    mock_get_settings.return_value = MagicMock(opspilot_mcp_tokens_table="opspilot-mcp-tokens")
    mock_client = MagicMock()
    mock_client.get_item.return_value = {}
    mock_get_client.return_value = mock_client

    assert mcp_auth_service.revoke_token() is False
    mock_client.update_item.assert_not_called()


@patch("app.services.mcp_auth_service.get_settings")
@patch("app.services.mcp_auth_service.get_dynamodb_client")
def test_revoke_token_returns_false_when_already_revoked(
    mock_get_client: MagicMock, mock_get_settings: MagicMock
) -> None:
    mock_get_settings.return_value = MagicMock(opspilot_mcp_tokens_table="opspilot-mcp-tokens")
    mock_client = MagicMock()
    mock_client.get_item.return_value = {
        "Item": {"id": {"S": "current"}, "revoked": {"BOOL": True}}
    }
    mock_get_client.return_value = mock_client

    assert mcp_auth_service.revoke_token() is False
    mock_client.update_item.assert_not_called()


@patch("app.services.mcp_auth_service.get_settings")
@patch("app.services.mcp_auth_service.get_dynamodb_client")
def test_revoke_token_flips_active_token_to_revoked(
    mock_get_client: MagicMock, mock_get_settings: MagicMock
) -> None:
    mock_get_settings.return_value = MagicMock(opspilot_mcp_tokens_table="opspilot-mcp-tokens")
    mock_client = MagicMock()
    mock_client.get_item.return_value = {
        "Item": {"id": {"S": "current"}, "revoked": {"BOOL": False}}
    }
    mock_get_client.return_value = mock_client

    assert mcp_auth_service.revoke_token() is True
    mock_client.update_item.assert_called_once()
    call_kwargs = mock_client.update_item.call_args.kwargs
    assert call_kwargs["ExpressionAttributeValues"][":true"] == {"BOOL": True}


@patch("app.services.mcp_auth_service.get_settings")
@patch("app.services.mcp_auth_service.get_dynamodb_client")
def test_get_status_no_token_ever_generated(
    mock_get_client: MagicMock, mock_get_settings: MagicMock
) -> None:
    mock_get_settings.return_value = MagicMock(opspilot_mcp_tokens_table="opspilot-mcp-tokens")
    mock_client = MagicMock()
    mock_client.get_item.return_value = {}
    mock_get_client.return_value = mock_client

    status = mcp_auth_service.get_status()

    assert status.has_active_token is False
    assert status.created_at is None
    assert status.revoked_at is None


@patch("app.services.mcp_auth_service.get_settings")
@patch("app.services.mcp_auth_service.get_dynamodb_client")
def test_get_status_active_token(
    mock_get_client: MagicMock, mock_get_settings: MagicMock
) -> None:
    mock_get_settings.return_value = MagicMock(opspilot_mcp_tokens_table="opspilot-mcp-tokens")
    mock_client = MagicMock()
    mock_client.get_item.return_value = {
        "Item": {
            "id": {"S": "current"},
            "revoked": {"BOOL": False},
            "created_at": {"S": "2026-07-11T00:00:00+00:00"},
        }
    }
    mock_get_client.return_value = mock_client

    status = mcp_auth_service.get_status()

    assert status.has_active_token is True
    assert status.created_at == "2026-07-11T00:00:00+00:00"
    assert status.revoked_at is None


@patch("app.services.mcp_auth_service.get_settings")
@patch("app.services.mcp_auth_service.get_dynamodb_client")
def test_is_token_valid_missing_plaintext_rejected(
    mock_get_client: MagicMock, mock_get_settings: MagicMock
) -> None:
    assert mcp_auth_service.is_token_valid(None) is False
    assert mcp_auth_service.is_token_valid("") is False
    mock_get_client.assert_not_called()


@patch("app.services.mcp_auth_service.get_settings")
@patch("app.services.mcp_auth_service.get_dynamodb_client")
def test_is_token_valid_no_token_generated_yet(
    mock_get_client: MagicMock, mock_get_settings: MagicMock
) -> None:
    mock_get_settings.return_value = MagicMock(opspilot_mcp_tokens_table="opspilot-mcp-tokens")
    mock_client = MagicMock()
    mock_client.get_item.return_value = {}
    mock_get_client.return_value = mock_client

    assert mcp_auth_service.is_token_valid("some-token") is False


@patch("app.services.mcp_auth_service.get_settings")
@patch("app.services.mcp_auth_service.get_dynamodb_client")
def test_is_token_valid_revoked_token_rejected(
    mock_get_client: MagicMock, mock_get_settings: MagicMock
) -> None:
    mock_get_settings.return_value = MagicMock(opspilot_mcp_tokens_table="opspilot-mcp-tokens")
    plaintext = "real-token"
    token_hash = bcrypt.hashpw(plaintext.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")
    mock_client = MagicMock()
    mock_client.get_item.return_value = {
        "Item": {
            "id": {"S": "current"},
            "revoked": {"BOOL": True},
            "token_hash": {"S": token_hash},
        }
    }
    mock_get_client.return_value = mock_client

    assert mcp_auth_service.is_token_valid(plaintext) is False


@patch("app.services.mcp_auth_service.get_settings")
@patch("app.services.mcp_auth_service.get_dynamodb_client")
def test_is_token_valid_wrong_token_rejected(
    mock_get_client: MagicMock, mock_get_settings: MagicMock
) -> None:
    mock_get_settings.return_value = MagicMock(opspilot_mcp_tokens_table="opspilot-mcp-tokens")
    token_hash = bcrypt.hashpw(b"the-real-token", bcrypt.gensalt()).decode("utf-8")
    mock_client = MagicMock()
    mock_client.get_item.return_value = {
        "Item": {
            "id": {"S": "current"},
            "revoked": {"BOOL": False},
            "token_hash": {"S": token_hash},
        }
    }
    mock_get_client.return_value = mock_client

    assert mcp_auth_service.is_token_valid("not-the-real-token") is False


@patch("app.services.mcp_auth_service.get_settings")
@patch("app.services.mcp_auth_service.get_dynamodb_client")
def test_is_token_valid_correct_active_token_accepted(
    mock_get_client: MagicMock, mock_get_settings: MagicMock
) -> None:
    mock_get_settings.return_value = MagicMock(opspilot_mcp_tokens_table="opspilot-mcp-tokens")
    plaintext = "the-real-token"
    token_hash = bcrypt.hashpw(plaintext.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")
    mock_client = MagicMock()
    mock_client.get_item.return_value = {
        "Item": {
            "id": {"S": "current"},
            "revoked": {"BOOL": False},
            "token_hash": {"S": token_hash},
        }
    }
    mock_get_client.return_value = mock_client

    assert mcp_auth_service.is_token_valid(plaintext) is True


@patch("app.services.mcp_auth_service.get_settings")
@patch("app.services.mcp_auth_service.get_dynamodb_client")
def test_is_token_valid_dynamodb_error_fails_closed(
    mock_get_client: MagicMock, mock_get_settings: MagicMock
) -> None:
    mock_get_settings.return_value = MagicMock(opspilot_mcp_tokens_table="opspilot-mcp-tokens")
    mock_client = MagicMock()
    mock_client.get_item.side_effect = RuntimeError("AWS is down")
    mock_get_client.return_value = mock_client

    assert mcp_auth_service.is_token_valid("some-token") is False
