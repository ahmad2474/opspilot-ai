"""S3 business logic.

list_buckets() is the original Phase 1 lookup. check_s3_waste (roadmap
phase 2 Section 1.2) is a genuinely different shape added on top: a
findings-list per bucket (zero or more independent flags), not a single
is_idle-style boolean -- see app/models/s3_waste.py's module docstring, and
the data-schema skill's "Findings-list tools" section.

Deliberately NOT built: an "objects sitting in Standard with no recent
access" sub-check. That needs S3 Storage Lens or Server Access Logging
enabled on the bucket to measure access recency honestly -- neither is
guaranteed on, and fabricating a "last accessed" claim without one would
break this app's own idle_since_is_estimated honesty pattern (see
app/models/idle.py's docstring: never assert a real time series where none
exists). Skipped outright per roadmap instructions, not shipped as a fake
signal -- if this gets built later, it must report "access-recency signal
not available, enable Storage Lens to unlock this check" rather than guess.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from botocore.exceptions import ClientError

from app.aws.client import get_pricing_client, get_s3_client
from app.core.config import get_settings
from app.models.dashboard import S3BucketSummary, S3Card
from app.models.s3_waste import (
    S3IncompleteMultipartUpload,
    S3StorageClassPriceGap,
    S3WasteFinding,
    S3WasteReport,
)
from app.services import cost_service

logger = logging.getLogger("app.services.s3")

# AWS Pricing API `volumeType` attribute values for AmazonS3's Storage
# productFamily -- "Standard" (S3 Standard) vs. the coldest generally
# available tier (S3 Glacier Deep Archive), used for check_s3_waste's
# price-gap note (roadmap: "worth knowing storage classes span roughly a
# 96% price range ... verify current numbers against the Pricing API before
# hardcoding anything"). Not live-verified against a real GetProducts
# response -- no AWS credentials exist in this build environment (see
# docs/BUILD_PROGRESS.md) -- confirm these exact attribute values against a
# real account before relying on the resulting numbers for a real bill.
_S3_STANDARD_VOLUME_TYPE = "Standard"
_S3_COLDEST_TIER_VOLUME_TYPE = "Glacier Deep Archive"
_S3_COLDEST_TIER_LABEL = "S3 Glacier Deep Archive"


def list_buckets() -> S3Card:
    client = get_s3_client()
    response = client.list_buckets()
    buckets = [
        S3BucketSummary(
            name=raw["Name"],
            creation_date=raw["CreationDate"].isoformat() if raw.get("CreationDate") else None,
        )
        for raw in response.get("Buckets", [])
    ]
    return S3Card(buckets=buckets, count=len(buckets))


def _get_lifecycle_rules(client, bucket: str) -> list[dict] | None:
    """None = no lifecycle configuration at all on this bucket (the
    'no_lifecycle_policy' finding). AWS reports this as a ClientError with
    code NoSuchLifecycleConfiguration, not an empty success response.
    """
    try:
        response = client.get_bucket_lifecycle_configuration(Bucket=bucket)
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") == "NoSuchLifecycleConfiguration":
            return None
        raise
    return response.get("Rules", [])


def _has_noncurrent_version_expiration(rules: list[dict] | None) -> bool:
    if not rules:
        return False
    return any(
        rule.get("Status") == "Enabled" and "NoncurrentVersionExpiration" in rule
        for rule in rules
    )


def _get_versioning_status(client, bucket: str) -> str:
    response = client.get_bucket_versioning(Bucket=bucket)
    # A bucket that has never had versioning touched returns no "Status"
    # key at all -- "Disabled" here is this function's own default label,
    # not a literal AWS-returned value.
    return response.get("Status", "Disabled")


def _sum_part_bytes(client, bucket: str, key: str, upload_id: str) -> int | None:
    try:
        total = 0
        paginator = client.get_paginator("list_parts")
        for page in paginator.paginate(Bucket=bucket, Key=key, UploadId=upload_id):
            total += sum(part.get("Size", 0) for part in page.get("Parts", []))
        return total
    except ClientError:
        # Best-effort sizing -- never let a ListParts failure drop the
        # finding itself, just its byte estimate (mirrors
        # idle_since_is_estimated's "don't fabricate, be honest instead"
        # spirit: None here, not a guessed number).
        logger.warning(
            "list_parts failed for bucket=%s key=%s upload_id=%s, omitting size estimate",
            bucket, key, upload_id, exc_info=True,
        )
        return None


def _list_stale_multipart_uploads(
    client, bucket: str, days: int
) -> list[S3IncompleteMultipartUpload]:
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    stale: list[S3IncompleteMultipartUpload] = []
    paginator = client.get_paginator("list_multipart_uploads")
    for page in paginator.paginate(Bucket=bucket):
        for upload in page.get("Uploads", []):
            initiated = upload.get("Initiated")
            if initiated is None or initiated > cutoff:
                continue
            age_days = (datetime.now(timezone.utc) - initiated).days
            stale.append(
                S3IncompleteMultipartUpload(
                    key=upload["Key"],
                    upload_id=upload["UploadId"],
                    initiated=initiated,
                    age_days=age_days,
                    estimated_bytes=_sum_part_bytes(
                        client, bucket, upload["Key"], upload["UploadId"]
                    ),
                )
            )
    return stale


def _get_s3_storage_gb_month_rate(volume_type: str, region: str) -> float:
    client = get_pricing_client()
    response = client.get_products(
        ServiceCode="AmazonS3",
        Filters=[
            {"Type": "TERM_MATCH", "Field": "productFamily", "Value": "Storage"},
            {"Type": "TERM_MATCH", "Field": "regionCode", "Value": region},
            {"Type": "TERM_MATCH", "Field": "volumeType", "Value": volume_type},
        ],
        MaxResults=1,
    )
    price_list = response.get("PriceList", [])
    if not price_list:
        raise ValueError(
            f"Pricing API returned no GB-month price for volume_type={volume_type!r} "
            f"region={region!r}"
        )
    # Shared with cost_service's other GetProducts callers (EC2/RDS/EBS/ELB,
    # and vpc_endpoint_service) -- same PriceList[0] -> USD JSON navigation,
    # no reason for S3 to keep its own duplicate (code-reviewer finding,
    # Phase 2 Tier 3 review).
    return cost_service.extract_usd_price(price_list)


def _get_storage_class_price_gap(region: str) -> S3StorageClassPriceGap | None:
    """Best-effort -- a Pricing API failure here omits the price-gap note
    entirely rather than breaking the rest of check_s3_waste's findings
    (same graceful-degradation pattern as resources.py's cpu/idle/cost
    try/except blocks)."""
    try:
        standard_rate = _get_s3_storage_gb_month_rate(_S3_STANDARD_VOLUME_TYPE, region)
        coldest_rate = _get_s3_storage_gb_month_rate(_S3_COLDEST_TIER_VOLUME_TYPE, region)
    except Exception:  # noqa: BLE001 - price-gap note is a nice-to-have, not a hard dependency
        logger.warning(
            "s3 storage-class pricing lookup failed, omitting price gap note", exc_info=True
        )
        return None

    return S3StorageClassPriceGap(
        region=region,
        standard_gb_month_usd=standard_rate,
        coldest_tier_gb_month_usd=coldest_rate,
        coldest_tier_name=_S3_COLDEST_TIER_LABEL,
        note=(
            f"S3 Standard costs ${standard_rate:.5f}/GB-month vs. "
            f"${coldest_rate:.5f}/GB-month for {_S3_COLDEST_TIER_LABEL} in {region} -- "
            "worth a manual lifecycle-transition review for any data here that's "
            "actually cold (this app has no access-recency signal to identify "
            "which objects qualify -- see this module's docstring)."
        ),
    )


def check_s3_waste(bucket: str, days: int = 7, region: str | None = None) -> S3WasteReport:
    """Section 1.2's three independent sub-checks, per bucket:

    1. No lifecycle policy at all -- purely configuration-based, zero
       ongoing API cost to check, the easiest win in this section.
    2. Incomplete multipart uploads older than `days` -- classic silent
       cost, nobody looks for these manually.
    3. Versioning enabled with no noncurrent-version-expiration rule --
       old versions accumulate storage cost indefinitely.

    Returns a findings list (zero to three entries), never a single
    is_idle-style boolean -- a bucket can have any combination of these at
    once. Also attaches a best-effort storage-class price-gap note (see
    _get_storage_class_price_gap) since it's one of the highest-dollar-per-
    line-of-code checks available here.
    """
    client = get_s3_client()
    findings: list[S3WasteFinding] = []

    lifecycle_rules = _get_lifecycle_rules(client, bucket)
    if lifecycle_rules is None:
        findings.append(
            S3WasteFinding(
                finding_type="no_lifecycle_policy",
                message=(
                    f"Bucket {bucket!r} has no S3 Lifecycle configuration at all -- "
                    "objects never transition to a cheaper storage class or expire "
                    "automatically, regardless of age."
                ),
            )
        )

    stale_uploads = _list_stale_multipart_uploads(client, bucket, days)
    if stale_uploads:
        total_bytes = sum(u.estimated_bytes or 0 for u in stale_uploads)
        findings.append(
            S3WasteFinding(
                finding_type="incomplete_multipart_uploads",
                message=(
                    f"{len(stale_uploads)} incomplete multipart upload(s) older than "
                    f"{days} days, holding at least {total_bytes} bytes of storage "
                    "that will never complete and is billed until aborted."
                ),
                incomplete_uploads=stale_uploads,
            )
        )

    versioning_status = _get_versioning_status(client, bucket)
    if versioning_status == "Enabled" and not _has_noncurrent_version_expiration(lifecycle_rules):
        findings.append(
            S3WasteFinding(
                finding_type="versioning_no_noncurrent_expiration",
                message=(
                    f"Bucket {bucket!r} has versioning enabled but no lifecycle rule "
                    "expires noncurrent versions -- every overwritten/deleted object "
                    "keeps its old versions in storage indefinitely."
                ),
            )
        )

    price_gap = _get_storage_class_price_gap(region or get_settings().aws_region)

    return S3WasteReport(
        bucket=bucket,
        window_days=days,
        findings=findings,
        storage_class_price_gap=price_gap,
        checked_at=datetime.now(timezone.utc),
    )
