"""EBS models. Mirrors app/models/ec2.py's shape/style.

`create_time` plays the same role EC2Instance.launch_time plays for the
younger-than-window idle edge case (roadmap 3.1) and for cost_service's
elapsed-hours calc (roadmap 3.2).
"""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class EbsVolume(BaseModel):
    volume_id: str
    size_gb: int
    volume_type: str = Field(description="e.g. gp3, gp2, io1, io2, st1, sc1")
    state: str = Field(description="e.g. available, in-use, creating, deleting")
    availability_zone: str
    create_time: datetime | None = None
    attached_instance_ids: list[str] = Field(
        default_factory=list,
        description="EC2 instance IDs this volume is currently attached to (usually 0 or 1).",
    )
    tags: dict[str, str] = Field(default_factory=dict)

    @property
    def is_attached(self) -> bool:
        return len(self.attached_instance_ids) > 0


class EbsVolumeList(BaseModel):
    volumes: list[EbsVolume]
    count: int
