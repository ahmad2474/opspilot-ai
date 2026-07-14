"use client";

import { relativeTime } from "@/lib/format";
import type { UseRegionScanResult } from "@/lib/useRegionScan";

// Shared region-picker + refresh/staleness header for the scan-derived
// panels (Idle Resources, Cost Overview) -- paired with lib/useRegionScan's
// state. Deliberately a plain in-flow `<select>` + button row (not
// GalaxyView's floating absolutely-positioned overlay cards) since these
// pages live in the normal document flow under the shared
// `mx-auto max-w-6xl px-6 py-8` page wrapper (see app/investigations/page.tsx
// etc.), not on top of GalaxyView's full-bleed starfield canvas.
export default function RegionScanToolbar({
  state,
  title,
}: {
  state: UseRegionScanResult;
  title: string;
}) {
  const {
    regions,
    region,
    setRegion,
    regionsError,
    scan,
    warning,
    dismissWarning,
    loading,
    refreshing,
    cooldown,
  } = state;

  return (
    <div className="mb-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h1 className="text-lg font-medium">{title}</h1>
        <div className="flex items-center gap-2">
          <select
            value={region}
            onChange={(e) => setRegion(e.target.value)}
            disabled={regions.length === 0}
            className="rounded-md border border-border bg-surfacealt px-2 py-1.5 font-mono text-xs text-text disabled:opacity-40"
          >
            {regions.length === 0 && <option value={region}>{region}</option>}
            {regions.map((r) => (
              <option key={r} value={r}>
                {r}
              </option>
            ))}
          </select>
          <button
            onClick={state.refresh}
            disabled={refreshing || cooldown > 0}
            className="whitespace-nowrap rounded-md border border-border px-3 py-1.5 text-xs text-muted transition-colors hover:border-accent hover:text-text disabled:opacity-40"
          >
            {refreshing ? "Refreshing…" : cooldown > 0 ? `Refresh in ${cooldown}s` : "Refresh"}
          </button>
        </div>
      </div>
      <div className="mt-1 text-xs text-muted">
        {scan
          ? `Last updated ${relativeTime(scan.last_updated)}`
          : loading
            ? "Scanning…"
            : "No data yet"}
        {regionsError && <span className="ml-2 text-status-bad">{regionsError}</span>}
      </div>

      {warning && (
        <div className="mt-3 flex items-start justify-between gap-3 rounded-lg border border-accent/40 bg-accent/10 px-4 py-2 text-xs text-accent">
          <span>{warning}</span>
          <button
            onClick={dismissWarning}
            className="text-accent/70 hover:text-accent"
            aria-label="Dismiss warning"
          >
            ✕
          </button>
        </div>
      )}
    </div>
  );
}
