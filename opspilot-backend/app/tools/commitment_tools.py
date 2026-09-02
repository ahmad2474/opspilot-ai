"""Agent-facing tool for Savings Plan/Reserved Instance utilization &
coverage analysis. Stays thin on purpose -- all the real logic lives in
app.services.commitment_service so it can be unit-tested without touching
the LLM at all.
"""
from __future__ import annotations

import logging
from typing import Annotated

from agents import function_tool

from app.services import commitment_service

logger = logging.getLogger("app.tools.commitment")


@function_tool
def analyze_commitment_utilization(
    days: Annotated[int, "Lookback window in days for the Cost Explorer query."] = 30,
) -> str:
    """Analyze Savings Plan and Reserved Instance utilization AND coverage
    for this account -- account-level, not about a single resource.
    UTILIZATION findings (underutilized_savings_plan/underutilized_reservation)
    are real wasted money: you already bought a commitment and aren't using
    all of it. COVERAGE findings (savings_plan_coverage_gap/
    reservation_coverage_gap) are a savings OPPORTUNITY, not waste in the
    same sense -- you're paying on-demand for usage a commitment could
    cover, but nothing has actually been overspent. Never conflate the two
    when reporting to the user. This is the one tool in this app that calls
    AWS Cost Explorer's PAID commitment APIs (4 requests per call, a small
    but real per-request cost -- see the response's `note`/
    `estimated_cost_explorer_api_cost_usd` and mention it if asked about
    cost of running checks)."""
    logger.info("tool_call analyze_commitment_utilization days=%d", days)
    result = commitment_service.analyze_commitment_utilization(days)
    logger.info(
        "tool_result analyze_commitment_utilization findings=%d", len(result.findings)
    )
    return result.model_dump_json()
