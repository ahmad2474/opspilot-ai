"""Dashboard data endpoint.

Deliberately bypasses the agent/LLM entirely — it calls ec2_service and
cloudwatch_service directly, the exact same functions app/tools wraps for
the agent. That shared source of truth is what guarantees the dashboard
and the chat answer can never disagree: there's only one place CPU data
or instance state is actually computed.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter

from app.models.resources import Ec2ResourceCard, ResourcesResponse
from app.services import cloudwatch_service, ec2_service

logger = logging.getLogger("app.api.resources")

router = APIRouter()


@router.get("/resources/ec2", response_model=ResourcesResponse)
async def get_ec2_resources() -> ResourcesResponse:
    instances = ec2_service.list_instances()
    logger.info("resources_ec2 instance_count=%d", instances.count)

    cards: list[Ec2ResourceCard] = []
    for instance in instances.instances:
        cpu = None
        if instance.state == "running":
            cpu = cloudwatch_service.get_cpu_utilization(instance.instance_id)
        cards.append(Ec2ResourceCard(instance=instance, cpu=cpu))

    return ResourcesResponse(ec2=cards)
