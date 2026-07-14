"""Response model for check_idle (roadmap Section 3.1).

Field names (`is_idle`, `idle_since`, `idle_days`, `younger_than_window`,
`idle_since_is_estimated`) match the `idle` block in the data-schema skill
exactly -- frontend-agent and mcp-agent consume this shape as-is, don't
rename these.
"""
from __future__ import annotations

from datetime import date

from pydantic import BaseModel, Field


class IdleCheckResult(BaseModel):
    resource_id: str
    resource_type: str = Field(
        description=(
            "One of the 15 roadmap-scoped types: 'ec2', 'ebs', 'rds', 'eip', "
            "'elb', 'lambda', 'nat_gateway', 'dynamodb', 'elasticache', "
            "'sagemaker', 'redshift', 'api_gateway', 'cloudfront', "
            "'opensearch', 'kinesis'."
        )
    )
    window_days: int = Field(description="The requested check_idle(days=...) window")

    is_idle: bool = Field(
        description=(
            "True only if every daily datapoint across the full requested "
            "window is below the idle threshold -- a burst on any single "
            "day makes this False for the whole window, even if the days "
            "since are idle (see idle_since/idle_days for that streak)."
        )
    )
    idle_since: date | None = Field(
        default=None,
        description=(
            "First day of the current trailing idle streak (walked "
            "backward from the most recent day to the first day that "
            "breaks the idle condition, +1). None if the resource is not "
            "currently idle."
        ),
    )
    idle_days: int = Field(
        default=0, description="Length of the current trailing idle streak, in days."
    )
    younger_than_window: bool = Field(
        default=False,
        description=(
            "True if the resource's own age is shorter than window_days -- "
            "idle_since/idle_days are then bounded by actual launch time, "
            "never a fabricated longer window."
        ),
    )
    idle_since_is_estimated: bool = Field(
        default=False,
        description=(
            "True only when idle_since/idle_days come from a point-in-time "
            "signal with no CloudWatch time series to verify how long it's "
            "actually held (e.g. an unassociated EIP, or an unattached EBS "
            "volume with no create_time to anchor a younger-than-window "
            "check) -- in that case idle_days is a worst-case 'known idle "
            "for at least the requested window' assumption, not a verified "
            "streak like every other branch. False for every CloudWatch- "
            "verified branch (EC2/RDS/ELB/attached-EBS) and for the "
            "EBS-unattached-younger-than-window case, which has a real "
            "create_time-anchored signal."
        ),
    )
