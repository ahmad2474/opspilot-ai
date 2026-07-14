"use client";

import { useMemo, useState } from "react";
import { TYPE_LABEL } from "@/components/GalaxyView";
import RegionScanToolbar from "@/components/RegionScanToolbar";
import { money, relativeTime } from "@/lib/format";
import { useRegionScan } from "@/lib/useRegionScan";
import type { GalaxyResource } from "@/lib/api";

// Roadmap Section 5's "Idle Resources" tab. Deliberately NOT a new backend
// endpoint -- reuses the exact same scanRegion()/getRegions() data
// GalaxyView.tsx already fetches (see lib/useRegionScan.ts), filtered
// client-side to resources crossing the idle threshold.
//
// Threshold: `idle.idle_days >= IDLE_PULSE_THRESHOLD_DAYS`, the SAME
// constant/definition GalaxyView already uses to decide "pulsing amber" on
// the galaxy canvas -- deliberately NOT `idle.is_idle` directly. Per the
// data-schema skill and GalaxyView's own comment (lines ~20-26):
// `is_idle` reflects whatever `window_days` the backend's idle-check
// happened to use for that specific check, while `idle_days >= 7` is this
// app's own, independent display threshold for what counts as "idle" in
// the UI. They only coincide today because the scan's check_idle call
// happens to use a 7-day window -- don't treat that as guaranteed, and
// don't invent a third definition here.
const IDLE_PULSE_THRESHOLD_DAYS = 7;

type SortKey = "idle_days" | "cost";

function isIdleForDisplay(r: GalaxyResource): boolean {
  return (r.idle?.idle_days ?? 0) >= IDLE_PULSE_THRESHOLD_DAYS;
}

export default function IdleResourcesPanel() {
  const state = useRegionScan();
  const { scan, hardError, loading } = state;
  const [sortKey, setSortKey] = useState<SortKey>("cost");

  const idleResources = useMemo(() => {
    const list = (scan?.resources ?? []).filter(isIdleForDisplay);
    return [...list].sort((a, b) => {
      if (sortKey === "idle_days") {
        return (b.idle?.idle_days ?? 0) - (a.idle?.idle_days ?? 0);
      }
      return (b.cost?.projected_monthly ?? 0) - (a.cost?.projected_monthly ?? 0);
    });
  }, [scan, sortKey]);

  return (
    <div>
      <RegionScanToolbar state={state} title="Idle Resources" />
      <p className="mb-4 max-w-2xl text-sm text-muted">
        Resources idle for {IDLE_PULSE_THRESHOLD_DAYS}+ days in {scan?.region ?? state.region} --
        the same threshold the Galaxy view pulses amber for. Sorted by{" "}
        <span className="font-mono text-xs">{sortKey === "cost" ? "projected cost" : "idle days"}</span>.
      </p>

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

      {scan && idleResources.length === 0 && (
        <div className="rounded-lg border border-border bg-surface p-6 text-sm text-muted">
          No idle resources found in this region.
        </div>
      )}

      {scan && idleResources.length > 0 && (
        <div className="overflow-x-auto rounded-lg border border-border bg-surface">
          <table className="w-full text-left text-sm">
            <thead>
              <tr className="border-b border-border text-xs uppercase tracking-wide text-muted">
                <th className="px-3 py-2 font-mono">Resource</th>
                <th className="px-3 py-2 font-mono">Type</th>
                <th
                  className="cursor-pointer select-none px-3 py-2 font-mono hover:text-text"
                  onClick={() => setSortKey("idle_days")}
                >
                  Idle days {sortKey === "idle_days" ? "▾" : ""}
                </th>
                <th className="px-3 py-2 font-mono">Idle since</th>
                <th
                  className="cursor-pointer select-none px-3 py-2 font-mono hover:text-text"
                  onClick={() => setSortKey("cost")}
                >
                  Projected/mo {sortKey === "cost" ? "▾" : ""}
                </th>
              </tr>
            </thead>
            <tbody>
              {idleResources.map((r) => (
                <tr key={r.id} className="border-b border-border last:border-0">
                  <td className="px-3 py-2">
                    <div className="text-text">{r.name}</div>
                    <div className="break-all font-mono text-[11px] text-muted">{r.id}</div>
                  </td>
                  <td className="px-3 py-2 text-muted">{TYPE_LABEL[r.type] ?? r.type}</td>
                  <td className="px-3 py-2 font-mono text-accent">
                    {r.idle?.idle_days ?? "—"}
                    {r.idle?.idle_since_is_estimated ? " (est.)" : ""}
                  </td>
                  <td className="px-3 py-2 text-muted">{relativeTime(r.idle?.idle_since ?? null)}</td>
                  <td className="px-3 py-2 font-mono text-text">
                    {r.cost ? `${money(r.cost.projected_monthly)}/mo` : "—"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
