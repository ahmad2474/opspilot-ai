"""Kinesis Data Stream models. Mirrors app/models/ebs.py's shape/style."""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class KinesisStream(BaseModel):
    stream_name: str
    stream_arn: str | None = None
    status: str = Field(description="e.g. ACTIVE, CREATING, DELETING, UPDATING")
    open_shard_count: int = 0
    retention_period_hours: int | None = None
    creation_timestamp: datetime | None = None


class KinesisStreamList(BaseModel):
    streams: list[KinesisStream]
    count: int
