"""EC2 business logic. No boto3 calls anywhere else in the app.

This module is what Phase 2's tests will exercise directly (mocking the
boto3 client), without needing to touch the agent or the LLM at all.
"""
from __future__ import annotations

from app.aws.client import get_ec2_client
from app.models.ec2 import EC2Instance, EC2InstanceList, EC2StatusCheck


def _flatten_tags(raw_tags: list[dict[str, str]] | None) -> dict[str, str]:
    if not raw_tags:
        return {}
    return {tag["Key"]: tag["Value"] for tag in raw_tags}


def _profile_name_from_arn(arn: str | None) -> str | None:
    """Strips an instance profile ARN down to just its trailing name
    segment (e.g. "arn:aws:iam::123456789012:instance-profile/my-profile"
    -> "my-profile") -- security: keeps the AWS account ID embedded in the
    ARN out of EC2Instance.iam_instance_profile_name, matching this app's
    existing precedent of scrubbing the account ID from every other
    caller-facing field. None in, None out.
    """
    if not arn:
        return None
    return arn.rsplit("/", 1)[-1]


def list_instances(
    state_filter: str | None = None, region: str | None = None
) -> EC2InstanceList:
    """List EC2 instances, optionally filtered by lifecycle state.

    state_filter: one of pending|running|shutting-down|terminated|stopping|stopped
    region: overrides the configured default region -- needed for
    region-wide scanning (roadmap 3.3). None/omitted uses the normal
    configured region, unchanged from before.
    """
    client = get_ec2_client(region=region)
    kwargs: dict[str, object] = {}
    if state_filter:
        kwargs["Filters"] = [{"Name": "instance-state-name", "Values": [state_filter]}]

    paginator = client.get_paginator("describe_instances")
    instances: list[EC2Instance] = []
    for page in paginator.paginate(**kwargs):
        for reservation in page.get("Reservations", []):
            for raw in reservation.get("Instances", []):
                instances.append(
                    EC2Instance(
                        instance_id=raw["InstanceId"],
                        instance_type=raw["InstanceType"],
                        state=raw["State"]["Name"],
                        availability_zone=raw["Placement"]["AvailabilityZone"],
                        public_ip=raw.get("PublicIpAddress"),
                        private_ip=raw.get("PrivateIpAddress"),
                        launch_time=raw.get("LaunchTime"),
                        tags=_flatten_tags(raw.get("Tags")),
                        security_group_ids=[
                            g["GroupId"] for g in raw.get("SecurityGroups", []) if g.get("GroupId")
                        ],
                        subnet_id=raw.get("SubnetId"),
                        vpc_id=raw.get("VpcId"),
                        iam_instance_profile_name=_profile_name_from_arn(
                            raw.get("IamInstanceProfile", {}).get("Arn")
                        ),
                        attached_volume_ids=[
                            bdm["Ebs"]["VolumeId"]
                            for bdm in raw.get("BlockDeviceMappings", [])
                            if bdm.get("Ebs", {}).get("VolumeId")
                        ],
                    )
                )

    return EC2InstanceList(instances=instances, count=len(instances))


def list_region_names() -> list[str]:
    """Enabled AWS region codes for this account, via `describe_regions`
    (roadmap 3.3 -- backs the region selector, and is the source of truth
    for which regions `scan_service` is allowed to scan). Rides on the
    same `ec2:DescribeRegions` grant already covered by the existing
    `ec2:Describe*` read-only IAM policy -- no new IAM action needed.
    """
    client = get_ec2_client()
    response = client.describe_regions(AllRegions=False)
    return sorted(r["RegionName"] for r in response.get("Regions", []))


def get_status_check(instance_id: str) -> EC2StatusCheck:
    """Instance/system status checks — rules out an infra-level fault as
    distinct from an application/load-level one (e.g. CPU pegged by a
    process vs. the underlying host having a problem).
    """
    client = get_ec2_client()
    # IncludeAllInstances so a stopped instance still returns a status
    # object instead of an empty list.
    response = client.describe_instance_status(
        InstanceIds=[instance_id], IncludeAllInstances=True
    )
    statuses = response.get("InstanceStatuses", [])
    if not statuses:
        return EC2StatusCheck(
            instance_id=instance_id,
            instance_state="unknown",
            system_status="insufficient-data",
            instance_status="insufficient-data",
        )

    raw = statuses[0]
    events = [
        f"{event.get('Code', 'event')}: {event.get('Description', '')}".strip(": ")
        for event in raw.get("Events", [])
    ]

    return EC2StatusCheck(
        instance_id=instance_id,
        instance_state=raw.get("InstanceState", {}).get("Name", "unknown"),
        system_status=raw.get("SystemStatus", {}).get("Status", "insufficient-data"),
        instance_status=raw.get("InstanceStatus", {}).get("Status", "insufficient-data"),
        scheduled_events=events,
    )


def get_instance(instance_id: str, region: str | None = None) -> EC2Instance | None:
    result = list_instances(region=region)
    for instance in result.instances:
        if instance.instance_id == instance_id:
            return instance
    return None
