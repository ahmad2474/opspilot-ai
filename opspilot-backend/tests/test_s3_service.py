import json
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

from botocore.exceptions import ClientError

from app.services import s3_service

NOW = datetime(2026, 9, 1, tzinfo=timezone.utc)


def _fake_paginator(pages: list[dict]) -> MagicMock:
    paginator = MagicMock()
    paginator.paginate.return_value = pages
    return paginator


def _no_such_lifecycle_error() -> ClientError:
    return ClientError(
        {"Error": {"Code": "NoSuchLifecycleConfiguration", "Message": "not configured"}},
        "GetBucketLifecycleConfiguration",
    )


def _mock_client_with(
    lifecycle_side_effect=None,
    lifecycle_return=None,
    versioning_return: dict | None = None,
    multipart_pages: list[dict] | None = None,
    list_parts_pages: list[dict] | None = None,
) -> MagicMock:
    client = MagicMock()
    if lifecycle_side_effect is not None:
        client.get_bucket_lifecycle_configuration.side_effect = lifecycle_side_effect
    else:
        client.get_bucket_lifecycle_configuration.return_value = lifecycle_return or {}
    client.get_bucket_versioning.return_value = versioning_return or {}

    def _get_paginator(operation_name: str) -> MagicMock:
        if operation_name == "list_multipart_uploads":
            return _fake_paginator(multipart_pages if multipart_pages is not None else [{}])
        if operation_name == "list_parts":
            return _fake_paginator(list_parts_pages if list_parts_pages is not None else [{}])
        raise AssertionError(f"unexpected paginator requested: {operation_name}")

    client.get_paginator.side_effect = _get_paginator
    return client


@patch("app.services.s3_service._get_storage_class_price_gap", return_value=None)
@patch("app.services.s3_service.get_s3_client")
def test_no_lifecycle_policy_finding_when_none_configured(
    mock_get_client: MagicMock, _mock_price_gap: MagicMock
) -> None:
    mock_get_client.return_value = _mock_client_with(
        lifecycle_side_effect=_no_such_lifecycle_error()
    )

    result = s3_service.check_s3_waste("my-bucket", days=7)

    types = {f.finding_type for f in result.findings}
    assert "no_lifecycle_policy" in types


@patch("app.services.s3_service._get_storage_class_price_gap", return_value=None)
@patch("app.services.s3_service.get_s3_client")
def test_no_finding_when_lifecycle_policy_exists(
    mock_get_client: MagicMock, _mock_price_gap: MagicMock
) -> None:
    mock_get_client.return_value = _mock_client_with(lifecycle_return={"Rules": []})

    result = s3_service.check_s3_waste("my-bucket", days=7)

    types = {f.finding_type for f in result.findings}
    assert "no_lifecycle_policy" not in types


@patch("app.services.s3_service._get_storage_class_price_gap", return_value=None)
@patch("app.services.s3_service.get_s3_client")
def test_incomplete_multipart_uploads_flagged_and_sized(
    mock_get_client: MagicMock, _mock_price_gap: MagicMock
) -> None:
    stale_initiated = NOW - timedelta(days=10)
    recent_initiated = NOW - timedelta(days=1)
    mock_get_client.return_value = _mock_client_with(
        lifecycle_return={"Rules": []},
        multipart_pages=[
            {
                "Uploads": [
                    {"Key": "stale.zip", "UploadId": "u-stale", "Initiated": stale_initiated},
                    {"Key": "recent.zip", "UploadId": "u-recent", "Initiated": recent_initiated},
                ]
            }
        ],
        list_parts_pages=[{"Parts": [{"Size": 100}, {"Size": 200}]}],
    )

    with patch("app.services.s3_service.datetime") as mock_datetime:
        mock_datetime.now.return_value = NOW
        result = s3_service.check_s3_waste("my-bucket", days=7)

    finding = next(f for f in result.findings if f.finding_type == "incomplete_multipart_uploads")
    assert len(finding.incomplete_uploads) == 1
    upload = finding.incomplete_uploads[0]
    assert upload.upload_id == "u-stale"
    assert upload.estimated_bytes == 300


@patch("app.services.s3_service._get_storage_class_price_gap", return_value=None)
@patch("app.services.s3_service.get_s3_client")
def test_versioning_enabled_without_noncurrent_expiration_flagged(
    mock_get_client: MagicMock, _mock_price_gap: MagicMock
) -> None:
    mock_get_client.return_value = _mock_client_with(
        lifecycle_return={"Rules": [{"Status": "Enabled", "ID": "expire-old"}]},
        versioning_return={"Status": "Enabled"},
    )

    result = s3_service.check_s3_waste("my-bucket", days=7)

    types = {f.finding_type for f in result.findings}
    assert "versioning_no_noncurrent_expiration" in types


@patch("app.services.s3_service._get_storage_class_price_gap", return_value=None)
@patch("app.services.s3_service.get_s3_client")
def test_versioning_with_noncurrent_expiration_not_flagged(
    mock_get_client: MagicMock, _mock_price_gap: MagicMock
) -> None:
    mock_get_client.return_value = _mock_client_with(
        lifecycle_return={
            "Rules": [
                {
                    "Status": "Enabled",
                    "ID": "expire-old",
                    "NoncurrentVersionExpiration": {"NoncurrentDays": 30},
                }
            ]
        },
        versioning_return={"Status": "Enabled"},
    )

    result = s3_service.check_s3_waste("my-bucket", days=7)

    types = {f.finding_type for f in result.findings}
    assert "versioning_no_noncurrent_expiration" not in types


@patch("app.services.s3_service._get_storage_class_price_gap", return_value=None)
@patch("app.services.s3_service.get_s3_client")
def test_versioning_never_enabled_not_flagged(
    mock_get_client: MagicMock, _mock_price_gap: MagicMock
) -> None:
    mock_get_client.return_value = _mock_client_with(
        lifecycle_side_effect=_no_such_lifecycle_error(), versioning_return={}
    )

    result = s3_service.check_s3_waste("my-bucket", days=7)

    types = {f.finding_type for f in result.findings}
    assert "versioning_no_noncurrent_expiration" not in types


def _pricing_price_list(price_per_gb_month: float) -> list[str]:
    return [
        json.dumps(
            {
                "terms": {
                    "OnDemand": {
                        "term1": {
                            "priceDimensions": {
                                "dim1": {"pricePerUnit": {"USD": str(price_per_gb_month)}}
                            }
                        }
                    }
                }
            }
        )
    ]


@patch("app.services.s3_service.get_pricing_client")
def test_storage_class_price_gap_computed_from_pricing_api(mock_get_pricing: MagicMock) -> None:
    mock_client = MagicMock()

    def _get_products(ServiceCode, Filters, MaxResults):  # noqa: N803 - matches boto3 kwarg casing
        volume_type = next(f["Value"] for f in Filters if f["Field"] == "volumeType")
        if volume_type == s3_service._S3_STANDARD_VOLUME_TYPE:
            return {"PriceList": _pricing_price_list(0.023)}
        return {"PriceList": _pricing_price_list(0.00099)}

    mock_client.get_products.side_effect = _get_products
    mock_get_pricing.return_value = mock_client

    gap = s3_service._get_storage_class_price_gap("us-east-1")

    assert gap is not None
    assert gap.standard_gb_month_usd == 0.023
    assert gap.coldest_tier_gb_month_usd == 0.00099
    assert gap.region == "us-east-1"


@patch("app.services.s3_service.get_pricing_client")
def test_storage_class_price_gap_returns_none_on_pricing_failure(
    mock_get_pricing: MagicMock,
) -> None:
    mock_client = MagicMock()
    mock_client.get_products.side_effect = RuntimeError("pricing API down")
    mock_get_pricing.return_value = mock_client

    assert s3_service._get_storage_class_price_gap("us-east-1") is None
