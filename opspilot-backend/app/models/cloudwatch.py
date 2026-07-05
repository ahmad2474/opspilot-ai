from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class MetricDatapoint(BaseModel):
    timestamp: datetime
    average: float | None = None
    maximum: float | None = None
    unit: str


class CpuUtilizationSummary(BaseModel):
    instance_id: str
    lookback_hours: int
    datapoints: list[MetricDatapoint]
    average_cpu_percent: float | None = None
    max_cpu_percent: float | None = None
    breached_80_percent: bool = False
