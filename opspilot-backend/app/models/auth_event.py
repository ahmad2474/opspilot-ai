"""Login audit event request/response shape (roadmap Section 4 / Step 7).

Backs POST /auth/login-audit -- see app/api/routes/auth_events.py for why
this route exists and how it authenticates its caller (HMAC signature over
this payload, not a session bearer token).
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

LoginEventAction = Literal["login_success", "login_failed"]


class LoginAuditRequest(BaseModel):
    action: LoginEventAction
    # The attempted email -- recorded even on failure ("someone tried
    # logging in as X and failed" is useful signal for a single-admin app).
    # A valid signature is trivially obtainable by anyone submitting the
    # real login form (recording failed attempts is the whole point), so
    # this is attacker-controlled input -- bounded to RFC 5321's 320-char
    # max to avoid an oversized DynamoDB item write attempt.
    email: str = Field(max_length=320)
    # Unix epoch seconds the signature was generated at. Checked against a
    # short freshness window server-side (replay protection) -- see
    # app/core/security.py's verify_login_event_signature.
    ts: int
    # HMAC-SHA256 hex digest over f"{action}:{email}:{ts}", signed with
    # AUTH_SHARED_SECRET on the Next.js side. Never the secret itself.
    signature: str


class LoginAuditResponse(BaseModel):
    recorded: bool
