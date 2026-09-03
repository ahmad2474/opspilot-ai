"""Deletion-impact analysis endpoint (roadmap phase 2 Section 3 -- the
permanent, read-only replacement for the retired write/approval Step 8).

Same "dashboard is just another caller of app/services/" pattern as every
other route file in this app (follows waste.py's shape) -- no AWS logic
lives here, this is a thin wrapper over app.services.deletion_impact_service.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Query

from app.models.deletion_impact import DeletionImpactReport
from app.services import deletion_impact_service

logger = logging.getLogger("app.api.deletion_impact")

router = APIRouter()


@router.get("/deletion-impact", response_model=DeletionImpactReport)
async def get_deletion_impact(
    resource_type: str = Query(..., description="'ec2', 'rds', or 'ebs'."),
    resource_id: str = Query(..., description="The resource ID to check, matching resource_type."),
) -> DeletionImpactReport:
    """What actually happens if this specific resource is deleted --
    read-only, this endpoint never deletes anything itself (roadmap phase
    2 Section 3.1). Returns will_be_removed / will_persist_and_keep_costing
    (each entry with a real dollar figure where computable) /
    behavioral_warnings / never_affected / check_errors (any live check
    that could not be verified -- e.g. a permission gap -- reported
    explicitly, never silently treated as 'no')."""
    try:
        return deletion_impact_service.check_deletion_impact(resource_type, resource_id)
    except deletion_impact_service.UnsupportedDeletionImpactResourceTypeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except ValueError as exc:
        # get_instance/get_volume returning None -- resource not found,
        # not an AWS-call failure (see the 502 branch below for that case).
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001 - surface a clean 502, don't leak a raw boto3 traceback
        logger.warning(
            "get_deletion_impact failed for resource_type=%s resource_id=%s",
            resource_type,
            resource_id,
            exc_info=True,
        )
        raise HTTPException(
            status_code=502, detail="Failed to analyze deletion impact."
        ) from exc
