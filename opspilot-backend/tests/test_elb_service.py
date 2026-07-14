from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from app.services import elb_service


def _fake_paginator(pages: list[dict]) -> MagicMock:
    paginator = MagicMock()
    paginator.paginate.return_value = pages
    return paginator


@patch("app.services.elb_service.get_elb_client")
@patch("app.services.elb_service.get_elbv2_client")
def test_list_load_balancers_merges_v2_and_classic(
    mock_get_elbv2: MagicMock, mock_get_elb: MagicMock
) -> None:
    mock_v2_client = MagicMock()
    mock_v2_client.get_paginator.return_value = _fake_paginator(
        [
            {
                "LoadBalancers": [
                    {
                        "LoadBalancerName": "my-alb",
                        "LoadBalancerArn": (
                            "arn:aws:elasticloadbalancing:us-east-1:123:"
                            "loadbalancer/app/my-alb/50dc6c495c0c9188"
                        ),
                        "Type": "application",
                        "State": {"Code": "active"},
                        "DNSName": "my-alb.elb.amazonaws.com",
                        "Scheme": "internet-facing",
                        "CreatedTime": datetime(2026, 6, 1, tzinfo=timezone.utc),
                    },
                    {
                        "LoadBalancerName": "my-gwlb",
                        "LoadBalancerArn": (
                            "arn:aws:elasticloadbalancing:us-east-1:123:"
                            "loadbalancer/gwy/my-gwlb/abc"
                        ),
                        "Type": "gateway",
                        "State": {"Code": "active"},
                    },
                ]
            }
        ]
    )
    mock_get_elbv2.return_value = mock_v2_client

    mock_classic_client = MagicMock()
    mock_classic_client.get_paginator.return_value = _fake_paginator(
        [
            {
                "LoadBalancerDescriptions": [
                    {
                        "LoadBalancerName": "my-classic-elb",
                        "DNSName": "my-classic-elb.elb.amazonaws.com",
                        "Scheme": "internet-facing",
                        "CreatedTime": datetime(2026, 5, 1, tzinfo=timezone.utc),
                    }
                ]
            }
        ]
    )
    mock_get_elb.return_value = mock_classic_client

    result = elb_service.list_load_balancers()

    # Gateway LB is deliberately excluded (not part of the 15 in-scope types).
    assert result.count == 2
    names = {lb.name for lb in result.load_balancers}
    assert names == {"my-alb", "my-classic-elb"}

    alb = next(lb for lb in result.load_balancers if lb.name == "my-alb")
    assert alb.lb_type == "application"
    classic = next(lb for lb in result.load_balancers if lb.name == "my-classic-elb")
    assert classic.lb_type == "classic"
    assert classic.arn is None


@patch("app.services.elb_service.get_elb_client")
@patch("app.services.elb_service.get_elbv2_client")
def test_list_load_balancers_parses_relation_fields(
    mock_get_elbv2: MagicMock, mock_get_elb: MagicMock
) -> None:
    """Roadmap 3.7 -- security_group_ids/subnet_ids/vpc_id for both the
    elbv2 (ALB/NLB) and classic paths."""
    mock_v2_client = MagicMock()
    mock_v2_client.get_paginator.return_value = _fake_paginator(
        [
            {
                "LoadBalancers": [
                    {
                        "LoadBalancerName": "my-alb-2",
                        "LoadBalancerArn": (
                            "arn:aws:elasticloadbalancing:us-east-1:123:"
                            "loadbalancer/app/my-alb-2/abc123"
                        ),
                        "Type": "application",
                        "State": {"Code": "active"},
                        "SecurityGroups": ["sg-1", "sg-2"],
                        "AvailabilityZones": [
                            {"SubnetId": "subnet-1"},
                            {"SubnetId": "subnet-2"},
                        ],
                        "VpcId": "vpc-1",
                    }
                ]
            }
        ]
    )
    mock_get_elbv2.return_value = mock_v2_client

    mock_classic_client = MagicMock()
    mock_classic_client.get_paginator.return_value = _fake_paginator(
        [
            {
                "LoadBalancerDescriptions": [
                    {
                        "LoadBalancerName": "my-classic-elb-2",
                        "SecurityGroups": ["sg-classic"],
                        "Subnets": ["subnet-classic"],
                        "VPCId": "vpc-classic",
                    }
                ]
            }
        ]
    )
    mock_get_elb.return_value = mock_classic_client

    result = elb_service.list_load_balancers()

    alb = next(lb for lb in result.load_balancers if lb.name == "my-alb-2")
    assert alb.security_group_ids == ["sg-1", "sg-2"]
    assert alb.subnet_ids == ["subnet-1", "subnet-2"]
    assert alb.vpc_id == "vpc-1"

    classic = next(lb for lb in result.load_balancers if lb.name == "my-classic-elb-2")
    assert classic.security_group_ids == ["sg-classic"]
    assert classic.subnet_ids == ["subnet-classic"]
    assert classic.vpc_id == "vpc-classic"


def test_cloudwatch_dimension_alb_parses_truncated_arn() -> None:
    from app.models.elb import LoadBalancer

    lb = LoadBalancer(
        name="my-alb",
        lb_type="application",
        arn="arn:aws:elasticloadbalancing:us-east-1:123:loadbalancer/app/my-alb/50dc6c495c0c9188",
        state="active",
    )
    namespace, dimension_name, dimension_value = elb_service.cloudwatch_dimension(lb)
    assert namespace == "AWS/ApplicationELB"
    assert dimension_name == "LoadBalancer"
    assert dimension_value == "app/my-alb/50dc6c495c0c9188"


def test_cloudwatch_dimension_classic_uses_name() -> None:
    from app.models.elb import LoadBalancer

    lb = LoadBalancer(name="my-classic-elb", lb_type="classic", arn=None, state="active")
    namespace, dimension_name, dimension_value = elb_service.cloudwatch_dimension(lb)
    assert namespace == "AWS/ELB"
    assert dimension_name == "LoadBalancerName"
    assert dimension_value == "my-classic-elb"


def test_cloudwatch_dimension_v2_without_arn_raises() -> None:
    from app.models.elb import LoadBalancer

    lb = LoadBalancer(name="my-nlb", lb_type="network", arn=None, state="active")
    with pytest.raises(ValueError, match="no ARN"):
        elb_service.cloudwatch_dimension(lb)
