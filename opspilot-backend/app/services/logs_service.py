"""CloudWatch Logs retention-waste check (roadmap phase 2 Section 1.3).

check_log_retention() is a findings-list tool, not a check_idle/
estimate_cost resource-type addition -- a log group's retention setting is
a one-time configuration fact ("was retentionInDays ever set?"), not a
CloudWatch time series, so there's no is_idle/idle_since/idle_days shape
that fits it. See the data-schema skill's "Findings-list tools" section for
how this differs from IdleCheckResult.

DescribeLogGroups is a free/cheap call and already returns storedBytes per
group in the same response (roadmap: "storage bytes are already reported
per group") -- no second CloudWatch call needed to size a finding, unlike
every check_idle branch that needs a separate GetMetricStatistics call.
"""
from __future__ import annotations

from datetime import datetime, timezone

from app.aws.client import get_logs_client
from app.models.logs import LogGroupRetentionFinding, LogRetentionReport


def check_log_retention(region: str | None = None) -> LogRetentionReport:
    client = get_logs_client(region=region)
    paginator = client.get_paginator("describe_log_groups")

    findings: list[LogGroupRetentionFinding] = []
    total_checked = 0
    for page in paginator.paginate():
        for raw in page.get("logGroups", []):
            total_checked += 1
            if raw.get("retentionInDays") is not None:
                continue  # has an explicit retention policy -- not a finding

            creation_time_ms = raw.get("creationTime")
            findings.append(
                LogGroupRetentionFinding(
                    log_group_name=raw["logGroupName"],
                    stored_bytes=raw.get("storedBytes", 0),
                    created_at=(
                        datetime.fromtimestamp(creation_time_ms / 1000, tz=timezone.utc)
                        if creation_time_ms
                        else None
                    ),
                )
            )

    return LogRetentionReport(
        findings=findings,
        flagged_count=len(findings),
        total_log_groups_checked=total_checked,
        total_stored_bytes_at_risk=sum(f.stored_bytes for f in findings),
    )
