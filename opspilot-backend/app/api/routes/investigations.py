"""Read-only view of investigation memory (Phase 8) — lets a human browse
what the agent has persisted, the same table find_similar_past_investigations
searches over.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter

from app.core.aws_errors import aws_error_to_http_exception
from app.models.investigation import InvestigationList
from app.services import investigation_service

logger = logging.getLogger("app.api.investigations")

router = APIRouter()


@router.get("/investigations", response_model=InvestigationList)
async def list_investigations() -> InvestigationList:
    try:
        investigations = investigation_service.list_recent_investigations()
    except Exception as exc:  # noqa: BLE001 - surface a clean 502, don't leak a raw boto3 traceback
        raise aws_error_to_http_exception(
            exc, logger=logger, context="list_investigations"
        ) from exc
    return InvestigationList(investigations=investigations)
