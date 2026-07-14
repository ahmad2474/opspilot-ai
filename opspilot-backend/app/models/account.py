"""Connected-account identity shape (roadmap Section 5's Settings tab,
"Connected account + IAM role ARN" section).

Deliberately minimal -- account_id and region only. This app uses a
static IAM user access key (no assumed role), so there is no real "IAM
role ARN" to show (see docs/SECURITY.md Section 3); showing the bare
account_id is the explicit ask, but there is no reason to also surface
the raw IAM principal ARN/UserId STS's GetCallerIdentity returns on top
of that. Same reasoning as the Step 5 ARN-stripping security fix this app
already applies elsewhere: an ARN embeds the 12-digit account ID into
caller-facing text, which is exactly the kind of leak vector that fix
exists to prevent -- no reason to reopen it here.

This model is dashboard-HTTP-only (see app/services/account_service.py
and app/api/routes/aws_account.py) -- never wired into an MCP tool or the
chat agent's tool list, so an account ID can never reach the LLM
provider.
"""
from __future__ import annotations

from pydantic import BaseModel


class AccountIdentity(BaseModel):
    account_id: str
    region: str
