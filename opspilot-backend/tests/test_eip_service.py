from unittest.mock import MagicMock, patch

from app.services import eip_service


@patch("app.services.eip_service.get_ec2_client")
def test_list_addresses_associated(mock_get_client: MagicMock) -> None:
    mock_client = MagicMock()
    mock_client.describe_addresses.return_value = {
        "Addresses": [
            {
                "AllocationId": "eipalloc-123",
                "PublicIp": "1.2.3.4",
                "Domain": "vpc",
                "AssociationId": "eipassoc-123",
                "InstanceId": "i-123",
                "NetworkInterfaceId": "eni-123",
            }
        ]
    }
    mock_get_client.return_value = mock_client

    result = eip_service.list_addresses()

    assert result.count == 1
    address = result.addresses[0]
    assert address.is_associated is True
    assert address.resource_id == "eipalloc-123"


@patch("app.services.eip_service.get_ec2_client")
def test_list_addresses_unassociated(mock_get_client: MagicMock) -> None:
    mock_client = MagicMock()
    mock_client.describe_addresses.return_value = {
        "Addresses": [
            {
                "AllocationId": "eipalloc-456",
                "PublicIp": "5.6.7.8",
                "Domain": "vpc",
            }
        ]
    }
    mock_get_client.return_value = mock_client

    result = eip_service.list_addresses()

    assert result.addresses[0].is_associated is False


@patch("app.services.eip_service.get_ec2_client")
def test_resource_id_falls_back_to_public_ip_for_ec2_classic(mock_get_client: MagicMock) -> None:
    mock_client = MagicMock()
    mock_client.describe_addresses.return_value = {
        "Addresses": [{"PublicIp": "9.9.9.9", "Domain": "standard"}]
    }
    mock_get_client.return_value = mock_client

    result = eip_service.list_addresses()

    assert result.addresses[0].allocation_id is None
    assert result.addresses[0].resource_id == "9.9.9.9"


@patch("app.services.eip_service.get_ec2_client")
def test_get_address_matches_by_resource_id(mock_get_client: MagicMock) -> None:
    mock_client = MagicMock()
    mock_client.describe_addresses.return_value = {
        "Addresses": [{"AllocationId": "eipalloc-789", "PublicIp": "1.1.1.1", "Domain": "vpc"}]
    }
    mock_get_client.return_value = mock_client

    assert eip_service.get_address("eipalloc-789") is not None
    assert eip_service.get_address("eipalloc-missing") is None
