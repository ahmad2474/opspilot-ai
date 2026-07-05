"""EC2 business logic. No boto3 calls anywhere else in the app.

This module is what Phase 2's tests will exercise directly (mocking the
boto3 client), without needing to touch the agent or the LLM at all.
"""
from __future__ import annotations

from app.aws.client import get_ec2_client
from app.models.ec2 import EC2Instance, EC2InstanceList


def _flatten_tags(raw_tags: list[dict[str, str]] | None) -> dict[str, str]:
    if not raw_tags:
        return {}
    return {tag["Key"]: tag["Value"] for tag in raw_tags}


def list_instances(state_filter: str | None = None) -> EC2InstanceList:
    """List EC2 instances, optionally filtered by lifecycle state.

    state_filter: one of pending|running|shutting-down|terminated|stopping|stopped
    """
    client = get_ec2_client()
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
                    )
                )

    return EC2InstanceList(instances=instances, count=len(instances))


def get_instance(instance_id: str) -> EC2Instance | None:
    result = list_instances()
    for instance in result.instances:
        if instance.instance_id == instance_id:
            return instance
    return None
