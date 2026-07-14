"""Tests for app/services/account_service.py (roadmap Section 5's
Settings tab "Connected account" section).
"""
from unittest.mock import MagicMock, patch

import pytest

from app.core.config import Settings
from app.services import account_service


@pytest.fixture(autouse=True)
def _clear_account_cache():
    """get_connected_account() is @lru_cache'd at process level (the
    account identity never changes for the life of a process using static
    credentials) -- tests must not leak that cache into each other."""
    account_service.get_connected_account.cache_clear()
    yield
    account_service.get_connected_account.cache_clear()


@patch("app.services.account_service.get_settings")
@patch("app.services.account_service.get_sts_client")
def test_get_connected_account_returns_account_id_and_region_only(
    mock_get_sts_client: MagicMock, mock_get_settings: MagicMock
) -> None:
    mock_client = MagicMock()
    mock_client.get_caller_identity.return_value = {
        "Account": "123456789012",
        "Arn": "arn:aws:iam::123456789012:user/opspilot",
        "UserId": "AIDAEXAMPLE",
    }
    mock_get_sts_client.return_value = mock_client
    mock_get_settings.return_value = Settings(aws_region="us-west-2")

    result = account_service.get_connected_account()

    assert result.account_id == "123456789012"
    assert result.region == "us-west-2"
    assert not hasattr(result, "arn")
    assert not hasattr(result, "user_id")
    assert set(result.model_dump().keys()) == {"account_id", "region"}


@patch("app.services.account_service.get_settings")
@patch("app.services.account_service.get_sts_client")
def test_get_connected_account_is_cached_across_calls(
    mock_get_sts_client: MagicMock, mock_get_settings: MagicMock
) -> None:
    mock_client = MagicMock()
    mock_client.get_caller_identity.return_value = {"Account": "123456789012"}
    mock_get_sts_client.return_value = mock_client
    mock_get_settings.return_value = Settings(aws_region="us-east-1")

    first = account_service.get_connected_account()
    second = account_service.get_connected_account()

    assert first is second
    mock_client.get_caller_identity.assert_called_once()
