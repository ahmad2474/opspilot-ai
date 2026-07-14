from __future__ import annotations

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import (
    audit_log,
    auth_events,
    aws_account,
    chat,
    dashboard,
    health,
    investigations,
    mcp_auth,
    mcp_info,
    resources,
)
from app.core.config import get_settings
from app.core.logging import RequestIdMiddleware, configure_logging
from app.core.security import require_session

configure_logging()

settings = get_settings()

# Auto-docs (/docs, /redoc, /openapi.json) are registered directly on the
# FastAPI app object, outside any router — so they never pass through
# require_session no matter how the routers below are wired. They'd
# otherwise be reachable with no token at all, contradicting the "every
# route requires a valid session" boundary this module exists to enforce.
# Simplest fix that matches the stated boundary: only expose them in local
# dev; anywhere else they're off entirely.
_docs_enabled = settings.opspilot_app_env == "local"

app = FastAPI(
    title="OpsPilot AI",
    version="0.1.0",
    docs_url="/docs" if _docs_enabled else None,
    redoc_url="/redoc" if _docs_enabled else None,
    openapi_url="/openapi.json" if _docs_enabled else None,
)

# Order matters: added last = runs first on the way in. Request-ID needs to
# wrap everything (including CORS) so every log line has an ID, but CORS
# headers still need to land on every response including errors.
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(RequestIdMiddleware)

# health.py stays open — it's a liveness check, nothing there requires a
# session, and load balancers/uptime checks won't have a bearer token.
app.include_router(health.router, tags=["health"])

# auth_events.py also stays outside `_session_required` -- it's called by
# NextAuth's authorize() *before* any session JWT exists (see that file's
# module docstring), so it cannot use the normal bearer-token dependency.
# It has its own equally strong gate instead: an HMAC signature over the
# request body, verified inside the route handler via
# app/core/security.py's verify_login_event_signature. This is the one
# other intentional exception to "every route requires a session."
app.include_router(auth_events.router, tags=["auth"])

# Every other router requires a valid session (roadmap Section 3.5 —
# FastAPI independently validates a session token on every route, the
# frontend's /login redirect is not the security boundary). New route
# files should be wired in the same way: add the router here with
# `dependencies=[Depends(require_session)]` rather than annotating each
# path operation individually. See app/core/security.py for the check
# itself.
_session_required = [Depends(require_session)]
app.include_router(chat.router, tags=["chat"], dependencies=_session_required)
app.include_router(resources.router, tags=["resources"], dependencies=_session_required)
app.include_router(dashboard.router, tags=["dashboard"], dependencies=_session_required)
app.include_router(
    investigations.router, tags=["investigations"], dependencies=_session_required
)
app.include_router(mcp_info.router, tags=["mcp"], dependencies=_session_required)
app.include_router(mcp_auth.router, tags=["mcp"], dependencies=_session_required)
app.include_router(audit_log.router, tags=["audit-log"], dependencies=_session_required)
app.include_router(aws_account.router, tags=["aws-account"], dependencies=_session_required)
