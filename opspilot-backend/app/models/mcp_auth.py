"""Response shapes for the MCP token lifecycle routes (roadmap Section 3.6).

Kept deliberately separate from app/models/mcp_info.py (read-only tool
introspection) -- this file is about the access-token lifecycle
(generate/revoke/status), not the tool list.
"""
from __future__ import annotations

from pydantic import BaseModel


class McpTokenGenerateResponse(BaseModel):
    # The ONLY place the plaintext token ever appears, in the one-time
    # generation response. Never stored, never logged, never returned by
    # any other route (see McpTokenStatus below).
    token: str
    created_at: str
    warning: str = "Copy this token now — it will not be shown again."


class McpTokenRevokeResponse(BaseModel):
    revoked: bool
    message: str


class McpTokenStatus(BaseModel):
    """Safe-to-display status -- never includes the token or its hash."""

    has_active_token: bool
    created_at: str | None
    revoked_at: str | None
