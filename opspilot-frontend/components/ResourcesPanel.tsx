"use client";

import { useCallback, useEffect, useState } from "react";
import { getEc2Resources, type Ec2ResourceCard } from "@/lib/api";
import Sparkline from "@/components/Sparkline";
import StatusBadge from "@/components/StatusBadge";
import ServiceCards from "@/components/ServiceCards";

export default function ResourcesPanel() {
  const [cards, setCards] = useState<Ec2ResourceCard[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await getEc2Resources();
      setCards(res.ec2);
    } catch (err) {
      setError(
        err instanceof Error
          ? err.message
          : "Couldn't load resources — confirm the backend is running on port 8000."
      );
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  return (
    <div>
      <div className="mb-4 flex items-center justify-between">
        <h1 className="text-lg font-medium">Resources</h1>
        <button
          onClick={load}
          disabled={loading}
          className="rounded-md border border-border px-3 py-1.5 text-xs text-muted transition-colors hover:border-accent hover:text-text disabled:opacity-40"
        >
          {loading ? "Refreshing…" : "Refresh"}
        </button>
      </div>

      {error && (
        <div className="rounded-lg border border-status-bad/40 bg-status-bad/10 p-4 text-sm text-status-bad">
          {error}
        </div>
      )}

      {!error && loading && !cards && (
        <div className="rounded-lg border border-border bg-surface p-6 text-sm text-muted">
          Loading EC2 state…
        </div>
      )}

      {!error && cards && cards.length === 0 && (
        <div className="rounded-lg border border-border bg-surface p-6 text-sm text-muted">
          No EC2 instances found in this account/region.
        </div>
      )}

      {!error && cards && cards.length > 0 && (
        <div className="mb-3 font-mono text-[11px] uppercase tracking-wide text-muted">
          EC2 — deep investigation
        </div>
      )}

      {!error && cards && cards.length > 0 && (
        <div className="grid gap-4 sm:grid-cols-2">
          {cards.map((card) => (
            <div
              key={card.instance.instance_id}
              className="rounded-lg border border-border bg-surface p-4"
            >
              <div className="mb-3 flex items-start justify-between">
                <div>
                  <div className="font-mono text-sm text-text">{card.instance.instance_id}</div>
                  <div className="text-xs text-muted">
                    {card.instance.instance_type} · {card.instance.availability_zone}
                  </div>
                </div>
                <StatusBadge state={card.instance.state} />
              </div>

              {card.cpu ? (
                <div className="flex items-end justify-between">
                  <div className="text-xs text-muted">
                    <div>
                      avg{" "}
                      <span className="font-mono text-text">
                        {card.cpu.average_cpu_percent?.toFixed(2) ?? "—"}%
                      </span>
                    </div>
                    <div>
                      max{" "}
                      <span
                        className={`font-mono ${
                          card.cpu.breached_80_percent ? "text-status-bad" : "text-text"
                        }`}
                      >
                        {card.cpu.max_cpu_percent?.toFixed(2) ?? "—"}%
                      </span>
                    </div>
                  </div>
                  <Sparkline
                    values={card.cpu.datapoints.map((d) => d.average ?? 0)}
                  />
                </div>
              ) : (
                <div className="text-xs text-muted">
                  CPU data unavailable — instance isn&apos;t running.
                </div>
              )}
            </div>
          ))}
        </div>
      )}

      <div className="mb-3 mt-8 font-mono text-[11px] uppercase tracking-wide text-muted">
        Account overview
      </div>
      <ServiceCards />
    </div>
  );
}
