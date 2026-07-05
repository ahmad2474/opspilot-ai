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
