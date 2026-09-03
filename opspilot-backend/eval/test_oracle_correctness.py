"""Tier (a) -- oracle/fixture correctness (roadmap phase 2 Section 2.1-2.2).

Zero LLM calls, zero network beyond moto's in-process simulation, always
runs (no `llm_judge` keyword, no provider check, no skip condition) --
this is the actual free floor of the eval harness: it proves the golden
account fixtures produce the exact ground truth this harness's other
tiers assume, by calling the real `services/` functions directly, the
same "generate ground truth from services/, never hand-type it" rule
every other tier here depends on.

This file existing separately from eval/test_deterministic_cases.py is a
deliberate split, not accidental duplication: test_deterministic_cases.py
grades the chat agent's *actual answer* against the oracle (which needs a
live, if free-tier, LLM call to produce that answer -- see that file's
own module docstring for why). These tests never touch the chat agent at
all -- they only prove the oracle itself is trustworthy, which has to be
established independently of whether any LLM is configured.
"""
from __future__ import annotations

from eval.fixtures.golden_account import GoldenAccountV1
from eval.oracle.build_oracle import oracle_check_idle, oracle_estimate_cost, oracle_scan_region


def test_idle_ec2_instance_is_idle(golden_account_v1: GoldenAccountV1) -> None:
    result = oracle_check_idle(
        "ec2", golden_account_v1.idle_ec2_instance_id, golden_account_v1.idle_window_days
    )
    assert result["is_idle"] is True
    assert result["idle_days"] == golden_account_v1.idle_window_days
    assert result["younger_than_window"] is False
    assert result["idle_since_is_estimated"] is False


def test_active_ec2_instance_is_not_idle(golden_account_v1: GoldenAccountV1) -> None:
    result = oracle_check_idle(
        "ec2", golden_account_v1.active_ec2_instance_id, golden_account_v1.idle_window_days
    )
    assert result["is_idle"] is False


def test_idle_rds_instance_is_idle(golden_account_v1: GoldenAccountV1) -> None:
    result = oracle_check_idle(
        "rds", golden_account_v1.idle_rds_identifier, golden_account_v1.idle_window_days
    )
    assert result["is_idle"] is True
    assert result["idle_days"] == golden_account_v1.idle_window_days


def test_unassociated_eip_is_idle_with_estimated_flag(golden_account_v1: GoldenAccountV1) -> None:
    """The real field this build step verified idle_since_is_estimated=True
    actually applies to (EIP has no creation timestamp at all -- see
    app/models/idle.py's docstring) -- deliberately not the RDS-based
    example the phase-2 doc's own illustrative YAML used, since that doc
    was written without repo access and, verified against the actual
    code, RDS's CloudWatch-metrics-driven branch never sets this flag
    (see young_resource_edge_case.yaml's module note for the full
    reasoning)."""
    result = oracle_check_idle(
        "eip", golden_account_v1.unassociated_eip_allocation_id, golden_account_v1.idle_window_days
    )
    assert result["is_idle"] is True
    assert result["idle_since_is_estimated"] is True
    assert result["idle_days"] == golden_account_v1.idle_window_days


def test_young_unattached_ebs_reports_younger_than_window_not_a_fabricated_streak(
    golden_account_v1: GoldenAccountV1,
) -> None:
    """The actual young-resource edge case this harness exercises: a
    resource genuinely younger than the requested idle window must report
    its real (short) age, never the full requested window."""
    result = oracle_check_idle(
        "ebs", golden_account_v1.unattached_ebs_volume_id, golden_account_v1.idle_window_days
    )
    assert result["is_idle"] is True
    assert result["younger_than_window"] is True
    assert result["idle_since_is_estimated"] is False
    # +1: _instant_idle_result's idle_days is inclusive of both the
    # creation day and today (same "+1" convention as
    # idle_service._trailing_idle_streak) -- verified against the actual
    # formula, not assumed to be a bare day-count difference.
    assert result["idle_days"] == golden_account_v1.young_resource_age_days + 1
    assert result["idle_days"] < golden_account_v1.idle_window_days


def test_estimate_cost_returns_real_nonzero_figures(golden_account_v1: GoldenAccountV1) -> None:
    """Proves the fake_pricing_client stand-in (see golden_account.py's
    module docstring on moto's real Pricing-API gap) actually flows
    through cost_service end to end, rather than every cost estimate
    silently coming back None."""
    result = oracle_estimate_cost("ec2", golden_account_v1.idle_ec2_instance_id)
    assert result["projected_monthly"] is not None
    assert result["projected_monthly"] > 0
    assert result["hourly_rate"] == 0.0104


def test_scan_region_idle_totals_match_the_sum_of_individual_checks(
    golden_account_v1: GoldenAccountV1,
) -> None:
    """scan_region's own idle_count/idle_monthly_waste totals (the exact
    tool the roadmap's own illustrative idle_ec2_basic.yaml example names)
    must agree with summing the same per-resource oracle facts
    independently -- this is the harness checking its own arithmetic, not
    just trusting scan_region's aggregation blindly."""
    scan = oracle_scan_region(golden_account_v1.region)
    assert scan["error"] is None

    idle_resources = [r for r in scan["resources"] if r.get("idle") and r["idle"]["is_idle"]]
    assert len(idle_resources) == scan["totals"]["idle_count"]

    resource_ids = {r["id"] for r in scan["resources"]}
    assert golden_account_v1.idle_ec2_instance_id in resource_ids
    assert golden_account_v1.active_ec2_instance_id in resource_ids
    assert golden_account_v1.unattached_ebs_volume_id in resource_ids
    assert golden_account_v1.unassociated_eip_allocation_id in resource_ids
    assert golden_account_v1.idle_rds_identifier in resource_ids

    expected_waste = round(
        sum(r["cost"]["projected_monthly"] for r in idle_resources if r.get("cost")), 2
    )
    assert scan["totals"]["idle_monthly_waste"] == expected_waste


def test_tag_injection_fixture_oracle_is_unaffected_by_the_malicious_tag(
    golden_account_injection_v1: GoldenAccountV1,
) -> None:
    """The oracle-layer half of the tag-injection defense (roadmap 2.3):
    check_idle never reads tags at all, so a malicious Name tag cannot
    possibly change the ground truth -- the idle EC2 instance here is
    identical in every way except its Name tag to the plain
    golden_account_v1 fixture's idle instance, and must produce the
    identical idle verdict. (Whether the *chat agent* also resists the
    embedded instruction in its visible answer is a separate, live-LLM
    question -- see eval/test_deterministic_cases.py's
    test_tag_injection_* case.)
    """
    result = oracle_check_idle(
        "ec2",
        golden_account_injection_v1.idle_ec2_instance_id,
        golden_account_injection_v1.idle_window_days,
    )
    assert result["is_idle"] is True
    assert result["idle_days"] == golden_account_injection_v1.idle_window_days
