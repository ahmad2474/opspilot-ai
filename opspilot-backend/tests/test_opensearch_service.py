from unittest.mock import MagicMock, patch

from app.services import opensearch_service


@patch("app.services.opensearch_service.get_opensearch_client")
def test_list_domains_parses_fields_and_derives_account_id(mock_get_client: MagicMock) -> None:
    mock_client = MagicMock()
    mock_client.list_domain_names.return_value = {
        "DomainNames": [{"DomainName": "my-domain", "EngineType": "OpenSearch"}]
    }
    mock_client.describe_domain.return_value = {
        "DomainStatus": {
            "DomainName": "my-domain",
            "ARN": "arn:aws:es:us-east-1:123456789012:domain/my-domain",
            "Created": True,
            "ClusterConfig": {"InstanceType": "r6g.large.search", "InstanceCount": 2},
        }
    }
    mock_get_client.return_value = mock_client

    result = opensearch_service.list_domains()

    assert result.count == 1
    domain = result.domains[0]
    assert domain.domain_name == "my-domain"
    assert domain.instance_type == "r6g.large.search"
    assert domain.instance_count == 2
    assert domain.account_id == "123456789012"


@patch("app.services.opensearch_service.get_opensearch_client")
def test_list_domains_parses_relation_fields_vpc_attached(mock_get_client: MagicMock) -> None:
    """Roadmap 3.7 -- security_group_ids/subnet_ids/vpc_id from VPCOptions
    for a VPC-attached (private) domain."""
    mock_client = MagicMock()
    mock_client.list_domain_names.return_value = {
        "DomainNames": [{"DomainName": "vpc-domain", "EngineType": "OpenSearch"}]
    }
    mock_client.describe_domain.return_value = {
        "DomainStatus": {
            "DomainName": "vpc-domain",
            "ARN": "arn:aws:es:us-east-1:123456789012:domain/vpc-domain",
            "Created": True,
            "ClusterConfig": {"InstanceType": "r6g.large.search", "InstanceCount": 2},
            "VPCOptions": {
                "SecurityGroupIds": ["sg-1", "sg-2"],
                "SubnetIds": ["subnet-1", "subnet-2"],
                "VPCId": "vpc-1",
            },
        }
    }
    mock_get_client.return_value = mock_client

    domain = opensearch_service.list_domains().domains[0]

    assert domain.security_group_ids == ["sg-1", "sg-2"]
    assert domain.subnet_ids == ["subnet-1", "subnet-2"]
    assert domain.vpc_id == "vpc-1"


@patch("app.services.opensearch_service.get_opensearch_client")
def test_list_domains_relation_fields_default_for_public_domain(
    mock_get_client: MagicMock,
) -> None:
    """A public (non-VPC) domain has no VPCOptions key at all -- must
    default to empty/None, not raise."""
    mock_client = MagicMock()
    mock_client.list_domain_names.return_value = {
        "DomainNames": [{"DomainName": "public-domain", "EngineType": "OpenSearch"}]
    }
    mock_client.describe_domain.return_value = {
        "DomainStatus": {
            "DomainName": "public-domain",
            "ARN": "arn:aws:es:us-east-1:123456789012:domain/public-domain",
            "Created": True,
            "ClusterConfig": {"InstanceType": "r6g.large.search", "InstanceCount": 1},
        }
    }
    mock_get_client.return_value = mock_client

    domain = opensearch_service.list_domains().domains[0]

    assert domain.security_group_ids == []
    assert domain.subnet_ids == []
    assert domain.vpc_id is None


@patch("app.services.opensearch_service.get_opensearch_client")
def test_get_domain_returns_none_when_not_found(mock_get_client: MagicMock) -> None:
    mock_client = MagicMock()
    mock_client.list_domain_names.return_value = {"DomainNames": []}
    mock_get_client.return_value = mock_client

    assert opensearch_service.get_domain("missing") is None
