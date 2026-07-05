from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class EC2Instance(BaseModel):
    instance_id: str
    instance_type: str
    state: str = Field(description="e.g. running, stopped, pending")
    availability_zone: str
    public_ip: str | None = None
    private_ip: str | None = None
    launch_time: datetime | None = None
    tags: dict[str, str] = Field(default_factory=dict)


class EC2InstanceList(BaseModel):
    instances: list[EC2Instance]
    count: int
