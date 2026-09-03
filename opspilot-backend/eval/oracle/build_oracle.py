"""Ground truth (roadmap phase 2 Section 2.2): thin wrappers around the
real `services/` calls, each returning a plain JSON-serializable dict --
the "answer key" every eval case's checks compare the chat agent's actual
answer against.

The rule this module exists to enforce: an oracle value is *never*
hand-typed and never derived from prompting an LLM to guess. Every
function here does nothing but call the one real `services/` function the
roadmap names for that fact and shape its result into a plain dict. If a
number in an `eval/cases/*.yaml` file doesn't trace back to one of these
calls, it doesn't belong in this harness.
"""
from __future__ import annotations

from app.models.cost import CostEstimate, DateRange
from app.models.idle import IdleCheckResult
from app.models.scan import ScanResponse
from app.services import cost_service, idle_service, scan_service


def oracle_check_idle(
    resource_type: str, resource_id: str, days: int, region: str | None = None
) -> dict:
    """Ground truth for a single resource's idle status -- calls
    idle_service.check_idle directly, bypassing the LLM entirely."""
    result: IdleCheckResult = idle_service.check_idle(
        resource_type, resource_id, days, region=region
    )
    return result.model_dump(mode="json")


def oracle_estimate_cost(
    resource_type: str,
    resource_id: str,
    date_range: DateRange | None = None,
    region: str | None = None,
) -> dict:
    """Ground truth for a single resource's cost estimate -- calls
    cost_service.estimate_cost directly (method='list_price', the only
    implemented method -- see cost_service.py's module docstring)."""
    result: CostEstimate = cost_service.estimate_cost(
        resource_type, resource_id, date_range=date_range, region=region
    )
    return result.model_dump(mode="json")


def oracle_scan_region(region: str, force: bool = True) -> dict:
    """Ground truth for a whole-region scan -- calls
    scan_service.scan_region directly. force=True by default here (unlike
    scan_service's own default) since an eval run always wants a fresh
    scan of the fixture's current state, never a stale cache from an
    earlier test."""
    result: ScanResponse = scan_service.scan_region(region, force=force)
    return result.model_dump(mode="json")


__all__ = ["oracle_check_idle", "oracle_estimate_cost", "oracle_scan_region"]
