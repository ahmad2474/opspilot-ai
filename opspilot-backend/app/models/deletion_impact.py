"""Response model for check_deletion_impact (roadmap phase 2 Section 3.1 --
the read-only replacement for the retired write/approval Step 8, see
Section 3.0 for why).

A genuinely different shape from both IdleCheckResult/CostEstimate and the
findings-list tools (see the data-schema skill) -- this isn't "is it idle"
or "what's wrong with it", it's "what actually happens, in four distinct
buckets, if this specific resource is deleted right now":

- `will_be_removed`: things that actually disappear along with the
  resource.
- `will_persist_and_keep_costing`: things that remain and keep accruing
  cost after the resource is gone -- each entry carries a real dollar
  figure wherever one is computable (via the existing estimate_cost),
  never just a category warning.
- `behavioral_warnings`: surprising operational consequences that aren't
  about a specific sub-resource disappearing or persisting (the ASG-
  replacement case is the canonical example -- roadmap 3.1 calls this
  "the single most valuable gotcha this tool can surface").
- `never_affected`: independent objects stated explicitly for
  completeness/reassurance (security groups, IAM role) -- never left
  implicit.

`check_errors` is a fifth, meta field: a queryable fact (ASG membership,
load balancer target registration, ...) that could not be verified live
(e.g. an AccessDenied on a not-yet-granted IAM action) is reported here as
an explicit gap, never silently treated as "false"/"no" -- same
anti-hallucination discipline as everything else in this app: a fact this
tool couldn't verify is reported as unverified, not guessed.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class WillBeRemovedEntry(BaseModel):
    resource_type: str = Field(
        description="e.g. 'ec2', 'ebs', 'rds', 'rds_automated_backup' -- not always one "
        "of the 15 TYPE_CODES, since some entries describe a category (e.g. automated "
        "backups) rather than a single addressable resource."
    )
    resource_id: str
    reason: str = Field(description="Why this specific thing is removed, in plain language.")


class WillPersistEntry(BaseModel):
    resource_type: str
    resource_id: str
    reason: str = Field(description="Why this specific thing survives the deletion.")
    estimated_monthly_cost_usd: float | None = Field(
        default=None,
        description=(
            "Real projected monthly cost from calling the existing "
            "cost_service.estimate_cost, wherever estimate_cost supports this "
            "resource_type (ebs, eip, rds) -- 'a category warning' is much less useful "
            "than a real dollar figure. None only when estimate_cost has no pricing "
            "path for this kind of thing at all (a snapshot -- see cost_note)."
        ),
    )
    cost_note: str | None = Field(
        default=None,
        description=(
            "Set only when estimated_monthly_cost_usd is None, explaining why no dollar "
            "figure was computed (e.g. pointing to check_snapshot_sprawl for a snapshot, "
            "which this tool references rather than duplicating its logic) -- or when a "
            "live cost lookup was attempted and failed."
        ),
    )


class BehavioralWarning(BaseModel):
    code: str = Field(
        description="Short stable identifier, e.g. 'asg_will_replace_instance' -- for "
        "callers that want to key off a specific warning rather than parsing `message`."
    )
    message: str


class NeverAffectedEntry(BaseModel):
    resource_type: str = Field(description="e.g. 'security_group', 'iam_role'.")
    resource_id: str | None = Field(
        default=None,
        description="None only for a general statement with no single addressable ID.",
    )
    message: str


class DeletionImpactReport(BaseModel):
    resource_type: Literal["ec2", "rds", "ebs"]
    resource_id: str
    will_be_removed: list[WillBeRemovedEntry]
    will_persist_and_keep_costing: list[WillPersistEntry]
    behavioral_warnings: list[BehavioralWarning]
    never_affected: list[NeverAffectedEntry]
    check_errors: list[str] = Field(
        default_factory=list,
        description=(
            "Queryable, instance-specific facts (roadmap 3.1) that could not be "
            "verified live for this call -- e.g. an AccessDenied on "
            "autoscaling:DescribeAutoScalingInstances before the IAM policy grants it. "
            "A non-empty list means this report is incomplete, not that the unverified "
            "facts are false -- never treat a check_errors entry as 'so the answer is "
            "no'."
        ),
    )
