from unittest.mock import MagicMock, patch

from app.services import lambda_service


def _fake_paginator(pages: list[dict]) -> MagicMock:
    paginator = MagicMock()
    paginator.paginate.return_value = pages
    return paginator


@patch("app.services.lambda_service.get_lambda_client")
def test_list_functions_parses_memory_size(mock_get_client: MagicMock) -> None:
    mock_client = MagicMock()
    mock_client.get_paginator.return_value = _fake_paginator(
        [
            {
                "Functions": [
                    {
                        "FunctionName": "fn-1",
                        "Runtime": "python3.12",
                        "LastModified": "2026-06-01T00:00:00.000+0000",
                        "MemorySize": 256,
                    }
                ]
            }
        ]
    )
    mock_get_client.return_value = mock_client

    result = lambda_service.list_functions()

    assert result.count == 1
    assert result.functions[0].memory_size_mb == 256


@patch("app.services.lambda_service.get_lambda_client")
def test_list_functions_parses_relation_fields_vpc_attached(mock_get_client: MagicMock) -> None:
    """Roadmap 3.7 -- security_group_ids/subnet_ids/vpc_id from VpcConfig,
    and role_name stripped down to just the bare role name (never the full
    ARN, which embeds the AWS account ID)."""
    mock_client = MagicMock()
    mock_client.get_paginator.return_value = _fake_paginator(
        [
            {
                "Functions": [
                    {
                        "FunctionName": "fn-vpc",
                        "Runtime": "python3.12",
                        "MemorySize": 512,
                        "Role": "arn:aws:iam::123456789012:role/my-lambda-role",
                        "VpcConfig": {
                            "SecurityGroupIds": ["sg-1", "sg-2"],
                            "SubnetIds": ["subnet-1", "subnet-2"],
                            "VpcId": "vpc-1",
                        },
                    }
                ]
            }
        ]
    )
    mock_get_client.return_value = mock_client

    function = lambda_service.list_functions().functions[0]

    assert function.security_group_ids == ["sg-1", "sg-2"]
    assert function.subnet_ids == ["subnet-1", "subnet-2"]
    assert function.vpc_id == "vpc-1"
    # Security: only the bare role name survives, never the ARN.
    assert function.role_name == "my-lambda-role"


@patch("app.services.lambda_service.get_lambda_client")
def test_list_functions_parses_relation_fields_no_vpc(mock_get_client: MagicMock) -> None:
    """A non-VPC-attached function (no VpcConfig key at all, the common
    case) must default to empty/None, not raise on a missing key."""
    mock_client = MagicMock()
    mock_client.get_paginator.return_value = _fake_paginator(
        [
            {
                "Functions": [
                    {
                        "FunctionName": "fn-no-vpc",
                        "Runtime": "python3.12",
                        "MemorySize": 128,
                        "Role": "arn:aws:iam::123456789012:role/another-role",
                    }
                ]
            }
        ]
    )
    mock_get_client.return_value = mock_client

    function = lambda_service.list_functions().functions[0]

    assert function.security_group_ids == []
    assert function.subnet_ids == []
    assert function.vpc_id is None
    assert function.role_name == "another-role"


@patch("app.services.lambda_service.get_lambda_client")
def test_list_functions_role_name_none_when_role_absent(mock_get_client: MagicMock) -> None:
    mock_client = MagicMock()
    mock_client.get_paginator.return_value = _fake_paginator(
        [{"Functions": [{"FunctionName": "fn-no-role", "MemorySize": 128}]}]
    )
    mock_get_client.return_value = mock_client

    function = lambda_service.list_functions().functions[0]

    assert function.role_name is None


@patch("app.services.lambda_service.get_lambda_client")
def test_get_function_returns_none_when_not_found(mock_get_client: MagicMock) -> None:
    mock_client = MagicMock()
    mock_client.get_paginator.return_value = _fake_paginator([{"Functions": []}])
    mock_get_client.return_value = mock_client

    assert lambda_service.get_function("fn-missing") is None


@patch("app.services.lambda_service.get_lambda_client")
def test_get_function_matches_by_name(mock_get_client: MagicMock) -> None:
    mock_client = MagicMock()
    mock_client.get_paginator.return_value = _fake_paginator(
        [{"Functions": [{"FunctionName": "fn-1", "MemorySize": 128}]}]
    )
    mock_get_client.return_value = mock_client

    result = lambda_service.get_function("fn-1")

    assert result is not None
    assert result.name == "fn-1"
