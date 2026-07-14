"""Tests for GET /resources/scan and GET /resources/regions (roadmap
3.3/3.4). scan_service itself is exercised in tests/test_scan_service.py --
these tests cover the route's status-code/header translation of
scan_service's exceptions, and auth gating.
"""
from __future__ import annotations

import asyncio
import time
from datetime import datetime, timezone
from unittest.mock import patch

from fastapi.testclient import TestClient
from httpx import ASGITransport, AsyncClient

from app.main import app
from app.models.scan import ScanResponse, ScanTotals
from app.services import scan_service

client = TestClient(app)


def _empty_scan(region: str = "us-east-1") -> ScanResponse:
    return ScanResponse(
        region=region,
        last_updated=datetime(2026, 7, 10, 9, 15, tzinfo=timezone.utc),
        resources=[],
        totals=ScanTotals(monthly_spend=0, idle_count=0, idle_monthly_waste=0),
        error=None,
    )


def test_scan_route_requires_session() -> None:
    response = client.get("/resources/scan", params={"region": "us-east-1"})
    assert response.status_code == 401


def test_regions_route_requires_session() -> None:
    response = client.get("/resources/regions")
    assert response.status_code == 401


@patch("app.api.routes.resources.scan_service.scan_region")
def test_scan_route_returns_scan_response(mock_scan, auth_headers) -> None:
    mock_scan.return_value = _empty_scan()

    response = client.get("/resources/scan", params={"region": "us-east-1"}, headers=auth_headers)

    assert response.status_code == 200
    body = response.json()
    assert body["region"] == "us-east-1"
    assert body["resources"] == []
    assert body["error"] is None
    mock_scan.assert_called_once_with("us-east-1", force=False)


@patch("app.api.routes.resources.scan_service.scan_region")
def test_scan_route_passes_force_flag(mock_scan, auth_headers) -> None:
    mock_scan.return_value = _empty_scan()

    client.get(
        "/resources/scan", params={"region": "us-east-1", "force": "true"}, headers=auth_headers
    )

    mock_scan.assert_called_once_with("us-east-1", force=True)


@patch("app.api.routes.resources.scan_service.scan_region")
def test_scan_route_cooldown_with_cache_returns_429_and_stale_data(mock_scan, auth_headers) -> None:
    cached = _empty_scan()
    mock_scan.side_effect = scan_service.ScanCooldownActive("us-east-1", 12.3, cached)

    response = client.get(
        "/resources/scan", params={"region": "us-east-1", "force": "true"}, headers=auth_headers
    )

    assert response.status_code == 429
    assert response.headers["Retry-After"] == "13"
    assert response.json()["region"] == "us-east-1"


@patch("app.api.routes.resources.scan_service.scan_region")
def test_scan_route_cooldown_with_no_cache_returns_429_error_body(mock_scan, auth_headers) -> None:
    mock_scan.side_effect = scan_service.ScanCooldownActive("us-east-1", 12.3, None)

    response = client.get(
        "/resources/scan", params={"region": "us-east-1", "force": "true"}, headers=auth_headers
    )

    assert response.status_code == 429
    assert "detail" in response.json()
    # Regression: this branch raises HTTPException rather than returning
    # the injected `response` object, so the Retry-After header has to be
    # passed on the exception itself -- mutating response.headers here (as
    # the exc.cached-is-not-None branch above does) would be silently
    # discarded once FastAPI builds a fresh response for the exception.
    assert response.headers["Retry-After"] == "13"


@patch("app.api.routes.resources.scan_service.scan_region")
def test_scan_route_no_cache_and_failure_returns_502(mock_scan, auth_headers) -> None:
    mock_scan.side_effect = scan_service.ScanFailedNoCacheError(
        "us-east-1",
        RuntimeError("AccessDenied for arn:aws:iam::123456789012:user/ahmad"),
    )

    response = client.get("/resources/scan", params={"region": "us-east-1"}, headers=auth_headers)

    assert response.status_code == 502
    # The underlying AWS/botocore exception message (which can embed the
    # IAM caller ARN / 12-digit account ID) must never be echoed back to
    # the caller -- only logged server-side.
    body_text = response.text
    assert "123456789012" not in body_text
    assert "AccessDenied" not in body_text


@patch("app.api.routes.resources.scan_service.scan_region")
def test_scan_route_invalid_region_returns_400(mock_scan, auth_headers) -> None:
    mock_scan.side_effect = scan_service.InvalidRegionError(
        "not-a-region", ["us-east-1", "us-west-2"]
    )

    response = client.get(
        "/resources/scan", params={"region": "not-a-region"}, headers=auth_headers
    )

    assert response.status_code == 400
    assert "not-a-region" in response.json()["detail"]


@patch("app.api.routes.resources.scan_service.scan_region")
def test_scan_route_normalizes_region_before_calling_service(mock_scan, auth_headers) -> None:
    mock_scan.return_value = _empty_scan()

    client.get(
        "/resources/scan", params={"region": "  US-EAST-1  "}, headers=auth_headers
    )

    mock_scan.assert_called_once_with("us-east-1", force=False)


@patch("app.api.routes.resources.scan_service.scan_region")
def test_scan_route_stale_cache_with_error_survives_serialization(mock_scan, auth_headers) -> None:
    """roadmap 3.4: a rescan that fell back to stale cache must still
    round-trip a non-null `error` alongside the (untouched) old data
    through the actual HTTP/JSON boundary, not just inside scan_service."""
    stale = _empty_scan().model_copy(
        update={"error": "Refresh failed (RuntimeError); showing last good data."}
    )
    mock_scan.return_value = stale

    response = client.get("/resources/scan", params={"region": "us-east-1"}, headers=auth_headers)

    assert response.status_code == 200
    body = response.json()
    assert body["error"] == "Refresh failed (RuntimeError); showing last good data."
    assert body["region"] == "us-east-1"
    assert body["resources"] == []


@patch("app.api.routes.resources.scan_service.list_available_regions")
def test_regions_route_returns_region_list(mock_regions, auth_headers) -> None:
    mock_regions.return_value = ["us-east-1", "us-west-2"]

    response = client.get("/resources/regions", headers=auth_headers)

    assert response.status_code == 200
    assert response.json() == {"regions": ["us-east-1", "us-west-2"]}


@patch("app.api.routes.resources.scan_service.list_available_regions")
def test_regions_route_failure_returns_502(mock_regions, auth_headers) -> None:
    mock_regions.side_effect = RuntimeError("boom")

    response = client.get("/resources/regions", headers=auth_headers)

    assert response.status_code == 502


def test_scan_route_runs_scan_region_in_threadpool_not_on_event_loop(auth_headers) -> None:
    """Regression test for the "galaxy view stuck indefinitely" bug:
    scan_service.scan_region() is a synchronous function that can take a
    long time (real first-scan measurements against a live AWS account:
    upwards of two minutes). GET /resources/scan is `async def` -- calling
    scan_region() directly (no `await`, no threadpool offload) blocks the
    single-threaded ASGI event loop for the whole scan, freezing every
    other concurrent request on the process, including an unrelated
    request like GET /resources/regions.

    This proves the opposite: a fast concurrent request started while a
    slow scan is in flight completes *before* the slow scan does, which is
    only possible if scan_region() is running off the event loop (in
    FastAPI's threadpool via run_in_threadpool) rather than blocking it.
    """
    events: list[str] = []

    def slow_scan_region(region: str, force: bool = False) -> ScanResponse:
        # Real, synchronous blocking work (not an awaitable) -- same shape
        # as the real scan_service.scan_region, which makes blocking
        # boto3 calls under the hood.
        time.sleep(0.3)
        events.append("scan_done")
        return _empty_scan(region)

    def fast_list_available_regions() -> list[str]:
        events.append("regions_done")
        return ["us-east-1", "us-west-2"]

    async def _run() -> tuple[int, int]:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            scan_task = asyncio.create_task(
                ac.get(
                    "/resources/scan", params={"region": "us-east-1"}, headers=auth_headers
                )
            )
            # Give the scan request a head start so it's the one already
            # "in flight" when the fast request is issued -- if scan_region
            # blocks the event loop, the regions request can't even start
            # running until the scan's synchronous call returns.
            await asyncio.sleep(0.05)
            regions_task = asyncio.create_task(
                ac.get("/resources/regions", headers=auth_headers)
            )
            scan_response, regions_response = await asyncio.gather(scan_task, regions_task)
            return scan_response.status_code, regions_response.status_code

    with (
        patch(
            "app.api.routes.resources.scan_service.scan_region",
            side_effect=slow_scan_region,
        ),
        patch(
            "app.api.routes.resources.scan_service.list_available_regions",
            side_effect=fast_list_available_regions,
        ),
    ):
        scan_status, regions_status = asyncio.run(_run())

    assert scan_status == 200
    assert regions_status == 200
    # The fast request must finish first -- if this were still ["scan_done",
    # "regions_done"], the event loop was blocked for the scan's duration.
    assert events == ["regions_done", "scan_done"]
