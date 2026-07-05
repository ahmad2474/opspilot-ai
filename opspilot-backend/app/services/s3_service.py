from __future__ import annotations

from app.aws.client import get_s3_client
from app.models.dashboard import S3BucketSummary, S3Card


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
