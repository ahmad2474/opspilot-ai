"""ECS container idle/rightsizing check (roadmap phase 2 Section 1.2, "Batch B").

check_container_idle(cluster, days) is task-level, not cluster-level, per
the roadmap's own instruction -- see app/models/ecs.py's module docstring
for the one real caveat on what "task-level" means given AWS's actual
Container Insights metric granularity (per TaskDefinitionFamily, not per
running task ARN).

Scoped to Fargate tasks only for the idle/over-provisioned sub-checks
(roadmap: "Fargate tasks running with near-zero CPU/memory utilization"),
matching this app's existing "task-level, not cluster-level" framing --
EC2-launch-type ECS tasks run on capacity this app doesn't separately price
(the EC2 instances themselves are a different, already-covered resource
type via `check_idle(resource_type="ec2", ...)`), so idle-checking them a
second time here would double-count the same underlying waste signal.

Container Insights is a per-cluster opt-in the account owner has to enable
(same "one-time-activation, graceful, non-fatal" shape as this codebase's
existing Redshift/Kinesis handling in scan_service.py) -- checked directly
via the cluster's own 'containerInsights' setting (DescribeClusters
include=['SETTINGS']), not by making a metrics call and catching a
failure. If disabled, this returns container_insights_enabled=False with a
clear how-to-opt-in note and an empty findings list (inventory counts are
still populated, since those come from ecs:List*/Describe* calls that don't
need Container Insights at all) -- never an unhandled exception.
"""
from __future__ import annotations

import logging
from datetime import date

from app.aws.client import get_ecs_client
from app.models.ecs import (
    EcsClusterList,
    EcsClusterSummary,
    EcsContainerFinding,
    EcsContainerIdleReport,
)
from app.services import cloudwatch_service, idle_service

logger = logging.getLogger("app.services.ecs")

FARGATE_LAUNCH_TYPE = "FARGATE"

# Demo-scope thresholds, same "not derived from real traffic analysis"
# caveat as every threshold in idle_service.py.
CONTAINER_IDLE_UTILIZATION_PERCENT = 2.0
CONTAINER_OVER_PROVISIONED_UTILIZATION_PERCENT = 20.0

CONTAINER_INSIGHTS_OPT_IN_NOTE = (
    "CloudWatch Container Insights is not enabled on this cluster -- enable it "
    "(ECS console -> Clusters -> <cluster> -> Update Cluster -> Container "
    "Insights, or `aws ecs update-cluster-settings --cluster <name> --settings "
    "name=containerInsights,value=enabled`) to unlock idle/rightsizing findings. "
    "This is a one-time, per-cluster opt-in, same shape as Redshift/Kinesis's "
    "account-level service activation elsewhere in this app."
)

# DescribeClusters/DescribeServices accept a bounded number of identifiers
# per call -- batched rather than assumed to always fit in one request.
_DESCRIBE_CLUSTERS_BATCH_SIZE = 100
_DESCRIBE_TASKS_BATCH_SIZE = 100
_DESCRIBE_SERVICES_BATCH_SIZE = 10


def _cluster_container_insights(raw: dict) -> str:
    for setting in raw.get("settings", []) or []:
        if setting.get("name") == "containerInsights":
            return setting.get("value", "disabled")
    return "disabled"


def list_clusters(region: str | None = None) -> EcsClusterList:
    client = get_ecs_client(region=region)
    arns: list[str] = []
    paginator = client.get_paginator("list_clusters")
    for page in paginator.paginate():
        arns.extend(page.get("clusterArns", []))

    clusters: list[EcsClusterSummary] = []
    for i in range(0, len(arns), _DESCRIBE_CLUSTERS_BATCH_SIZE):
        batch = arns[i : i + _DESCRIBE_CLUSTERS_BATCH_SIZE]
        response = client.describe_clusters(clusters=batch, include=["SETTINGS"])
        for raw in response.get("clusters", []):
            clusters.append(
                EcsClusterSummary(
                    cluster_arn=raw["clusterArn"],
                    cluster_name=raw.get("clusterName", raw["clusterArn"]),
                    status=raw.get("status", "unknown"),
                    running_tasks_count=raw.get("runningTasksCount", 0),
                    pending_tasks_count=raw.get("pendingTasksCount", 0),
                    active_services_count=raw.get("activeServicesCount", 0),
                    container_insights=_cluster_container_insights(raw),
                )
            )
    return EcsClusterList(clusters=clusters, count=len(clusters))


def _get_cluster(cluster: str, client) -> dict | None:
    response = client.describe_clusters(clusters=[cluster], include=["SETTINGS"])
    raw_clusters = response.get("clusters", [])
    return raw_clusters[0] if raw_clusters else None


def _list_running_task_arns(client, cluster: str) -> list[str]:
    arns: list[str] = []
    paginator = client.get_paginator("list_tasks")
    for page in paginator.paginate(cluster=cluster, desiredStatus="RUNNING"):
        arns.extend(page.get("taskArns", []))
    return arns


def _describe_tasks(client, cluster: str, task_arns: list[str]) -> list[dict]:
    tasks: list[dict] = []
    for i in range(0, len(task_arns), _DESCRIBE_TASKS_BATCH_SIZE):
        batch = task_arns[i : i + _DESCRIBE_TASKS_BATCH_SIZE]
        response = client.describe_tasks(cluster=cluster, tasks=batch)
        tasks.extend(response.get("tasks", []))
    return tasks


def _list_service_arns(client, cluster: str) -> list[str]:
    arns: list[str] = []
    paginator = client.get_paginator("list_services")
    for page in paginator.paginate(cluster=cluster):
        arns.extend(page.get("serviceArns", []))
    return arns


def _describe_services(client, cluster: str, service_arns: list[str]) -> list[dict]:
    services: list[dict] = []
    for i in range(0, len(service_arns), _DESCRIBE_SERVICES_BATCH_SIZE):
        batch = service_arns[i : i + _DESCRIBE_SERVICES_BATCH_SIZE]
        response = client.describe_services(cluster=cluster, services=batch)
        services.extend(response.get("services", []))
    return services


def _family_from_task_definition_arn(task_definition_arn: str) -> str:
    """'arn:aws:ecs:...:task-definition/my-family:12' -> 'my-family'."""
    tail = task_definition_arn.rsplit("/", 1)[-1]
    return tail.rsplit(":", 1)[0]


def _daily_utilization_ratios(
    cluster: str,
    days: int,
    region: str | None,
    utilized_metric: str,
    reserved_metric: str,
    extra_dimensions: list[tuple[str, str]],
) -> dict[date, float]:
    """Per-day utilized/reserved ratio (%), one entry per calendar day that
    has a datapoint for BOTH metrics that day. Deliberately NOT averaged
    across the window first (that was a real bug, caught in code review --
    see idle_service.check_idle_via_metrics's own "every datapoint, not
    average" rule, roadmap Section 3.1: a task busy on day 1 and idle for
    the rest of a 7-day window must never get averaged into a false
    "idle" verdict). Reuses idle_service.bucket_by_day for the same
    last-datapoint-wins-per-day bucketing every other metric-driven check
    in this app already uses, rather than duplicating it."""
    utilized_points = cloudwatch_service.get_daily_datapoints(
        namespace="ECS/ContainerInsights",
        metric_name=utilized_metric,
        dimension_name="ClusterName",
        dimension_value=cluster,
        days=days,
        statistic="Average",
        unit=None,
        extra_dimensions=extra_dimensions,
        region=region,
    )
    reserved_points = cloudwatch_service.get_daily_datapoints(
        namespace="ECS/ContainerInsights",
        metric_name=reserved_metric,
        dimension_name="ClusterName",
        dimension_value=cluster,
        days=days,
        statistic="Average",
        unit=None,
        extra_dimensions=extra_dimensions,
        region=region,
    )
    utilized_by_day = idle_service.bucket_by_day(utilized_points)
    reserved_by_day = idle_service.bucket_by_day(reserved_points)
    ratios: dict[date, float] = {}
    for day, reserved in reserved_by_day.items():
        utilized = utilized_by_day.get(day)
        if utilized is None or reserved <= 0:
            continue
        ratios[day] = round((utilized / reserved) * 100.0, 2)
    return ratios


def _all_days_below(ratios: dict[date, float], threshold: float) -> bool:
    """True only if every day with data is below threshold -- an empty
    dict (no day had data for both metrics) is never "idle by default",
    same "don't guess from missing data" rule as everywhere else in this
    app. Caller is responsible for treating an empty ratios dict as
    "no data, skip" before ever calling this."""
    return bool(ratios) and all(v < threshold for v in ratios.values())


def _worst_day_ratio(ratios: dict[date, float]) -> float | None:
    """The highest single-day ratio in the window -- used for the
    reported percentage precisely because it's the value that would have
    disqualified an idle/over-provisioned verdict if it were too high.
    Reporting "peaked at X% this window" is honest in a way a window
    average isn't (see the module-level fix note above)."""
    return max(ratios.values()) if ratios else None


def _family_utilization(
    cluster: str, family: str, days: int, region: str | None
) -> tuple[dict[date, float], dict[date, float]]:
    dims = [("TaskDefinitionFamily", family)]
    cpu_ratios = _daily_utilization_ratios(
        cluster, days, region, "CpuUtilized", "CpuReserved", dims
    )
    mem_ratios = _daily_utilization_ratios(
        cluster, days, region, "MemoryUtilized", "MemoryReserved", dims
    )
    return cpu_ratios, mem_ratios


def _service_utilization(
    cluster: str, service_name: str, days: int, region: str | None
) -> tuple[dict[date, float], dict[date, float]]:
    dims = [("ServiceName", service_name)]
    cpu_ratios = _daily_utilization_ratios(
        cluster, days, region, "CpuUtilized", "CpuReserved", dims
    )
    mem_ratios = _daily_utilization_ratios(
        cluster, days, region, "MemoryUtilized", "MemoryReserved", dims
    )
    return cpu_ratios, mem_ratios


def check_container_idle(
    cluster: str, days: int, region: str | None = None
) -> EcsContainerIdleReport:
    client = get_ecs_client(region=region)
    raw_cluster = _get_cluster(cluster, client)
    if raw_cluster is None:
        return EcsContainerIdleReport(
            cluster=cluster,
            window_days=days,
            container_insights_enabled=False,
            container_insights_note=f"ECS cluster {cluster!r} not found.",
            findings=[],
            total_tasks_checked=0,
            total_services_checked=0,
        )

    container_insights = _cluster_container_insights(raw_cluster)

    task_arns = _list_running_task_arns(client, cluster)
    tasks = _describe_tasks(client, cluster, task_arns) if task_arns else []
    service_arns = _list_service_arns(client, cluster)
    services = _describe_services(client, cluster, service_arns) if service_arns else []

    if container_insights == "disabled":
        return EcsContainerIdleReport(
            cluster=cluster,
            window_days=days,
            container_insights_enabled=False,
            container_insights_note=CONTAINER_INSIGHTS_OPT_IN_NOTE,
            findings=[],
            total_tasks_checked=len(tasks),
            total_services_checked=len(services),
        )

    findings: list[EcsContainerFinding] = []

    # --- Task-level idle / over-provisioned, Fargate tasks only ---------
    # Every finding below requires EVERY day in the window below threshold
    # (_all_days_below), never a window average -- see
    # _daily_utilization_ratios's docstring for why that was a real bug.
    family_cache: dict[str, tuple[dict[date, float], dict[date, float]]] = {}
    for task in tasks:
        if task.get("launchType") != FARGATE_LAUNCH_TYPE:
            continue
        family = _family_from_task_definition_arn(task.get("taskDefinitionArn", ""))
        if not family:
            continue
        if family not in family_cache:
            family_cache[family] = _family_utilization(cluster, family, days, region)
        cpu_ratios, mem_ratios = family_cache[family]
        if not cpu_ratios or not mem_ratios:
            continue  # no Container Insights data for this family yet -- don't guess

        task_arn = task.get("taskArn")
        cpu_worst = _worst_day_ratio(cpu_ratios)
        mem_worst = _worst_day_ratio(mem_ratios)
        if _all_days_below(cpu_ratios, CONTAINER_IDLE_UTILIZATION_PERCENT) and _all_days_below(
            mem_ratios, CONTAINER_IDLE_UTILIZATION_PERCENT
        ):
            findings.append(
                EcsContainerFinding(
                    finding_type="task_idle_utilization",
                    cluster=cluster,
                    task_arn=task_arn,
                    task_definition_family=family,
                    launch_type=FARGATE_LAUNCH_TYPE,
                    cpu_utilization_percent=cpu_worst,
                    memory_utilization_percent=mem_worst,
                    message=(
                        f"Task {task_arn} (family {family}) never exceeded {cpu_worst:.1f}% of "
                        f"its reserved CPU or {mem_worst:.1f}% of its reserved memory on any "
                        f"day in the last {days} days -- near-idle every day, not just on "
                        "average."
                    ),
                )
            )
        elif _all_days_below(
            cpu_ratios, CONTAINER_OVER_PROVISIONED_UTILIZATION_PERCENT
        ) and _all_days_below(mem_ratios, CONTAINER_OVER_PROVISIONED_UTILIZATION_PERCENT):
            findings.append(
                EcsContainerFinding(
                    finding_type="task_over_provisioned",
                    cluster=cluster,
                    task_arn=task_arn,
                    task_definition_family=family,
                    launch_type=FARGATE_LAUNCH_TYPE,
                    cpu_utilization_percent=cpu_worst,
                    memory_utilization_percent=mem_worst,
                    message=(
                        f"Task {task_arn} (family {family}) is allocated far more vCPU/memory "
                        f"than it uses (peaked at {cpu_worst:.1f}% CPU, {mem_worst:.1f}% memory "
                        "on its busiest day this window) -- a rightsizing candidate."
                    ),
                )
            )

    # --- Service-level standby capacity ----------------------------------
    for service in services:
        desired_count = service.get("desiredCount", 0)
        if desired_count <= 0:
            continue
        service_name = service.get("serviceName", "")
        cpu_ratios, mem_ratios = _service_utilization(cluster, service_name, days, region)
        if not cpu_ratios or not mem_ratios:
            continue
        cpu_worst = _worst_day_ratio(cpu_ratios)
        mem_worst = _worst_day_ratio(mem_ratios)
        if _all_days_below(cpu_ratios, CONTAINER_IDLE_UTILIZATION_PERCENT) and _all_days_below(
            mem_ratios, CONTAINER_IDLE_UTILIZATION_PERCENT
        ):
            findings.append(
                EcsContainerFinding(
                    finding_type="service_standby_capacity",
                    cluster=cluster,
                    service_name=service_name,
                    launch_type=service.get("launchType"),
                    cpu_utilization_percent=cpu_worst,
                    memory_utilization_percent=mem_worst,
                    desired_count=desired_count,
                    running_count=service.get("runningCount", 0),
                    message=(
                        f"Service {service_name} keeps {desired_count} task(s) running "
                        "(non-zero minimum desired count) but never exceeded "
                        f"{cpu_worst:.1f}% CPU / {mem_worst:.1f}% memory utilization on any "
                        f"day in the last {days} days -- paying for standby capacity "
                        "nobody's using."
                    ),
                )
            )

    return EcsContainerIdleReport(
        cluster=cluster,
        window_days=days,
        container_insights_enabled=True,
        container_insights_note=None,
        findings=findings,
        total_tasks_checked=len(tasks),
        total_services_checked=len(services),
    )
