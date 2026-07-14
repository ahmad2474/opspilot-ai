"""EBS business logic. No boto3 calls anywhere else in the app.

Mirrors ec2_service.py's shape/style exactly (list_* / get_* pair,
_flatten_tags helper) so idle_service/cost_service can treat every
resource type's service module the same way.
"""
from __future__ import annotations

from app.aws.client import get_ec2_client
from app.models.ebs import EbsVolume, EbsVolumeList


def _flatten_tags(raw_tags: list[dict[str, str]] | None) -> dict[str, str]:
    if not raw_tags:
        return {}
    return {tag["Key"]: tag["Value"] for tag in raw_tags}


def list_volumes(region: str | None = None) -> EbsVolumeList:
    client = get_ec2_client(region=region)
    paginator = client.get_paginator("describe_volumes")
    volumes: list[EbsVolume] = []
    for page in paginator.paginate():
        for raw in page.get("Volumes", []):
            attachments = raw.get("Attachments", []) or []
            volumes.append(
                EbsVolume(
                    volume_id=raw["VolumeId"],
                    size_gb=raw.get("Size", 0),
                    volume_type=raw.get("VolumeType", "unknown"),
                    state=raw.get("State", "unknown"),
                    availability_zone=raw.get("AvailabilityZone", "unknown"),
                    create_time=raw.get("CreateTime"),
                    attached_instance_ids=[
                        a["InstanceId"] for a in attachments if a.get("InstanceId")
                    ],
                    tags=_flatten_tags(raw.get("Tags")),
                )
            )
    return EbsVolumeList(volumes=volumes, count=len(volumes))


def get_volume(volume_id: str, region: str | None = None) -> EbsVolume | None:
    result = list_volumes(region=region)
    for volume in result.volumes:
        if volume.volume_id == volume_id:
            return volume
    return None
