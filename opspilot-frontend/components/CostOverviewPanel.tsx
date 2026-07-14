"use client";

import { useMemo } from "react";
import { TYPE_LABEL } from "@/components/GalaxyView";
import RegionScanToolbar from "@/components/RegionScanToolbar";
import { money } from "@/lib/format";
import { useRegionScan } from "@/lib/useRegionScan";
import type { GalaxyResource } from "@/lib/api";

// Roadmap Section 5's "Cost Overview" tab. Same scan data source as
// GalaxyView/IdleResourcesPanel (lib/useRegionScan.ts) -- no new backend
// endpoint. `scan.totals` (monthly_spend, idle_count, idle_monthly_waste)
// comes straight from the backend; the per-type breakdown below is
// computed client-side by grouping `scan.resources` by `type` and summing
// `cost.projected_monthly`, skipping any resource whose `cost` block is
// null (a per-resource cost-lookup failure -- data-schema skill -- must
// not crash the aggregation or silently count as $0 in a way that implies
// "actually free").
interface TypeBreakdownRow {
  type: string;
  label: string;
  monthly: number;
  count: number;
  missingCostCount: number;
}

function buildBreakdown(resources: GalaxyResource[]): TypeBreakdownRow[] {
  const byType = new Map<string, TypeBreakdownRow>();
  for (const r of resources) {
    let row = byType.get(r.type);
    if (!row) {
      row = { type: r.type, label: TYPE_LABEL[r.type] ?? r.type, monthly: 0, count: 0, missingCostCount: 0 };
      byType.set(r.type, row);
    }
    row.count += 1;
    if (r.cost) {
      row.monthly += r.cost.projected_monthly;
    } else {
      row.missingCostCount += 1;
    }
  }
  return [...byType.values()].sort((a, b) => b.monthly - a.monthly);
}

export default function CostOverviewPanel() {
  const state = useRegionScan();
  const { scan, hardError, loading } = state;

  const breakdown = useMemo(() => buildBreakdown(scan?.resources ?? []), [scan]);
  const maxMonthly = breakdown.length > 0 ? breakdown[0].monthly : 0;

  const sortedResources = useMemo(() => {
    return [...(scan?.resources ?? [])].sort(
      (a, b) => (b.cost?.projected_monthly ?? 0) - (a.cost?.projected_monthly ?? 0)
    );
  }, [scan]);

  return (
    <div>
      <RegionScanToolbar state={state} title="Cost Overview" />

      {hardError && !scan && (
        <div className="rounded-lg border border-status-bad/40 bg-status-bad/10 p-4 text-sm text-status-bad">
          {hardError}
        </div>
      )}

      {!hardError && loading && !scan && (
        <div className="rounded-lg border border-border bg-surface p-6 text-sm text-muted">
          Scanning {state.region}…
        </div>
      )}

      {scan && (
        <>
          {/* Method disclosure -- data-schema skill's explicit UI requirement:
              every populated cost block's `method` is currently always
              "list_price" (AWS Pricing API on-demand rate), never a real
              billed-cost lookup. Say that plainly, near the total, so this
              never reads like an actual bill. */}
          <div className="mb-4 rounded-lg border border-accent/30 bg-accent/5 px-4 py-2 text-xs text-accent">
            List-price estimate (AWS Pricing API on-demand rate), not billed cost --
            Cost Explorer&apos;s actual-spend lookup is not used here (roadmap 3.2).
          </div>

          <div className="mb-6 grid gap-3 sm:grid-cols-3">
            <div className="rounded-lg border border-border bg-surface p-4">
              <div className="font-mono text-[11px] uppercase tracking-wide text-muted">
                Total monthly spend
              </div>
              <div className="mt-1 font-mono text-2xl text-text">
                {money(scan.totals.monthly_spend)}
              </div>
            </div>
            <div className="rounded-lg border border-border bg-surface p-4">
              <div className="font-mono text-[11px] uppercase tracking-wide text-muted">
                Idle resources
              </div>
              <div className="mt-1 font-mono text-2xl text-text">{scan.totals.idle_count}</div>
            </div>
            <div className="rounded-lg border border-accent/30 bg-accent/10 p-4">
              <div className="font-mono text-[11px] uppercase tracking-wide text-accent">
                Idle waste, could be saved
              </div>
              <div className="mt-1 font-mono text-2xl text-accent">
                {money(scan.totals.idle_monthly_waste)}/mo
              </div>
            </div>
          </div>

          <div className="mb-2 font-mono text-[11px] uppercase tracking-wide text-muted">
            By resource type
          </div>
          {breakdown.length === 0 ? (
            <div className="rounded-lg border border-border bg-surface p-6 text-sm text-muted">
              No supported resources found in {scan.region}.
            </div>
          ) : (
            <div className="mb-6 space-y-2">
              {breakdown.map((row) => (
                <div key={row.type} className="rounded-lg border border-border bg-surface p-3">
                  <div className="mb-1.5 flex items-center justify-between gap-3 text-sm">
                    <span className="text-text">
                      {row.label} <span className="text-xs text-muted">×{row.count}</span>
                    </span>
                    <span className="font-mono text-text">{money(row.monthly)}/mo</span>
                  </div>
                  <div className="h-1.5 w-full overflow-hidden rounded-full bg-surfacealt">
                    <div
                      className="h-full rounded-full bg-accent"
                      style={{
                        width: `${maxMonthly > 0 ? Math.max(2, (row.monthly / maxMonthly) * 100) : 0}%`,
                      }}
                    />
                  </div>
                  {row.missingCostCount > 0 && (
                    <div className="mt-1 text-[11px] text-muted">
                      Cost lookup failed for {row.missingCostCount} of {row.count} -- excluded from
                      this total, not counted as $0.
                    </div>
                  )}
                </div>
              ))}
            </div>
          )}

          <div className="mb-2 font-mono text-[11px] uppercase tracking-wide text-muted">
            By resource, descending
          </div>
          <div className="overflow-x-auto rounded-lg border border-border bg-surface">
            <table className="w-full text-left text-sm">
              <thead>
                <tr className="border-b border-border text-xs uppercase tracking-wide text-muted">
                  <th className="px-3 py-2 font-mono">Resource</th>
                  <th className="px-3 py-2 font-mono">Type</th>
                  <th className="px-3 py-2 font-mono">Projected/mo</th>
                </tr>
              </thead>
              <tbody>
                {sortedResources.map((r) => (
                  <tr key={r.id} className="border-b border-border last:border-0">
                    <td className="px-3 py-2">
                      <div className="text-text">{r.name}</div>
                      <div className="break-all font-mono text-[11px] text-muted">{r.id}</div>
                    </td>
                    <td className="px-3 py-2 text-muted">{TYPE_LABEL[r.type] ?? r.type}</td>
                    <td className="px-3 py-2 font-mono text-text">
                      {r.cost ? `${money(r.cost.projected_monthly)}/mo` : "unavailable"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
    </div>
  );
}
