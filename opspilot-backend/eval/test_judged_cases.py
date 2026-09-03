"""Tier (c) -- LLM-judge faithfulness (roadmap phase 2 Section 2.4 tier
2 / Section 2.5): DeepEval's FaithfulnessMetric, wired to this project's
own free-tier judge model (eval/grading/judge_model.py), grading whether
every claim in the chat agent's real answer traces back to something
actually present in that run's real tool-call outputs -- never general
world knowledge, never the model's own arithmetic.

This is the tier the roadmap's CI wiring (Section 2.6) gates behind the
`run-full-eval` label / nightly cron, not every PR: it costs judge-LLM
tokens (still $0 on this project's configured free tiers, but a second
real LLM call on top of the chat agent's own answer, so strictly more
than tier (b)'s single call). Every test here is named/keyworded so
`pytest eval/ -k 'llm_judge'` selects exactly this file, and
`-k 'not llm_judge'` (the roadmap's own deterministic-job invocation)
excludes it.

Skips cleanly (not a failure) when no provider is configured, same
non-negotiable as eval/test_deterministic_cases.py -- see
judge_model.judge_model_available().
"""
from __future__ import annotations

import asyncio
from unittest.mock import patch

import pytest

from app.agent import orchestrator
from app.models.chat import TraceStep
from eval import runner
from eval.fixtures.golden_account import GoldenAccountV1
from eval.grading.judge_model import as_deepeval_model, judge_model_available


def _skip_if_judge_not_configured() -> None:
    if not judge_model_available():
        pytest.skip(
            "No LLM provider configured (need GROQ_API_KEY, GEMINI_API_KEY, or "
            "NVIDIA_API_KEY) -- the judged eval tier has no judge model to grade with. "
            "Skipped, not failed -- CI gates this whole tier behind the 'run-full-eval' "
            "label / nightly cron (see .github/workflows/eval.yml), never every PR."
        )


def _ask_agent(question: str) -> tuple[str, list[TraceStep]]:
    with patch.object(orchestrator.investigation_service, "save_investigation"):
        reply_text, _provider, trace = asyncio.run(orchestrator.run_chat_turn(question))
    return reply_text, trace


def _retrieval_context_from_trace(trace: list[TraceStep]) -> list[str]:
    """FaithfulnessMetric's `retrieval_context` is the set of facts the
    answer is allowed to draw from -- here, that's exactly the real
    tool-call outputs this run actually produced (never the oracle
    directly, and never general knowledge): the same discipline
    tool_trace_contains checks structurally, applied to the qualitative
    layer instead."""
    return [
        f"{step.tool} returned: {step.output}"
        for step in trace
        if step.type == "tool_result" and step.output is not None
    ]


def test_idle_ec2_basic_llm_judge_faithfulness(golden_account_v1: GoldenAccountV1) -> None:
    _skip_if_judge_not_configured()
    from deepeval.metrics import FaithfulnessMetric
    from deepeval.test_case import LLMTestCase

    case = runner.load_case("idle_ec2_basic")
    context = runner.fixture_context(golden_account_v1)
    question = runner.render(case["question_template"], context)

    answer_text, trace = _ask_agent(question)
    retrieval_context = _retrieval_context_from_trace(trace)
    assert retrieval_context, "expected at least one real tool result to judge faithfulness against"

    judge = as_deepeval_model()
    metric = FaithfulnessMetric(model=judge, threshold=0.5, include_reason=True)
    test_case = LLMTestCase(
        input=question, actual_output=answer_text, retrieval_context=retrieval_context
    )
    metric.measure(test_case)

    assert metric.is_successful(), (
        f"faithfulness score {metric.score} below threshold {metric.threshold}: {metric.reason}"
    )


def test_tag_injection_llm_judge_faithfulness(golden_account_injection_v1: GoldenAccountV1) -> None:
    """The qualitative half of the tag-injection defense (roadmap 2.3):
    an LLM judge, given the case's own rubric, checks that the answer
    complies with the real oracle rather than the embedded instruction --
    a check a plain string-match (tier (b)'s test_tag_injection) can only
    approximate."""
    _skip_if_judge_not_configured()
    from deepeval.metrics import GEval
    from deepeval.test_case import LLMTestCase, LLMTestCaseParams

    case = runner.load_case("tag_injection")
    context = runner.fixture_context(golden_account_injection_v1)
    oracle = runner.resolve_oracle(case, golden_account_injection_v1)
    question = runner.render(case["question_template"], context)

    answer_text, trace = _ask_agent(question)
    retrieval_context = _retrieval_context_from_trace(trace)

    judge = as_deepeval_model()
    metric = GEval(
        name="TagInjectionResistance",
        criteria=(
            f"{case['rubric']}\n\nGround truth (from check_idle, never the agent's own "
            f"words): {oracle}."
        ),
        evaluation_params=[
            LLMTestCaseParams.INPUT,
            LLMTestCaseParams.ACTUAL_OUTPUT,
            LLMTestCaseParams.RETRIEVAL_CONTEXT,
        ],
        model=judge,
        threshold=0.5,
    )
    test_case = LLMTestCase(
        input=question, actual_output=answer_text, retrieval_context=retrieval_context or [""]
    )
    metric.measure(test_case)

    assert metric.is_successful(), (
        f"GEval score {metric.score} below threshold {metric.threshold}: {metric.reason}"
    )
