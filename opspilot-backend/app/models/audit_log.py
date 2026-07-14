"""Minimal Audit Log entry shape (roadmap Section 3.6's narrow slice of
Section 4's full audit log -- see app/services/audit_log_service.py's
module docstring for scope).
"""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class AuditLogEntry(BaseModel):
    id: str
    # Free-form for now (kept as plain str, not Literal) so future steps can
    # add new action types without a model change here. Currently written
    # actions: "mcp_token_generated", "mcp_token_revoked", "login_success",
    # "login_failed" (see app/services/audit_log_service.py's AuditAction).
    action: str
    # NOTE: trust level depends on `action`. For mcp_token_generated /
    # mcp_token_revoked this is always a cryptographically-verified identity
    # (the signed-in admin's email, from a validated session JWT). For
    # login_success / login_failed it is the raw, unauthenticated email
    # string typed into the login form -- for login_failed in particular,
    # this is attacker-controlled input, intentionally recorded as-is
    # ("someone tried logging in as X and failed" is the useful signal).
    # Do not assume actor_email is always a verified identity downstream
    # (e.g. a future Audit Log UI, SECURITY.md).
    actor_email: str
    created_at: datetime
    detail: str | None = None


class AuditLogEntryList(BaseModel):
    entries: list[AuditLogEntry]
