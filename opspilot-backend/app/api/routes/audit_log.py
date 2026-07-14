"""Audit Log tab backend (roadmap Section 5's tab list) -- read-only HTTP
front door onto the write path app/services/audit_log_service.py already
built (Section 3.6, widened in Step 7/Section 4). `list_recent_entries()`
existed with no route exposing it yet (by design -- its own docstring says
this is exactly the read path a later step would wire up); this route is
that later step, nothing more.

Gated by `require_session` exactly like every other route (see main.py) --
no new AWS/DynamoDB permission is needed since the existing
OpspilotMcpTokenAndAuditLog IAM statement already covers dynamodb:Scan on
opspilot-audit-log.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Query

from app.core.aws_errors import aws_error_to_http_exception
from app.models.audit_log import AuditLogEntryList
from app.services import audit_log_service

logger = logging.getLogger("app.api.audit_log")

router = APIRouter()

# Sensible cap on `limit` -- list_recent_entries() itself has no upper
# bound of its own (it scans the whole table then slices), so the cap
# belongs here at the one HTTP entry point rather than inside the service.
_MAX_LIMIT = 200
_DEFAULT_LIMIT = 50


@router.get("/audit-log", response_model=AuditLogEntryList)
async def get_audit_log(
    limit: int = Query(_DEFAULT_LIMIT, ge=1, le=_MAX_LIMIT),
) -> AuditLogEntryList:
    try:
        entries = audit_log_service.list_recent_entries(limit=limit)
    except Exception as exc:  # noqa: BLE001 - surface a clean 502, don't leak a raw boto3 traceback
        raise aws_error_to_http_exception(exc, logger=logger, context="get_audit_log") from exc
    return AuditLogEntryList(entries=entries)
