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
    """Analyze Savings Plan / Reserved Instance utilization AND coverage,
    account-level. See system instructions for the waste-vs-opportunity
    distinction. Calls AWS Cost Explorer's PAID commitment APIs (4
    requests/call -- see response `note`/`estimated_cost_explorer_api_cost_usd`)."""
    logger.info("tool_call analyze_commitment_utilization days=%d", days)
    result = commitment_service.analyze_commitment_utilization(days)
    logger.info(
        "tool_result analyze_commitment_utilization findings=%d", len(result.findings)
    )
    return result.model_dump_json()
