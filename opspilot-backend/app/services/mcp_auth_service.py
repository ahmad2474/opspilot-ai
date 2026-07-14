"""MCP access-token lifecycle (roadmap Section 3.6).

Single-admin, single-active-token scope -- there is realistically only
ever one token that matters at a time. Storage model: one fixed-key item
(id="current") in `opspilot_mcp_tokens_table`. "Generate" always
overwrites this item wholesale, which both mints a new token AND
invalidates whatever token existed before (revoked or not) -- there is no
history of prior token hashes kept in this table. That history question
("who generated/revoked, and when") is answered by the audit log
(app/services/audit_log_service.py) instead, not by keeping old hash rows
here. This mirrors the same get_dynamodb_client()/Settings-driven-table
pattern as app/services/investigation_service.py -- no boto3 usage
outside app/aws/client.py.

Hashing: bcrypt, via the `bcrypt` package -- the same primitive
opspilot-frontend/lib/auth.ts already uses (bcryptjs) for
ADMIN_PASSWORD_HASH, for consistency across the two halves of this app's
auth story even though they're different languages/libraries. The
plaintext token is generated here, returned to the caller exactly once,
and never written to DynamoDB, a log line, or anywhere else.
"""
from __future__ import annotations

import logging
import secrets
from dataclasses import dataclass
from datetime import datetime, timezone

import bcrypt

from app.aws.client import get_dynamodb_client
from app.core.config import get_settings

logger = logging.getLogger("app.services.mcp_auth")

_ITEM_ID = "current"
_TOKEN_ENTROPY_BYTES = 32  # 256 bits, urlsafe-encoded


@dataclass
class McpTokenStatusResult:
    has_active_token: bool
    created_at: str | None
    revoked_at: str | None


def generate_token() -> tuple[str, str]:
    """Mint a new MCP access token.

    Returns (plaintext_token, created_at_iso). Overwrites (invalidates)
    any previously issued token -- see module docstring for why that's
    the deliberate design, not an oversight.
    """
    settings = get_settings()
    client = get_dynamodb_client()

    plaintext = secrets.token_urlsafe(_TOKEN_ENTROPY_BYTES)
    token_hash = bcrypt.hashpw(plaintext.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")
    created_at = datetime.now(timezone.utc).isoformat()

    client.put_item(
        TableName=settings.opspilot_mcp_tokens_table,
        Item={
            "id": {"S": _ITEM_ID},
            "token_hash": {"S": token_hash},
            "created_at": {"S": created_at},
            "revoked": {"BOOL": False},
        },
    )
    logger.info("mcp_token_generated created_at=%s", created_at)
    return plaintext, created_at


def revoke_token() -> bool:
    """Flip the current token to revoked.

    Returns False (no-op) if there is nothing to revoke -- no token has
    ever been generated, or the current one is already revoked -- so the
    route can decide how to respond without this function writing a
    misleading state change that didn't actually happen.
    """
    settings = get_settings()
    client = get_dynamodb_client()

    existing = client.get_item(
        TableName=settings.opspilot_mcp_tokens_table, Key={"id": {"S": _ITEM_ID}}
    ).get("Item")
    if existing is None or existing.get("revoked", {}).get("BOOL", False):
        return False

    client.update_item(
        TableName=settings.opspilot_mcp_tokens_table,
        Key={"id": {"S": _ITEM_ID}},
        UpdateExpression="SET revoked = :true, revoked_at = :now",
        ExpressionAttributeValues={
            ":true": {"BOOL": True},
            ":now": {"S": datetime.now(timezone.utc).isoformat()},
        },
    )
    logger.info("mcp_token_revoked")
    return True


def get_status() -> McpTokenStatusResult:
    """Safe-to-display status -- never returns the token or its hash."""
    settings = get_settings()
    client = get_dynamodb_client()

    item = client.get_item(
        TableName=settings.opspilot_mcp_tokens_table, Key={"id": {"S": _ITEM_ID}}
    ).get("Item")
    if item is None:
        return McpTokenStatusResult(has_active_token=False, created_at=None, revoked_at=None)

    revoked = item.get("revoked", {}).get("BOOL", False)
    return McpTokenStatusResult(
        has_active_token=not revoked,
        created_at=item.get("created_at", {}).get("S"),
        revoked_at=item.get("revoked_at", {}).get("S"),
    )


def is_token_valid(plaintext: str | None) -> bool:
    """Used by the MCP server transport (app/mcp/server.py) to gate every
    tool call (roadmap 3.6). Fails closed on every branch -- missing
    token, no token ever generated, revoked token, wrong token, or a
    DynamoDB error looking it up -- never raises out of here, since a
    lookup failure must reject the call, not silently let it through.
    """
    if not plaintext:
        return False

    settings = get_settings()
    client = get_dynamodb_client()

    try:
        item = client.get_item(
            TableName=settings.opspilot_mcp_tokens_table, Key={"id": {"S": _ITEM_ID}}
        ).get("Item")
    except Exception:
        logger.exception("mcp_token_validation_lookup_failed")
        return False

    if item is None:
        return False
    if item.get("revoked", {}).get("BOOL", False):
        return False

    token_hash = item.get("token_hash", {}).get("S")
    if not token_hash:
        return False

    try:
        return bcrypt.checkpw(plaintext.encode("utf-8"), token_hash.encode("utf-8"))
    except ValueError:
        # Malformed/corrupt stored hash -- fail closed, not a 500.
        logger.exception("mcp_token_hash_compare_failed")
        return False
