"""Tests for GET /resources/ec2 -- specifically the graceful-degradation
behavior (roadmap 3.4: "never blank the dashboard"). Each of cpu/idle/cost
is fetched independently and any one of them failing must not 500 the
whole response or take the other two down with it.
"""
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

from app.main import app
from app.models.cloudwatch import CpuUtilizationSummary
from app.models.cost import CostEstimate, DateRange
from app.models.ec2 import EC2Instance, EC2InstanceList
from app.models.idle import IdleCheckResult

client = TestClient(app)


def _running_instance() -> EC2InstanceList:
    return EC2InstanceList(
        instances=[
            EC2Instance(
                instance_id="i-123",
                instance_type="t3.micro",
                state="running",
                availability_zone="us-east-1d",
                launch_time=datetime(2026, 7, 1, tzinfo=timezone.utc),
                tags={},
            )
        ],
        count=1,
    )


def _idle_result() -> IdleCheckResult:
    return IdleCheckResult(
        resource_id="i-123",
        resource_type="ec2",
        window_days=7,
        is_idle=True,
        idle_since="2026-07-03",
        idle_days=7,
        younger_than_window=False,
    )


def _cost_result() -> CostEstimate:
    return CostEstimate(
        resource_id="i-123",
        resource_type="ec2",
        date_range=DateRange(
            start=datetime(2026, 7, 1, tzinfo=timezone.utc),
            end=datetime(2026, 7, 10, tzinfo=timezone.utc),
        ),
        method="list_price",
        hourly_rate=0.0104,
        projected_monthly=7.59,
        incurred_so_far=2.25,
    )


def _cpu_result() -> CpuUtilizationSummary:
    return CpuUtilizationSummary(
        instance_id="i-123",
        lookback_hours=3,
        datapoints=[],
        average_cpu_percent=0.2,
        max_cpu_percent=0.3,
        breached_80_percent=False,
    )


@patch("app.api.routes.resources.cost_service.estimate_cost")
@patch("app.api.routes.resources.idle_service.check_idle")
@patch("app.api.routes.resources.cloudwatch_service.get_cpu_utilization")
@patch("app.api.routes.resources.ec2_service.list_instances")
def test_idle_failure_degrades_to_none_without_500(
    mock_list: MagicMock,
    mock_cpu: MagicMock,
    mock_idle: MagicMock,
    mock_cost: MagicMock,
    auth_headers: dict[str, str],
) -> None:
    mock_list.return_value = _running_instance()
    mock_cpu.return_value = _cpu_result()
    mock_idle.side_effect = RuntimeError("CloudWatch throttled")
    mock_cost.return_value = _cost_result()

    response = client.get("/resources/ec2", headers=auth_headers)

    assert response.status_code == 200
    card = response.json()["ec2"][0]
    assert card["idle"] is None
    assert card["cost"] is not None
    assert card["cost"]["projected_monthly"] == 7.59
    assert card["cpu"] is not None


@patch("app.api.routes.resources.cost_service.estimate_cost")
@patch("app.api.routes.resources.idle_service.check_idle")
@patch("app.api.routes.resources.cloudwatch_service.get_cpu_utilization")
@patch("app.api.routes.resources.ec2_service.list_instances")
def test_cost_failure_degrades_to_none_without_500(
    mock_list: MagicMock,
    mock_cpu: MagicMock,
    mock_idle: MagicMock,
    mock_cost: MagicMock,
    auth_headers: dict[str, str],
) -> None:
    mock_list.return_value = _running_instance()
    mock_cpu.return_value = _cpu_result()
    mock_idle.return_value = _idle_result()
    mock_cost.side_effect = ValueError("Pricing API returned no on-demand price")

    response = client.get("/resources/ec2", headers=auth_headers)

    assert response.status_code == 200
    card = response.json()["ec2"][0]
    assert card["cost"] is None
    assert card["idle"] is not None
    assert card["idle"]["is_idle"] is True
    assert card["cpu"] is not None


@patch("app.api.routes.resources.cost_service.estimate_cost")
@patch("app.api.routes.resources.idle_service.check_idle")
@patch("app.api.routes.resources.cloudwatch_service.get_cpu_utilization")
@patch("app.api.routes.resources.ec2_service.list_instances")
def test_cpu_failure_degrades_to_none_without_500(
    mock_list: MagicMock,
    mock_cpu: MagicMock,
    mock_idle: MagicMock,
    mock_cost: MagicMock,
    auth_headers: dict[str, str],
) -> None:
    """The pre-existing CPU lookup sits in the same per-instance loop as
    idle/cost -- it must degrade the same way, not 500 the whole response."""
    mock_list.return_value = _running_instance()
    mock_cpu.side_effect = RuntimeError("CloudWatch throttled")
    mock_idle.return_value = _idle_result()
    mock_cost.return_value = _cost_result()

    response = client.get("/resources/ec2", headers=auth_headers)

    assert response.status_code == 200
    card = response.json()["ec2"][0]
    assert card["cpu"] is None
    assert card["idle"] is not None
    assert card["cost"] is not None


@patch("app.api.routes.resources.ec2_service.list_instances")
def test_stopped_instance_has_no_cpu_idle_or_cost(
    mock_list: MagicMock, auth_headers: dict[str, str]
) -> None:
    instances = _running_instance()
    instances.instances[0].state = "stopped"
    mock_list.return_value = instances

    response = client.get("/resources/ec2", headers=auth_headers)

    assert response.status_code == 200
    card = response.json()["ec2"][0]
    assert card["cpu"] is None
    assert card["idle"] is None
    assert card["cost"] is None


def test_resources_route_requires_session() -> None:
    response = client.get("/resources/ec2")
    assert response.status_code == 401
