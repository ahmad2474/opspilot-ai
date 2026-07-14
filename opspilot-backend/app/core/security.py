"""Server-side session validation (roadmap Section 3.5).

Every API route in this app — except the liveness check in health.py —
must independently prove a valid, non-expired session before doing any
work. The Next.js middleware redirect to /login is a UX nicety; it is
NOT a security boundary, since nothing stops someone from calling this
API directly (curl, another client, etc). This module is that boundary.

How the session gets here: NextAuth (opspilot-frontend/lib/auth.ts) mints a
short-lived HS256 JWT on sign-in, signed with AUTH_SHARED_SECRET, and the
frontend attaches it as `Authorization: Bearer <token>` on every call to
this backend. We verify the signature and expiry here with the same
shared secret — no shared database or session store needed, no calling
back into Next.js on every request.

Pattern for every other agent adding a new route:
    Don't add `Depends(require_session)` to individual path operations.
    Instead, add the dependency once at `app.include_router(...)` in
    app/main.py, e.g.:

        app.include_router(
            my_new.router, tags=["my-new-thing"],
            dependencies=[Depends(require_session)],
        )

    That single line protects every route in that router, present and
    future, without each route handler having to remember to do it.
"""
from __future__ import annotations

import hashlib
import hmac
import logging
import time

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.core.config import Settings, get_settings

logger = logging.getLogger("app.core.security")

# auto_error=False so we can raise our own consistent 401 (with a WWW-Authenticate
# header) instead of FastAPI's default when the header is missing entirely.
_bearer_scheme = HTTPBearer(auto_error=False)

# Client-facing text for any "auth isn't configured" failure. Deliberately
# generic -- naming the specific missing env var (e.g. "AUTH_SHARED_SECRET")
# in a response body reachable pre-auth tells an unauthenticated caller
# exactly why the server is misconfigured, which is free reconnaissance for
# no benefit (Step 7 security audit finding). The specific reason still goes
# to the server-side log line right before this is raised, for the admin's
# own debugging.
AUTH_UNAVAILABLE_MESSAGE = "Authentication is unavailable — please try again later."


class SessionUser:
    """Minimal identity extracted from a verified session token.

    Kept intentionally small (single-admin scope) but exists as a real
    type so downstream code (e.g. a future audit log) has something to
    depend on instead of a bare dict.
    """

    def __init__(self, email: str) -> None:
        self.email = email

    def __repr__(self) -> str:  # pragma: no cover - debugging aid only
        return f"SessionUser(email={self.email!r})"


def _unauthorized(detail: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=detail,
        headers={"WWW-Authenticate": "Bearer"},
    )


async def require_session(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer_scheme),
    settings: Settings = Depends(get_settings),
) -> SessionUser:
    """FastAPI dependency: verify the bearer token on the incoming request.

    Raises 401 if the header is missing, malformed, expired, or signed
    with the wrong secret. Returns the decoded session identity on success
    so route handlers (or future audit logging) can use it if needed.

    `settings` is itself a dependency (rather than called directly) so
    tests can override get_settings() via app.dependency_overrides without
    needing a real .env / AUTH_SHARED_SECRET in the test environment.
    """
    if not settings.auth_shared_secret:
        # Fail closed: an unconfigured secret must never silently open the
        # API up to unauthenticated access.
        logger.error("AUTH_SHARED_SECRET is not configured — rejecting all requests")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=AUTH_UNAVAILABLE_MESSAGE,
        )

    if credentials is None or not credentials.credentials:
        raise _unauthorized("Missing bearer token.")

    try:
        payload = jwt.decode(
            credentials.credentials,
            settings.auth_shared_secret,
            algorithms=["HS256"],
            # A validly-signed token that simply omits exp/sub would
            # otherwise sail through (PyJWT only checks expiry if `exp` is
            # present at all, and `sub` isn't checked unless required).
            # Only our own frontend can mint valid tokens today, but this
            # closes the gap rather than relying on that staying true.
            options={"require": ["exp", "sub"]},
        )
    except jwt.ExpiredSignatureError as exc:
        raise _unauthorized("Session expired — please sign in again.") from exc
    except jwt.InvalidTokenError as exc:
        raise _unauthorized("Invalid session token.") from exc

    email = payload.get("sub")
    if not email:
        raise _unauthorized("Session token is missing a subject.")

    if settings.admin_email and email.strip().lower() != settings.admin_email.strip().lower():
        raise _unauthorized("Session does not match the configured admin account.")

    return SessionUser(email=email)


# --- Login audit event signing (roadmap Section 4 / Step 7) ----------------
#
# NextAuth's authorize() (opspilot-frontend/lib/auth.ts) determines login
# success/failure server-side in Next.js, *before* any session JWT exists --
# so it cannot call this backend through the normal require_session bearer
# check above (there is nothing to send yet). To let it still write an
# audit-log entry (app/services/audit_log_service) without exposing an
# unauthenticated, spoofable "write me an audit entry" endpoint to the
# internet, it signs {action, email, ts} with AUTH_SHARED_SECRET -- the same
# secret already shared between the two services for the session JWT above,
# reused here as a plain HMAC-SHA256 signature instead of a JWT. This module
# verifies that signature plus a short timestamp freshness window (replay
# protection) so the backend can trust the caller is really this app's own
# Next.js server process holding the shared secret, not an arbitrary client.
LOGIN_EVENT_MAX_AGE_SECONDS = 60


def _login_event_message(action: str, email: str, ts: int) -> str:
    """Canonical string that both sides sign/verify. Keep this in exact sync
    with the Next.js signer in opspilot-frontend/lib/auth.ts -- any format
    change here must be mirrored there or every login event will fail
    verification."""
    return f"{action}:{email}:{ts}"


def sign_login_event(action: str, email: str, ts: int, secret: str) -> str:
    """HMAC-SHA256 hex digest over the canonical login-event payload. Exists
    on this side mainly so backend tests can construct valid signatures
    without duplicating the message format inline."""
    message = _login_event_message(action, email, ts)
    return hmac.new(secret.encode("utf-8"), message.encode("utf-8"), hashlib.sha256).hexdigest()


def verify_login_event_signature(
    *, action: str, email: str, ts: int, signature: str, settings: Settings
) -> None:
    """Raises 503 if auth isn't configured, 401 if the signature is missing/
    wrong/stale. Does not return anything -- callers proceed only if this
    doesn't raise."""
    if not settings.auth_shared_secret:
        logger.error("AUTH_SHARED_SECRET is not configured — rejecting login audit event")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=AUTH_UNAVAILABLE_MESSAGE,
        )

    now = int(time.time())
    if abs(now - ts) > LOGIN_EVENT_MAX_AGE_SECONDS:
        raise _unauthorized("Login audit event is stale or has an invalid timestamp.")

    expected_signature = sign_login_event(action, email, ts, settings.auth_shared_secret)
    if not signature or not hmac.compare_digest(expected_signature, signature):
        raise _unauthorized("Invalid login audit event signature.")
