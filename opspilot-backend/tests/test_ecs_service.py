from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

from app.models.cloudwatch import MetricDatapoint
from app.services import ecs_service

NOW = datetime(2026, 9, 1, tzinfo=timezone.utc)


def _fake_paginator(pages: list[dict]) -> MagicMock:
    paginator = MagicMock()
    paginator.paginate.return_value = pages
    return paginator


def _task(
    task_arn: str = "arn:aws:ecs:us-east-1:123:task/my-cluster/abc",
    family: str = "my-family",
    launch_type: str = "FARGATE",
) -> dict:
    return {
        "taskArn": task_arn,
        "taskDefinitionArn": f"arn:aws:ecs:us-east-1:123:task-definition/{family}:3",
        "launchType": launch_type,
        "lastStatus": "RUNNING",
    }


def _service(name: str = "my-service", desired_count: int = 2, running_count: int = 2) -> dict:
    return {
        "serviceName": name,
        "desiredCount": desired_count,
        "runningCount": running_count,
        "launchType": "FARGATE",
    }


def _points(value: float | None) -> list[MetricDatapoint]:
    if value is None:
        return []
    return [MetricDatapoint(timestamp=NOW, average=value, maximum=None, unit="None")]


def _points_by_day(values: list[float]) -> list[MetricDatapoint]:
    """One datapoint per day, oldest first, `len(values)` days ending at
    NOW -- for tests that need real multi-day data instead of `_points`'
    single fake datapoint (which can't exercise the "every day, not
    average" rule at all)."""
    return [
        MetricDatapoint(
            timestamp=NOW - timedelta(days=len(values) - 1 - i),
            average=v,
            maximum=None,
            unit="None",
        )
        for i, v in enumerate(values)
    ]


def _cluster_response(container_insights: str = "enabled") -> dict:
    return {
        "clusters": [
            {
                "clusterArn": "arn:aws:ecs:us-east-1:123:cluster/my-cluster",
                "clusterName": "my-cluster",
                "status": "ACTIVE",
                "settings": [{"name": "containerInsights", "value": container_insights}],
            }
        ]
    }


def _make_client(
    container_insights: str = "enabled",
    tasks: list[dict] | None = None,
    services: list[dict] | None = None,
) -> MagicMock:
    tasks = tasks or []
    services = services or []
    client = MagicMock()
    client.describe_clusters.return_value = _cluster_response(container_insights)
    client.get_paginator.side_effect = lambda op: {
        "list_tasks": _fake_paginator([{"taskArns": [t["taskArn"] for t in tasks]}]),
        "list_services": _fake_paginator(
            [{"serviceArns": [s["serviceName"] for s in services]}]
        ),
    }[op]
    client.describe_tasks.return_value = {"tasks": tasks}
    client.describe_services.return_value = {"services": services}
    return client


@patch("app.services.ecs_service.get_ecs_client")
def test_cluster_not_found_returns_graceful_result(mock_get_client: MagicMock) -> None:
    client = MagicMock()
    client.describe_clusters.return_value = {"clusters": []}
    mock_get_client.return_value = client

    result = ecs_service.check_container_idle("missing-cluster", 7)

    assert result.container_insights_enabled is False
    assert "not found" in result.container_insights_note
    assert result.findings == []
    assert result.total_tasks_checked == 0


@patch("app.services.ecs_service.get_ecs_client")
def test_container_insights_disabled_returns_opt_in_note_but_still_counts_inventory(
    mock_get_client: MagicMock,
) -> None:
    client = _make_client(container_insights="disabled", tasks=[_task()], services=[_service()])
    mock_get_client.return_value = client

    result = ecs_service.check_container_idle("my-cluster", 7)

    assert result.container_insights_enabled is False
    assert "Container Insights" in result.container_insights_note
    assert result.findings == []
    assert result.total_tasks_checked == 1
    assert result.total_services_checked == 1


@patch("app.services.ecs_service.cloudwatch_service.get_daily_datapoints")
@patch("app.services.ecs_service.get_ecs_client")
def test_fargate_task_idle_utilization_flagged(
    mock_get_client: MagicMock, mock_get_points: MagicMock
) -> None:
    client = _make_client(tasks=[_task()])
    mock_get_client.return_value = client

    def _side_effect(*, metric_name: str, **kwargs: object) -> list[MetricDatapoint]:
        if metric_name in ("CpuUtilized", "MemoryUtilized"):
            return _points(1.0)  # near-zero utilized
        return _points(100.0)  # reserved

    mock_get_points.side_effect = _side_effect

    result = ecs_service.check_container_idle("my-cluster", 7)

    assert result.container_insights_enabled is True
    assert len(result.findings) == 1
    finding = result.findings[0]
    assert finding.finding_type == "task_idle_utilization"
    assert finding.task_definition_family == "my-family"
    assert finding.cpu_utilization_percent == 1.0
    assert finding.memory_utilization_percent == 1.0


@patch("app.services.ecs_service.cloudwatch_service.get_daily_datapoints")
@patch("app.services.ecs_service.get_ecs_client")
def test_fargate_task_over_provisioned_flagged(
    mock_get_client: MagicMock, mock_get_points: MagicMock
) -> None:
    client = _make_client(tasks=[_task()])
    mock_get_client.return_value = client

    def _side_effect(*, metric_name: str, **kwargs: object) -> list[MetricDatapoint]:
        if metric_name in ("CpuUtilized", "MemoryUtilized"):
            return _points(10.0)  # 10% of reserved -- over-provisioned, not idle
        return _points(100.0)

    mock_get_points.side_effect = _side_effect

    result = ecs_service.check_container_idle("my-cluster", 7)

    assert len(result.findings) == 1
    assert result.findings[0].finding_type == "task_over_provisioned"


@patch("app.services.ecs_service.cloudwatch_service.get_daily_datapoints")
@patch("app.services.ecs_service.get_ecs_client")
def test_day_one_burst_prevents_false_idle_verdict(
    mock_get_client: MagicMock, mock_get_points: MagicMock
) -> None:
    """Code-review finding, the important one: a task busy on day 1 and
    idle for the rest of a 7-day window must never get averaged into a
    false "idle" verdict (roadmap Section 3.1's own worked example, and
    the exact rule idle_service.check_idle_via_metrics already enforces
    for every other metric-driven check in this app). CPU is 90% reserved
    on day 1, then 1% every day after -- a naive window-average would land
    around 14%, well under the idle threshold, and wrongly flag this
    idle. Per-day evaluation must not."""
    client = _make_client(tasks=[_task()])
    mock_get_client.return_value = client

    def _side_effect(*, metric_name: str, **kwargs: object) -> list[MetricDatapoint]:
        if metric_name in ("CpuUtilized", "MemoryUtilized"):
            return _points_by_day([90.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0])
        return _points_by_day([100.0] * 7)

    mock_get_points.side_effect = _side_effect

    result = ecs_service.check_container_idle("my-cluster", 7)

    # Day 1's 90% also fails the (higher) over-provisioned threshold, so
    # this should produce no finding at all, not just "not idle."
    assert result.findings == []


@patch("app.services.ecs_service.cloudwatch_service.get_daily_datapoints")
@patch("app.services.ecs_service.get_ecs_client")
def test_healthy_utilization_not_flagged(
    mock_get_client: MagicMock, mock_get_points: MagicMock
) -> None:
    client = _make_client(tasks=[_task()])
    mock_get_client.return_value = client

    def _side_effect(*, metric_name: str, **kwargs: object) -> list[MetricDatapoint]:
        if metric_name in ("CpuUtilized", "MemoryUtilized"):
            return _points(80.0)  # healthy, well-used
        return _points(100.0)

    mock_get_points.side_effect = _side_effect

    result = ecs_service.check_container_idle("my-cluster", 7)

    assert result.findings == []


@patch("app.services.ecs_service.cloudwatch_service.get_daily_datapoints")
@patch("app.services.ecs_service.get_ecs_client")
def test_ec2_launch_type_tasks_are_skipped(
    mock_get_client: MagicMock, mock_get_points: MagicMock
) -> None:
    """Scoped to Fargate only -- EC2-launch-type ECS tasks run on EC2
    instances this app already idle-checks separately."""
    client = _make_client(tasks=[_task(launch_type="EC2")])
    mock_get_client.return_value = client
    mock_get_points.return_value = _points(1.0)

    result = ecs_service.check_container_idle("my-cluster", 7)

    assert result.findings == []
    mock_get_points.assert_not_called()


@patch("app.services.ecs_service.cloudwatch_service.get_daily_datapoints")
@patch("app.services.ecs_service.get_ecs_client")
def test_missing_utilization_data_is_not_flagged(
    mock_get_client: MagicMock, mock_get_points: MagicMock
) -> None:
    """No Container Insights datapoints yet for this family -- never guess."""
    client = _make_client(tasks=[_task()])
    mock_get_client.return_value = client
    mock_get_points.return_value = []

    result = ecs_service.check_container_idle("my-cluster", 7)

    assert result.findings == []


@patch("app.services.ecs_service.cloudwatch_service.get_daily_datapoints")
@patch("app.services.ecs_service.get_ecs_client")
def test_service_standby_capacity_flagged(
    mock_get_client: MagicMock, mock_get_points: MagicMock
) -> None:
    client = _make_client(services=[_service(desired_count=2, running_count=2)])
    mock_get_client.return_value = client
    mock_get_points.side_effect = lambda *, metric_name, **kwargs: (
        _points(1.0) if metric_name in ("CpuUtilized", "MemoryUtilized") else _points(100.0)
    )

    result = ecs_service.check_container_idle("my-cluster", 7)

    assert len(result.findings) == 1
    finding = result.findings[0]
    assert finding.finding_type == "service_standby_capacity"
    assert finding.service_name == "my-service"
    assert finding.desired_count == 2


@patch("app.services.ecs_service.get_ecs_client")
def test_service_with_zero_desired_count_is_skipped(mock_get_client: MagicMock) -> None:
    client = _make_client(services=[_service(desired_count=0, running_count=0)])
    mock_get_client.return_value = client

    result = ecs_service.check_container_idle("my-cluster", 7)

    assert result.findings == []


@patch("app.services.ecs_service.get_ecs_client")
def test_list_clusters_reports_container_insights_setting(mock_get_client: MagicMock) -> None:
    client = MagicMock()
    client.get_paginator.return_value = _fake_paginator(
        [{"clusterArns": ["arn:aws:ecs:us-east-1:123:cluster/my-cluster"]}]
    )
    client.describe_clusters.return_value = _cluster_response("enhanced")
    mock_get_client.return_value = client

    result = ecs_service.list_clusters()

    assert result.count == 1
    assert result.clusters[0].container_insights == "enhanced"
    assert result.clusters[0].cluster_name == "my-cluster"
