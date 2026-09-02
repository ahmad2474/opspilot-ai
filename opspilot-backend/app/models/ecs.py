"""ECS container idle-detection models (roadmap phase 2 Section 1.2, "Batch B").

check_container_idle(cluster, days) is a findings-list tool, like the
Batch A checks in app/models/logs.py/s3_waste.py/snapshot.py -- a cluster
can independently have idle tasks, over-provisioned tasks, AND standby
services with no traffic, all at once. See the data-schema skill's
"Findings-list tools" section.

Every finding here depends on CloudWatch Container Insights being enabled
on the cluster -- a per-cluster opt-in the account owner has to turn on
explicitly (same one-time-activation shape as Redshift/Kinesis's
account-level service opt-in, and Compute Optimizer's account-level opt-in
elsewhere in this batch). `container_insights_enabled=False` is a graceful,
non-fatal result (see ecs_service.check_container_idle's docstring), never
an unhandled exception.

Real caveat, worth recording plainly rather than silently assuming the
roadmap's exact phrasing holds literally: **standard (non-"enhanced")
Container Insights for ECS does not publish metrics per individual running
task ARN** -- it aggregates at the TaskDefinitionFamily level within a
cluster. "Task-level, not cluster-level" (roadmap phase 2 Section 1.2) is
implemented here as "per running task, using its task-definition family's
utilization" -- genuinely finer-grained than a cluster-wide average (a
cluster running two very different task families is no longer averaged
together), but not literally per-task-ARN, since AWS itself doesn't expose
that granularity without the newer "enhanced observability" tier this
build does not assume is enabled. Not live-verified against a real
Container Insights response -- no ECS infrastructure exists in this
build's AWS account (confirmed via a live `describe_clusters` call
returning zero clusters); flagged for verification once real Fargate
tasks/an enabled cluster exist to check against.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class EcsClusterSummary(BaseModel):
    cluster_arn: str
    cluster_name: str
    status: str
    running_tasks_count: int
    pending_tasks_count: int
    active_services_count: int
    container_insights: Literal["enabled", "enhanced", "disabled"] = Field(
        description=(
            "From the cluster's own 'containerInsights' setting "
            "(DescribeClusters include=['SETTINGS']) -- 'disabled' means "
            "check_container_idle can only report inventory counts for this "
            "cluster, not idle/rightsizing findings."
        )
    )


class EcsClusterList(BaseModel):
    clusters: list[EcsClusterSummary]
    count: int


class EcsContainerFinding(BaseModel):
    finding_type: Literal[
        "task_idle_utilization",
        "task_over_provisioned",
        "service_standby_capacity",
    ] = Field(
        description=(
            "'task_idle_utilization': a running Fargate task's family shows "
            "near-zero CPU AND memory utilization over the window. "
            "'task_over_provisioned': utilized well below what's reserved, but "
            "not idle outright -- a rightsizing-flavored finding, same spirit "
            "as Compute Optimizer. 'service_standby_capacity': a service's "
            "desired_count > 0 but its tasks show near-zero utilization -- "
            "paying for standby capacity nobody's using."
        )
    )
    cluster: str
    task_arn: str | None = Field(
        default=None,
        description="Populated for task_idle_utilization/task_over_provisioned only.",
    )
    task_definition_family: str | None = Field(
        default=None,
        description="Populated for task_idle_utilization/task_over_provisioned only.",
    )
    service_name: str | None = Field(
        default=None, description="Populated for service_standby_capacity only."
    )
    launch_type: str | None = None
    cpu_utilization_percent: float | None = Field(
        default=None,
        description=(
            "utilized/reserved CPU x 100, computed from Container Insights "
            "CpuUtilized over CpuReserved -- both raw same-unit CPU-unit "
            "values per AWS's own metric design. Not live-verified against a "
            "real Container Insights response (see module docstring)."
        ),
    )
    memory_utilization_percent: float | None = None
    desired_count: int | None = None
    running_count: int | None = None
    message: str


class EcsContainerIdleReport(BaseModel):
    cluster: str
    window_days: int
    container_insights_enabled: bool = Field(
        description=(
            "False when the cluster's containerInsights setting is 'disabled' "
            "(or the cluster wasn't found) -- every finding below requires "
            "Container Insights' task/service CPU+memory utilization data."
        )
    )
    container_insights_note: str | None = Field(
        default=None,
        description=(
            "Populated only when container_insights_enabled=False -- how to "
            "opt in (or that the cluster wasn't found), instead of a "
            "fabricated finding or an unhandled exception."
        ),
    )
    findings: list[EcsContainerFinding]
    total_tasks_checked: int = Field(
        description="Every RUNNING task in the cluster, regardless of launch type."
    )
    total_services_checked: int
