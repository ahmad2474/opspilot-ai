"""Read-only view of investigation memory (Phase 8) — lets a human browse
what the agent has persisted, the same table find_similar_past_investigations
searches over.
"""
from __future__ import annotations

from fastapi import APIRouter

from app.models.investigation import InvestigationList
from app.services import investigation_service

router = APIRouter()


@router.get("/investigations", response_model=InvestigationList)
async def list_investigations() -> InvestigationList:
    investigations = investigation_service.list_recent_investigations()
    return InvestigationList(investigations=investigations)
