"""Agent-facing tool for region-wide scanning (roadmap Section 3.3/3.4).
Stays thin on purpose -- all the real logic (aggregation, caching,
cooldown) lives in app.services.scan_service so it can be unit-tested
without touching the LLM at all, same precedent as idle_tools.py/
cost_tools.py.
"""
from __future__ import annotations

import json
import logging
from typing import Annotated

from agents import function_tool

from app.services import scan_service

logger = logging.getLogger("app.tools.scan")


@function_tool
def scan_region(
    region: Annotated[
        str, "AWS region to scan, e.g. us-east-1. Use list_regions if unsure what's available."
    ],
    force: Annotated[
        bool,
        (
            "True to force a fresh rescan (subject to a short anti-spam cooldown if one "
            "was just run). False (default) to use the cached scan if one exists."
        ),
    ] = False,
) -> str:
    """Scan one AWS region across all 15 roadmap-scoped resource types
    (ec2, ebs, rds, eip, elb, lambda, nat_gateway, dynamodb, elasticache,
    sagemaker, redshift, api_gateway, cloudfront, opensearch, kinesis) --
    returns every resource found plus its idle/cost status, and
    account-wide totals (monthly_spend, idle_count, idle_monthly_waste).
    Use this for broad "what's running / what's this costing me in
    <region>" questions rather than calling check_idle/estimate_cost
    resource-by-resource. If the underlying scan hits a cooldown (a scan
    for this region ran too recently -- still returns any cached data
    alongside the notice), an unrecognized region, or an AWS failure with
    no prior data, the tool reports that plainly rather than fabricating
    results.
    """
    logger.info("tool_call scan_region region=%s force=%s", region, force)
    # scan_service.scan_region_as_dict() is the single place this
    # cooldown/failure/invalid-region -> JSON translation lives, shared
    # with the MCP tool below so the two front doors can't drift on what
    # a cooldown response looks like (see its own docstring).
    result = scan_service.scan_region_as_dict(region, force=force)

    if "error" in result:
        logger.info("tool_result scan_region region=%s error=%s", region, result["error"])
    else:
        logger.info(
            "tool_result scan_region region=%s resource_count=%d monthly_spend=%s",
            region,
            len(result.get("resources", [])),
            result.get("totals", {}).get("monthly_spend"),
        )
    return json.dumps(result)


@function_tool
def list_regions() -> str:
    """List enabled AWS regions in this account, for scan_region's
    `region` argument."""
    regions = scan_service.list_available_regions()
    return json.dumps({"regions": regions})
