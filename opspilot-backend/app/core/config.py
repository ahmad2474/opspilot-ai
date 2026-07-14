"""Centralized, env-driven configuration.

Nothing in this app should hardcode a region, model name, instance ID, or
URL outside of this module's defaults. Everything is overridable via .env.
"""
from __future__ import annotations

from functools import lru_cache
from typing import Literal

from dotenv import load_dotenv
from pydantic_settings import BaseSettings, SettingsConfigDict

load_dotenv()

LLMProviderName = Literal["groq", "gemini", "nvidia"]

# Fixed fallback order: try primary first, then walk this list skipping
# whichever provider was already tried as primary.
PROVIDER_FALLBACK_ORDER: tuple[LLMProviderName, ...] = ("groq", "gemini", "nvidia")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="", env_file=".env", extra="ignore")

    # --- AWS ---------------------------------------------------------
    # AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY are deliberately NOT modeled
    # here. boto3 reads them from the environment on its own; keeping them
    # out of this object means they never get logged or serialized by
    # accident (e.g. in a debug endpoint that dumps settings).
    aws_region: str = "us-east-1"

    # The EC2 instance the agent investigates. Set once per environment,
    # never hardcoded in a tool/service.
    opspilot_ec2_instance_id: str | None = None

    # --- LLM provider selection ---------------------------------------
    opspilot_llm_primary_provider: LLMProviderName = "groq"

    groq_api_key: str | None = None
    groq_model: str = "llama-3.3-70b-versatile"
    groq_base_url: str = "https://api.groq.com/openai/v1"

    gemini_api_key: str | None = None
    gemini_model: str = "gemini-flash-latest"
    gemini_base_url: str = "https://generativelanguage.googleapis.com/v1beta/openai/"
    gemini_embedding_model: str = "gemini-embedding-001"

    nvidia_api_key: str | None = None
    nvidia_model: str = "meta/llama-3.3-70b-instruct"
    nvidia_base_url: str = "https://integrate.api.nvidia.com/v1"

    # --- App -----------------------------------------------------------
    opspilot_app_env: Literal["local", "ci", "prod"] = "local"
    opspilot_cors_origins: str = "http://localhost:3000"

    # --- Investigation memory (RAG) -------------------------------------
    opspilot_investigations_table: str = "opspilot-investigations"

    # --- MCP token auth + audit log (Section 3.6) ------------------------
    # Both DynamoDB, not Postgres -- see docs/BUILD_PROGRESS.md "Decisions
    # made" (2026-07-11): this app has no Postgres infrastructure anywhere,
    # DynamoDB is the only persistent datastore already in use
    # (investigations table above), and stays inside the free tier at this
    # single-admin scale. opspilot_mcp_tokens_table holds exactly one item
    # (the current hashed token; "Generate" overwrites it, invalidating any
    # previous token). opspilot_audit_log_table holds one item per
    # generate/revoke event -- the narrow slice of Section 4's full audit
    # log that this step is responsible for; Step 7 extends the same table/
    # write path to cover every action type rather than building a second
    # logging mechanism.
    opspilot_mcp_tokens_table: str = "opspilot-mcp-tokens"
    opspilot_audit_log_table: str = "opspilot-audit-log"

    # --- Auth (Section 3.5) ---------------------------------------------
    # Shared with the frontend's AUTH_SHARED_SECRET — used to verify the
    # short-lived HS256 bearer token NextAuth mints on sign-in. This is the
    # server-side session check; the frontend's /login redirect is not
    # trusted as the security boundary on its own. Every non-health route
    # requires a valid token (see app/core/security.py, wired in main.py).
    auth_shared_secret: str | None = None
    # Optional extra check: if set, the token's `sub` claim (the signed-in
    # email) must match this exactly. Leave unset to accept any validly
    # signed token (fine for true single-admin scope).
    admin_email: str | None = None

    @property
    def cors_origins_list(self) -> list[str]:
        origins = self.opspilot_cors_origins.split(",")
        return [origin.strip() for origin in origins if origin.strip()]

    @property
    def provider_order(self) -> tuple[LLMProviderName, ...]:
        """Primary provider first, then the rest of the fixed fallback chain."""
        primary = self.opspilot_llm_primary_provider
        rest = tuple(p for p in PROVIDER_FALLBACK_ORDER if p != primary)
        return (primary, *rest)


@lru_cache
def get_settings() -> Settings:
    """Cached settings instance — read env once per process."""
    return Settings()