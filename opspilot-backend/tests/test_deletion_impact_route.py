"""Tests for GET /deletion-impact (roadmap phase 2 Section 3) -- auth
gating plus the route-level exception-to-HTTP-status translation, same
convention as test_waste_route.py.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

from app.main import app
from app.models.deletion_impact import DeletionImpactReport, WillBeRemovedEntry
from app.services import deletion_impact_service

client = TestClient(app)


def test_deletion_impact_route_requires_session() -> None:
    response = client.get(
        "/deletion-impact", params={"resource_type": "ec2", "resource_id": "i-123"}
    )
    assert response.status_code == 401


@patch("app.api.routes.deletion_impact.deletion_impact_service.check_deletion_impact")
def test_deletion_impact_route_returns_report(
    mock_check: MagicMock, auth_headers: dict[str, str]
) -> None:
    mock_check.return_value = DeletionImpactReport(
        resource_type="ec2",
        resource_id="i-123",
        will_be_removed=[
            WillBeRemovedEntry(resource_type="ec2", resource_id="i-123", reason="terminated")
        ],
        will_persist_and_keep_costing=[],
        behavioral_warnings=[],
        never_affected=[],
        check_errors=[],
    )

    response = client.get(
        "/deletion-impact",
        params={"resource_type": "ec2", "resource_id": "i-123"},
        headers=auth_headers,
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["resource_type"] == "ec2"
    assert payload["will_be_removed"][0]["resource_id"] == "i-123"
    mock_check.assert_called_once_with("ec2", "i-123")


def test_deletion_impact_route_translates_unsupported_type_to_400(
    auth_headers: dict[str, str],
) -> None:
    response = client.get(
        "/deletion-impact",
        params={"resource_type": "sqs", "resource_id": "queue-1"},
        headers=auth_headers,
    )
    assert response.status_code == 400


@patch("app.api.routes.deletion_impact.deletion_impact_service.check_deletion_impact")
def test_deletion_impact_route_translates_not_found_to_404(
    mock_check: MagicMock, auth_headers: dict[str, str]
) -> None:
    mock_check.side_effect = ValueError("EC2 instance 'i-missing' not found")

    response = client.get(
        "/deletion-impact",
        params={"resource_type": "ec2", "resource_id": "i-missing"},
        headers=auth_headers,
    )
    assert response.status_code == 404


@patch("app.api.routes.deletion_impact.deletion_impact_service.check_deletion_impact")
def test_deletion_impact_route_translates_unexpected_error_to_502(
    mock_check: MagicMock, auth_headers: dict[str, str]
) -> None:
    mock_check.side_effect = RuntimeError("boom")

    response = client.get(
        "/deletion-impact",
        params={"resource_type": "ec2", "resource_id": "i-123"},
        headers=auth_headers,
    )
    assert response.status_code == 502


def test_deletion_impact_error_class_used_by_route() -> None:
    # Sanity check the route imports the same exception class the service
    # raises -- a drifted import here would silently fall through to a 500
    # instead of the documented 400.
    assert issubclass(
        deletion_impact_service.UnsupportedDeletionImpactResourceTypeError, ValueError
    )
