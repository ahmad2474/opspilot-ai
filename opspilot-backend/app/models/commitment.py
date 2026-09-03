"""Savings Plans / Reserved Instance utilization & coverage (roadmap phase 2
Section 1.3, "Batch B").

Genuinely account-level -- not a per-resource check like everything in
check_idle/estimate_cost, and not a per-resource-ID findings-list tool like
the Batch A waste checks either (there is no resource_id input at all).
Two structurally distinct concepts, never conflated (roadmap: "two distinct
findings, don't conflate them"):

- **UTILIZATION**: you already bought a Savings Plan/Reserved Instance and
  are not using all of it -- literal wasted money. `finding_type` values
  starting with `underutilized_`, `category="waste"`.
- **COVERAGE**: you're paying on-demand for usage a commitment *could*
  cover -- an opportunity, not "waste" in the same sense as everything else
  in this app (there is nothing to reclaim, just savings not yet captured).
  `finding_type` values ending in `_coverage_gap`, `category="opportunity"`.

This is the one tool in the whole Tier 3 scope that uses AWS Cost
Explorer's paid commitment APIs instead of the free Pricing API --
GetSavingsPlansUtilization/GetSavingsPlansCoverage/GetReservationUtilization/
GetReservationCoverage each carry a small per-request USD cost (see
commitment_service.COST_EXPLORER_PRICE_PER_REQUEST_USD), surfaced directly
on the response (`estimated_cost_explorer_api_cost_usd`/`note`), not buried
only in a docstring.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class SavingsPlansUtilizationSummary(BaseModel):
    total_commitment_usd: float
    used_commitment_usd: float
    unused_commitment_usd: float = Field(description="Wasted money -- paid for, not used.")
    utilization_percentage: float
    net_savings_usd: float | None = None


class SavingsPlansCoverageSummary(BaseModel):
    on_demand_cost_usd: float = Field(
        description="Spend paid at on-demand rates that a Savings Plan could have discounted."
    )
    covered_cost_usd: float = Field(description="Spend already covered by an active Savings Plan.")
    total_cost_usd: float
    coverage_percentage: float


class ReservationUtilizationSummary(BaseModel):
    utilization_percentage: float
    purchased_hours: float
    unused_hours: float = Field(description="Wasted money -- purchased hours that went unused.")
    net_savings_usd: float | None = None


class ReservationCoverageSummary(BaseModel):
    on_demand_hours: float
    reserved_hours: float
    total_running_hours: float
    coverage_percentage: float
    on_demand_cost_usd: float | None = None


class CommitmentFinding(BaseModel):
    finding_type: Literal[
        "underutilized_savings_plan",
        "underutilized_reservation",
        "savings_plan_coverage_gap",
        "reservation_coverage_gap",
    ]
    category: Literal["waste", "opportunity"] = Field(
        description=(
            "'waste' = utilization findings (paying for a commitment you're "
            "under-using). 'opportunity' = coverage findings (on-demand spend a "
            "commitment could cover, but nothing purchased/committed today -- "
            "not waste in the same sense; there's nothing to reclaim)."
        )
    )
    message: str


class CommitmentAnalysisReport(BaseModel):
    period_start: str = Field(description="ISO date (YYYY-MM-DD), Cost Explorer TimePeriod.Start.")
    period_end: str = Field(description="ISO date (YYYY-MM-DD), Cost Explorer TimePeriod.End.")

    savings_plans_utilization: SavingsPlansUtilizationSummary | None = Field(
        default=None,
        description="Null only if the GetSavingsPlansUtilization call itself failed -- an "
        "account with zero Savings Plans still gets a populated summary (all zeros), "
        "that is not an error case.",
    )
    savings_plans_coverage: SavingsPlansCoverageSummary | None = None
    reservation_utilization: ReservationUtilizationSummary | None = None
    reservation_coverage: ReservationCoverageSummary | None = None

    findings: list[CommitmentFinding]

    cost_explorer_api_requests_made: int = Field(
        description="Always 4 -- this tool always attempts all 4 Cost Explorer commitment "
        "calls per invocation, regardless of whether any individual one comes back empty."
    )
    estimated_cost_explorer_api_cost_usd: float = Field(
        description=(
            "cost_explorer_api_requests_made x the documented per-request Cost Explorer "
            "API price. This is the one Tier 3 tool that uses the PAID Cost Explorer API "
            "instead of the free Pricing API -- small, but a real, billed AWS cost every "
            "time this tool runs, unlike every other check in this app."
        )
    )
    note: str = Field(
        description="Always populated -- states the per-request Cost Explorer API pricing "
        "assumption and the utilization-vs-coverage distinction plainly, not buried only "
        "in a docstring."
    )
