"""Response model for check_s3_waste (roadmap phase 2 Section 1.2).

Findings-list shape, structurally different from IdleCheckResult -- a
bucket can carry zero, one, two, or three independent findings at once,
there is no single is_idle-style verdict for S3 (roadmap: "returns a
findings list ... not a single is_idle boolean"). See the data-schema
skill's "Findings-list tools" section.

Deliberately NOT built: an "objects sitting in Standard with no recent
access" finding -- that needs S3 Storage Lens or Server Access Logging
enabled to measure access recency honestly, and neither is guaranteed
enabled on a given bucket. Fabricating a "last accessed" claim without one
of those would violate this app's own idle_since_is_estimated honesty
pattern (see app/models/idle.py). Skipped outright rather than shipped as
a fake signal -- see s3_service.check_s3_waste's docstring.
"""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class S3IncompleteMultipartUpload(BaseModel):
    key: str
    upload_id: str
    initiated: datetime
    age_days: int
    estimated_bytes: int | None = Field(
        default=None,
        description=(
            "Sum of already-uploaded part sizes (via ListParts), the real "
            "storage this incomplete upload is billed for. Null if the "
            "ListParts lookup failed for this upload -- never fabricated."
        ),
    )


class S3StorageClassPriceGap(BaseModel):
    """The 'should this be in a colder tier' cost angle (roadmap: 'one of
    the highest-dollar-per-line-of-code checks in this whole document') --
    live AWS Pricing API rates, not a hardcoded constant, since storage
    class pricing is exactly the kind of number the roadmap says to verify
    rather than hardcode.
    """

    region: str
    standard_gb_month_usd: float
    coldest_tier_gb_month_usd: float
    coldest_tier_name: str
    note: str


class S3WasteFinding(BaseModel):
    finding_type: Literal[
        "no_lifecycle_policy",
        "incomplete_multipart_uploads",
        "versioning_no_noncurrent_expiration",
    ]
    message: str
    incomplete_uploads: list[S3IncompleteMultipartUpload] | None = Field(
        default=None, description="Populated only when finding_type='incomplete_multipart_uploads'."
    )


class S3WasteReport(BaseModel):
    bucket: str
    window_days: int = Field(
        description="Age threshold used for the incomplete-multipart-upload sub-check."
    )
    findings: list[S3WasteFinding] = Field(
        description="Zero or more independent findings -- NOT a single is_idle-style boolean."
    )
    storage_class_price_gap: S3StorageClassPriceGap | None = Field(
        default=None,
        description=(
            "Best-effort; null if the Pricing API lookup failed (never blocks "
            "the rest of the report -- same graceful-degradation pattern used "
            "elsewhere in this app for nice-to-have lookups)."
        ),
    )
    checked_at: datetime
