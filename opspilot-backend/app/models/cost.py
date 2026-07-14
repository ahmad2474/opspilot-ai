"""Response model for estimate_cost (roadmap Section 3.2).

Field names (`projected_monthly`, `incurred_so_far`, `method`) match the
`cost` block in the data-schema skill exactly. The two cost numbers are
kept as distinct fields on purpose (roadmap 3.1a) -- projected_monthly
drives star/bubble sizing, incurred_so_far does not, and they must never
be collapsed into one figure.
"""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class DateRange(BaseModel):
    """Timezone-aware UTC datetimes expected for both ends."""

    start: datetime
    end: datetime


class CostEstimate(BaseModel):
    resource_id: str
    resource_type: str = Field(
        description=(
            "One of the 15 roadmap-scoped types: 'ec2', 'ebs', 'rds', 'eip', "
            "'elb', 'lambda', 'nat_gateway', 'dynamodb', 'elasticache', "
            "'sagemaker', 'redshift', 'api_gateway', 'cloudfront', "
            "'opensearch', 'kinesis'."
        )
    )
    date_range: DateRange = Field(
        description="Window incurred_so_far was computed over (defaults to launch->now)."
    )

    method: Literal["list_price", "billed"] = Field(
        description=(
            "'list_price' = AWS Pricing API on-demand rate (ignores "
            "reserved/savings pricing). 'billed' = Cost Explorer actual "
            "billed cost via cost allocation tags -- not yet implemented. "
            "Callers must label which method produced a figure, never "
            "present list price as if it were billed cost."
        )
    )
    hourly_rate: float | None = Field(
        default=None,
        description=(
            "On-demand USD/hour rate used for both figures below. None for "
            "usage-based types with no fixed hourly rate at all (Lambda, "
            "DynamoDB on-demand/PAY_PER_REQUEST, API Gateway, CloudFront) "
            "-- for those, incurred_so_far/projected_monthly are computed "
            "directly from observed usage x a per-unit price instead."
        ),
    )

    projected_monthly: float = Field(
        description="hourly_rate x ~730 hours (a full month) -- drives star/bubble sizing."
    )
    incurred_so_far: float = Field(
        description=(
            "hourly_rate x hours actually elapsed within date_range, capped "
            "at the resource's own age if it's younger than date_range."
        )
    )


class InstanceCostEstimate(BaseModel):
    """estimate_instance_cost's response (roadmap 3.8) -- a hypothetical
    EC2 on-demand rate lookup, independent of any real resource in the
    account. Deliberately a separate, smaller model from CostEstimate:
    there is no resource_id/date_range/incurred_so_far here at all (no
    real resource exists to have incurred anything), just the two rate
    numbers a "what would a big EC2 cost" question needs.
    """

    instance_type: str
    region: str
    method: Literal["list_price"] = Field(
        default="list_price",
        description="Always 'list_price' (AWS Pricing API on-demand rate) -- there is no "
        "real resource to look up a 'billed' Cost Explorer figure for.",
    )
    hourly_rate: float = Field(description="On-demand USD/hour rate for this instance type/region.")
    monthly_rate: float = Field(description="hourly_rate x ~730 hours (a full month).")
