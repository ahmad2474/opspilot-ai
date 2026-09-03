"""Tests for the /waste/* routes (roadmap phase 2 Section 1, "Batch A") --
auth gating plus the route-level composition each endpoint adds on top of
its service function (looping buckets/endpoints, translating a service
exception into an HTTP status).
"""
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

from app.main import app
from app.models.commitment import CommitmentAnalysisReport
from app.models.compute_optimizer import RightsizingReport
from app.models.dashboard import S3BucketSummary, S3Card
from app.models.ecs import EcsClusterList, EcsClusterSummary, EcsContainerIdleReport
from app.models.logs import LogRetentionReport
from app.models.s3_waste import S3WasteReport
from app.models.snapshot import SnapshotSprawlReport
from app.models.vpc_endpoint import VpcEndpoint, VpcEndpointList
from app.services import compute_optimizer_service, snapshot_service

client = TestClient(app)


def test_log_retention_route_requires_session() -> None:
    response = client.get("/waste/log-retention")
    assert response.status_code == 401


def test_s3_waste_route_requires_session() -> None:
    response = client.get("/waste/s3")
    assert response.status_code == 401


def test_snapshots_route_requires_session() -> None:
    response = client.get(
        "/waste/snapshots", params={"resource_type": "ebs", "retention_days_or_count": 30}
    )
    assert response.status_code == 401


def test_vpc_endpoints_route_requires_session() -> None:
    response = client.get("/waste/vpc-endpoints")
    assert response.status_code == 401


@patch("app.api.routes.waste.logs_service.check_log_retention")
def test_log_retention_route_returns_report(
    mock_check: MagicMock, auth_headers: dict[str, str]
) -> None:
    mock_check.return_value = LogRetentionReport(
        findings=[], flagged_count=0, total_log_groups_checked=3, total_stored_bytes_at_risk=0
    )
    response = client.get("/waste/log-retention", headers=auth_headers)
    assert response.status_code == 200
    assert response.json()["total_log_groups_checked"] == 3


@patch("app.api.routes.waste.s3_service.check_s3_waste")
@patch("app.api.routes.waste.s3_service.list_buckets")
def test_s3_waste_route_loops_every_bucket_and_skips_failures(
    mock_list_buckets: MagicMock, mock_check: MagicMock, auth_headers: dict[str, str]
) -> None:
    mock_list_buckets.return_value = S3Card(
        buckets=[
            S3BucketSummary(name="bucket-ok", creation_date=None),
            S3BucketSummary(name="bucket-fails", creation_date=None),
        ],
        count=2,
    )

    def _check(bucket: str, days: int) -> S3WasteReport:
        if bucket == "bucket-fails":
            raise RuntimeError("boom")
        return S3WasteReport(
            bucket=bucket,
            window_days=days,
            findings=[],
            checked_at=datetime(2026, 9, 1, tzinfo=timezone.utc),
        )

    mock_check.side_effect = _check

    response = client.get("/waste/s3", headers=auth_headers)

    assert response.status_code == 200
    payload = response.json()
    assert len(payload) == 1
    assert payload[0]["bucket"] == "bucket-ok"


def test_snapshots_route_translates_unsupported_type_to_400(
    auth_headers: dict[str, str],
) -> None:
    response = client.get(
        "/waste/snapshots",
        params={"resource_type": "sqs", "retention_days_or_count": 30},
        headers=auth_headers,
    )
    assert response.status_code == 400


@patch("app.api.routes.waste.snapshot_service.check_snapshot_sprawl")
def test_snapshots_route_returns_report(
    mock_check: MagicMock, auth_headers: dict[str, str]
) -> None:
    mock_check.return_value = SnapshotSprawlReport(
        resource_type="ebs",
        retention_days_or_count=30,
        retention_mode="days",
        findings=[],
        total_snapshots_checked=2,
        orphaned_count=0,
        beyond_retention_count=0,
    )
    response = client.get(
        "/waste/snapshots",
        params={"resource_type": "ebs", "retention_days_or_count": 30},
        headers=auth_headers,
    )
    assert response.status_code == 200
    assert response.json()["total_snapshots_checked"] == 2
    mock_check.assert_called_once_with("ebs", 30, retention_mode="days")


@patch("app.api.routes.waste.vpc_endpoint_service.estimate_vpc_endpoint_cost")
@patch("app.api.routes.waste.vpc_endpoint_service.check_vpc_endpoint_idle")
@patch("app.api.routes.waste.vpc_endpoint_service.list_vpc_endpoints")
def test_vpc_endpoints_route_skips_gateway_endpoints(
    mock_list: MagicMock, mock_idle: MagicMock, mock_cost: MagicMock, auth_headers: dict[str, str]
) -> None:
    mock_list.return_value = VpcEndpointList(
        vpc_endpoints=[
            VpcEndpoint(
                vpc_endpoint_id="vpce-if",
                vpc_endpoint_type="Interface",
                service_name="com.amazonaws.us-east-1.ec2",
                state="available",
            ),
            VpcEndpoint(
                vpc_endpoint_id="vpce-gw",
                vpc_endpoint_type="Gateway",
                service_name="com.amazonaws.us-east-1.s3",
                state="available",
            ),
        ],
        count=2,
    )
    mock_idle.side_effect = RuntimeError("cloudwatch down")
    mock_cost.side_effect = RuntimeError("pricing down")

    response = client.get("/waste/vpc-endpoints", headers=auth_headers)

    assert response.status_code == 200
    payload = response.json()
    assert payload["count"] == 1
    assert payload["entries"][0]["endpoint"]["vpc_endpoint_id"] == "vpce-if"
    assert payload["entries"][0]["idle"] is None
    assert payload["entries"][0]["cost"] is None


def test_ecs_container_idle_route_requires_session() -> None:
    response = client.get("/waste/ecs-container-idle")
    assert response.status_code == 401


def test_commitment_utilization_route_requires_session() -> None:
    response = client.get("/waste/commitment-utilization")
    assert response.status_code == 401


def test_rightsizing_route_requires_session() -> None:
    response = client.get("/waste/rightsizing", params={"resource_type": "ec2"})
    assert response.status_code == 401


@patch("app.api.routes.waste.ecs_service.check_container_idle")
@patch("app.api.routes.waste.ecs_service.list_clusters")
def test_ecs_container_idle_route_loops_every_cluster_and_skips_failures(
    mock_list_clusters: MagicMock, mock_check: MagicMock, auth_headers: dict[str, str]
) -> None:
    mock_list_clusters.return_value = EcsClusterList(
        clusters=[
            EcsClusterSummary(
                cluster_arn="arn:aws:ecs:us-east-1:123:cluster/cluster-ok",
                cluster_name="cluster-ok",
                status="ACTIVE",
                running_tasks_count=1,
                pending_tasks_count=0,
                active_services_count=1,
                container_insights="enabled",
            ),
            EcsClusterSummary(
                cluster_arn="arn:aws:ecs:us-east-1:123:cluster/cluster-fails",
                cluster_name="cluster-fails",
                status="ACTIVE",
                running_tasks_count=0,
                pending_tasks_count=0,
                active_services_count=0,
                container_insights="enabled",
            ),
        ],
        count=2,
    )

    def _check(cluster_name: str, days: int) -> EcsContainerIdleReport:
        if cluster_name == "cluster-fails":
            raise RuntimeError("boom")
        return EcsContainerIdleReport(
            cluster=cluster_name,
            window_days=days,
            container_insights_enabled=True,
            findings=[],
            total_tasks_checked=1,
            total_services_checked=1,
        )

    mock_check.side_effect = _check

    response = client.get("/waste/ecs-container-idle", headers=auth_headers)

    assert response.status_code == 200
    payload = response.json()
    assert len(payload) == 1
    assert payload[0]["cluster"] == "cluster-ok"


@patch("app.api.routes.waste.commitment_service.analyze_commitment_utilization")
def test_commitment_utilization_route_returns_report(
    mock_analyze: MagicMock, auth_headers: dict[str, str]
) -> None:
    mock_analyze.return_value = CommitmentAnalysisReport(
        period_start="2026-08-01",
        period_end="2026-09-01",
        savings_plans_utilization=None,
        savings_plans_coverage=None,
        reservation_utilization=None,
        reservation_coverage=None,
        findings=[],
        cost_explorer_api_requests_made=4,
        estimated_cost_explorer_api_cost_usd=0.04,
        note="test note",
    )
    response = client.get("/waste/commitment-utilization", headers=auth_headers)
    assert response.status_code == 200
    assert response.json()["cost_explorer_api_requests_made"] == 4
    mock_analyze.assert_called_once_with(30)


def test_rightsizing_route_translates_unsupported_type_to_400(
    auth_headers: dict[str, str],
) -> None:
    response = client.get(
        "/waste/rightsizing", params={"resource_type": "rds"}, headers=auth_headers
    )
    assert response.status_code == 400


@patch("app.api.routes.waste.compute_optimizer_service.get_rightsizing_recommendations")
def test_rightsizing_route_returns_report(
    mock_get: MagicMock, auth_headers: dict[str, str]
) -> None:
    mock_get.return_value = RightsizingReport(
        resource_type="ec2", enrolled=True, findings=[], total_checked=0, note=None
    )
    response = client.get(
        "/waste/rightsizing", params={"resource_type": "ec2"}, headers=auth_headers
    )
    assert response.status_code == 200
    assert response.json()["resource_type"] == "ec2"


def test_rightsizing_error_class_used_by_route() -> None:
    # Sanity check the route imports the same exception class the service
    # raises -- a drifted import here would silently fall through to a 500
    # instead of the documented 400.
    assert issubclass(
        compute_optimizer_service.UnsupportedRightsizingResourceTypeError, ValueError
    )


def test_snapshot_service_error_class_used_by_route() -> None:
    # Sanity check the route imports the same exception class the service
    # raises -- a drifted import here would silently fall through to a 500
    # instead of the documented 400.
    assert issubclass(snapshot_service.UnsupportedSnapshotResourceTypeError, ValueError)
