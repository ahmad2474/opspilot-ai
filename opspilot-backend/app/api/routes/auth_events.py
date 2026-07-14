"""Server-to-server login audit endpoint (roadmap Section 4 / Step 7).

Extends the one audit-log write path (app/services/audit_log_service) to
cover login_success/login_failed -- the highest-value audit coverage gap
found by the Step 7 security review: a single-admin app otherwise has zero
record of who signed in or failed to sign in.

Why this route exists separately from every other route in this app:
NextAuth's authorize() (opspilot-frontend/lib/auth.ts) runs server-side in
Next.js and is the only place login success/failure is actually determined
-- but it runs *before* any session JWT exists (that's literally what it's
deciding), so it cannot call this backend behind the normal
`require_session` bearer-token check every other route uses (see
app/core/security.py, wired in app/main.py).

Instead of an unauthenticated public route -- which would let anyone on the
internet POST fake login_success/login_failed entries -- this reuses the
same AUTH_SHARED_SECRET already shared between Next.js and FastAPI for
signing the session JWT, but as a plain HMAC-SHA256 signature over
{action, email, ts} rather than a JWT. authorize() signs the payload
server-side (the secret never leaves either server process) and this route
verifies the signature plus a short timestamp freshness window (replay
protection) before trusting the caller is really this app's own Next.js
server, not an arbitrary client. See app/core/security.py's
verify_login_event_signature for the exact mechanism.

Deliberately NOT wired behind `dependencies=[Depends(require_session)]` in
main.py -- this is the one intentional exception to "every route requires a
session," for the reason above, and it has its own equally strong (HMAC,
not "nothing") gate instead.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends

from app.core.config import Settings, get_settings
from app.core.security import verify_login_event_signature
from app.models.auth_event import LoginAuditRequest, LoginAuditResponse
from app.services import audit_log_service

logger = logging.getLogger("app.api.auth_events")

router = APIRouter()


@router.post("/auth/login-audit", response_model=LoginAuditResponse)
async def record_login_audit(
    payload: LoginAuditRequest,
    settings: Settings = Depends(get_settings),
) -> LoginAuditResponse:
    # `settings` comes in via Depends (not a direct get_settings() call) so
    # tests can override it through app.dependency_overrides the same way
    # every other route does -- see app/core/security.py's require_session
    # docstring for why that matters.
    verify_login_event_signature(
        action=payload.action,
        email=payload.email,
        ts=payload.ts,
        signature=payload.signature,
        settings=settings,
    )

    # The login attempt itself already fully resolved (success or failure)
    # by the time authorize() calls this endpoint -- a transient DynamoDB
    # failure on the audit write must not turn into an error the caller
    # surfaces to the user, or worse, block the sign-in flow. Same
    # non-blocking try/except-and-log shape as
    # app/api/routes/mcp_auth.py's token generate/revoke audit writes.
    try:
        audit_log_service.write_entry(payload.action, actor_email=payload.email)
    except Exception:  # noqa: BLE001 - audit write is best-effort; must never block login
        logger.warning("audit_log_write_failed action=%s", payload.action, exc_info=True)

    return LoginAuditResponse(recorded=True)
