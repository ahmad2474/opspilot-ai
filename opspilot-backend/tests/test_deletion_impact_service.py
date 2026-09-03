"""Tests for check_deletion_impact (roadmap phase 2 Section 3.1) -- mocks
every AWS-calling dependency, so this exercises the pure
synthesis/composition logic without touching the LLM or a real AWS
account at all (same convention as every other service test in this
suite).
"""
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest
from botocore.exceptions import ClientError

from app.models.cost import CostEstimate, DateRange
from app.models.dashboard import RdsInstanceSummary
from app.models.ebs import EbsVolume
from app.models.ec2 import BlockDeviceMapping, EC2Instance
from app.models.eip import ElasticIp, ElasticIpList
from app.models.snapshot import SnapshotSummary
from app.services import deletion_impact_service

NOW = datetime(2026, 9, 1, tzinfo=timezone.utc)


def _access_denied(action: str) -> ClientError:
    return ClientError(
        {"Error": {"Code": "AccessDenied", "Message": f"not authorized to perform {action}"}},
        action,
    )


def _cost_estimate(resource_id: str, resource_type: str, monthly: float) -> CostEstimate:
    return CostEstimate(
        resource_id=resource_id,
        resource_type=resource_type,
        date_range=DateRange(start=NOW, end=NOW),
        method="list_price",
        hourly_rate=monthly / 730.0,
        projected_monthly=monthly,
        incurred_so_far=0.0,
    )


def _ec2_instance(
    *,
    instance_id: str = "i-123",
    block_device_mappings: list[BlockDeviceMapping] | None = None,
    security_group_ids: list[str] | None = None,
    iam_instance_profile_name: str | None = None,
) -> EC2Instance:
    return EC2Instance(
        instance_id=instance_id,
        instance_type="t3.micro",
        state="running",
        availability_zone="us-east-1a",
        launch_time=NOW,
        security_group_ids=security_group_ids or [],
        iam_instance_profile_name=iam_instance_profile_name,
        block_device_mappings=block_device_mappings or [],
    )


# =====================================================================
# check_deletion_impact dispatch
# =====================================================================


def test_unsupported_resource_type_raises() -> None:
    with pytest.raises(deletion_impact_service.UnsupportedDeletionImpactResourceTypeError):
        deletion_impact_service.check_deletion_impact("sqs", "some-id")


# =====================================================================
# _run_fanout -- same graceful-degradation shape as
# scan_service._run_collectors_concurrently.
# =====================================================================


def test_run_fanout_calls_every_check_and_returns_by_key() -> None:
    result = deletion_impact_service._run_fanout(
        {"a": lambda: 1, "b": lambda: 2, "c": lambda: 3}
    )
    assert result == {"a": 1, "b": 2, "c": 3}


def test_run_fanout_one_failure_does_not_blank_the_others() -> None:
    def _boom():
        raise RuntimeError("boom")

    result = deletion_impact_service._run_fanout({"ok": lambda: "fine", "bad": _boom})
    assert result["ok"] == "fine"
    assert result["bad"] is None


# =====================================================================
# EC2 termination impact
# =====================================================================


@patch("app.services.deletion_impact_service.ec2_service.get_instance")
def test_ec2_instance_not_found_raises(mock_get_instance: MagicMock) -> None:
    mock_get_instance.return_value = None
    with pytest.raises(ValueError, match="i-missing"):
        deletion_impact_service.check_deletion_impact("ec2", "i-missing")


@patch("app.services.deletion_impact_service.get_elbv2_client")
@patch("app.services.deletion_impact_service.get_autoscaling_client")
@patch("app.services.deletion_impact_service.eip_service.list_addresses")
@patch("app.services.deletion_impact_service.cost_service.estimate_cost")
@patch("app.services.deletion_impact_service.ec2_service.get_instance")
def test_ec2_root_volume_delete_on_termination_true_is_removed(
    mock_get_instance: MagicMock,
    mock_estimate_cost: MagicMock,
    mock_list_addresses: MagicMock,
    mock_get_asg_client: MagicMock,
    mock_get_elbv2_client: MagicMock,
) -> None:
    mock_get_instance.return_value = _ec2_instance(
        block_device_mappings=[
            BlockDeviceMapping(
                device_name="/dev/xvda", volume_id="vol-root", delete_on_termination=True
            )
        ]
    )
    mock_list_addresses.return_value = ElasticIpList(addresses=[], count=0)
    _no_asg_membership(mock_get_asg_client)
    _no_target_groups(mock_get_elbv2_client)

    result = deletion_impact_service.check_deletion_impact("ec2", "i-123")

    removed_ids = {(e.resource_type, e.resource_id) for e in result.will_be_removed}
    assert ("ebs", "vol-root") in removed_ids
    assert result.will_persist_and_keep_costing == []
    mock_estimate_cost.assert_not_called()


@patch("app.services.deletion_impact_service.get_elbv2_client")
@patch("app.services.deletion_impact_service.get_autoscaling_client")
@patch("app.services.deletion_impact_service.eip_service.list_addresses")
@patch("app.services.deletion_impact_service.cost_service.estimate_cost")
@patch("app.services.deletion_impact_service.ec2_service.get_instance")
def test_ec2_data_volume_delete_on_termination_false_persists_with_real_cost(
    mock_get_instance: MagicMock,
    mock_estimate_cost: MagicMock,
    mock_list_addresses: MagicMock,
    mock_get_asg_client: MagicMock,
    mock_get_elbv2_client: MagicMock,
) -> None:
    mock_get_instance.return_value = _ec2_instance(
        block_device_mappings=[
            BlockDeviceMapping(
                device_name="/dev/xvda", volume_id="vol-root", delete_on_termination=True
            ),
            BlockDeviceMapping(
                device_name="/dev/sdf", volume_id="vol-data", delete_on_termination=False
            ),
        ]
    )
    mock_estimate_cost.return_value = _cost_estimate("vol-data", "ebs", 4.20)
    mock_list_addresses.return_value = ElasticIpList(addresses=[], count=0)
    _no_asg_membership(mock_get_asg_client)
    _no_target_groups(mock_get_elbv2_client)

    result = deletion_impact_service.check_deletion_impact("ec2", "i-123")

    assert len(result.will_persist_and_keep_costing) == 1
    entry = result.will_persist_and_keep_costing[0]
    assert entry.resource_type == "ebs"
    assert entry.resource_id == "vol-data"
    assert entry.estimated_monthly_cost_usd == 4.20
    mock_estimate_cost.assert_called_once_with("ebs", "vol-data", region=None)
    # root volume (DeleteOnTermination=true) is removed, not persisted
    removed_ids = {e.resource_id for e in result.will_be_removed}
    assert "vol-root" in removed_ids


@patch("app.services.deletion_impact_service.get_elbv2_client")
@patch("app.services.deletion_impact_service.get_autoscaling_client")
@patch("app.services.deletion_impact_service.eip_service.list_addresses")
@patch("app.services.deletion_impact_service.ec2_service.get_instance")
def test_ec2_associated_eip_persists_with_documented_idle_rate_not_current_zero_cost(
    mock_get_instance: MagicMock,
    mock_list_addresses: MagicMock,
    mock_get_asg_client: MagicMock,
    mock_get_elbv2_client: MagicMock,
) -> None:
    mock_get_instance.return_value = _ec2_instance()
    mock_list_addresses.return_value = ElasticIpList(
        addresses=[
            ElasticIp(
                allocation_id="eipalloc-1",
                public_ip="1.2.3.4",
                domain="vpc",
                association_id="eipassoc-1",
                instance_id="i-123",
            )
        ],
        count=1,
    )
    _no_asg_membership(mock_get_asg_client)
    _no_target_groups(mock_get_elbv2_client)

    result = deletion_impact_service.check_deletion_impact("ec2", "i-123")

    assert len(result.will_persist_and_keep_costing) == 1
    entry = result.will_persist_and_keep_costing[0]
    assert entry.resource_type == "eip"
    assert entry.resource_id == "eipalloc-1"
    # Must NOT be $0 -- estimate_cost would report $0 for an EIP that is
    # still currently associated, which is the wrong (misleading) figure
    # for "what will this cost after termination".
    expected = deletion_impact_service.EIP_POST_DELETION_MONTHLY_COST_USD
    assert entry.estimated_monthly_cost_usd == expected
    assert entry.estimated_monthly_cost_usd > 0


def _asg_client_with_membership(group_name: str, desired_capacity: int | None) -> MagicMock:
    client = MagicMock()
    client.describe_auto_scaling_instances.return_value = {
        "AutoScalingInstances": [{"AutoScalingGroupName": group_name}]
    }
    if desired_capacity is None:
        client.describe_auto_scaling_groups.return_value = {"AutoScalingGroups": []}
    else:
        client.describe_auto_scaling_groups.return_value = {
            "AutoScalingGroups": [{"DesiredCapacity": desired_capacity}]
        }
    return client


def _no_asg_membership(mock_get_client: MagicMock) -> None:
    client = MagicMock()
    client.describe_auto_scaling_instances.return_value = {"AutoScalingInstances": []}
    mock_get_client.return_value = client


def _no_target_groups(mock_get_client: MagicMock) -> None:
    client = MagicMock()
    paginator = MagicMock()
    paginator.paginate.return_value = [{"TargetGroups": []}]
    client.get_paginator.return_value = paginator
    mock_get_client.return_value = client


@patch("app.services.deletion_impact_service.get_elbv2_client")
@patch("app.services.deletion_impact_service.get_autoscaling_client")
@patch("app.services.deletion_impact_service.eip_service.list_addresses")
@patch("app.services.deletion_impact_service.ec2_service.get_instance")
def test_ec2_asg_membership_surfaces_replacement_warning_with_desired_capacity(
    mock_get_instance: MagicMock,
    mock_list_addresses: MagicMock,
    mock_get_asg_client: MagicMock,
    mock_get_elbv2_client: MagicMock,
) -> None:
    mock_get_instance.return_value = _ec2_instance()
    mock_list_addresses.return_value = ElasticIpList(addresses=[], count=0)
    mock_get_asg_client.return_value = _asg_client_with_membership("my-asg", 3)
    _no_target_groups(mock_get_elbv2_client)

    result = deletion_impact_service.check_deletion_impact("ec2", "i-123")

    warning_codes = {w.code for w in result.behavioral_warnings}
    assert "asg_will_replace_instance" in warning_codes
    warning = next(w for w in result.behavioral_warnings if w.code == "asg_will_replace_instance")
    assert "my-asg" in warning.message
    assert "3" in warning.message
    assert result.check_errors == []


@patch("app.services.deletion_impact_service.get_elbv2_client")
@patch("app.services.deletion_impact_service.get_autoscaling_client")
@patch("app.services.deletion_impact_service.eip_service.list_addresses")
@patch("app.services.deletion_impact_service.ec2_service.get_instance")
def test_ec2_asg_permission_gap_reported_as_check_error_not_false_negative(
    mock_get_instance: MagicMock,
    mock_list_addresses: MagicMock,
    mock_get_asg_client: MagicMock,
    mock_get_elbv2_client: MagicMock,
) -> None:
    mock_get_instance.return_value = _ec2_instance()
    mock_list_addresses.return_value = ElasticIpList(addresses=[], count=0)
    client = MagicMock()
    client.describe_auto_scaling_instances.side_effect = _access_denied(
        "DescribeAutoScalingInstances"
    )
    mock_get_asg_client.return_value = client
    _no_target_groups(mock_get_elbv2_client)

    result = deletion_impact_service.check_deletion_impact("ec2", "i-123")

    # No ASG warning is fabricated, and the gap is explicit in check_errors
    # -- never silently treated as "not a member".
    assert result.behavioral_warnings == []
    assert any("Auto Scaling Group" in err for err in result.check_errors)


@patch("app.services.deletion_impact_service.get_elbv2_client")
@patch("app.services.deletion_impact_service.get_autoscaling_client")
@patch("app.services.deletion_impact_service.eip_service.list_addresses")
@patch("app.services.deletion_impact_service.ec2_service.get_instance")
def test_ec2_registered_lb_target_surfaces_warning(
    mock_get_instance: MagicMock,
    mock_list_addresses: MagicMock,
    mock_get_asg_client: MagicMock,
    mock_get_elbv2_client: MagicMock,
) -> None:
    mock_get_instance.return_value = _ec2_instance()
    mock_list_addresses.return_value = ElasticIpList(addresses=[], count=0)
    _no_asg_membership(mock_get_asg_client)

    client = MagicMock()
    paginator = MagicMock()
    paginator.paginate.return_value = [{"TargetGroups": [{"TargetGroupArn": "arn:tg-1"}]}]
    client.get_paginator.return_value = paginator
    client.describe_target_health.return_value = {
        "TargetHealthDescriptions": [{"Target": {"Id": "i-123"}}]
    }
    mock_get_elbv2_client.return_value = client

    result = deletion_impact_service.check_deletion_impact("ec2", "i-123")

    warning_codes = {w.code for w in result.behavioral_warnings}
    assert "lb_target_deregistered" in warning_codes


@patch("app.services.deletion_impact_service.get_elbv2_client")
@patch("app.services.deletion_impact_service.get_autoscaling_client")
@patch("app.services.deletion_impact_service.eip_service.list_addresses")
@patch("app.services.deletion_impact_service.ec2_service.get_instance")
def test_ec2_never_affected_lists_security_groups_and_iam_role(
    mock_get_instance: MagicMock,
    mock_list_addresses: MagicMock,
    mock_get_asg_client: MagicMock,
    mock_get_elbv2_client: MagicMock,
) -> None:
    mock_get_instance.return_value = _ec2_instance(
        security_group_ids=["sg-1", "sg-2"], iam_instance_profile_name="my-profile"
    )
    mock_list_addresses.return_value = ElasticIpList(addresses=[], count=0)
    _no_asg_membership(mock_get_asg_client)
    _no_target_groups(mock_get_elbv2_client)

    result = deletion_impact_service.check_deletion_impact("ec2", "i-123")

    never_affected_ids = {e.resource_id for e in result.never_affected}
    assert {"sg-1", "sg-2", "my-profile"} <= never_affected_ids
    types = {e.resource_type for e in result.never_affected}
    assert types == {"security_group", "iam_role"}


# =====================================================================
# RDS deletion impact
# =====================================================================


def _rds_instance(
    *,
    identifier: str = "db-1",
    read_replicas: list[str] | None = None,
    vpc_security_group_ids: list[str] | None = None,
) -> RdsInstanceSummary:
    return RdsInstanceSummary(
        identifier=identifier,
        engine="postgres",
        instance_class="db.t3.micro",
        status="available",
        instance_create_time=NOW,
        read_replica_db_instance_identifiers=read_replicas or [],
        vpc_security_group_ids=vpc_security_group_ids or [],
    )


@patch("app.services.deletion_impact_service.rds_service.get_instance")
def test_rds_instance_not_found_raises(mock_get_instance: MagicMock) -> None:
    mock_get_instance.return_value = None
    with pytest.raises(ValueError, match="db-missing"):
        deletion_impact_service.check_deletion_impact("rds", "db-missing")


@patch("app.services.deletion_impact_service.snapshot_service.list_snapshots_for_source")
@patch("app.services.deletion_impact_service.rds_service.get_instance")
def test_rds_automated_snapshots_summarized_as_will_be_removed(
    mock_get_instance: MagicMock, mock_list_snapshots: MagicMock
) -> None:
    mock_get_instance.return_value = _rds_instance()
    mock_list_snapshots.return_value = [
        SnapshotSummary(
            snapshot_id="rds:db-1-2026-09-01", source_resource_id="db-1", age_days=1,
            size_gb=20, snapshot_type="automated",
        ),
        SnapshotSummary(
            snapshot_id="rds:db-1-2026-08-31", source_resource_id="db-1", age_days=2,
            size_gb=20, snapshot_type="automated",
        ),
    ]

    result = deletion_impact_service.check_deletion_impact("rds", "db-1")

    automated_entries = [
        e for e in result.will_be_removed if e.resource_type == "rds_automated_backup"
    ]
    assert len(automated_entries) == 1
    assert "2" in automated_entries[0].reason
    assert result.will_persist_and_keep_costing == []


@patch("app.services.deletion_impact_service.snapshot_service.list_snapshots_for_source")
@patch("app.services.deletion_impact_service.rds_service.get_instance")
def test_rds_manual_snapshots_persist_without_fabricated_cost(
    mock_get_instance: MagicMock, mock_list_snapshots: MagicMock
) -> None:
    mock_get_instance.return_value = _rds_instance()
    mock_list_snapshots.return_value = [
        SnapshotSummary(
            snapshot_id="manual-snap-1", source_resource_id="db-1", age_days=10,
            size_gb=20, snapshot_type="manual",
        ),
    ]

    result = deletion_impact_service.check_deletion_impact("rds", "db-1")

    persist_entries = [
        e for e in result.will_persist_and_keep_costing if e.resource_type == "rds_snapshot"
    ]
    assert len(persist_entries) == 1
    entry = persist_entries[0]
    assert entry.resource_id == "manual-snap-1"
    assert entry.estimated_monthly_cost_usd is None
    assert "check_snapshot_sprawl" in entry.cost_note


@patch("app.services.deletion_impact_service.snapshot_service.list_snapshots_for_source")
@patch("app.services.deletion_impact_service.cost_service.estimate_cost")
@patch("app.services.deletion_impact_service.rds_service.get_instance")
def test_rds_read_replicas_persist_with_real_cost(
    mock_get_instance: MagicMock, mock_estimate_cost: MagicMock, mock_list_snapshots: MagicMock
) -> None:
    mock_get_instance.return_value = _rds_instance(read_replicas=["db-1-replica"])
    mock_list_snapshots.return_value = []
    mock_estimate_cost.return_value = _cost_estimate("db-1-replica", "rds", 30.0)

    result = deletion_impact_service.check_deletion_impact("rds", "db-1")

    replica_entries = [e for e in result.will_persist_and_keep_costing if e.resource_type == "rds"]
    assert len(replica_entries) == 1
    assert replica_entries[0].resource_id == "db-1-replica"
    assert replica_entries[0].estimated_monthly_cost_usd == 30.0
    mock_estimate_cost.assert_called_once_with("rds", "db-1-replica", region=None)


@patch("app.services.deletion_impact_service.snapshot_service.list_snapshots_for_source")
@patch("app.services.deletion_impact_service.rds_service.get_instance")
def test_rds_final_snapshot_choice_is_always_surfaced(
    mock_get_instance: MagicMock, mock_list_snapshots: MagicMock
) -> None:
    mock_get_instance.return_value = _rds_instance()
    mock_list_snapshots.return_value = []

    result = deletion_impact_service.check_deletion_impact("rds", "db-1")

    codes = {w.code for w in result.behavioral_warnings}
    assert "final_snapshot_is_a_deletion_time_choice" in codes


@patch("app.services.deletion_impact_service.snapshot_service.list_snapshots_for_source")
@patch("app.services.deletion_impact_service.rds_service.get_instance")
def test_rds_never_affected_lists_security_groups(
    mock_get_instance: MagicMock, mock_list_snapshots: MagicMock
) -> None:
    mock_get_instance.return_value = _rds_instance(vpc_security_group_ids=["sg-rds-1"])
    mock_list_snapshots.return_value = []

    result = deletion_impact_service.check_deletion_impact("rds", "db-1")

    assert {"sg-rds-1"} <= {e.resource_id for e in result.never_affected}


# =====================================================================
# EBS standalone volume deletion impact
# =====================================================================


def _ebs_volume(
    *, volume_id: str = "vol-1", attached_instance_ids: list[str] | None = None
) -> EbsVolume:
    return EbsVolume(
        volume_id=volume_id,
        size_gb=20,
        volume_type="gp3",
        state="available" if not attached_instance_ids else "in-use",
        availability_zone="us-east-1a",
        create_time=NOW,
        attached_instance_ids=attached_instance_ids or [],
    )


@patch("app.services.deletion_impact_service.ebs_service.get_volume")
def test_ebs_volume_not_found_raises(mock_get_volume: MagicMock) -> None:
    mock_get_volume.return_value = None
    with pytest.raises(ValueError, match="vol-missing"):
        deletion_impact_service.check_deletion_impact("ebs", "vol-missing")


@patch("app.services.deletion_impact_service.snapshot_service.list_snapshots_for_source")
@patch("app.services.deletion_impact_service.ebs_service.get_volume")
def test_ebs_snapshots_persist_and_reference_snapshot_sprawl_check(
    mock_get_volume: MagicMock, mock_list_snapshots: MagicMock
) -> None:
    mock_get_volume.return_value = _ebs_volume()
    mock_list_snapshots.return_value = [
        SnapshotSummary(
            snapshot_id="snap-1", source_resource_id="vol-1", age_days=5, size_gb=20,
            snapshot_type=None,
        )
    ]

    result = deletion_impact_service.check_deletion_impact("ebs", "vol-1")

    assert len(result.will_persist_and_keep_costing) == 1
    entry = result.will_persist_and_keep_costing[0]
    assert entry.resource_type == "ebs_snapshot"
    assert entry.resource_id == "snap-1"
    assert entry.estimated_monthly_cost_usd is None
    assert "check_snapshot_sprawl" in entry.cost_note
    assert any(e.resource_id == "vol-1" for e in result.will_be_removed)


@patch("app.services.deletion_impact_service.snapshot_service.list_snapshots_for_source")
@patch("app.services.deletion_impact_service.ebs_service.get_volume")
def test_ebs_currently_attached_volume_surfaces_warning(
    mock_get_volume: MagicMock, mock_list_snapshots: MagicMock
) -> None:
    mock_get_volume.return_value = _ebs_volume(attached_instance_ids=["i-999"])
    mock_list_snapshots.return_value = []

    result = deletion_impact_service.check_deletion_impact("ebs", "vol-1")

    codes = {w.code for w in result.behavioral_warnings}
    assert "volume_currently_attached" in codes
    warning = next(w for w in result.behavioral_warnings if w.code == "volume_currently_attached")
    assert "i-999" in warning.message


@patch("app.services.deletion_impact_service.snapshot_service.list_snapshots_for_source")
@patch("app.services.deletion_impact_service.ebs_service.get_volume")
def test_ebs_not_attached_has_no_attachment_warning(
    mock_get_volume: MagicMock, mock_list_snapshots: MagicMock
) -> None:
    mock_get_volume.return_value = _ebs_volume()
    mock_list_snapshots.return_value = []

    result = deletion_impact_service.check_deletion_impact("ebs", "vol-1")

    assert result.behavioral_warnings == []
