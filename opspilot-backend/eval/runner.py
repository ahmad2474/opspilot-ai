"""Ties eval/cases/*.yaml to the oracle and grading layers.

Deliberately NOT a fully generic "YAML describes everything, one engine
runs every case" framework -- with five cases in the bank, an explicit
test function per case (eval/test_deterministic_cases.py) stays far more
readable and debuggable than a generic interpreter would, and matches
this repo's existing preference for plain, explicit code over
metaprogramming. What *is* shared and worth centralizing here: loading a
case file, rendering its `{field}` placeholders against a fixture's real
(fixture-run-specific, e.g. moto-random resource IDs) values, resolving
its declared oracle, and running its declared `checks` list against a
real answer -- the exact same three steps every case needs, regardless of
which specific fixture/question it uses.
"""
from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
from typing import Any

import yaml

from app.models.chat import TraceStep
from eval.grading import deterministic as det
from eval.oracle.build_oracle import oracle_check_idle, oracle_estimate_cost, oracle_scan_region

CASES_DIR = Path(__file__).parent / "cases"


def load_case(name: str) -> dict[str, Any]:
    with open(CASES_DIR / f"{name}.yaml", encoding="utf-8") as f:
        return yaml.safe_load(f)


def render(value: Any, context: dict[str, Any]) -> Any:
    """Recursively `.format(**context)`s every string in `value` (a
    string, or a list/dict of them) -- used for both a case's
    `question_template` and any templated strings inside its `checks`
    (e.g. young_resource_edge_case.yaml's forbidden phrases, which
    reference `{idle_window_days}`)."""
    if isinstance(value, str):
        return value.format(**context)
    if isinstance(value, list):
        return [render(item, context) for item in value]
    if isinstance(value, dict):
        return {key: render(item, context) for key, item in value.items()}
    return value


def fixture_context(account: Any) -> dict[str, Any]:
    """A plain dict of every field on a GoldenAccountV1 (or None dataclass
    for fixture-less cases) -- the substitution context for `render()`."""
    if account is None:
        return {}
    return asdict(account)


def _get_dotted(d: dict[str, Any], dotted_path: str) -> Any:
    current: Any = d
    for part in dotted_path.split("."):
        current = current[part]
    return current


def resolve_oracle(case: dict[str, Any], account: Any) -> Any:
    """Calls the real services/ function the case's `oracle.tool` names,
    against the resource/region the case points at -- this is the one
    ground-truth value every check in the case compares the chat agent's
    actual answer to."""
    oracle_spec = case.get("oracle")
    if not oracle_spec:
        return None

    tool = oracle_spec["tool"]
    if tool == "scan_region":
        return oracle_scan_region(account.region)
    if tool == "check_idle":
        resource_id = getattr(account, oracle_spec["resource_id_field"])
        return oracle_check_idle(
            oracle_spec["resource_type"], resource_id, account.idle_window_days
        )
    if tool == "estimate_cost":
        resource_id = getattr(account, oracle_spec["resource_id_field"])
        return oracle_estimate_cost(oracle_spec["resource_type"], resource_id)
    raise ValueError(f"Unknown oracle.tool {tool!r} in case {case['name']!r}")


def run_checks(
    case: dict[str, Any],
    context: dict[str, Any],
    oracle: Any,
    answer_text: str,
    trace: list[TraceStep],
) -> list[det.CheckResult]:
    """Runs every check the case declares, against the real chat-agent
    answer/trace for this run -- collects every result rather than
    stopping at the first failure, so a test failure message shows every
    check's outcome at once."""
    results: list[det.CheckResult] = []
    for raw_check in case.get("checks", []):
        check = render(raw_check, context)
        check_type = check["type"]
        label = check.get("label", check_type)

        if check_type == "exact_number_match":
            expected = _get_dotted(oracle, check["field"])
            results.append(det.exact_number_match(answer_text, expected, label))
        elif check_type == "tolerance_number_match":
            expected = _get_dotted(oracle, check["field"])
            results.append(
                det.tolerance_number_match(answer_text, expected, check["tolerance_pct"], label)
            )
        elif check_type == "tool_trace_contains":
            results.append(det.tool_trace_contains(trace, check["tool"]))
        elif check_type == "tool_trace_contains_none":
            results.append(det.tool_trace_contains_none(trace))
        elif check_type == "string_must_not_contain":
            results.append(det.string_must_not_contain(answer_text, check["forbidden"]))
        elif check_type == "string_must_contain":
            results.append(det.string_must_contain(answer_text, check["required"]))
        else:
            raise ValueError(f"Unknown check type {check_type!r} in case {case['name']!r}")
    return results


def assert_all_passed(results: list[det.CheckResult]) -> None:
    failures = [r for r in results if not r.passed]
    if failures:
        detail = "\n".join(f"  - [{r.check_type}] {r.detail}" for r in results)
        raise AssertionError(f"{len(failures)}/{len(results)} check(s) failed:\n{detail}")


__all__ = [
    "load_case",
    "render",
    "fixture_context",
    "resolve_oracle",
    "run_checks",
    "assert_all_passed",
]
