"""Response models for roadmap Section 3.8's chat capability tools:
list_resources, get_resource_health, get_resource_age.

Field shape deliberately reuses `GalaxyResource` (app/models/scan.py)
rather than inventing a parallel resource shape -- per the data-schema
skill, every new tool's response conforms to that one contract. This file
only adds the wrapper/aggregate shapes specific to these three tools.
"""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from app.models.ec2 import EC2StatusCheck
from app.models.scan import GalaxyResource


class ResourceListResponse(BaseModel):
    """list_resources' response. `resources` is always sorted by (type,
    name) -- grouped by type, alphabetical by name within each group --
    matching roadmap 3.8's "list queries" formatting rule up front, so the
    caller (dashboard, MCP client, or the chat agent) doesn't have to
    re-sort. Each entry's `name` already falls back to the resource's own
    AWS id when untagged (GalaxyResource's own rule) -- never silently
    omitted.
    """

    region: str
    resources: list[GalaxyResource]
    count: int
    by_type: dict[str, int] = Field(
        description="Resource count per type code present in `resources`."
    )
    by_status: dict[str, int] = Field(
        description="Resource count per lifecycle status string present in `resources` "
        "(e.g. running/stopped/available/associated) -- always populated, cheap to "
        "compute from identity/status data alone, independent of idle_data_source."
    )
    idle_count: int | None = Field(
        default=None,
        description="Count of resources where idle.is_idle is True, from a CloudWatch- "
        "verified check_idle result -- populated ONLY when idle_data_source is "
        "'cached_scan' AND every returned resource's idle lookup succeeded (a partial "
        "mix of known/unknown idle status is reported as null rather than silently "
        "undercounting). Null when idle_data_source is 'unavailable' -- call scan_region "
        "first (or check_idle per resource) for a verified idle/active split.",
    )
    not_idle_count: int | None = Field(
        default=None,
        description="count - idle_count, populated under the exact same condition as "
        "idle_count (both null or both populated together).",
    )
    idle_data_source: Literal["cached_scan", "unavailable"] = Field(
        description="'cached_scan' = idle_count/not_idle_count come from a scan_region() "
        "result already cached for this region (free to reuse, no new AWS calls). "
        "'unavailable' = no cached scan exists for this region yet, so list_resources "
        "only did the cheap identity/status listing (roadmap 3.8: counting/listing must "
        "not pay for a CloudWatch/Pricing lookup per resource) -- idle_count/"
        "not_idle_count are null in that case, by_status is still populated either way."
    )
    cache_last_updated: datetime | None = Field(
        default=None,
        description="The reused cached scan's last_updated timestamp, when "
        "idle_data_source is 'cached_scan' -- null otherwise.",
    )


class ResourceHealthResult(BaseModel):
    """get_resource_health's response for a single resource."""

    resource_id: str
    resource_type: str
    region: str
    found: bool = Field(description="False if the resource id could not be located at all.")
    name: str | None = Field(default=None, description="Name tag, falling back to the id.")
    status: str | None = Field(
        default=None, description="Lifecycle status/state, type-appropriate (e.g. running, "
        "stopped, available, associated/unassociated, active)."
    )
    primary_metric: str | None = Field(
        default=None, description="This type's idle-signal metric name (roadmap 2a), for context."
    )
    recent_activity_idle: bool | None = Field(
        default=None,
        description="Whether this resource looked idle over a short window right now "
        "(see recent_activity_window_days; reuses check_idle's CloudWatch signal) -- "
        "null if that check itself failed (e.g. CloudWatch throttled), not fabricated "
        "as True/False.",
    )
    recent_activity_window_days: int = 1
    ec2_status_check: EC2StatusCheck | None = Field(
        default=None,
        description="Instance/system status checks + scheduled events -- populated only "
        "for resource_type='ec2' (AWS exposes no equivalent status-check API for the "
        "other 14 types).",
    )


class ResourceAgeResult(BaseModel):
    """get_resource_age's response for a single resource."""

    resource_id: str
    resource_type: str
    region: str
    found: bool = Field(description="False if the resource id could not be located at all.")
    name: str | None = None
    created_at: datetime | None = None
    age_days: int | None = Field(
        default=None,
        description="Whole days since created_at. Null whenever age_is_known is False -- "
        "never a fabricated/guessed age.",
    )
    age_is_known: bool = Field(
        description="False when this resource type exposes no creation timestamp at all "
        "(EIP, Lambda, CloudFront -- and OpenSearch while a domain is still being "
        "created) or the resource could not be found -- see `reason`."
    )
    reason: str | None = Field(
        default=None, description="Populated only when age_is_known is False, explaining why."
    )
