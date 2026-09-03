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
    # "llama-3.3-70b-versatile" was retired from Groq's catalog (live-verified
    # 2026-09-02: a real completion call returned 404 model_not_found; GET
    # /v1/models with the same key shows no plain Llama chat model at all
    # anymore, only openai/gpt-oss-*, qwen/*, groq/compound* etc).
    #
    # openai/gpt-oss-120b was tried first and rejected: live-verified against
    # this app's real agentic tool-calling loop (not just a single completion
    # call), a broad multi-region "what's idle" question drove it into
    # persistent 429 Too Many Requests for 6.5 minutes straight before the
    # request finally gave up with a 503 -- effectively broken for real use,
    # even though a single isolated completion call to it succeeds fine.
    # qwen/qwen3.6-27b was rejected too: it leaks its raw <think>...</think>
    # reasoning directly into the visible message content instead of a
    # separate hidden channel, which would show up as literal text in the
    # chat UI. groq/compound-mini has a much larger token budget but flatly
    # rejects tool-calling ("`tool calling` is not supported with this
    # model") -- a hard disqualifier for an app whose entire agent loop is
    # tool calls.
    #
    # openai/gpt-oss-20b is the one that actually held up: live-verified with
    # a real single-tool-call chat question through the running app end to
    # end (real AWS account, real Groq call, correct answer, reasoning trace
    # rendered) in ~32s including one transient 429 that resolved on its own
    # retry -- not the 120b model's runaway failure. Same reasoning-tokens
    # behavior as 120b (hidden, not visible, harmless since no caller here
    # sets a low max_tokens), just a smaller model with a rate limit this
    # account's free tier can actually sustain for this app's tool-call
    # volume. A heavy multi-region investigation can still occasionally hit
    # a 429 -- it retries and succeeds rather than failing outright, but this
    # is worth revisiting if it proves too slow under real use.
    groq_model: str = "openai/gpt-oss-20b"
    groq_base_url: str = "https://api.groq.com/openai/v1"

    gemini_api_key: str | None = None
    gemini_model: str = "gemini-flash-latest"
    gemini_base_url: str = "https://generativelanguage.googleapis.com/v1beta/openai/"
    gemini_embedding_model: str = "gemini-embedding-001"

    nvidia_api_key: str | None = None
    nvidia_model: str = "meta/llama-3.3-70b-instruct"
    nvidia_base_url: str = "https://integrate.api.nvidia.com/v1"

    # Per-provider deadline for one run_chat_turn attempt (app/agent/
    # orchestrator.py). Live-verified bug, 2026-09-03: with no deadline
    # anywhere in the LLM call path, a single chat turn hung 15m22s --
    # Groq rejected instantly (413), Gemini answered but then failed a
    # follow-up turn (400), and NVIDIA hung on Runner.run for ~5 minutes
    # per attempt across 3 internal SDK retries before finally giving up.
    # run_chat_turn wraps each provider's Runner.run in
    # asyncio.wait_for(..., timeout=this) so a dead/hanging provider is
    # abandoned and the next one tried, instead of blocking the whole
    # turn. 45s is generous headroom over every real successful call
    # observed in this app's logs (a full multi-tool-call turn has taken
    # well under 15s), while still cutting off a hang like NVIDIA's long
    # before it costs minutes.
    opspilot_llm_provider_timeout_seconds: float = 45.0

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