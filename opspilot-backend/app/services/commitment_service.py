"""Savings Plans / Reserved Instance utilization & coverage (roadmap phase 2
Section 1.3, "Batch B") -- see app/models/commitment.py's module docstring
for the utilization-vs-coverage distinction this tool exists to keep
separate.

Cost Explorer's commitment APIs (GetSavingsPlansUtilization/
GetSavingsPlansCoverage/GetReservationUtilization/GetReservationCoverage)
were verified against the actual installed boto3 (1.35.x) service model
before writing this, and the exact IAM action names were independently
confirmed via real `AccessDenied` errors against a real AWS account (see
docs/BUILD_PROGRESS.md) -- field names below (`UtilizationPercentage`,
`SpendCoveredBySavingsPlans` -- actually `Coverage.OnDemandCost`/
`Coverage.TotalCost`, `CoverageHoursPercentage`, etc.) are not guessed.

Each of the 4 calls is independently wrapped in try/except: an account
with no Savings Plans (or no Reserved Instances) is not an error case at
all -- the API returns a valid response with zeroed totals for that
section -- but a real permission/outage/`DataUnavailableException` failure
on any ONE of the 4 must not blank the other 3, same per-section
graceful-degradation principle as s3_service's per-bucket try/except.
"""
from __future__ import annotations

import logging
from datetime import date, timedelta
from typing import Any

from app.aws.client import get_ce_client
from app.models.commitment import (
    CommitmentAnalysisReport,
    CommitmentFinding,
    ReservationCoverageSummary,
    ReservationUtilizationSummary,
    SavingsPlansCoverageSummary,
    SavingsPlansUtilizationSummary,
)

logger = logging.getLogger("app.services.commitment")

DEFAULT_LOOKBACK_DAYS = 30

# This tool always makes exactly 4 Cost Explorer commitment API calls per
# invocation (one per section below), regardless of whether any individual
# one comes back empty.
COST_EXPLORER_API_REQUESTS_PER_CALL = 4

# AWS Cost Explorer API pricing, commonly cited as $0.01 per API request as
# of this build (the Cost Explorer *console* is free; this is the *API*
# charge) -- verify against AWS's current Cost Explorer pricing page before
# relying on this figure for a real bill, same "documented constant, not
# scraped live" precedent as cost_service.EIP_IDLE_HOURLY_RATE_USD.
COST_EXPLORER_PRICE_PER_REQUEST_USD = 0.01

# Demo-scope thresholds informed directly by the roadmap's own FinOps
# Foundation citation ("~80% commitment coverage for mature orgs, ~60% for
# teams just starting") -- not derived from this account's own spend
# history, same "demo-scope, not derived from real traffic analysis" caveat
# as every threshold in idle_service.py.
SAVINGS_PLAN_UTILIZATION_WASTE_THRESHOLD_PERCENT = 70.0
RESERVATION_UTILIZATION_WASTE_THRESHOLD_PERCENT = 70.0
SAVINGS_PLAN_COVERAGE_OPPORTUNITY_THRESHOLD_PERCENT = 60.0
RESERVATION_COVERAGE_OPPORTUNITY_THRESHOLD_PERCENT = 60.0

# Minimum on-demand dollars in the window before a coverage-gap finding is
# worth surfacing at all -- a trivially small account is technically "0%
# Savings Plan covered" but nobody should be told to go buy a commitment
# for a few dollars a month.
COVERAGE_GAP_MIN_ON_DEMAND_USD = 10.0


def _to_float(value: str | None) -> float:
    try:
        return float(value) if value is not None else 0.0
    except (TypeError, ValueError):
        return 0.0


def _get_savings_plans_utilization(
    client: Any, start: str, end: str
) -> SavingsPlansUtilizationSummary | None:
    try:
        response = client.get_savings_plans_utilization(TimePeriod={"Start": start, "End": end})
    except Exception:  # noqa: BLE001 - one section failing must not blank the rest
        logger.warning("get_savings_plans_utilization failed", exc_info=True)
        return None
    total = response.get("Total", {})
    utilization = total.get("Utilization", {})
    savings = total.get("Savings") or {}
    return SavingsPlansUtilizationSummary(
        total_commitment_usd=_to_float(utilization.get("TotalCommitment")),
        used_commitment_usd=_to_float(utilization.get("UsedCommitment")),
        unused_commitment_usd=_to_float(utilization.get("UnusedCommitment")),
        utilization_percentage=_to_float(utilization.get("UtilizationPercentage")),
        net_savings_usd=_to_float(savings.get("NetSavings")) if savings else None,
    )


def _get_savings_plans_coverage(
    client: Any, start: str, end: str
) -> SavingsPlansCoverageSummary | None:
    try:
        response = client.get_savings_plans_coverage(TimePeriod={"Start": start, "End": end})
    except Exception:  # noqa: BLE001
        logger.warning("get_savings_plans_coverage failed", exc_info=True)
        return None
    entries = response.get("SavingsPlansCoverages", [])
    if not entries:
        return SavingsPlansCoverageSummary(
            on_demand_cost_usd=0.0,
            covered_cost_usd=0.0,
            total_cost_usd=0.0,
            coverage_percentage=0.0,
        )
    # No GroupBy requested -- a single account-wide aggregate row is
    # expected; summed defensively in case AWS ever returns more than one
    # row for the plain (ungrouped) request.
    on_demand = sum(_to_float(e["Coverage"].get("OnDemandCost")) for e in entries)
    total = sum(_to_float(e["Coverage"].get("TotalCost")) for e in entries)
    covered = max(total - on_demand, 0.0)
    coverage_pct = (covered / total * 100.0) if total > 0 else 0.0
    return SavingsPlansCoverageSummary(
        on_demand_cost_usd=round(on_demand, 2),
        covered_cost_usd=round(covered, 2),
        total_cost_usd=round(total, 2),
        coverage_percentage=round(coverage_pct, 2),
    )


def _get_reservation_utilization(
    client: Any, start: str, end: str
) -> ReservationUtilizationSummary | None:
    try:
        response = client.get_reservation_utilization(TimePeriod={"Start": start, "End": end})
    except Exception:  # noqa: BLE001
        logger.warning("get_reservation_utilization failed", exc_info=True)
        return None
    total = response.get("Total", {})
    net_savings = total.get("NetRISavings")
    return ReservationUtilizationSummary(
        utilization_percentage=_to_float(total.get("UtilizationPercentage")),
        purchased_hours=_to_float(total.get("PurchasedHours")),
        unused_hours=_to_float(total.get("UnusedHours")),
        net_savings_usd=_to_float(net_savings) if net_savings is not None else None,
    )


def _get_reservation_coverage(
    client: Any, start: str, end: str
) -> ReservationCoverageSummary | None:
    try:
        response = client.get_reservation_coverage(TimePeriod={"Start": start, "End": end})
    except Exception:  # noqa: BLE001
        logger.warning("get_reservation_coverage failed", exc_info=True)
        return None
    total = response.get("Total", {})
    hours = total.get("CoverageHours", {})
    cost = total.get("CoverageCost") or {}
    return ReservationCoverageSummary(
        on_demand_hours=_to_float(hours.get("OnDemandHours")),
        reserved_hours=_to_float(hours.get("ReservedHours")),
        total_running_hours=_to_float(hours.get("TotalRunningHours")),
        coverage_percentage=_to_float(hours.get("CoverageHoursPercentage")),
        on_demand_cost_usd=_to_float(cost.get("OnDemandCost")) if cost else None,
    )


def _build_findings(
    sp_util: SavingsPlansUtilizationSummary | None,
    sp_cov: SavingsPlansCoverageSummary | None,
    ri_util: ReservationUtilizationSummary | None,
    ri_cov: ReservationCoverageSummary | None,
) -> list[CommitmentFinding]:
    findings: list[CommitmentFinding] = []

    if (
        sp_util is not None
        and sp_util.total_commitment_usd > 0
        and sp_util.utilization_percentage < SAVINGS_PLAN_UTILIZATION_WASTE_THRESHOLD_PERCENT
    ):
        findings.append(
            CommitmentFinding(
                finding_type="underutilized_savings_plan",
                category="waste",
                message=(
                    f"Savings Plan utilization is {sp_util.utilization_percentage:.1f}% "
                    f"(${sp_util.unused_commitment_usd:.2f} of committed spend went unused "
                    f"over this window) -- below the "
                    f"{SAVINGS_PLAN_UTILIZATION_WASTE_THRESHOLD_PERCENT:.0f}% threshold."
                ),
            )
        )

    if (
        ri_util is not None
        and ri_util.purchased_hours > 0
        and ri_util.utilization_percentage < RESERVATION_UTILIZATION_WASTE_THRESHOLD_PERCENT
    ):
        findings.append(
            CommitmentFinding(
                finding_type="underutilized_reservation",
                category="waste",
                message=(
                    f"Reserved Instance utilization is {ri_util.utilization_percentage:.1f}% "
                    f"({ri_util.unused_hours:.1f} purchased hours went unused over this "
                    f"window) -- below the "
                    f"{RESERVATION_UTILIZATION_WASTE_THRESHOLD_PERCENT:.0f}% threshold."
                ),
            )
        )

    if (
        sp_cov is not None
        and sp_cov.on_demand_cost_usd >= COVERAGE_GAP_MIN_ON_DEMAND_USD
        and sp_cov.coverage_percentage < SAVINGS_PLAN_COVERAGE_OPPORTUNITY_THRESHOLD_PERCENT
    ):
        findings.append(
            CommitmentFinding(
                finding_type="savings_plan_coverage_gap",
                category="opportunity",
                message=(
                    f"Only {sp_cov.coverage_percentage:.1f}% of eligible spend is covered "
                    f"by a Savings Plan -- ${sp_cov.on_demand_cost_usd:.2f} was paid "
                    "on-demand this window that a Savings Plan could have discounted. An "
                    "opportunity, not waste: nothing is being paid for unnecessarily today."
                ),
            )
        )

    if (
        ri_cov is not None
        and (ri_cov.on_demand_cost_usd or 0.0) >= COVERAGE_GAP_MIN_ON_DEMAND_USD
        and ri_cov.coverage_percentage < RESERVATION_COVERAGE_OPPORTUNITY_THRESHOLD_PERCENT
    ):
        findings.append(
            CommitmentFinding(
                finding_type="reservation_coverage_gap",
                category="opportunity",
                message=(
                    f"Only {ri_cov.coverage_percentage:.1f}% of eligible instance-hours are "
                    "covered by a Reserved Instance -- an opportunity, not waste: nothing "
                    "is being paid for unnecessarily today."
                ),
            )
        )

    return findings


def analyze_commitment_utilization(days: int = DEFAULT_LOOKBACK_DAYS) -> CommitmentAnalysisReport:
    """Roadmap phase 2 Section 1.3. Account-level, no resource_id -- there
    is nothing to scope this to but the whole account's billing data.

    Cost Explorer's TimePeriod.End must not be in the future; window is
    [today - days, today), a trailing lookback ending today. `days` has a
    demo-scope default (30) but is always caller-overridable -- there is no
    single "correct" lookback window any more than there's a universal
    snapshot retention default elsewhere in this app.

    Makes 4 real, individually-billed Cost Explorer API calls every time
    this runs (see COST_EXPLORER_PRICE_PER_REQUEST_USD) -- the one place in
    this app's whole Tier 3 scope that reaches for the paid Cost Explorer
    API instead of the free Pricing API, surfaced directly on the response
    (`estimated_cost_explorer_api_cost_usd`/`note`), not just in this
    docstring.
    """
    client = get_ce_client()
    end = date.today()
    start = end - timedelta(days=days)
    start_str, end_str = start.isoformat(), end.isoformat()

    sp_util = _get_savings_plans_utilization(client, start_str, end_str)
    sp_cov = _get_savings_plans_coverage(client, start_str, end_str)
    ri_util = _get_reservation_utilization(client, start_str, end_str)
    ri_cov = _get_reservation_coverage(client, start_str, end_str)

    findings = _build_findings(sp_util, sp_cov, ri_util, ri_cov)

    return CommitmentAnalysisReport(
        period_start=start_str,
        period_end=end_str,
        savings_plans_utilization=sp_util,
        savings_plans_coverage=sp_cov,
        reservation_utilization=ri_util,
        reservation_coverage=ri_cov,
        findings=findings,
        cost_explorer_api_requests_made=COST_EXPLORER_API_REQUESTS_PER_CALL,
        estimated_cost_explorer_api_cost_usd=round(
            COST_EXPLORER_API_REQUESTS_PER_CALL * COST_EXPLORER_PRICE_PER_REQUEST_USD, 4
        ),
        note=(
            "This tool calls AWS Cost Explorer's paid commitment APIs "
            f"({COST_EXPLORER_API_REQUESTS_PER_CALL} requests per call, "
            f"~${COST_EXPLORER_PRICE_PER_REQUEST_USD:.2f} each as of this build -- verify "
            "against AWS's current Cost Explorer API pricing page) -- unlike every other "
            "waste check in this app, which uses the free Pricing/Describe*/List* APIs. "
            "'Utilization' findings are wasted money (paying for an under-used "
            "commitment); 'coverage' findings are a savings opportunity, not waste in the "
            "same sense (nothing purchased today, so nothing to reclaim)."
        ),
    )
