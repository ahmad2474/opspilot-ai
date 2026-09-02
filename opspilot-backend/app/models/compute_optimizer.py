"""AWS Compute Optimizer rightsizing recommendations (roadmap phase 2
Section 1.3, "Batch B").

Not idle detection -- AWS's own ML-driven over/under-provisioning signal. A
busy-but-oversized resource is never idle (check_idle would never catch it)
but might cost multiples of what it needs to; Compute Optimizer is the one
check in this whole app whose findings are AWS-generated, not
self-computed from a CloudWatch threshold this app picked (roadmap-phase2
Section 1.4's tool->layer table: "New shape (AWS-generated, not
self-computed)").

Requires a one-time account-level opt-in (AWS console -> Compute Optimizer
-> Get started) -- same "one-time-activation, graceful, non-fatal" shape
this app already has for Redshift/Kinesis (scan_service.py) and ECS
Container Insights (ecs_service.py) elsewhere in this batch. `enrolled=False`
(caught via the client's own `OptInRequiredException`, confirmed to exist
on the real boto3 compute-optimizer client) is a normal, non-error result
with a plain how-to-opt-in note, never an unhandled exception.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class RightsizingFinding(BaseModel):
    resource_id: str = Field(description="Instance/volume/function/service ARN.")
    finding: str = Field(
        description=(
            "AWS's own verdict, verbatim -- e.g. 'Overprovisioned', "
            "'Underprovisioned', 'NotOptimized' (never 'Optimized', those are "
            "filtered out -- an already-optimal resource isn't a finding)."
        )
    )
    current_configuration: str
    recommended_configuration: str | None = Field(
        default=None, description="AWS's top-ranked (rank=1) recommendation option, if any."
    )
    estimated_monthly_savings_usd: float | None = Field(
        default=None,
        description=(
            "From the top-ranked recommendation option's savingsOpportunity."
            "estimatedMonthlySavings -- null if AWS didn't return one."
        ),
    )
    lookback_period_days: float | None = None


class RightsizingReport(BaseModel):
    resource_type: Literal["ec2", "ebs", "lambda", "ecs"]
    enrolled: bool = Field(
        description=(
            "False if this account is not opted into Compute Optimizer -- "
            "findings is then always [], see `note` for how to opt in."
        )
    )
    findings: list[RightsizingFinding] = Field(
        description="Excludes resources AWS's own 'finding' already reports as 'Optimized'."
    )
    total_checked: int = Field(
        description="Every resource Compute Optimizer returned, including Optimized ones."
    )
    note: str | None = Field(
        default=None,
        description="Populated when enrolled=False (how to opt in), or informationally "
        "when findings is empty because every checked resource is already Optimized.",
    )
