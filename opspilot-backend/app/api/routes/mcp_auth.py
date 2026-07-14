"""MCP access-token lifecycle routes (roadmap Section 3.6) -- Settings ->
MCP Access "Generate token" / "Revoke" buttons call these. Gated by the
same `require_session` dependency as every other route (see main.py),
same single-admin scope as the rest of this app's auth.

Every generate/revoke here writes an Audit Log entry automatically
(roadmap 3.6's explicit requirement) via app/services/audit_log_service.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, status

from app.core.aws_errors import aws_error_to_http_exception
from app.core.security import SessionUser, require_session
from app.models.mcp_auth import (
    McpTokenGenerateResponse,
    McpTokenRevokeResponse,
    McpTokenStatus,
)
from app.services import audit_log_service, mcp_auth_service

logger = logging.getLogger("app.api.mcp_auth")

router = APIRouter()


@router.post("/mcp/token/generate", response_model=McpTokenGenerateResponse)
async def generate_mcp_token(
    user: SessionUser = Depends(require_session),
) -> McpTokenGenerateResponse:
    # generate_token() is the source of truth: once it has persisted the
    # new token, the caller must still get it back even if the audit
    # write itself fails -- otherwise a transient DynamoDB blip on the
    # audit table would turn an already-successful token rotation into an
    # unrecoverable one-time secret the caller never saw. See
    # audit_log_service.write_entry's docstring: it deliberately does NOT
    # swallow failures itself, so this route is the layer responsible for
    # not letting that propagate into a 500 on top of a real mutation.
    try:
        plaintext, created_at = mcp_auth_service.generate_token()
    except Exception as exc:  # noqa: BLE001 - surface a clean 502, don't leak a raw boto3 traceback
        raise aws_error_to_http_exception(
            exc, logger=logger, context="generate_mcp_token"
        ) from exc
    try:
        audit_log_service.write_entry("mcp_token_generated", actor_email=user.email)
    except Exception:  # noqa: BLE001 - audit write is best-effort here; the token mutation already succeeded
        logger.warning("audit_log_write_failed action=mcp_token_generated", exc_info=True)
    return McpTokenGenerateResponse(token=plaintext, created_at=created_at)


@router.post("/mcp/token/revoke", response_model=McpTokenRevokeResponse)
async def revoke_mcp_token(
    user: SessionUser = Depends(require_session),
) -> McpTokenRevokeResponse:
    try:
        revoked = mcp_auth_service.revoke_token()
    except Exception as exc:  # noqa: BLE001 - surface a clean 502, don't leak a raw boto3 traceback
        raise aws_error_to_http_exception(exc, logger=logger, context="revoke_mcp_token") from exc
    if not revoked:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No active MCP token to revoke.",
        )
    try:
        audit_log_service.write_entry("mcp_token_revoked", actor_email=user.email)
    except Exception:  # noqa: BLE001 - audit write is best-effort here; the token mutation already succeeded
        logger.warning("audit_log_write_failed action=mcp_token_revoked", exc_info=True)
    return McpTokenRevokeResponse(revoked=True, message="MCP access token revoked.")


@router.get("/mcp/token/status", response_model=McpTokenStatus)
async def get_mcp_token_status(
    user: SessionUser = Depends(require_session),
) -> McpTokenStatus:
    try:
        result = mcp_auth_service.get_status()
    except Exception as exc:  # noqa: BLE001 - surface a clean 502, don't leak a raw boto3 traceback
        raise aws_error_to_http_exception(
            exc, logger=logger, context="get_mcp_token_status"
        ) from exc
    return McpTokenStatus(
        has_active_token=result.has_active_token,
        created_at=result.created_at,
        revoked_at=result.revoked_at,
    )
