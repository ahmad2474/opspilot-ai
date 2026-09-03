from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class BlockDeviceMapping(BaseModel):
    """One entry from DescribeInstances' BlockDeviceMappings -- carries the
    real, per-volume `DeleteOnTermination` flag deletion_impact_service
    needs (roadmap phase 2 Section 3.1: "each attached EBS volume's actual
    DeleteOnTermination flag ... this is a real, per-volume, queryable
    field, not something to assume"). EC2Instance.attached_volume_ids
    above only carries the bare volume IDs; this carries the full mapping.
    No new AWS call -- parsed from the same BlockDeviceMappings
    list_instances() already receives.
    """

    device_name: str | None = None
    volume_id: str | None = None
    delete_on_termination: bool | None = Field(
        default=None,
        description=(
            "The actual DeleteOnTermination flag AWS reports for this "
            "specific attached volume -- never assumed from the 'root "
            "usually true, data usually false' default. None only when "
            "AWS omitted the Ebs block entirely (a non-EBS device)."
        ),
    )


class EC2Instance(BaseModel):
    instance_id: str
    instance_type: str
    state: str = Field(description="e.g. running, stopped, pending")
    availability_zone: str
    public_ip: str | None = None
    private_ip: str | None = None
    launch_time: datetime | None = None
    tags: dict[str, str] = Field(default_factory=dict)

    # Roadmap 3.7 relation-shaping fields -- every one of these is already
    # present in DescribeInstances' response, just not previously mapped
    # into the model. No new AWS call; see scan_service.py's
    # _relations_for() for how these turn into GalaxyResource.relations.
    security_group_ids: list[str] = Field(default_factory=list)
    subnet_id: str | None = None
    vpc_id: str | None = None
    iam_instance_profile_name: str | None = Field(
        default=None,
        description=(
            "Just the trailing path segment of IamInstanceProfile.Arn (e.g. "
            "'my-ec2-profile', not 'arn:aws:iam::123456789012:instance-profile/"
            "my-ec2-profile') -- an instance *profile* name, not the underlying "
            "role's name (resolving profile -> role would need a separate "
            "iam:GetInstanceProfile call). Deliberately not the full ARN: "
            "this app otherwise keeps the AWS account ID out of every "
            "caller-facing field (it's scrubbed from error messages for the "
            "same reason), and the roadmap 3.7 'assumes' relation only ever "
            "needs an identifier to display, never the full ARN."
        ),
    )
    attached_volume_ids: list[str] = Field(
        default_factory=list,
        description="EBS volume IDs from BlockDeviceMappings -- the EC2 side of the "
        "EC2<->EBS 'attached' relation (roadmap 3.7).",
    )
    block_device_mappings: list[BlockDeviceMapping] = Field(
        default_factory=list,
        description=(
            "Full per-volume block device mapping detail, including the real "
            "per-volume delete_on_termination flag -- roadmap phase 2 Section "
            "3.1's check_deletion_impact needs this (attached_volume_ids above "
            "only carries bare volume IDs). Parsed from the same "
            "BlockDeviceMappings field, no new AWS call."
        ),
    )


class EC2InstanceList(BaseModel):
    instances: list[EC2Instance]
    count: int


class EC2StatusCheck(BaseModel):
    instance_id: str
    instance_state: str
    system_status: str = Field(description="e.g. ok, impaired, insufficient-data")
    instance_status: str = Field(description="e.g. ok, impaired, insufficient-data")
    scheduled_events: list[str] = Field(
        default_factory=list, description="Any AWS-scheduled maintenance/events on this instance"
    )


class InstanceStatusSummary(BaseModel):
    instance_id: str
    instance_status: str = Field(description="ok | impaired | insufficient-data | not-applicable")
    system_status: str = Field(description="ok | impaired | insufficient-data | not-applicable")
    scheduled_events: list[str] = Field(
        default_factory=list, description="Any scheduled maintenance/retirement events, if present"
    )

    @property
    def all_checks_passed(self) -> bool:
        return self.instance_status == "ok" and self.system_status == "ok"
