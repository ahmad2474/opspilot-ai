"""Connected-account identity route (roadmap Section 5's Settings tab,
"Connected account + IAM role ARN" section -- see app/models/account.py's
docstring for why this shows account_id + region instead of a role ARN).

Dashboard-only, deliberately: gated by `require_session` like every other
route, but NOT registered as an MCP tool and NOT added to the chat agent's
tool list (app/agent/orchestrator.py) -- see account_service.py's module
docstring for why that boundary matters here specifically.
"""
from __future__ import annotations

from fastapi import APIRouter

from app.models.account import AccountIdentity
from app.services import account_service

router = APIRouter()


@router.get("/aws/account", response_model=AccountIdentity)
async def get_aws_account() -> AccountIdentity:
    return account_service.get_connected_account()
