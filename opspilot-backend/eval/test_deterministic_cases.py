"""Tier (b) -- end-to-end deterministic grading (roadmap phase 2 Section
2.3/2.4 tier 1 + tier 3): ask the real chat agent (`run_chat_turn`) the
question an eval case names, then grade its actual answer/tool-trace
against the oracle using plain number/string extraction -- no judge LLM
involved anywhere in this file.

**A genuine architectural fork, flagged rather than forced** (see this
build step's own instructions on what to do when one comes up): the
harness's whole point is checking whether the chat agent's *actual*
answer agrees with the oracle, and producing that actual answer
necessarily means one real call to whichever LLM provider is configured
(this project's own free-tier Groq/Gemini/NVIDIA, never OpenAI/a paid
model). That is a genuinely different thing from tier (c)'s LLM-as-judge
step (eval/test_judged_cases.py) -- there is no second "judge" model
call here, no probabilistic qualitative grading, and $0 real spend on
this project's configured free tiers -- but it is still a live network
call to a third-party API, which the build brief's non-negotiable says
the deterministic suite must never *require*. The resolution: every test
below calls `_skip_if_no_provider()` first and skips cleanly (not a
failure) when no provider is configured, so a bare `pytest` in a fresh
checkout (or a fork PR's CI run with no secrets) never errors -- but when
a provider *is* configured (this project's own CI, using its own
free-tier key as a secret), these are exactly the tests that catch a real
regression in what the agent actually tells a user, which
eval/test_oracle_correctness.py's fixture-only tests structurally cannot.

Investigation-memory persistence (`investigation_service.save_investigation`,
called best-effort at the end of every `run_chat_turn`) is patched out in
every test here -- it would otherwise make its own real, uncontrolled
Gemini-embedding network call as a side effect of grading an unrelated
answer. eval/test_deterministic_cases.py's own `test_recall_accuracy`
exercises that code path deliberately and explicitly instead.
"""
from __future__ import annotations

import asyncio
from unittest.mock import patch

import pytest

from app.agent import orchestrator
from app.aws.client import get_dynamodb_client
from app.core.config import get_settings
from app.models.chat import TraceStep
from app.services import investigation_service
from eval import runner
from eval.fixtures.fake_embeddings import deterministic_embedding
from eval.fixtures.golden_account import GoldenAccountV1, mock_aws, moto_env
from eval.oracle.build_oracle import oracle_check_idle


def _any_provider_configured() -> bool:
    settings = get_settings()
    return bool(settings.groq_api_key or settings.gemini_api_key or settings.nvidia_api_key)


def _skip_if_no_provider() -> None:
    if not _any_provider_configured():
        pytest.skip(
            "No LLM provider configured (need GROQ_API_KEY, GEMINI_API_KEY, or "
            "NVIDIA_API_KEY) -- this case needs a real chat-agent answer to grade "
            "deterministically. Skipped, not failed: the deterministic eval tier "
            "must never require these to be set (see this file's module docstring)."
        )


def _ask_agent(question: str) -> tuple[str, list[TraceStep]]:
    """Runs one real chat turn, with investigation-memory persistence
    patched to a no-op (see module docstring)."""
    with patch.object(orchestrator.investigation_service, "save_investigation"):
        reply_text, _provider, trace = asyncio.run(orchestrator.run_chat_turn(question))
    return reply_text, trace


def test_idle_ec2_basic(golden_account_v1: GoldenAccountV1) -> None:
    _skip_if_no_provider()
    case = runner.load_case("idle_ec2_basic")
    context = runner.fixture_context(golden_account_v1)
    oracle = runner.resolve_oracle(case, golden_account_v1)
    question = runner.render(case["question_template"], context)

    answer_text, trace = _ask_agent(question)

    results = runner.run_checks(case, context, oracle, answer_text, trace)
    runner.assert_all_passed(results)


def test_young_resource_edge_case(golden_account_v1: GoldenAccountV1) -> None:
    _skip_if_no_provider()
    case = runner.load_case("young_resource_edge_case")
    context = runner.fixture_context(golden_account_v1)
    oracle = runner.resolve_oracle(case, golden_account_v1)
    question = runner.render(case["question_template"], context)

    answer_text, trace = _ask_agent(question)

    results = runner.run_checks(case, context, oracle, answer_text, trace)
    runner.assert_all_passed(results)


def test_tag_injection(golden_account_injection_v1: GoldenAccountV1) -> None:
    _skip_if_no_provider()
    case = runner.load_case("tag_injection")
    context = runner.fixture_context(golden_account_injection_v1)
    oracle = runner.resolve_oracle(case, golden_account_injection_v1)
    question = runner.render(case["question_template"], context)

    answer_text, trace = _ask_agent(question)

    results = runner.run_checks(case, context, oracle, answer_text, trace)
    runner.assert_all_passed(results)


def test_out_of_scope_redirect() -> None:
    """No golden_account fixture requested on purpose (the whole case is
    that no AWS lookup should happen at all) -- moto is still entered
    directly so that IF the agent mistakenly calls a tool anyway, it hits
    a fake, empty moto account rather than any real AWS credentials that
    might happen to be present in this environment."""
    _skip_if_no_provider()
    case = runner.load_case("out_of_scope_redirect")
    question = runner.render(case["question_template"], {})

    with moto_env(), mock_aws():
        answer_text, trace = _ask_agent(question)

    results = runner.run_checks(case, {}, None, answer_text, trace)
    runner.assert_all_passed(results)


def _create_investigations_table() -> None:
    """Provisions the investigations table against the currently-active
    moto DynamoDB backend -- schema matches exactly what
    investigation_service.save_investigation/find_similar_past_investigations
    read and write (a single "id" partition key, everything else a plain
    attribute)."""
    settings = get_settings()
    client = get_dynamodb_client()
    client.create_table(
        TableName=settings.opspilot_investigations_table,
        KeySchema=[{"AttributeName": "id", "KeyType": "HASH"}],
        AttributeDefinitions=[{"AttributeName": "id", "AttributeType": "S"}],
        BillingMode="PAY_PER_REQUEST",
    )


def test_recall_accuracy(golden_account_v1: GoldenAccountV1) -> None:
    """Roadmap phase 2 Section 2.3's recall-accuracy case: a recalled past
    investigation must match, not contradict, the current one.

    No live LLM call anywhere in this test (see this file's module
    docstring and eval/fixtures/fake_embeddings.py's docstring for why the
    embedding step is a deterministic stand-in here) -- this exercises the
    real find_similar_past_investigations ranking/cosine-similarity logic
    and a real moto DynamoDB table, checked against a real, fresh
    check_idle oracle call for the same resource.
    """
    _create_investigations_table()
    idle_id = golden_account_v1.idle_ec2_instance_id

    with patch.object(investigation_service, "_embed", side_effect=deterministic_embedding):
        related = investigation_service.save_investigation(
            question=f"Is {idle_id} idle?",
            trace_summary="Checked CPUUtilization and NetworkIn/NetworkOut over the window.",
            conclusion=(
                f"{idle_id} is idle -- CPU and network traffic have stayed near zero "
                "for the full window checked."
            ),
        )
        unrelated = investigation_service.save_investigation(
            question="What is my S3 bucket's lifecycle policy?",
            trace_summary="Checked GetBucketLifecycleConfiguration.",
            conclusion="No lifecycle policy is configured on this bucket.",
        )

        recalled = investigation_service.find_similar_past_investigations(
            query=f"Didn't we already check whether {idle_id} was idle recently?"
        )

    assert recalled, "expected at least one recalled investigation"
    top = recalled[0]
    assert top.id == related.id, (
        f"expected the genuinely related investigation ({related.id}) to rank first, "
        f"got {top.id!r} (unrelated investigation id: {unrelated.id!r}) -- "
        f"recalled order: {[r.id for r in recalled]}"
    )

    fresh_oracle = oracle_check_idle("ec2", idle_id, golden_account_v1.idle_window_days)
    conclusion_lower = top.conclusion.lower()
    if fresh_oracle["is_idle"]:
        assert "idle" in conclusion_lower and "not idle" not in conclusion_lower, (
            f"fresh oracle says {idle_id} is idle, but the recalled investigation's "
            f"conclusion does not agree: {top.conclusion!r}"
        )
    else:
        assert "not idle" in conclusion_lower or "idle" not in conclusion_lower, (
            f"fresh oracle says {idle_id} is NOT idle, but the recalled investigation's "
            f"conclusion contradicts it: {top.conclusion!r}"
        )
