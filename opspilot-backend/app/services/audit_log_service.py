"""Audit log write/read path (roadmap Section 3.6 / narrow slice of
Section 4).

Originally scoped to MCP token generate/revoke events only. Step 7
(roadmap Section 4) extended this same write path (`write_entry`) --
rather than inventing a second logging mechanism alongside it -- to also
cover login_success/login_failed, the highest-value audit gap found in
that step's review. There was no prior audit-log code in this repo before
the original step (see docs/BUILD_PROGRESS.md "Decisions made"), so this
established the pattern, following the same DynamoDB
get_dynamodb_client()/Settings-driven-table-name shape as
app/services/investigation_service.py. Any future action types should
keep extending this one write path rather than adding a new one.
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Literal

from app.aws.client import get_dynamodb_client
from app.core.config import get_settings
from app.models.audit_log import AuditLogEntry

logger = logging.getLogger("app.services.audit_log")

# Every action this write path covers. Step 7 (roadmap Section 4) widened
# this from the original MCP-token-only pair to also cover login
# success/failure -- the single highest-value audit gap found in that
# step's review, since a single-admin app otherwise has zero record of who
# signed in or failed to. Kept as an explicit Literal (not a bare `str`) so
# a typo in an action name fails fast.
AuditAction = Literal[
    "mcp_token_generated",
    "mcp_token_revoked",
    "login_success",
    "login_failed",
]


def write_entry(action: AuditAction, actor_email: str, detail: str | None = None) -> AuditLogEntry:
    """Write one audit log entry. Roadmap 3.6 requires every token
    generate/revoke to write an entry automatically -- deliberately does
    NOT swallow DynamoDB failures here (unlike investigation_service's
    save path, which callers may treat as best-effort); a failed audit
    write should surface to the route so it isn't silently lost.
    """
    settings = get_settings()
    client = get_dynamodb_client()

    entry = AuditLogEntry(
        id=str(uuid.uuid4()),
        action=action,
        actor_email=actor_email,
        created_at=datetime.now(timezone.utc),
        detail=detail,
    )

    item: dict = {
        "id": {"S": entry.id},
        "action": {"S": entry.action},
        "actor_email": {"S": entry.actor_email},
        "created_at": {"S": entry.created_at.isoformat()},
    }
    if detail:
        item["detail"] = {"S": detail}

    client.put_item(TableName=settings.opspilot_audit_log_table, Item=item)
    logger.info("audit_log_entry_written action=%s actor=%s", action, actor_email)
    return entry


def list_recent_entries(limit: int = 50) -> list[AuditLogEntry]:
    """Every persisted audit entry, newest first. No dedicated route/UI
    consumes this yet (that's Section 5's Audit Log tab, Step 7) -- this
    exists now so the table isn't write-only from day one and Step 7 has
    a read path to build on rather than inventing its own.
    """
    settings = get_settings()
    client = get_dynamodb_client()

    items: list[dict] = []
    paginator = client.get_paginator("scan")
    for page in paginator.paginate(TableName=settings.opspilot_audit_log_table):
        items.extend(page.get("Items", []))

    items.sort(key=lambda raw: raw["created_at"]["S"], reverse=True)

    return [
        AuditLogEntry(
            id=raw["id"]["S"],
            action=raw["action"]["S"],
            actor_email=raw["actor_email"]["S"],
            created_at=raw["created_at"]["S"],
            detail=raw.get("detail", {}).get("S"),
        )
        for raw in items[:limit]
    ]
