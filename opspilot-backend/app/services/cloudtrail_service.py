"""CloudTrail business logic.

Used two ways: as an investigation tool here in Phase 3 (correlate a
perceived issue with something someone actually did — stop/start/reboot/
modify — rather than a real fault), and later as the Phase 4 dashboard
card's data source. Same function, two consumers — no duplicated logic.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.aws.client import get_cloudtrail_client
from app.models.cloudtrail import CloudTrailEvent, CloudTrailEventList


def list_events_for_resource(resource_id: str, lookback_hours: int = 24) -> CloudTrailEventList:
    client = get_cloudtrail_client()
    end_time = datetime.now(timezone.utc)
    start_time = end_time - timedelta(hours=lookback_hours)

    response = client.lookup_events(
        LookupAttributes=[{"AttributeKey": "ResourceName", "AttributeValue": resource_id}],
        StartTime=start_time,
        EndTime=end_time,
        MaxResults=20,
    )

    events = [
        CloudTrailEvent(
            event_name=raw.get("EventName", "Unknown"),
            event_time=raw["EventTime"],
            username=raw.get("Username"),
        )
        for raw in response.get("Events", [])
    ]

    return CloudTrailEventList(resource_id=resource_id, lookback_hours=lookback_hours, events=events)
