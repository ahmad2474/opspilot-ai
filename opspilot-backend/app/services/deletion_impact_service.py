"""Deletion-impact analysis (roadmap phase 2 Section 3 -- the permanent,
read-only replacement for the retired write/approval Step 8; see Section
3.0 for why that retirement is real and permanent, not a pause). This
module makes zero mutating AWS calls and never will -- the IAM policy this
app ships stays Describe*/List*/Get*/pricing:* forever (docs/iam-policy.json).

check_deletion_impact(resource_type, resource_id) answers "what actually
happens if I delete this" for ec2 (terminate), rds (delete instance), and
ebs (delete a standalone volume) -- a structured report, not prose:
will_be_removed / will_persist_and_keep_costing / behavioral_warnings /
never_affected (see app/models/deletion_impact.py's docstring for why this
shape, distinct from both IdleCheckResult/CostEstimate and the
findings-list tools).

Same anti-hallucination discipline the eval harness (roadmap Section 2)
exists to enforce, and the exact distinction roadmap 3.1 draws:
- Queryable, instance-specific facts (per-volume DeleteOnTermination, EIP
  association, ASG membership, load balancer target registration, RDS
  read replicas/manual snapshots) are ALWAYS queried live for the specific
  resource_id given -- never assumed from a type-level default. When one
  of these live checks itself fails (e.g. a not-yet-granted IAM action),
  that gap is reported in the response's `check_errors`, never silently
  treated as "no"/"false".
- Static, resource-type-level behavioral facts (EIPs are never
  auto-released and are billed whether associated or not; an ASG replaces
  a terminated member to maintain desired capacity; RDS read replicas and
  manual snapshots outlive their source; snapshots outlive their source
  volume/instance) were hand-verified once against current AWS behavior
  (roadmap 3.1) and are encoded directly below as fixed strings/logic --
  never re-derived or guessed at call time.

Concurrency (roadmap 3.2): fans out to the fixed, known set of directly-
connected resources per type using the exact same ThreadPoolExecutor +
as_completed + per-key-exception-caught pattern
scan_service._run_collectors_concurrently already established for
region-scan parallelization -- see _run_fanout() below, deliberately the
same shape, not a new pattern. This is v1, fixed-depth on purpose: no
"go one hop deeper if X" conditional branching here at all -- that's a
separate v2 scoped to LangGraph (roadmap 3.2/3.3, a different agent),
built only after this v1 has shipped and seen real usage.

Synthesizer, not a new AWS-calling layer: every fact below is fetched by
calling into the existing per-resource-type service that already owns
that AWS call (ec2_service, ebs_service, eip_service, rds_service,
snapshot_service, cost_service). Two genuinely new AWS calls were needed
that don't fit any existing service's scope -- Auto Scaling Group
membership and load balancer target registration, neither of which any
other feature in this app currently queries -- so they live here, as this
module's own small private helpers, going through app.aws.client like
every other AWS call in this app.
"""
from __future__ import annotations

import logging
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

from botocore.exceptions import BotoCoreError, ClientError

from app.aws.client import get_autoscaling_client, get_elbv2_client
from app.models.dashboard import RdsInstanceSummary
from app.models.deletion_impact import (
    BehavioralWarning,
    DeletionImpactReport,
    NeverAffectedEntry,
    WillBeRemovedEntry,
    WillPersistEntry,
)
from app.models.ec2 import EC2Instance
from app.services import (
    cost_service,
    ebs_service,
    ec2_service,
    eip_service,
    rds_service,
    snapshot_service,
)

logger = logging.getLogger("app.services.deletion_impact")

# Documented judgment call (see module docstring's "static facts" note):
# an Elastic IP still associated with the instance reports $0/mo via
# cost_service.estimate_cost right now (an associated EIP is free) -- but
# that is this instant's cost, not what matters here. What this tool needs
# is the cost the EIP will start accruing the moment the instance
# disappears and it becomes unassociated. Reusing cost_service's own
# public EIP_IDLE_HOURLY_RATE_USD/HOURS_PER_MONTH constants directly (the
# exact figure estimate_cost itself computes for an unassociated EIP)
# gives that forward-looking number without duplicating any pricing logic
# -- calling estimate_cost() here would instead report today's $0 and
# mislead the caller into thinking this EIP is free to leave behind.
EIP_POST_DELETION_MONTHLY_COST_USD = round(
    cost_service.EIP_IDLE_HOURLY_RATE_USD * cost_service.HOURS_PER_MONTH, 2
)

_SNAPSHOT_COST_NOTE = (
    "Dollar figure not computed here -- estimate_cost has no snapshot pricing path, and "
    "duplicating one here would just re-implement it. Use "
    "check_snapshot_sprawl(resource_type={resource_type!r}, ...) to track this "
    "snapshot's retention/orphan status and size."
)


class UnsupportedDeletionImpactResourceTypeError(ValueError):
    """Raised when check_deletion_impact is asked about a type other than
    'ec2', 'rds', or 'ebs' -- v1's fixed, known scope (roadmap 3.1)."""


def _run_fanout(checks: dict[str, Callable[[], Any]]) -> dict[str, Any]:
    """Runs each zero-arg callable in `checks` concurrently on real OS
    threads -- the exact same ThreadPoolExecutor/as_completed shape as
    scan_service._run_collectors_concurrently (every check below makes
    synchronous boto3 calls, same reason that one runs on threads rather
    than asyncio). One check's exception is caught, logged, and reported
    as None for that key rather than failing the whole report -- same
    graceful-degradation principle scan_service already established for
    one type's collector failing; the caller decides whether a None
    becomes a `check_errors` entry.
    """
    results: dict[str, Any] = {}
    with ThreadPoolExecutor(max_workers=max(1, len(checks))) as executor:
        future_to_key = {executor.submit(fn): key for key, fn in checks.items()}
        for future in as_completed(future_to_key):
            key = future_to_key[future]
            try:
                results[key] = future.result()
            except Exception:  # noqa: BLE001 - one check's failure must not blank the report
                logger.warning("deletion_impact: check '%s' failed", key, exc_info=True)
                results[key] = None
    return results


def check_deletion_impact(
    resource_type: str, resource_id: str, region: str | None = None
) -> DeletionImpactReport:
    """Entry point. `region` overrides the configured default region, same
    convention as check_idle/estimate_cost -- the agent-facing tool
    wrappers (dashboard/MCP/chat) omit it, matching this tool's documented
    two-argument signature.
    """
    if resource_type == "ec2":
        return _check_ec2_termination_impact(resource_id, region)
    if resource_type == "rds":
        return _check_rds_deletion_impact(resource_id, region)
    if resource_type == "ebs":
        return _check_ebs_deletion_impact(resource_id, region)
    raise UnsupportedDeletionImpactResourceTypeError(
        f"check_deletion_impact for resource_type={resource_type!r} is not supported -- "
        "only 'ec2', 'rds', and 'ebs' are in scope for v1 (roadmap phase 2 Section 3.1)."
    )


# =====================================================================
# EC2 termination
# =====================================================================


def _eip_check(instance_id: str, region: str | None) -> dict[str, Any]:
    """Queryable fact: is an Elastic IP currently associated with this
    instance (DescribeAddresses, via the existing eip_service)."""
    try:
        addresses = eip_service.list_addresses(region=region).addresses
    except (ClientError, BotoCoreError):
        logger.warning("deletion_impact: EIP listing failed for %s", instance_id, exc_info=True)
        return {"associated_eips": [], "error": "Could not check Elastic IP association."}
    return {
        "associated_eips": [a for a in addresses if a.instance_id == instance_id],
        "error": None,
    }


def _asg_membership_check(instance_id: str, region: str | None) -> dict[str, Any]:
    """Queryable fact: real Auto Scaling Group membership, via
    DescribeAutoScalingInstances -- not the aws:autoscaling:groupName tag,
    which a caller could have opted out of propagating; a live API call is
    authoritative either way (roadmap 3.1 offers both, this picks the one
    that can't silently miss membership). DescribeAutoScalingGroups is a
    second, best-effort call only to enrich the warning message with the
    group's desired capacity -- its failure doesn't blank membership
    itself, which is already known from the first call.

    Both actions are new to this app's IAM policy (empirically confirmed
    via a real AccessDenied naming each action exactly, against the real
    account in .env -- see docs/iam-policy.json and this build's own
    notes) -- a not-yet-granted policy surfaces here as 'error' set,
    never as a false "not a member".
    """
    client = get_autoscaling_client(region=region)
    try:
        resp = client.describe_auto_scaling_instances(InstanceIds=[instance_id])
    except (ClientError, BotoCoreError):
        logger.warning(
            "deletion_impact: ASG membership check failed for %s", instance_id, exc_info=True
        )
        return {
            "is_member": False,
            "group_name": None,
            "desired_capacity": None,
            "error": (
                "Could not verify Auto Scaling Group membership (AWS permission or "
                "connectivity issue)."
            ),
        }

    instances = resp.get("AutoScalingInstances", [])
    if not instances:
        return {"is_member": False, "group_name": None, "desired_capacity": None, "error": None}

    group_name = instances[0].get("AutoScalingGroupName")
    desired_capacity = None
    try:
        groups_resp = client.describe_auto_scaling_groups(AutoScalingGroupNames=[group_name])
        groups = groups_resp.get("AutoScalingGroups", [])
        if groups:
            desired_capacity = groups[0].get("DesiredCapacity")
    except (ClientError, BotoCoreError):
        # Best-effort enrichment only -- membership itself is already
        # confirmed above, so this failing doesn't change 'is_member'.
        logger.warning(
            "deletion_impact: ASG desired-capacity lookup failed for group %s",
            group_name,
            exc_info=True,
        )

    return {
        "is_member": True,
        "group_name": group_name,
        "desired_capacity": desired_capacity,
        "error": None,
    }


def _lb_target_check(instance_id: str, region: str | None) -> dict[str, Any]:
    """Queryable fact: is this instance a registered target in any elbv2
    (ALB/NLB) target group, via DescribeTargetGroups + DescribeTargetHealth
    -- both already covered by this app's existing
    elasticloadbalancing:Describe* wildcard, empirically confirmed
    (describe_target_groups succeeds against the real account in .env; no
    target groups exist there to also exercise describe_target_health
    live, but it shares the same action prefix already granted). Classic
    ELB's equivalent (DescribeInstanceHealth) is skipped -- same
    lower-priority precedent elb_service.py's own module docstring already
    sets for Classic ELB generally.
    """
    client = get_elbv2_client(region=region)
    try:
        target_group_arns = [
            tg["TargetGroupArn"]
            for page in client.get_paginator("describe_target_groups").paginate()
            for tg in page.get("TargetGroups", [])
        ]
    except (ClientError, BotoCoreError):
        logger.warning("deletion_impact: target group listing failed", exc_info=True)
        return {
            "registered_target_groups": [],
            "error": "Could not list load balancer target groups.",
        }

    registered_in: list[str] = []
    for arn in target_group_arns:
        try:
            health = client.describe_target_health(TargetGroupArn=arn)
        except (ClientError, BotoCoreError):
            logger.warning("deletion_impact: target health check failed for %s", arn, exc_info=True)
            continue
        for desc in health.get("TargetHealthDescriptions", []):
            if desc.get("Target", {}).get("Id") == instance_id:
                registered_in.append(arn)
                break

    return {"registered_target_groups": registered_in, "error": None}


def _ebs_volume_persist_check(instance: EC2Instance, region: str | None) -> list[tuple]:
    """Queryable fact: each attached volume's real DeleteOnTermination flag
    (already carried on `instance.block_device_mappings` -- no extra AWS
    call for the flag itself). For every volume NOT confirmed
    DeleteOnTermination=true (False, or None/unreported -- roadmap 3.1:
    "always query the actual per-volume flag, never assume from the
    default"), computes its real ongoing cost via the existing
    cost_service.estimate_cost -- an EBS volume's price doesn't change
    when its attached instance terminates, so its current estimate_cost
    figure IS the correct post-termination figure (unlike the EIP case
    above).
    """
    entries = []
    for mapping in instance.block_device_mappings:
        if mapping.volume_id is None or mapping.delete_on_termination is True:
            continue
        try:
            monthly_cost = cost_service.estimate_cost(
                "ebs", mapping.volume_id, region=region
            ).projected_monthly
        except Exception:  # noqa: BLE001 - one volume's cost lookup must not blank the rest
            logger.warning(
                "deletion_impact: EBS cost estimate failed for %s", mapping.volume_id,
                exc_info=True,
            )
            monthly_cost = None
        entries.append((mapping, monthly_cost))
    return entries


def _check_ec2_termination_impact(instance_id: str, region: str | None) -> DeletionImpactReport:
    instance = ec2_service.get_instance(instance_id, region=region)
    if instance is None:
        raise ValueError(f"EC2 instance {instance_id!r} not found")

    check_results = _run_fanout(
        {
            "eip": lambda: _eip_check(instance_id, region),
            "asg": lambda: _asg_membership_check(instance_id, region),
            "lb_targets": lambda: _lb_target_check(instance_id, region),
            "ebs_volumes": lambda: _ebs_volume_persist_check(instance, region),
        }
    )

    will_be_removed = [
        WillBeRemovedEntry(
            resource_type="ec2",
            resource_id=instance_id,
            reason="The instance itself is terminated.",
        )
    ]
    will_persist: list[WillPersistEntry] = []
    warnings: list[BehavioralWarning] = []
    never_affected: list[NeverAffectedEntry] = []
    check_errors: list[str] = []

    # EBS volumes -- both the persisting ones (with cost) and the
    # removed ones (DeleteOnTermination=true), from the same
    # block_device_mappings data queried once above.
    ebs_entries = check_results.get("ebs_volumes")
    if ebs_entries is None:
        check_errors.append("Could not determine per-volume DeleteOnTermination status.")
    else:
        for mapping, monthly_cost in ebs_entries:
            will_persist.append(
                WillPersistEntry(
                    resource_type="ebs",
                    resource_id=mapping.volume_id,
                    reason=(
                        "DeleteOnTermination is not confirmed true for this volume "
                        f"({mapping.device_name or 'unknown device'}) -- it will remain "
                        "after the instance is terminated."
                    ),
                    estimated_monthly_cost_usd=monthly_cost,
                    cost_note=None if monthly_cost is not None else "Cost estimate unavailable.",
                )
            )
    for mapping in instance.block_device_mappings:
        if mapping.volume_id and mapping.delete_on_termination is True:
            will_be_removed.append(
                WillBeRemovedEntry(
                    resource_type="ebs",
                    resource_id=mapping.volume_id,
                    reason=(
                        "DeleteOnTermination is true for this volume -- it is deleted "
                        "with the instance."
                    ),
                )
            )

    # Elastic IP(s)
    eip_result = check_results.get("eip")
    if eip_result is None or eip_result.get("error"):
        check_errors.append(
            (eip_result or {}).get("error") or "Could not check Elastic IP association."
        )
    else:
        for address in eip_result["associated_eips"]:
            will_persist.append(
                WillPersistEntry(
                    resource_type="eip",
                    resource_id=address.resource_id,
                    reason=(
                        "Elastic IPs are never released automatically on termination -- this "
                        "EIP will remain allocated (and unassociated) after the instance is "
                        "gone, and AWS charges for every EIP whether associated or not."
                    ),
                    estimated_monthly_cost_usd=EIP_POST_DELETION_MONTHLY_COST_USD,
                    cost_note=(
                        "Projected using cost_service's documented unassociated-EIP rate -- "
                        "calling estimate_cost() directly would report this EIP's CURRENT "
                        "cost ($0, while still associated), not its post-termination cost."
                    ),
                )
            )

    # ASG membership -- the single most valuable warning this tool can
    # surface (roadmap 3.1).
    asg_result = check_results.get("asg")
    if asg_result is None or asg_result.get("error"):
        check_errors.append(
            (asg_result or {}).get("error") or "Could not verify Auto Scaling Group membership."
        )
    elif asg_result["is_member"]:
        capacity_note = (
            f" (desired capacity {asg_result['desired_capacity']})"
            if asg_result["desired_capacity"] is not None
            else ""
        )
        warnings.append(
            BehavioralWarning(
                code="asg_will_replace_instance",
                message=(
                    f"This instance is a member of Auto Scaling Group "
                    f"'{asg_result['group_name']}'{capacity_note}. Terminating it directly "
                    "will NOT reduce compute spend -- the ASG will automatically launch a "
                    "replacement instance to maintain its desired capacity. To actually "
                    "reduce capacity, lower the ASG's desired capacity (or detach this "
                    "instance from the ASG) first."
                ),
            )
        )

    # Load balancer target registration.
    lb_result = check_results.get("lb_targets")
    if lb_result is None or lb_result.get("error"):
        check_errors.append(
            (lb_result or {}).get("error") or "Could not check load balancer target registration."
        )
    elif lb_result["registered_target_groups"]:
        warnings.append(
            BehavioralWarning(
                code="lb_target_deregistered",
                message=(
                    "This instance is a registered target in "
                    f"{len(lb_result['registered_target_groups'])} load balancer target "
                    "group(s). Terminating it removes it from active rotation immediately "
                    "-- verify no in-flight traffic depends solely on this target first."
                ),
            )
        )

    # Never affected -- stated explicitly, not left implicit (roadmap 3.1).
    for sg_id in instance.security_group_ids:
        never_affected.append(
            NeverAffectedEntry(
                resource_type="security_group",
                resource_id=sg_id,
                message=(
                    "Security groups are independent VPC objects -- terminating this "
                    "instance never deletes them."
                ),
            )
        )
    if instance.iam_instance_profile_name:
        never_affected.append(
            NeverAffectedEntry(
                resource_type="iam_role",
                resource_id=instance.iam_instance_profile_name,
                message=(
                    "The IAM instance profile/role is an independent IAM object -- "
                    "terminating this instance never deletes it."
                ),
            )
        )

    return DeletionImpactReport(
        resource_type="ec2",
        resource_id=instance_id,
        will_be_removed=will_be_removed,
        will_persist_and_keep_costing=will_persist,
        behavioral_warnings=warnings,
        never_affected=never_affected,
        check_errors=check_errors,
    )


# =====================================================================
# RDS instance deletion
# =====================================================================


def _rds_read_replica_cost_check(
    instance: RdsInstanceSummary, region: str | None
) -> list[tuple[str, float | None]]:
    """Queryable fact: this instance's own ReadReplicaDBInstanceIdentifiers
    (already carried on the RdsInstanceSummary -- no extra AWS call for
    the list itself). Each replica's ongoing cost is a real, independent
    RDS instance, so the existing cost_service.estimate_cost("rds", ...)
    applies directly and accurately -- it keeps running exactly as before
    once the source is deleted."""
    results = []
    for replica_id in instance.read_replica_db_instance_identifiers:
        try:
            monthly_cost = cost_service.estimate_cost(
                "rds", replica_id, region=region
            ).projected_monthly
        except Exception:  # noqa: BLE001 - one replica's cost lookup must not blank the rest
            logger.warning(
                "deletion_impact: RDS read replica cost estimate failed for %s",
                replica_id,
                exc_info=True,
            )
            monthly_cost = None
        results.append((replica_id, monthly_cost))
    return results


def _check_rds_deletion_impact(identifier: str, region: str | None) -> DeletionImpactReport:
    instance = rds_service.get_instance(identifier, region=region)
    if instance is None:
        raise ValueError(f"RDS instance {identifier!r} not found")

    check_results = _run_fanout(
        {
            "snapshots": lambda: snapshot_service.list_snapshots_for_source(
                "rds", identifier, region=region
            ),
            "read_replicas": lambda: _rds_read_replica_cost_check(instance, region),
        }
    )

    will_be_removed = [
        WillBeRemovedEntry(
            resource_type="rds", resource_id=identifier, reason="The DB instance itself is deleted."
        )
    ]
    will_persist: list[WillPersistEntry] = []
    warnings: list[BehavioralWarning] = [
        BehavioralWarning(
            code="final_snapshot_is_a_deletion_time_choice",
            message=(
                "A final snapshot is optional and is chosen explicitly at deletion time -- "
                "it is not created automatically. Decide before deleting whether you want one."
            ),
        )
    ]
    never_affected: list[NeverAffectedEntry] = []
    check_errors: list[str] = []

    snapshots = check_results.get("snapshots")
    if snapshots is None:
        check_errors.append("Could not list snapshots for this DB instance.")
    else:
        automated = [s for s in snapshots if s.snapshot_type == "automated"]
        manual = [s for s in snapshots if s.snapshot_type == "manual"]
        if automated:
            will_be_removed.append(
                WillBeRemovedEntry(
                    resource_type="rds_automated_backup",
                    resource_id=identifier,
                    reason=(
                        f"{len(automated)} automated backup snapshot(s) are deleted along "
                        "with the instance UNLESS the deletion explicitly retains them."
                    ),
                )
            )
        for snap in manual:
            will_persist.append(
                WillPersistEntry(
                    resource_type="rds_snapshot",
                    resource_id=snap.snapshot_id,
                    reason=(
                        "Manual snapshots are never deleted by instance deletion -- they "
                        "persist independently."
                    ),
                    estimated_monthly_cost_usd=None,
                    cost_note=_SNAPSHOT_COST_NOTE.format(resource_type="rds"),
                )
            )

    replica_results = check_results.get("read_replicas")
    if replica_results is None:
        check_errors.append("Could not estimate cost for read replicas.")
    else:
        for replica_id, monthly_cost in replica_results:
            will_persist.append(
                WillPersistEntry(
                    resource_type="rds",
                    resource_id=replica_id,
                    reason=(
                        "Read replicas are NOT deleted when their source instance is "
                        "deleted -- they become independent, still-costing resources."
                    ),
                    estimated_monthly_cost_usd=monthly_cost,
                    cost_note=None if monthly_cost is not None else "Cost estimate unavailable.",
                )
            )

    for sg_id in instance.vpc_security_group_ids:
        never_affected.append(
            NeverAffectedEntry(
                resource_type="security_group",
                resource_id=sg_id,
                message=(
                    "Security groups are independent VPC objects -- deleting this instance "
                    "never deletes them."
                ),
            )
        )

    return DeletionImpactReport(
        resource_type="rds",
        resource_id=identifier,
        will_be_removed=will_be_removed,
        will_persist_and_keep_costing=will_persist,
        behavioral_warnings=warnings,
        never_affected=never_affected,
        check_errors=check_errors,
    )


# =====================================================================
# EBS standalone volume deletion
# =====================================================================


def _check_ebs_deletion_impact(volume_id: str, region: str | None) -> DeletionImpactReport:
    volume = ebs_service.get_volume(volume_id, region=region)
    if volume is None:
        raise ValueError(f"EBS volume {volume_id!r} not found")

    check_results = _run_fanout(
        {
            "snapshots": lambda: snapshot_service.list_snapshots_for_source(
                "ebs", volume_id, region=region
            ),
        }
    )

    will_be_removed = [
        WillBeRemovedEntry(
            resource_type="ebs", resource_id=volume_id, reason="The volume itself is deleted."
        )
    ]
    will_persist: list[WillPersistEntry] = []
    warnings: list[BehavioralWarning] = []
    check_errors: list[str] = []

    snapshots = check_results.get("snapshots")
    if snapshots is None:
        check_errors.append("Could not list snapshots taken from this volume.")
    else:
        for snap in snapshots:
            will_persist.append(
                WillPersistEntry(
                    resource_type="ebs_snapshot",
                    resource_id=snap.snapshot_id,
                    reason=(
                        "Snapshots taken from this volume are unaffected by deleting the "
                        "volume -- they persist as independent objects."
                    ),
                    estimated_monthly_cost_usd=None,
                    cost_note=_SNAPSHOT_COST_NOTE.format(resource_type="ebs"),
                )
            )

    # Real, queryable, easy-to-miss fact: AWS refuses DeleteVolume on an
    # attached volume until it's detached -- worth surfacing explicitly
    # rather than letting the caller find out only by trying it.
    if volume.is_attached:
        warnings.append(
            BehavioralWarning(
                code="volume_currently_attached",
                message=(
                    "This volume is currently attached to instance(s): "
                    f"{', '.join(volume.attached_instance_ids)}. AWS will refuse to delete "
                    "an attached volume until it is detached first."
                ),
            )
        )

    return DeletionImpactReport(
        resource_type="ebs",
        resource_id=volume_id,
        will_be_removed=will_be_removed,
        will_persist_and_keep_costing=will_persist,
        behavioral_warnings=warnings,
        never_affected=[],
        check_errors=check_errors,
    )
