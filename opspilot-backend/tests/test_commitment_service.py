from datetime import date
from unittest.mock import MagicMock, patch

from app.services import commitment_service


def _sp_utilization_response(
    total_commitment: str = "100.0",
    used_commitment: str = "80.0",
    unused_commitment: str = "20.0",
    utilization_pct: str = "80.0",
) -> dict:
    return {
        "Total": {
            "Utilization": {
                "TotalCommitment": total_commitment,
                "UsedCommitment": used_commitment,
                "UnusedCommitment": unused_commitment,
                "UtilizationPercentage": utilization_pct,
            },
            "Savings": {"NetSavings": "12.5"},
        }
    }


def _sp_coverage_response(on_demand: str = "50.0", total: str = "100.0") -> dict:
    return {
        "SavingsPlansCoverages": [
            {
                "Coverage": {
                    "OnDemandCost": on_demand,
                    "TotalCost": total,
                },
                "TimePeriod": {"Start": "2026-08-01", "End": "2026-09-01"},
            }
        ]
    }


def _ri_utilization_response(
    purchased_hours: str = "720", unused_hours: str = "100", pct: str = "86.1"
) -> dict:
    return {
        "Total": {
            "UtilizationPercentage": pct,
            "PurchasedHours": purchased_hours,
            "UnusedHours": unused_hours,
            "NetRISavings": "5.0",
        }
    }


def _ri_coverage_response(
    on_demand_hours: str = "10",
    reserved_hours: str = "90",
    pct: str = "90.0",
    on_demand_cost: str = "20.0",
) -> dict:
    return {
        "Total": {
            "CoverageHours": {
                "OnDemandHours": on_demand_hours,
                "ReservedHours": reserved_hours,
                "TotalRunningHours": "100",
                "CoverageHoursPercentage": pct,
            },
            "CoverageCost": {"OnDemandCost": on_demand_cost},
        }
    }


@patch("app.services.commitment_service.get_ce_client")
def test_analyze_commitment_utilization_happy_path(mock_get_client: MagicMock) -> None:
    client = MagicMock()
    client.get_savings_plans_utilization.return_value = _sp_utilization_response()
    client.get_savings_plans_coverage.return_value = _sp_coverage_response()
    client.get_reservation_utilization.return_value = _ri_utilization_response()
    client.get_reservation_coverage.return_value = _ri_coverage_response()
    mock_get_client.return_value = client

    result = commitment_service.analyze_commitment_utilization(days=30)

    assert result.savings_plans_utilization.total_commitment_usd == 100.0
    assert result.savings_plans_utilization.utilization_percentage == 80.0
    assert result.savings_plans_coverage.on_demand_cost_usd == 50.0
    assert result.reservation_utilization.purchased_hours == 720.0
    assert result.reservation_coverage.coverage_percentage == 90.0
    assert result.cost_explorer_api_requests_made == 4
    assert result.estimated_cost_explorer_api_cost_usd == round(4 * 0.01, 4)
    assert "Cost Explorer" in result.note
    assert result.period_end == date.today().isoformat()


@patch("app.services.commitment_service.get_ce_client")
def test_underutilized_savings_plan_flagged_as_waste(mock_get_client: MagicMock) -> None:
    client = MagicMock()
    client.get_savings_plans_utilization.return_value = _sp_utilization_response(
        total_commitment="100.0", used_commitment="30.0", unused_commitment="70.0",
        utilization_pct="30.0",
    )
    client.get_savings_plans_coverage.return_value = _sp_coverage_response(
        on_demand="0.0", total="0.0"
    )
    client.get_reservation_utilization.return_value = _ri_utilization_response(
        purchased_hours="0", unused_hours="0", pct="0.0"
    )
    client.get_reservation_coverage.return_value = _ri_coverage_response(
        on_demand_hours="0", reserved_hours="0", pct="0.0"
    )
    mock_get_client.return_value = client

    result = commitment_service.analyze_commitment_utilization()

    waste_findings = [f for f in result.findings if f.category == "waste"]
    assert len(waste_findings) == 1
    assert waste_findings[0].finding_type == "underutilized_savings_plan"


@patch("app.services.commitment_service.get_ce_client")
def test_savings_plan_coverage_gap_flagged_as_opportunity_not_waste(
    mock_get_client: MagicMock,
) -> None:
    client = MagicMock()
    client.get_savings_plans_utilization.return_value = _sp_utilization_response(
        total_commitment="0.0", used_commitment="0.0", unused_commitment="0.0",
        utilization_pct="0.0",
    )
    client.get_savings_plans_coverage.return_value = _sp_coverage_response(
        on_demand="500.0", total="1000.0"
    )
    client.get_reservation_utilization.return_value = _ri_utilization_response(
        purchased_hours="0", unused_hours="0", pct="0.0"
    )
    client.get_reservation_coverage.return_value = _ri_coverage_response(
        on_demand_hours="0", reserved_hours="0", pct="0.0"
    )
    mock_get_client.return_value = client

    result = commitment_service.analyze_commitment_utilization()

    coverage_findings = [
        f for f in result.findings if f.finding_type == "savings_plan_coverage_gap"
    ]
    assert len(coverage_findings) == 1
    assert coverage_findings[0].category == "opportunity"
    # never conflated with waste
    assert all(f.category != "waste" for f in coverage_findings)


@patch("app.services.commitment_service.get_ce_client")
def test_coverage_gap_suppressed_below_min_on_demand_spend_floor(
    mock_get_client: MagicMock,
) -> None:
    """Code-review finding: COVERAGE_GAP_MIN_ON_DEMAND_USD (roadmap: don't
    tell a trivial account to buy a commitment) is a real, wired-in check,
    not just described -- this proves the suppression, not just the
    triggering case the other coverage-gap test already covers. $5 on-demand
    spend has the same low ~50% coverage percentage as the triggering test
    above (on_demand=500/total=1000), but sits below the $10 floor, so no
    finding should fire despite the coverage percentage alone being low
    enough to qualify."""
    client = MagicMock()
    client.get_savings_plans_utilization.return_value = _sp_utilization_response(
        total_commitment="0.0", used_commitment="0.0", unused_commitment="0.0",
        utilization_pct="0.0",
    )
    client.get_savings_plans_coverage.return_value = _sp_coverage_response(
        on_demand="5.0", total="10.0"
    )
    client.get_reservation_utilization.return_value = _ri_utilization_response(
        purchased_hours="0", unused_hours="0", pct="0.0"
    )
    client.get_reservation_coverage.return_value = _ri_coverage_response(
        on_demand_hours="0", reserved_hours="0", pct="0.0"
    )
    mock_get_client.return_value = client

    result = commitment_service.analyze_commitment_utilization()

    assert not any(f.finding_type == "savings_plan_coverage_gap" for f in result.findings)


@patch("app.services.commitment_service.get_ce_client")
def test_zero_commitment_produces_no_utilization_finding(mock_get_client: MagicMock) -> None:
    """An account with no Savings Plans/RIs at all shouldn't be told it's
    'wasting' a commitment it never purchased."""
    client = MagicMock()
    client.get_savings_plans_utilization.return_value = _sp_utilization_response(
        total_commitment="0.0", used_commitment="0.0", unused_commitment="0.0",
        utilization_pct="0.0",
    )
    client.get_savings_plans_coverage.return_value = _sp_coverage_response(
        on_demand="0.0", total="0.0"
    )
    client.get_reservation_utilization.return_value = _ri_utilization_response(
        purchased_hours="0", unused_hours="0", pct="0.0"
    )
    client.get_reservation_coverage.return_value = _ri_coverage_response(
        on_demand_hours="0", reserved_hours="0", pct="0.0", on_demand_cost="0.0"
    )
    mock_get_client.return_value = client

    result = commitment_service.analyze_commitment_utilization()

    assert result.findings == []


@patch("app.services.commitment_service.get_ce_client")
def test_one_section_failing_does_not_blank_the_others(mock_get_client: MagicMock) -> None:
    client = MagicMock()
    client.get_savings_plans_utilization.side_effect = RuntimeError("DataUnavailableException")
    client.get_savings_plans_coverage.return_value = _sp_coverage_response()
    client.get_reservation_utilization.return_value = _ri_utilization_response()
    client.get_reservation_coverage.return_value = _ri_coverage_response()
    mock_get_client.return_value = client

    result = commitment_service.analyze_commitment_utilization()

    assert result.savings_plans_utilization is None
    assert result.savings_plans_coverage is not None
    assert result.reservation_utilization is not None
    assert result.reservation_coverage is not None
