// Small formatting helpers shared by the region-scan-backed panels
// (components/IdleResourcesPanel.tsx, components/CostOverviewPanel.tsx).
// components/GalaxyView.tsx keeps its own local copies of the same two
// functions rather than importing these -- it predates this file and
// isn't touched here to keep this build step's diff to the pages it
// actually adds/fixes (see PR notes). Keep the two implementations in
// sync by eyeballing if either ever changes; they're a few lines each.

export function relativeTime(iso: string | null): string {
  if (!iso) return "unknown time";
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return "unknown time";
  const diffMs = Date.now() - then;
  if (diffMs < 0) return "just now";
  const diffMin = Math.round(diffMs / 60000);
  if (diffMin < 1) return "just now";
  if (diffMin < 60) return `${diffMin}m ago`;
  const diffHr = Math.round(diffMin / 60);
  if (diffHr < 24) return `${diffHr}h ago`;
  const diffDay = Math.round(diffHr / 24);
  return `${diffDay}d ago`;
}

export function money(n: number): string {
  return `$${n.toFixed(2)}`;
}
