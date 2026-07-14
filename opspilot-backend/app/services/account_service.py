"""Connected AWS account identity (roadmap Section 5's Settings tab).

Dashboard-only, on purpose: `get_connected_account()` is consumed solely
by app/api/routes/aws_account.py's GET /aws/account. Do NOT wire this into
an MCP tool (app/mcp/server.py) or the chat agent's tool list
(app/agent/orchestrator.py) -- an AWS account ID reaching the LLM provider
via a tool call/response is exactly the leak vector the Step 5 ARN-
stripping fix already exists to prevent (see app/models/account.py's
docstring). If a future step genuinely needs account identity inside a
chat/MCP tool, that is a new, deliberate security decision to make
explicitly -- not something to fall into by importing this function from
a tool module.
"""
from __future__ import annotations

from functools import lru_cache

from app.aws.client import get_sts_client
from app.core.config import get_settings
from app.models.account import AccountIdentity


@lru_cache
def get_connected_account() -> AccountIdentity:
    """The AWS account this app's static credentials belong to, plus the
    configured region. Cached at process level -- like this file's peers
    (e.g. app/aws/client.py's _session()), the account identity never
    changes for the life of a process using a fixed IAM user access key,
    so there is no reason to pay for a live STS round trip on every call.

    Deliberately returns ONLY account_id and region -- never the Arn or
    UserId fields GetCallerIdentity also returns (see AccountIdentity's
    docstring for why).
    """
    client = get_sts_client()
    identity = client.get_caller_identity()
    return AccountIdentity(
        account_id=identity["Account"],
        region=get_settings().aws_region,
    )
