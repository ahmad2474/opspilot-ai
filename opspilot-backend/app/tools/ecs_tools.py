"""Agent-facing tools for ECS container idle/rightsizing checking. Stay
thin on purpose -- all the real logic lives in app.services.ecs_service so
it can be unit-tested without touching the LLM at all.
"""
from __future__ import annotations

import logging
from typing import Annotated

from agents import function_tool

from app.services import ecs_service

logger = logging.getLogger("app.tools.ecs")


@function_tool
def list_ecs_clusters() -> str:
    """List ECS clusters in the configured region, including each cluster's
    CloudWatch Container Insights setting ('enabled'/'enhanced'/'disabled').
    Use this to find a `cluster` name for check_container_idle -- a cluster
    with Container Insights 'disabled' will only get inventory counts from
    that tool, not idle/rightsizing findings, until it's enabled."""
    logger.info("tool_call list_ecs_clusters")
    result = ecs_service.list_clusters()
    logger.info("tool_result list_ecs_clusters count=%d", result.count)
    return result.model_dump_json()


@function_tool
def check_container_idle(
    cluster: Annotated[str, "ECS cluster name or ARN."],
    days: Annotated[int, "How many days back to check for idleness."] = 7,
) -> str:
    """Find ECS/Fargate container waste in one cluster: running Fargate
    tasks with near-zero CPU/memory utilization, tasks allocated far more
    vCPU/memory than they use (a rightsizing-flavored finding), and services
    with a non-zero minimum desired count but near-zero real traffic
    (paying for standby capacity nobody's using). Task-level, not
    cluster-level. Requires CloudWatch Container Insights enabled on the
    cluster (a one-time opt-in, same pattern as Redshift/Kinesis) -- if
    disabled, returns container_insights_enabled=false with a plain
    how-to-opt-in note instead of fabricating a result or erroring. Returns
    a findings list, not a single verdict (roadmap phase 2 Section 1.2)."""
    logger.info("tool_call check_container_idle cluster=%s days=%d", cluster, days)
    result = ecs_service.check_container_idle(cluster, days)
    logger.info(
        "tool_result check_container_idle cluster=%s container_insights_enabled=%s findings=%d",
        cluster,
        result.container_insights_enabled,
        len(result.findings),
    )
    return result.model_dump_json()
