"""Regression test for a real, live-observed hang: on 2026-09-03, a single
chat turn took 15m22s end-to-end because no provider call anywhere had a
deadline. Groq rejected instantly (413, oversized prompt), Gemini answered
correctly but then failed a follow-up turn (400), and NVIDIA hung on
`Runner.run` for ~5 minutes per attempt across 3 retries before the whole
request finally gave up with a 503 -- entirely inside the openai SDK's own
retry/backoff, invisible to `run_chat_turn`'s fallback loop the whole time.

`run_chat_turn` must never let one provider's call run unbounded: each
provider gets `opspilot_llm_provider_timeout_seconds` before it's treated
as failed and the loop falls through to the next configured provider.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from app.agent import orchestrator
from app.core.config import Settings


@pytest.mark.asyncio
async def test_run_chat_turn_falls_back_when_a_provider_hangs(monkeypatch):
    settings = Settings(
        groq_api_key="fake-groq-key",
        gemini_api_key="fake-gemini-key",
        opspilot_llm_provider_timeout_seconds=0.05,
    )
    monkeypatch.setattr(orchestrator, "get_settings", lambda: settings)
    monkeypatch.setattr(orchestrator, "_build_agent", lambda provider: object())
    monkeypatch.setattr(orchestrator, "_save_investigation", lambda *a, **k: None)

    calls: list[str] = []

    async def fake_run(agent, user_message):
        calls.append("call")
        if len(calls) == 1:
            # Simulates NVIDIA hanging with no deadline -- this must be cut
            # off by run_chat_turn's own timeout, not actually waited out.
            await asyncio.sleep(10)
            raise AssertionError("hung provider call should have been cancelled")
        return SimpleNamespace(new_items=[], final_output="answer from the second provider")

    monkeypatch.setattr(orchestrator.Runner, "run", fake_run)

    loop = asyncio.get_event_loop()
    started = loop.time()
    reply, provider, _trace = await orchestrator.run_chat_turn("What's idle right now?")
    elapsed = loop.time() - started

    assert reply == "answer from the second provider"
    assert provider == "gemini"
    assert len(calls) == 2
    # Without the fix this takes >=10s (the full sleep); the timeout must
    # cut the hung first attempt off at ~0.05s instead.
    assert elapsed < 2.0


@pytest.mark.asyncio
async def test_run_chat_turn_raises_promptly_when_every_provider_hangs(monkeypatch):
    """The real 2026-09-03 incident: every configured provider ends up
    unreachable. run_chat_turn must still raise within roughly
    num_providers * timeout, not hang forever."""
    settings = Settings(
        groq_api_key="fake-groq-key",
        gemini_api_key="fake-gemini-key",
        nvidia_api_key="fake-nvidia-key",
        opspilot_llm_provider_timeout_seconds=0.05,
    )
    monkeypatch.setattr(orchestrator, "get_settings", lambda: settings)
    monkeypatch.setattr(orchestrator, "_build_agent", lambda provider: object())

    async def fake_run(agent, user_message):
        await asyncio.sleep(10)
        raise AssertionError("hung provider call should have been cancelled")

    monkeypatch.setattr(orchestrator.Runner, "run", fake_run)

    loop = asyncio.get_event_loop()
    started = loop.time()
    with pytest.raises(RuntimeError, match="All configured LLM providers failed"):
        await orchestrator.run_chat_turn("What's idle right now?")
    elapsed = loop.time() - started

    assert elapsed < 2.0
