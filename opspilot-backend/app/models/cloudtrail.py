from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class CloudTrailEvent(BaseModel):
    event_name: str
    event_time: datetime
    username: str | None = None


class CloudTrailEventList(BaseModel):
    resource_id: str
    lookback_hours: int
    events: list[CloudTrailEvent]
