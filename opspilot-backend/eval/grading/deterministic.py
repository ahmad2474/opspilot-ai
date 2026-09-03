"""Deterministic grading (roadmap phase 2 Section 2.4, tier 1): extract
numbers/entities from the chat agent's actual final answer text and
compare against the oracle, with an explicit tolerance where the roadmap
calls for one (dollar estimates) and none where it doesn't (counts,
resource IDs, idle-day figures).

Deliberately simple, regex-based extraction rather than any NLP/semantic
layer -- "deterministic" per the roadmap means "the same input always
produces the same verdict, no LLM in the loop," not "linguistically
sophisticated." A human-readable answer like "You have 4 idle resources"
or "$3.65/month" is checked by scanning for number-shaped tokens in the
text, not by trying to bind a specific number to a specific claimed
meaning -- the accompanying `tool_trace_contains`/faithfulness layers
(tiers 2-3) are what catch a number that's merely *present* in the text
but attached to the wrong claim.

Every check function here takes the full answer text plus whatever it
needs from the oracle/case, and returns a `CheckResult` -- never raises,
so a runner can collect every check's outcome for one case instead of
stopping at the first failure.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from app.models.chat import TraceStep

# Matches an integer or decimal number, optionally preceded by a currency
# sign and/or comma-grouped (e.g. "4", "$3.65", "1,234", "3.65").
_NUMBER_RE = re.compile(r"[-+]?\$?\d[\d,]*(?:\.\d+)?")


@dataclass(frozen=True)
class CheckResult:
    check_type: str
    passed: bool
    detail: str


def _numbers_in_text(text: str) -> list[float]:
    values: list[float] = []
    for match in _NUMBER_RE.findall(text):
        cleaned = match.replace("$", "").replace(",", "")
        try:
            values.append(float(cleaned))
        except ValueError:
            continue
    return values


def exact_number_match(answer_text: str, expected: int | float, label: str) -> CheckResult:
    """The exact expected value must appear verbatim as a number token
    somewhere in the answer -- used for counts, resource IDs' numeric
    parts, and idle-day figures, where the roadmap calls for an exact
    match, never a fuzzy one."""
    found = _numbers_in_text(answer_text)
    passed = any(value == float(expected) for value in found)
    return CheckResult(
        check_type="exact_number_match",
        passed=passed,
        detail=(
            f"{label}: expected exact value {expected!r} "
            f"{'found' if passed else 'NOT found'} among number tokens {found} in the answer."
        ),
    )


def tolerance_number_match(
    answer_text: str, expected: float, tolerance_pct: float, label: str
) -> CheckResult:
    """At least one number token in the answer must fall within
    `tolerance_pct` percent of `expected` -- used for dollar estimates,
    since Pricing API values can drift (roadmap 2.4)."""
    found = _numbers_in_text(answer_text)
    if expected == 0:
        passed = any(abs(value) < 1e-9 for value in found)
    else:
        tolerance = abs(expected) * (tolerance_pct / 100.0)
        passed = any(abs(value - expected) <= tolerance for value in found)
    return CheckResult(
        check_type="tolerance_number_match",
        passed=passed,
        detail=(
            f"{label}: expected {expected!r} +/-{tolerance_pct}% "
            f"{'matched by' if passed else 'NOT matched by any of'} number tokens {found}."
        ),
    )


def tool_trace_contains(trace: list[TraceStep], tool_name: str) -> CheckResult:
    """Asserts the named tool was actually called during this run (roadmap
    2.4 tier 3: tool-trace correctness) -- a right-but-lucky answer that
    skipped the real tool call must fail even if its numbers happen to
    match. Reads the *real* reasoning trace `run_chat_turn` already
    returns (agent/orchestrator.py's `_extract_trace`), not a re-derived
    or separately-logged trace -- no second source of truth for what the
    agent actually did.
    """
    called = {step.tool for step in trace if step.type == "tool_call" and step.tool}
    passed = tool_name in called
    verb = "was" if passed else "was NOT"
    return CheckResult(
        check_type="tool_trace_contains",
        passed=passed,
        detail=f"tool={tool_name!r} {verb} called; tools called: {sorted(called)}",
    )


def tool_trace_contains_none(trace: list[TraceStep]) -> CheckResult:
    """The counterpart to tool_trace_contains -- asserts NO tool was
    called at all, used by the out-of-scope-redirect case (roadmap 3.8):
    a question with nothing to investigate should never trigger a tool
    call in the first place, not just fail to find anything useful with
    one.
    """
    called = {step.tool for step in trace if step.type == "tool_call" and step.tool}
    passed = not called
    return CheckResult(
        check_type="tool_trace_contains_none",
        passed=passed,
        detail=(
            "no tool was called, as expected."
            if passed
            else f"expected no tool calls, but these were called: {sorted(called)}"
        ),
    )


def string_must_not_contain(answer_text: str, forbidden: list[str]) -> CheckResult:
    """None of `forbidden` may appear (case-insensitive) -- used for the
    young-resource edge case (never claim a longer idle streak than the
    resource's real age) and the tag-injection case (never comply with an
    embedded instruction)."""
    lowered = answer_text.lower()
    hits = [phrase for phrase in forbidden if phrase.lower() in lowered]
    return CheckResult(
        check_type="string_must_not_contain",
        passed=not hits,
        detail=(
            "no forbidden phrase found."
            if not hits
            else f"forbidden phrase(s) found in answer: {hits}"
        ),
    )


def string_must_contain(answer_text: str, required: list[str]) -> CheckResult:
    """Every phrase in `required` must appear (case-insensitive) --
    counterpart to string_must_not_contain, used to assert a real fact
    (e.g. a resource ID, or the calm out-of-scope redirect language) is
    actually present, not merely that nothing bad is present."""
    lowered = answer_text.lower()
    missing = [phrase for phrase in required if phrase.lower() not in lowered]
    return CheckResult(
        check_type="string_must_contain",
        passed=not missing,
        detail=(
            "every required phrase found."
            if not missing
            else f"required phrase(s) missing from answer: {missing}"
        ),
    )


__all__ = [
    "CheckResult",
    "exact_number_match",
    "tolerance_number_match",
    "tool_trace_contains",
    "tool_trace_contains_none",
    "string_must_not_contain",
    "string_must_contain",
]
