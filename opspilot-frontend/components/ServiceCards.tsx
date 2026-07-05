"use client";

import { useCallback, useEffect, useState } from "react";
import { getDashboardOverview, type DashboardOverview } from "@/lib/api";

export default function ServiceCards() {
  const [data, setData] = useState<DashboardOverview | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setData(await getDashboardOverview());
    } catch (err) {
      setError(
        err instanceof Error ? err.message : "Couldn't load service status."
      );
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  if (loading && !data) {
    return (
      <div className="rounded-lg border border-border bg-surface p-6 text-sm text-muted">
        Loading account overview…
      </div>
    );
  }

  if (error) {
    return (
      <div className="rounded-lg border border-status-bad/40 bg-status-bad/10 p-4 text-sm text-status-bad">
        {error}
      </div>
    );
  }

  if (!data) return null;

  const cards = [
    { label: "Lambda", value: `${data.lambda_functions.count} function${data.lambda_functions.count === 1 ? "" : "s"}` },
    { label: "S3", value: `${data.s3.count} bucket${data.s3.count === 1 ? "" : "s"}` },
    { label: "DynamoDB", value: `${data.dynamodb.count} table${data.dynamodb.count === 1 ? "" : "s"}` },
    { label: "SNS", value: `${data.sns.count} topic${data.sns.count === 1 ? "" : "s"}` },
    { label: "RDS", value: `${data.rds.count} instance${data.rds.count === 1 ? "" : "s"}` },
  ];

  return (
    <div>
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 md:grid-cols-5">
        {cards.map((c) => (
          <div key={c.label} className="rounded-lg border border-border bg-surface p-3">
            <div className="font-mono text-[11px] uppercase tracking-wide text-muted">
              {c.label}
            </div>
            <div className="mt-1 text-sm text-text">{c.value}</div>
          </div>
        ))}
      </div>

      <div className="mt-4 rounded-lg border border-border bg-surface p-4">
        <div className="mb-2 font-mono text-[11px] uppercase tracking-wide text-muted">
          CloudTrail — recent activity
        </div>
        {data.cloudtrail.events.length === 0 ? (
          <div className="text-xs text-muted">No recent management events.</div>
        ) : (
          <ul className="space-y-1">
            {data.cloudtrail.events.map((e, i) => (
              <li key={i} className="font-mono text-xs text-text">
                {e.event_name}{" "}
                <span className="text-muted">
                  — {e.username ?? "unknown"} — {new Date(e.event_time).toLocaleString()}
                </span>
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}
