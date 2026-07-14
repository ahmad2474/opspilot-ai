"use client";

import { useCallback, useEffect, useMemo, useRef, useState, type PointerEvent } from "react";
import { useChatLauncher } from "@/components/ChatLauncherProvider";
import {
  getRegions,
  scanRegion,
  ScanCooldownError,
  type GalaxyResource,
  type RelationLink,
  type ScanResponse,
} from "@/lib/api";

// Mirrors opspilot-backend/app/services/scan_service.py::COOLDOWN_SECONDS --
// used client-side to disable the refresh button proactively (not just
// reactively on a 429), so we don't even attempt a spammy force=true call
// against billed AWS APIs (roadmap 3.4).
const REFRESH_COOLDOWN_SECONDS = 45;

// Click-vs-drag threshold (screen px, pointer-down to pointer-up distance).
// Below this, a star press is treated as a click (opens the detail panel);
// at/above it, it's a completed drag (detail panel suppressed). Mid-range
// of the 4-6px window that's standard for this pattern.
const DRAG_CLICK_THRESHOLD_PX = 5;

// Roadmap Section 5: "pulsing amber = idle >= 7 days." Per the data-schema
// skill, this is deliberately frontend-agent's OWN display threshold
// applied to `idle.idle_days`, independent of `idle.is_idle` (which
// reflects whatever window_days the backend happened to check) -- the two
// only coincide today because the scan's check_idle call uses a 7-day
// window. Don't swap this for `idle.is_idle`.
const IDLE_PULSE_THRESHOLD_DAYS = 7;

// Exported (Family, TYPE_LABEL) so other resource-list views built off the
// same scan data -- components/IdleResourcesPanel.tsx,
// components/CostOverviewPanel.tsx -- can reuse this one 15-entry type-label
// table instead of re-typing it a second/third time and risking drift. Pure
// additive exports; no behavior here changes.
export type Family = "compute" | "storage" | "database" | "networking" | "streaming";

// Roadmap Section 5's per-family table, keyed by the 15 TYPE_CODES
// (data-schema skill).
const TYPE_FAMILY: Record<string, Family> = {
  ec2: "compute",
  lambda: "compute",
  sagemaker: "compute",
  ebs: "storage",
  rds: "database",
  dynamodb: "database",
  elasticache: "database",
  redshift: "database",
  opensearch: "database",
  elb: "networking",
  nat_gateway: "networking",
  eip: "networking",
  api_gateway: "networking",
  cloudfront: "networking",
  kinesis: "streaming",
};

const FAMILY_LABEL: Record<Family, string> = {
  compute: "Compute",
  storage: "Storage",
  database: "Database",
  networking: "Networking",
  streaming: "Streaming",
};

const ALL_FAMILIES: Family[] = ["compute", "storage", "database", "networking", "streaming"];

export const TYPE_LABEL: Record<string, string> = {
  ec2: "EC2 Instance",
  ebs: "EBS Volume",
  rds: "RDS Database",
  eip: "Elastic IP",
  elb: "Load Balancer",
  lambda: "Lambda Function",
  nat_gateway: "NAT Gateway",
  dynamodb: "DynamoDB Table",
  elasticache: "ElastiCache Cluster",
  sagemaker: "SageMaker Endpoint",
  redshift: "Redshift Cluster",
  api_gateway: "API Gateway",
  cloudfront: "CloudFront Distribution",
  opensearch: "OpenSearch Domain",
  kinesis: "Kinesis Stream",
};

// Cluster view (roadmap 3.7 / "View connections"). Cost-bearing relation
// targets are the same 15 TYPE_CODES as TYPE_LABEL above -- reuse its key
// set rather than re-listing them so the two can't drift.
const COST_BEARING_KINDS = new Set(Object.keys(TYPE_LABEL));

// Non-cost-bearing infra relation targets (data-schema skill's
// INFRA_KINDS) -- never present in `resources[]`, id-only, rendered
// smaller and in violet in the cluster view.
const INFRA_KIND_LABEL: Record<string, string> = {
  security_group: "Security Group",
  subnet: "Subnet",
  vpc: "VPC",
  iam_role: "IAM Role",
};

const RELATION_LABEL_TEXT: Record<string, string> = {
  attached: "attached",
  secured_by: "secured by",
  in: "in",
  routed_by: "routed by",
  assumes: "assumes",
};

// Colors: idle/active status is the ONLY thing color encodes for
// cost-bearing resources (roadmap Section 5 -- do not overload color with
// type too). Type is conveyed by the Glyph component instead. These hex
// values (not Tailwind theme tokens) intentionally match the locked-in
// prototype's palette (docs/aws-galaxy-dashboard.jsx) so the visual
// language carries over exactly. COLOR_INFRA is the one deliberate
// exception -- the cluster view's non-cost-bearing infra nodes (security
// group / subnet / vpc / iam role) are always violet regardless of
// idle/active, since they have no idle/cost data of their own to color by
// (roadmap 3.7).
const COLOR_ACTIVE = "#7fd7ff";
const COLOR_IDLE = "#f0a202";
const COLOR_UNKNOWN = "#6E7681";
const COLOR_INFRA = "#a78bfa";

function familyFor(type: string): Family {
  return TYPE_FAMILY[type] ?? "compute";
}

interface PositionedResource extends GalaxyResource {
  x: number;
  y: number;
  radius: number;
}

// One node in the "View connections" cluster view (roadmap 3.7). Either
// the centered resource itself, a cost-bearing related resource (looked
// up by id in this scan's resources, sized/colored like the main galaxy),
// or a non-cost-bearing infra node (security_group/subnet/vpc/iam_role --
// id-only, smaller, violet).
interface ClusterNode {
  key: string;
  id: string;
  x: number;
  y: number;
  radius: number;
  displayLabel: string;
  kindLabel: string;
  isCenter: boolean;
  costBearing: boolean;
  resource: PositionedResource | null;
  edgeLabel: string | null; // relation label from center to this node; null for the center itself
}

// Star/bubble radius is driven by projected monthly cost ONLY (roadmap
// 3.1a) -- never incurred-so-far. A resource with no cost block at all
// (the per-resource-nullable case) gets a small, neutral fallback size
// rather than vanishing or crashing.
function radiusFor(monthlyCost: number | null): number {
  if (monthlyCost == null) return 1.3;
  return Math.max(1.1, Math.min(4.6, 1.1 + Math.sqrt(monthlyCost) * 0.3));
}

// The backend has no concept of x/y (data-schema skill: "frontend-agent
// maps this schema into whatever local rendering shape, x/y layout,
// etc."). This lays resources out on a golden-angle spiral so the galaxy
// reads as intentional, not randomly scattered, and is deterministic for
// a given resource set (sorted by type then id) so a re-scan with the
// same resources doesn't jump around visually.
function layoutResources(resources: GalaxyResource[]): PositionedResource[] {
  const sorted = [...resources].sort((a, b) =>
    a.type === b.type ? a.id.localeCompare(b.id) : a.type.localeCompare(b.type)
  );
  const n = sorted.length;
  const cx = 50;
  const cy = 48;
  const maxR = 40;
  return sorted.map((r, i) => {
    const angle = i * 137.508 * (Math.PI / 180); // golden angle -- sunflower phyllotaxis
    const t = n <= 1 ? 0 : i / (n - 1);
    const radiusFromCenter = maxR * Math.sqrt(t);
    return {
      ...r,
      x: cx + radiusFromCenter * Math.cos(angle),
      y: cy + radiusFromCenter * Math.sin(angle) * 0.92,
      radius: radiusFor(r.cost?.projected_monthly ?? null),
    };
  });
}

// Builds the cluster layout for a "View connections" focus on `center`
// (roadmap 3.7): center resource in the middle, its `relations[]` orbiting
// evenly around it, connected by labeled dashed lines. `byId` is the full
// (unfiltered by legend) galaxy position map so cost-bearing relation
// targets pick up the same radius/position math as the main galaxy.
function layoutCluster(
  center: PositionedResource,
  byId: Map<string, PositionedResource>
): ClusterNode[] {
  const cx = 50;
  const cy = 48;
  const orbitR = 30;
  const relations = center.relations;
  const n = relations.length;

  const nodes: ClusterNode[] = [
    {
      key: `center:${center.id}`,
      id: center.id,
      x: cx,
      y: cy,
      radius: radiusFor(center.cost?.projected_monthly ?? null),
      displayLabel: center.name,
      kindLabel: TYPE_LABEL[center.type] ?? center.type,
      isCenter: true,
      costBearing: true,
      resource: center,
      edgeLabel: null,
    },
  ];

  relations.forEach((rel: RelationLink, i: number) => {
    const angle = -Math.PI / 2 + (2 * Math.PI * i) / Math.max(n, 1);
    const x = cx + orbitR * Math.cos(angle);
    const y = cy + orbitR * Math.sin(angle) * 0.92;

    if (COST_BEARING_KINDS.has(rel.kind)) {
      // Per the data-schema skill, a cost-bearing relation target is
      // always also present in this scan's resources[] -- but guard
      // against a missing lookup rather than crashing, same as the
      // existing relationLines logic does.
      const target = byId.get(rel.id);
      if (!target) return;
      nodes.push({
        key: `rel:${rel.id}:${rel.kind}`,
        id: target.id,
        x,
        y,
        radius: target.radius,
        displayLabel: target.name,
        kindLabel: TYPE_LABEL[target.type] ?? target.type,
        isCenter: false,
        costBearing: true,
        resource: target,
        edgeLabel: rel.label,
      });
    } else {
      nodes.push({
        key: `rel:${rel.id}:${rel.kind}`,
        id: rel.id,
        x,
        y,
        radius: 1.4,
        displayLabel: rel.id,
        kindLabel: INFRA_KIND_LABEL[rel.kind] ?? rel.kind,
        isCenter: false,
        costBearing: false,
        resource: null,
        edgeLabel: rel.label,
      });
    }
  });

  return nodes;
}

function relativeTime(iso: string | null): string {
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

function money(n: number): string {
  return `$${n.toFixed(2)}`;
}

// Converts a pointer event's screen-space client coordinates into the
// galaxy `<svg viewBox="0 0 100 100">`'s own 100x100 coordinate space.
// Uses getScreenCTM().inverse() rather than a naive
// getBoundingClientRect()-based width/height ratio -- the canvas is a
// responsive, non-square element (`h-[calc(100vh-66px)] w-full`) holding a
// square viewBox with the SVG default `preserveAspectRatio="xMidYMid
// meet"`, which uniformly scales and letterboxes/pillarboxes the content
// rather than stretching each axis independently. The CTM (current
// transformation matrix) already accounts for that letterboxing, so this
// stays accurate at any window size/aspect ratio.
function clientToSvgPoint(svg: SVGSVGElement, clientX: number, clientY: number): { x: number; y: number } {
  const ctm = svg.getScreenCTM();
  if (!ctm) return { x: 50, y: 50 };
  const point = svg.createSVGPoint();
  point.x = clientX;
  point.y = clientY;
  const transformed = point.matrixTransform(ctm.inverse());
  return { x: transformed.x, y: transformed.y };
}

// Small hand-drawn glyph per resource family (roadmap Section 5's icon
// table). No icon library is installed in this project (see package.json)
// so these are plain SVG primitives rather than a new dependency --
// intentionally abstract/minimal so they stay legible at small star
// sizes. `stroke` controls the glyph color; callers pass "currentColor"
// for standalone use (legend/detail panel) or a fixed dark tone when
// drawn on top of a bright star fill.
function Glyph({ family, size, stroke }: { family: Family; size: number; stroke: string }) {
  const half = size / 2;
  const sw = Math.max(0.12, size * 0.09);
  switch (family) {
    case "compute": // chip/cpu
      return (
        <g>
          <rect
            x={-half * 0.55}
            y={-half * 0.55}
            width={half * 1.1}
            height={half * 1.1}
            rx={half * 0.12}
            fill="none"
            stroke={stroke}
            strokeWidth={sw}
          />
          <circle cx={0} cy={0} r={size * 0.09} fill={stroke} />
        </g>
      );
    case "storage": // disk
      return (
        <g>
          <circle cx={0} cy={0} r={half * 0.55} fill="none" stroke={stroke} strokeWidth={sw} />
          <line x1={0} y1={0} x2={half * 0.5} y2={0} stroke={stroke} strokeWidth={sw} />
          <circle cx={0} cy={0} r={size * 0.08} fill={stroke} />
        </g>
      );
    case "database": // cylinder
      return (
        <g>
          <ellipse
            cx={0}
            cy={-half * 0.32}
            rx={half * 0.5}
            ry={half * 0.18}
            fill="none"
            stroke={stroke}
            strokeWidth={sw}
          />
          <path
            d={`M ${-half * 0.5} ${-half * 0.32} V ${half * 0.32} A ${half * 0.5} ${half * 0.18} 0 0 0 ${half * 0.5} ${half * 0.32} V ${-half * 0.32}`}
            fill="none"
            stroke={stroke}
            strokeWidth={sw}
          />
        </g>
      );
    case "networking": // node/link
      return (
        <g>
          <circle cx={0} cy={0} r={size * 0.09} fill={stroke} />
          <circle cx={half * 0.5} cy={-half * 0.4} r={size * 0.07} fill={stroke} />
          <circle cx={-half * 0.5} cy={half * 0.4} r={size * 0.07} fill={stroke} />
          <line x1={0} y1={0} x2={half * 0.5} y2={-half * 0.4} stroke={stroke} strokeWidth={sw * 0.8} />
          <line x1={0} y1={0} x2={-half * 0.5} y2={half * 0.4} stroke={stroke} strokeWidth={sw * 0.8} />
        </g>
      );
    case "streaming": // wave
      return (
        <path
          d={`M ${-half * 0.6} 0 Q ${-half * 0.3} ${-half * 0.55} 0 0 Q ${half * 0.3} ${half * 0.55} ${half * 0.6} 0`}
          fill="none"
          stroke={stroke}
          strokeWidth={sw}
        />
      );
    default:
      return null;
  }
}

function StandaloneGlyph({ family, className }: { family: Family; className?: string }) {
  return (
    <svg viewBox="-5 -5 10 10" width={14} height={14} className={className}>
      <Glyph family={family} size={8} stroke="currentColor" />
    </svg>
  );
}

function DetailPanel({
  resource,
  region,
  onClose,
  onViewConnections,
}: {
  resource: PositionedResource;
  region: string;
  onClose: () => void;
  onViewConnections: (id: string) => void;
}) {
  const { openChat } = useChatLauncher();
  const idle = resource.idle;
  const cost = resource.cost;
  const idlePulsing = (idle?.idle_days ?? 0) >= IDLE_PULSE_THRESHOLD_DAYS;

  return (
    <div className="relative p-6">
      <button
        onClick={onClose}
        className="absolute right-4 top-4 text-muted transition-colors hover:text-text"
        aria-label="Close detail panel"
      >
        ✕
      </button>

      <div className="mb-1 flex items-center gap-2 text-xs text-muted">
        <StandaloneGlyph family={familyFor(resource.type)} className="text-[#7fd7ff]" />
        {TYPE_LABEL[resource.type] ?? resource.type} · {FAMILY_LABEL[familyFor(resource.type)]}
      </div>
      <div className="mb-4 break-words font-mono text-xl text-text">{resource.name}</div>

      <div className="mb-5 grid grid-cols-2 gap-3 text-sm">
        <div>
          <div className="text-xs text-muted">Region</div>
          <div className="font-mono">{region}</div>
        </div>
        <div>
          <div className="text-xs text-muted">Resource ID</div>
          <div className="break-all font-mono text-xs">{resource.id}</div>
        </div>
        <div>
          <div className="text-xs text-muted">Health status</div>
          <div className="font-mono">{resource.health.status}</div>
        </div>
        <div>
          <div className="text-xs text-muted">Idle status</div>
          {idle ? (
            <div className={idlePulsing ? "text-accent" : "text-[#7fd7ff]"}>
              {idlePulsing
                ? `Idle ${idle.younger_than_window ? "since launch" : `${idle.idle_days}d`}${
                    idle.idle_since_is_estimated ? " (est.)" : ""
                  }`
                : "Active"}
            </div>
          ) : (
            <div className="text-muted">Unavailable</div>
          )}
        </div>
      </div>

      {idle && !idlePulsing && idle.idle_days > 0 && (
        <div className="-mt-3 mb-5 text-[11px] text-muted">
          {idle.idle_days}d trailing idle streak, currently broken by activity within the checked
          window.
        </div>
      )}

      <div className="mb-5 rounded-lg border border-border bg-surface p-3">
        <div className="mb-1.5 font-mono text-[11px] uppercase tracking-wide text-muted">
          Cost
        </div>
        {cost ? (
          <>
            <div className="text-sm leading-relaxed text-text">
              Projected:{" "}
              <span className="font-mono text-accent">{money(cost.projected_monthly)}/mo</span>
              {" · "}
              Incurred so far: <span className="font-mono">{money(cost.incurred_so_far)}</span>{" "}
              <span className="text-xs text-muted">
                (created {relativeTime(resource.created_at)})
              </span>
            </div>
            <div className="mt-1.5 text-[11px] text-muted">
              method:{" "}
              <span className="font-mono">
                {cost.method === "list_price"
                  ? "list price (Pricing API)"
                  : "billed (Cost Explorer)"}
              </span>
            </div>
          </>
        ) : (
          <div className="text-sm text-muted">
            Cost data unavailable for this resource -- the last scan&apos;s cost lookup failed for
            it specifically (rest of the scan is unaffected).
          </div>
        )}
      </div>

      <div className="mb-6 text-xs text-muted">
        primary signal: <span className="font-mono text-text">{resource.health.primary_metric}</span>
        {resource.health.primary_metric_value != null && (
          <span className="font-mono text-text"> = {resource.health.primary_metric_value}</span>
        )}
      </div>

      <div className="flex flex-col gap-2">
        <button
          onClick={() => openChat({ id: resource.id, label: resource.name })}
          className="w-full rounded-lg border border-accent/40 bg-accent/10 py-2 text-sm text-accent transition-colors hover:bg-accent/20"
        >
          Ask about this resource
        </button>

        {resource.relations.length > 0 && (
          <button
            onClick={() => onViewConnections(resource.id)}
            className="w-full rounded-lg border border-[#a78bfa]/40 bg-[#a78bfa]/10 py-2 text-sm text-[#c4b5fd] transition-colors hover:bg-[#a78bfa]/20"
          >
            View connections ({resource.relations.length})
          </button>
        )}
      </div>
    </div>
  );
}

export default function GalaxyView() {
  const [regions, setRegions] = useState<string[]>([]);
  const [region, setRegion] = useState("us-east-1");
  const [regionOpen, setRegionOpen] = useState(false);
  const [regionsError, setRegionsError] = useState<string | null>(null);

  const [scan, setScan] = useState<ScanResponse | null>(null);
  const [hardError, setHardError] = useState<string | null>(null);
  const [warning, setWarning] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [cooldown, setCooldown] = useState(0);

  // Elapsed-time readout for the "Scanning…" loading state (bug fix). A
  // legitimate first-ever, never-cached scan can legitimately take 100+
  // seconds (see scanRegion's SCAN_TIMEOUT_MS comment in lib/api.ts) -- with
  // no ticking indicator, that's indistinguishable from a genuinely stuck
  // request. Only ticks while the initial-load spinner (`loading && !scan`)
  // is showing, mirroring that same condition below.
  const [elapsedSeconds, setElapsedSeconds] = useState(0);
  useEffect(() => {
    if (!loading || scan) {
      setElapsedSeconds(0);
      return;
    }
    const start = Date.now();
    setElapsedSeconds(0);
    const id = setInterval(() => setElapsedSeconds(Math.round((Date.now() - start) / 1000)), 1000);
    return () => clearInterval(id);
  }, [loading, scan]);

  const [selectedId, setSelectedId] = useState<string | null>(null);
  // Non-null => cluster view (roadmap 3.7 "View connections"), centered on
  // this resource id. Null => normal full star-field galaxy.
  const [clusterCenterId, setClusterCenterId] = useState<string | null>(null);
  const [enabledFamilies, setEnabledFamilies] = useState<Set<Family>>(new Set(ALL_FAMILIES));
  const [legendOpen, setLegendOpen] = useState(true);
  const [, forceTick] = useState(0);

  // Session-local, click-and-drag star positions (post-ship "make the
  // galaxy feel alive" addition, main galaxy view only -- cluster view
  // keeps its own click-to-recenter semantics unchanged). Keyed by
  // resource id, overlaying (never mutating) the deterministic
  // layoutResources()/`positioned` output below -- only present for
  // resources the user has actually dragged this session. Deliberately
  // NOT persisted (no backend field, no localStorage): cleared on every
  // successful scan landing (see runScan's success branch) so a
  // region switch or a manual refresh always returns to the golden-angle
  // layout, never leftover drag state from stale resource data.
  const [dragPositions, setDragPositions] = useState<Map<string, { x: number; y: number }>>(
    new Map()
  );
  const svgRef = useRef<SVGSVGElement | null>(null);
  // Read-only mirror of dragPositions for synchronous reads inside the
  // pointer-event handlers below (handleStarPointerDown needs the current
  // "pre-drag" position of a star to support reverting on pointercancel --
  // see handleStarPointerCancel). Kept as a ref (same pattern as scanRef
  // above) so those handlers can stay referentially stable ([] deps)
  // instead of being recreated on every dragPositions change.
  const dragPositionsRef = useRef(dragPositions);
  useEffect(() => {
    dragPositionsRef.current = dragPositions;
  }, [dragPositions]);
  // Per-gesture drag bookkeeping lives in a ref, not state -- it's pure
  // pointer-event plumbing that shouldn't itself trigger a re-render; only
  // the resulting dragPositions state update (in handleStarPointerMove)
  // should.
  //
  // Keyed by pointerId (a Map, not a single object) so two independent
  // drag gestures on two *different* stars -- e.g. multi-touch, or a stray
  // second pointer, both real cases given this uses pointer events with
  // touchAction:"none" specifically for cross-input consistency -- never
  // clobber each other. pointerId is unique per active pointer per the
  // Pointer Events spec, so each concurrent gesture gets its own isolated
  // entry: a second star's pointerdown can no longer overwrite (and a
  // second star's pointerup can no longer null out) a first star's
  // still-active session.
  const dragSessionRef = useRef<
    Map<
      number,
      {
        id: string;
        startClientX: number;
        startClientY: number;
        moved: boolean;
        // The star's dragPositions entry (or null if it had none, i.e. it
        // was still sitting at its deterministic layout position)
        // immediately before this gesture started -- restored verbatim on
        // pointercancel so a browser-aborted gesture doesn't leave the
        // star stuck at whatever partial position it last reached.
        prevPos: { x: number; y: number } | null;
      }
    >
  >(new Map());

  const scanRef = useRef<ScanResponse | null>(null);
  useEffect(() => {
    scanRef.current = scan;
  }, [scan]);

  // Monotonically increasing id of the most recently issued scan request
  // -- see runScan below for why this exists (out-of-order response guard).
  const requestIdRef = useRef(0);

  // In-flight request de-dupe, keyed by `${targetRegion}:${force}` -- a
  // DIFFERENT bug than the one requestIdRef guards against. This file's
  // mount effect (`useEffect(() => { ... runScan(region, false); },
  // [region])`) fires once per real mount, but reactStrictMode (see
  // next.config.mjs) double-invokes effects in dev: mount -> cleanup ->
  // mount again, synchronously in the same tick. Both invocations call
  // runScan("us-east-1", false) with nothing to distinguish them, so
  // without this map, TWO real `GET /resources/scan` requests fire back to
  // back (live-reproduced: ~52ms apart) against a billed-AWS-API-backed
  // endpoint. requestIdRef does NOT prevent this -- it only stops a late/
  // stale *response* from overwriting newer state after both requests are
  // already in flight; it does nothing to stop the second redundant
  // *request* from being sent in the first place. This map does: the
  // second (or Nth) caller for the exact same target reuses the first
  // caller's still-pending promise instead of calling scanRegion() again.
  // Cleared per-key once that promise settles (success or failure) so a
  // later, genuinely legitimate re-scan of the same target (e.g. the user
  // switches away and back to a region) is never wrongly deduped against a
  // long-finished request -- only requests truly overlapping in time share
  // a promise.
  const inFlightRef = useRef<Map<string, Promise<ScanResponse>>>(new Map());

  // Keep the "Last updated Nm ago" readout fresh without refetching data.
  useEffect(() => {
    const id = setInterval(() => forceTick((t) => t + 1), 30_000);
    return () => clearInterval(id);
  }, []);

  // Client-side cooldown countdown (belt-and-suspenders alongside the
  // backend's own debounce -- see runScan below).
  useEffect(() => {
    if (cooldown <= 0) return;
    const id = setTimeout(() => setCooldown((c) => Math.max(0, c - 1)), 1000);
    return () => clearTimeout(id);
  }, [cooldown]);

  useEffect(() => {
    (async () => {
      try {
        const res = await getRegions();
        setRegions(res.regions);
        if (res.regions.length > 0) {
          setRegion((prev) => (res.regions.includes(prev) ? prev : res.regions[0]));
        }
      } catch (err) {
        setRegionsError(err instanceof Error ? err.message : "Couldn't load the region list.");
      }
    })();
  }, []);

  const runScan = useCallback(async (targetRegion: string, force: boolean) => {
    // Guard against out-of-order responses: React 18 dev-mode double-
    // invokes effects, and a region switch can leave a slower, now-stale
    // request for the PREVIOUS region still in flight. Without this, a
    // late-arriving response for a region the user already switched away
    // from can silently overwrite the newer, correct state -- discovered
    // via a live click-through where the canvas kept showing us-east-1
    // data after switching to us-west-2. Only the most recently issued
    // request is allowed to apply its result.
    const myRequestId = ++requestIdRef.current;

    if (force) setRefreshing(true);
    else setLoading(true);

    // Reuse an already-in-flight request for this exact target instead of
    // issuing a second one -- see inFlightRef's own comment above for why
    // this is necessary (StrictMode's dev-only double-invoke) and why
    // requestIdRef alone isn't enough.
    const inFlightKey = `${targetRegion}:${force}`;
    let scanPromise = inFlightRef.current.get(inFlightKey);
    if (!scanPromise) {
      scanPromise = scanRegion(targetRegion, force);
      inFlightRef.current.set(inFlightKey, scanPromise);
      // Swallow here so this bookkeeping chain never surfaces an
      // "unhandled rejection" -- the real error is still delivered to
      // every actual caller below via their own `await scanPromise`.
      scanPromise.catch(() => {}).finally(() => {
        if (inFlightRef.current.get(inFlightKey) === scanPromise) {
          inFlightRef.current.delete(inFlightKey);
        }
      });
    }

    try {
      const res = await scanPromise;
      if (requestIdRef.current !== myRequestId) return;
      setScan(res);
      // Fresh scan data may add/remove/reposition resources -- discard any
      // session-local dragged positions rather than have them silently
      // apply to a now-different resource set. Covers both a region switch
      // AND a same-region manual refresh (force=true), since both paths
      // funnel through this same success branch.
      setDragPositions(new Map());
      setHardError(null);
      // Non-null `.error` = stale cache served after a failed rescan
      // (roadmap 3.4) -- surface as a warning, keep showing res.resources.
      setWarning(res.error);
      if (force) setCooldown(REFRESH_COOLDOWN_SECONDS);
    } catch (err) {
      if (requestIdRef.current !== myRequestId) return;
      if (err instanceof ScanCooldownError) {
        if (err.cached) {
          setScan(err.cached);
          setHardError(null);
        }
        setWarning(`Refresh is cooling down -- try again in ${err.retryAfterSeconds}s.`);
        setCooldown(err.retryAfterSeconds);
      } else {
        const msg = err instanceof Error ? err.message : "Unknown error";
        if (scanRef.current) {
          // Never blank the dashboard (roadmap 3.4) -- keep whatever was
          // last shown and surface a non-blocking warning instead.
          setWarning(`Couldn't load ${targetRegion}: ${msg} -- showing last available data.`);
        } else {
          setHardError(msg);
        }
      }
    } finally {
      if (requestIdRef.current === myRequestId) {
        setLoading(false);
        setRefreshing(false);
      }
    }
  }, []);

  // Initial load + every region switch: cheap cache-or-scan (force=false).
  useEffect(() => {
    setSelectedId(null);
    setClusterCenterId(null);
    runScan(region, false);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [region]);

  const positioned = useMemo(() => layoutResources(scan?.resources ?? []), [scan]);
  const byId = useMemo(() => new Map(positioned.map((r) => [r.id, r])), [positioned]);
  const selected = selectedId ? byId.get(selectedId) ?? null : null;

  const visible = useMemo(
    () => positioned.filter((r) => enabledFamilies.has(familyFor(r.type))),
    [positioned, enabledFamilies]
  );

  // Single shared source of truth for "where does this resource actually
  // render right now" -- the deterministic layoutResources() position,
  // overridden by a session-local dragged position if the user has moved
  // this star this session. Both the star circles/glyphs/labels AND the
  // constellation lines read a given resource's position through this one
  // helper so they can never drift out of sync (e.g. a line updating only
  // on drag-end, or only one endpoint tracking the drag).
  const getRenderPos = useCallback(
    (id: string, fallbackX: number, fallbackY: number) => dragPositions.get(id) ?? { x: fallbackX, y: fallbackY },
    [dragPositions]
  );

  // Dashed constellation lines, generalized off `relations` (roadmap 3.7's
  // field -- now populated by the backend for 10 of 15 resource types; see
  // data-schema skill). Only draws a line when the related resource is
  // also present in this scan's resource list -- this only ever fires for
  // cost-bearing targets since infra relation targets (security_group /
  // subnet / vpc / iam_role) are never in `resources[]`. Used for the
  // full-galaxy view; the cluster ("View connections") view below draws
  // its own labeled edges instead. Endpoints are read through
  // getRenderPos so a line attached to a currently-dragged star follows it
  // smoothly in real time, not just after the drag is released.
  const relationLines = useMemo(() => {
    const lines: { key: string; x1: number; y1: number; x2: number; y2: number }[] = [];
    for (const r of positioned) {
      const rPos = getRenderPos(r.id, r.x, r.y);
      for (const rel of r.relations) {
        const target = byId.get(rel.id);
        if (!target) continue;
        const tPos = getRenderPos(target.id, target.x, target.y);
        lines.push({ key: `${r.id}->${rel.id}`, x1: rPos.x, y1: rPos.y, x2: tPos.x, y2: tPos.y });
      }
    }
    return lines;
  }, [positioned, byId, getRenderPos]);

  // Cluster view (roadmap 3.7 "View connections"): centered resource +
  // its relations laid out around it. `clusterCenter` looks the requested
  // center id up in the full (unfiltered) galaxy position map, not
  // `visible`, so cluster mode isn't affected by the legend's family
  // toggles.
  const clusterCenter = clusterCenterId ? byId.get(clusterCenterId) ?? null : null;
  // A rescan can drop the resource currently centered in cluster mode
  // (e.g. it was terminated) -- fall back to the full galaxy instead of
  // leaving cluster mode stuck pointing at nothing.
  useEffect(() => {
    if (clusterCenterId && !clusterCenter) setClusterCenterId(null);
  }, [clusterCenterId, clusterCenter]);
  const clusterNodes = useMemo(
    () => (clusterCenter ? layoutCluster(clusterCenter, byId) : []),
    [clusterCenter, byId]
  );
  // Sum of center + every connected cost-bearing resource's own projected
  // monthly cost (skip nulls, skip infra nodes) -- roadmap 3.7's cluster
  // spend HUD.
  const clusterSpend = useMemo(
    () =>
      clusterNodes.reduce(
        (sum, n) => sum + (n.costBearing ? n.resource?.cost?.projected_monthly ?? 0 : 0),
        0
      ),
    [clusterNodes]
  );

  const familyCounts = useMemo(() => {
    const counts: Record<Family, number> = {
      compute: 0,
      storage: 0,
      database: 0,
      networking: 0,
      streaming: 0,
    };
    for (const r of positioned) counts[familyFor(r.type)] += 1;
    return counts;
  }, [positioned]);

  function toggleFamily(f: Family) {
    setEnabledFamilies((prev) => {
      const next = new Set(prev);
      if (next.has(f)) next.delete(f);
      else next.add(f);
      return next;
    });
  }

  function handleRefreshClick() {
    if (refreshing || cooldown > 0) return;
    runScan(region, true);
  }

  // Click-and-drag for main-galaxy stars only (cluster view keeps its own
  // click-to-recenter semantics untouched). Pointer events (not separate
  // mouse/touch handlers) for cross-input consistency, with
  // setPointerCapture so the same <g> keeps receiving move/up events even
  // once the pointer strays outside its bounds mid-drag. Click vs. drag is
  // disambiguated by total on-screen movement since pointer-down (see
  // DRAG_CLICK_THRESHOLD_PX) -- there's no separate onClick handler on the
  // star at all; handleStarPointerUp is the only thing that ever opens the
  // detail panel, precisely so a completed drag can never also fire a
  // stray synthetic click.
  const handleStarPointerDown = useCallback((e: PointerEvent<SVGGElement>, id: string) => {
    // Only the primary button (left click, or the sole "button" a touch/
    // pen contact reports) starts a drag session -- a right- or
    // middle-click on a star shouldn't hijack the pointer or block the
    // browser's own context menu / default handling for those buttons.
    if (e.button !== 0) return;
    e.currentTarget.setPointerCapture(e.pointerId);
    dragSessionRef.current.set(e.pointerId, {
      id,
      startClientX: e.clientX,
      startClientY: e.clientY,
      moved: false,
      prevPos: dragPositionsRef.current.get(id) ?? null,
    });
  }, []);

  const handleStarPointerMove = useCallback((e: PointerEvent<SVGGElement>, id: string) => {
    const session = dragSessionRef.current.get(e.pointerId);
    if (!session || session.id !== id) return;
    if (!session.moved) {
      const dx = e.clientX - session.startClientX;
      const dy = e.clientY - session.startClientY;
      if (Math.hypot(dx, dy) < DRAG_CLICK_THRESHOLD_PX) return;
      session.moved = true;
    }
    const svg = svgRef.current;
    if (!svg) return;
    const point = clientToSvgPoint(svg, e.clientX, e.clientY);
    setDragPositions((prev) => {
      const next = new Map(prev);
      next.set(id, point);
      return next;
    });
  }, []);

  const handleStarPointerUp = useCallback((e: PointerEvent<SVGGElement>, id: string) => {
    const session = dragSessionRef.current.get(e.pointerId);
    if (e.currentTarget.hasPointerCapture(e.pointerId)) {
      e.currentTarget.releasePointerCapture(e.pointerId);
    }
    if (!session || session.id !== id) return;
    dragSessionRef.current.delete(e.pointerId);
    // Only a movement-free press opens the detail panel -- a completed
    // drag (session.moved === true) intentionally suppresses it.
    if (!session.moved) {
      setSelectedId(id);
    }
  }, []);

  const handleStarPointerCancel = useCallback((e: PointerEvent<SVGGElement>, id: string) => {
    const session = dragSessionRef.current.get(e.pointerId);
    if (!session || session.id !== id) return;
    dragSessionRef.current.delete(e.pointerId);
    // A browser-aborted gesture (e.g. the OS hands the pointer to a
    // scroll/zoom gesture mid-drag) is NOT a completed drop -- revert to
    // wherever this star was sitting immediately before this gesture
    // started, rather than leaving it stranded at its last partial
    // position as if the drag had finished normally.
    if (session.moved) {
      setDragPositions((prev) => {
        const next = new Map(prev);
        if (session.prevPos) next.set(id, session.prevPos);
        else next.delete(id);
        return next;
      });
    }
  }, []);

  // Deterministic (not Math.random()) on purpose: this is a client
  // component, but Next.js App Router still does an initial SSR pass for
  // it -- Math.random() here would compute different values on the
  // server vs. during client hydration and throw a React hydration
  // mismatch on every load. A cheap pseudo-random hash keeps the same
  // scattered-twinkle look while staying identical between passes.
  const bgStars = useMemo(() => {
    const pseudoRandom = (seed: number) => {
      const v = Math.sin(seed * 12.9898) * 43758.5453;
      return v - Math.floor(v);
    };
    return Array.from({ length: 100 }).map((_, i) => ({
      x: pseudoRandom(i * 3 + 1) * 100,
      y: pseudoRandom(i * 3 + 2) * 100,
      r: pseudoRandom(i * 3 + 3) * 1.1 + 0.25,
      delay: pseudoRandom(i * 3 + 4) * 6,
    }));
  }, []);

  const showCanvas = !hardError || scan !== null;
  // Cluster mode is only really "active" if the requested center resource
  // still resolves in the current scan (e.g. a refresh could have dropped
  // it) -- otherwise fall back to the normal galaxy rather than rendering
  // an empty cluster.
  const clusterActive = clusterCenterId !== null && clusterCenter !== null;

  function backToGalaxy() {
    setClusterCenterId(null);
  }

  function viewConnections(id: string) {
    setClusterCenterId(id);
    setSelectedId(id);
  }

  return (
    <div>
      {hardError && !scan && (
        <div className="rounded-lg border border-status-bad/40 bg-status-bad/10 p-4 text-sm text-status-bad">
          {hardError}
        </div>
      )}

      {showCanvas && (
        // Full-bleed starfield -- no border/rounding, no page title above it
        // (prototype parity, docs/aws-galaxy-dashboard.jsx line 95). 66px is
        // NavBar's actual rendered height, computed directly from its own
        // classes (components/NavBar.tsx) rather than borrowed from other
        // pages' `calc(100vh-9rem)` budgets (those also count their own
        // page padding/heading, so they aren't a clean stand-in for NavBar's
        // height alone): row `py-4` (32px) + tallest row content, a nav tab
        // `<Link>` (`text-sm` line-height 20px + `py-1.5` 12px + `border`
        // 2px = 34px) = 32 + 34 = 66px. NavBar no longer has its own
        // `border-b` (dropped in the nav visual-fidelity fix, see
        // components/NavBar.tsx) so that 1px is no longer part of the sum.
        // app/galaxy/page.tsx renders no page padding of its own, so
        // NavBar's height is the only thing to subtract here.
        <div className="relative h-[calc(100vh-66px)] min-h-[560px] w-full overflow-hidden">
          <style>{`
            @keyframes galaxyIdlePulse {
              0%, 100% { opacity: 0.5; filter: brightness(0.8); }
              50% { opacity: 0.95; filter: brightness(1.15); }
            }
            @keyframes galaxyTwinkle {
              0%, 100% { opacity: 0.2; }
              50% { opacity: 0.85; }
            }
            .galaxy-star-idle { animation: galaxyIdlePulse 3.2s ease-in-out infinite; }
            .galaxy-bgstar { animation: galaxyTwinkle 5s ease-in-out infinite; }
          `}</style>

          {/* deep space backdrop */}
          <div
            className="absolute inset-0"
            style={{ background: "radial-gradient(ellipse at 50% 40%, #10152b 0%, #05070f 70%)" }}
          />
          <svg className="pointer-events-none absolute inset-0 h-full w-full">
            {bgStars.map((s, i) => (
              <circle
                key={i}
                className="galaxy-bgstar"
                cx={`${s.x.toFixed(2)}%`}
                cy={`${s.y.toFixed(2)}%`}
                r={Number(s.r.toFixed(2))}
                fill="#cfd8ff"
                style={{ animationDelay: `${s.delay.toFixed(2)}s` }}
              />
            ))}
          </svg>

          {/* region selector -- top left */}
          <div className="absolute left-4 top-4 z-20">
            <button
              onClick={() => setRegionOpen((o) => !o)}
              className="flex items-center gap-2 rounded-lg border border-border bg-surface/90 px-3 py-2 text-sm backdrop-blur transition-colors hover:border-accent"
            >
              <span className="h-1.5 w-1.5 rounded-full" style={{ background: COLOR_ACTIVE }} />
              <span className="font-mono">{region}</span>
              <span className={`text-muted transition-transform ${regionOpen ? "rotate-180" : ""}`}>
                ▾
              </span>
            </button>
            {regionOpen && (
              <div className="mt-1 max-h-64 w-44 overflow-y-auto rounded-lg border border-border bg-surface/95 backdrop-blur">
                {regions.length === 0 && (
                  <div className="px-3 py-2 text-xs text-muted">
                    {regionsError ?? "Loading regions…"}
                  </div>
                )}
                {regions.map((r) => (
                  <button
                    key={r}
                    onClick={() => {
                      setRegion(r);
                      setRegionOpen(false);
                    }}
                    className={`block w-full px-3 py-2 text-left font-mono text-sm hover:bg-surfacealt ${
                      r === region ? "text-accent" : "text-muted"
                    }`}
                  >
                    {r}
                  </button>
                ))}
              </div>
            )}
          </div>

          {/* HUD + refresh -- top right. In cluster mode this switches to
              the cluster spend readout + a "Back to galaxy" control
              (roadmap 3.7); the refresh/cooldown/staleness controls stay
              available since they act on the underlying scan regardless
              of which view is on screen. */}
          <div className="absolute right-4 top-4 z-20 w-72 rounded-lg border border-border bg-surface/90 px-4 py-3 backdrop-blur">
            <div className="mb-1 flex items-center justify-between gap-2">
              <span className="font-mono text-[11px] uppercase tracking-wide text-muted">
                {clusterActive ? "Cluster spend" : "Monthly spend"}
              </span>
              <button
                onClick={handleRefreshClick}
                disabled={refreshing || cooldown > 0}
                className="whitespace-nowrap rounded border border-border px-2 py-0.5 text-[11px] text-muted transition-colors hover:border-accent hover:text-text disabled:opacity-40"
              >
                {refreshing ? "Refreshing…" : cooldown > 0 ? `Refresh in ${cooldown}s` : "Refresh"}
              </button>
            </div>
            {clusterActive ? (
              <>
                <div className="font-mono text-lg text-text">{money(clusterSpend)}/mo</div>
                <div className="mt-2 text-[11px] text-muted">
                  {clusterNodes.length - 1} connected node
                  {clusterNodes.length - 1 === 1 ? "" : "s"} ·{" "}
                  {clusterNodes.filter((n) => !n.isCenter && n.costBearing).length} cost-bearing
                </div>
                <button
                  onClick={backToGalaxy}
                  className="mt-2 w-full rounded border border-border px-2 py-1 text-[11px] text-muted transition-colors hover:border-accent hover:text-text"
                >
                  ← Back to galaxy
                </button>
              </>
            ) : (
              <>
                <div className="font-mono text-lg text-text">
                  {scan ? `${money(scan.totals.monthly_spend)}/mo` : "—"}
                </div>
                <div className="mt-2 flex items-start gap-1.5 text-xs text-accent">
                  <span>⚠</span>
                  <span>
                    {scan
                      ? `${scan.totals.idle_count} idle · ${money(scan.totals.idle_monthly_waste)}/mo could be saved`
                      : "—"}
                  </span>
                </div>
              </>
            )}
            <div className="mt-2 text-[11px] text-muted">
              {scan
                ? `Last updated ${relativeTime(scan.last_updated)}`
                : loading
                  ? "Scanning…"
                  : "No data yet"}
            </div>
          </div>

          {/* non-blocking warning banner (stale cache, cooldown, or a
              region-switch fetch failure with prior data still on screen) */}
          {warning && (
            <div className="absolute left-1/2 top-4 z-20 max-w-md -translate-x-1/2 rounded-lg border border-accent/40 bg-accent/10 px-4 py-2 text-center text-xs text-accent backdrop-blur">
              {warning}
              <button
                onClick={() => setWarning(null)}
                className="ml-2 text-accent/70 hover:text-accent"
                aria-label="Dismiss warning"
              >
                ✕
              </button>
            </div>
          )}

          {/* legend -- bottom left. Full-galaxy mode: family filter
              (necessary once real scans return dozens of resources across
              15 types, roadmap Section 5). Cluster mode: a color key
              instead, since the family toggles don't apply to a
              relations-driven node set. */}
          <div className="absolute bottom-4 left-4 z-20 w-48 rounded-lg border border-border bg-surface/90 p-3 backdrop-blur">
            <button
              onClick={() => setLegendOpen((o) => !o)}
              className="mb-1 flex w-full items-center justify-between font-mono text-[11px] uppercase tracking-wide text-muted"
            >
              <span>Legend</span>
              <span>{legendOpen ? "▾" : "▸"}</span>
            </button>
            {legendOpen && clusterActive && (
              <div className="flex flex-col gap-1.5 text-[11px] text-muted">
                <span className="flex items-center gap-1.5">
                  <span className="h-2 w-2 rounded-full" style={{ background: COLOR_ACTIVE }} />
                  cost-bearing, active
                </span>
                <span className="flex items-center gap-1.5">
                  <span className="h-2 w-2 rounded-full" style={{ background: COLOR_IDLE }} />
                  cost-bearing, idle ≥7d
                </span>
                <span className="flex items-center gap-1.5">
                  <span className="h-2 w-2 rounded-full" style={{ background: COLOR_INFRA }} />
                  infra (no cost data)
                </span>
                <div className="mt-1 border-t border-border pt-1.5">
                  Click a cost-bearing node to re-center the cluster on it.
                </div>
              </div>
            )}
            {legendOpen && !clusterActive && (
              <div className="flex flex-col gap-1">
                {ALL_FAMILIES.map((f) => {
                  const on = enabledFamilies.has(f);
                  return (
                    <button
                      key={f}
                      onClick={() => toggleFamily(f)}
                      className={`flex items-center gap-2 rounded px-1.5 py-1 text-left text-xs transition-colors ${
                        on ? "text-text hover:bg-surfacealt" : "text-muted/50 hover:bg-surfacealt"
                      }`}
                    >
                      <StandaloneGlyph family={f} className={on ? "text-[#7fd7ff]" : "text-muted"} />
                      <span>{FAMILY_LABEL[f]}</span>
                      <span className="ml-auto font-mono text-muted">{familyCounts[f]}</span>
                    </button>
                  );
                })}
                <div className="mt-1.5 flex flex-wrap items-center gap-2 border-t border-border pt-1.5 text-[10px] text-muted">
                  <span className="flex items-center gap-1">
                    <span className="h-2 w-2 rounded-full" style={{ background: COLOR_ACTIVE }} />
                    active
                  </span>
                  <span className="flex items-center gap-1">
                    <span className="h-2 w-2 rounded-full" style={{ background: COLOR_IDLE }} />
                    idle ≥7d
                  </span>
                  <span className="flex items-center gap-1">
                    <span className="h-2 w-2 rounded-full" style={{ background: COLOR_UNKNOWN }} />
                    unknown
                  </span>
                </div>
              </div>
            )}
          </div>

          {/* galaxy / cluster canvas */}
          <svg
            ref={svgRef}
            viewBox="0 0 100 100"
            className="absolute inset-0 h-full w-full"
            style={{ opacity: loading && !scan ? 0 : 1, transition: "opacity 0.4s ease" }}
          >
            {clusterActive ? (
              <>
                {clusterNodes
                  .filter((n) => !n.isCenter)
                  .map((n) => {
                    const center = clusterNodes.find((c) => c.isCenter);
                    if (!center) return null;
                    const midX = (center.x + n.x) / 2;
                    const midY = (center.y + n.y) / 2;
                    const labelText = n.edgeLabel ? RELATION_LABEL_TEXT[n.edgeLabel] ?? n.edgeLabel : "";
                    return (
                      <g key={`edge:${n.key}`}>
                        <line
                          x1={center.x}
                          y1={center.y}
                          x2={n.x}
                          y2={n.y}
                          stroke={n.costBearing ? "#3d4a7a" : "#4a3d7a"}
                          strokeWidth={0.18}
                          strokeDasharray="0.6,0.6"
                        />
                        {labelText && (
                          <>
                            <rect
                              x={midX - labelText.length * 0.85}
                              y={midY - 1.6}
                              width={labelText.length * 1.7}
                              height={2.6}
                              rx={0.5}
                              fill="#05070f"
                              opacity={0.75}
                            />
                            <text
                              x={midX}
                              y={midY}
                              textAnchor="middle"
                              dominantBaseline="middle"
                              fontSize={1.7}
                              fill="#aab4d8"
                              className="font-mono"
                            >
                              {labelText}
                            </text>
                          </>
                        )}
                      </g>
                    );
                  })}
                {clusterNodes.map((n) => {
                  const idlePulsing =
                    n.costBearing && (n.resource?.idle?.idle_days ?? 0) >= IDLE_PULSE_THRESHOLD_DAYS;
                  const unknownIdle = n.costBearing && !n.resource?.idle;
                  const color = !n.costBearing
                    ? COLOR_INFRA
                    : unknownIdle
                      ? COLOR_UNKNOWN
                      : idlePulsing
                        ? COLOR_IDLE
                        : COLOR_ACTIVE;
                  const glyphSize = Math.max(1.1, n.radius * 1.05);
                  const family = n.costBearing && n.resource ? familyFor(n.resource.type) : null;
                  const clickable = n.costBearing && !n.isCenter;
                  return (
                    <g
                      key={n.key}
                      className={idlePulsing ? "galaxy-star-idle" : ""}
                      style={{ cursor: clickable ? "pointer" : "default" }}
                      onClick={clickable ? () => viewConnections(n.id) : undefined}
                    >
                      <circle cx={n.x} cy={n.y} r={n.radius * 2.1} fill={color} opacity={0.14} />
                      <circle
                        cx={n.x}
                        cy={n.y}
                        r={n.radius}
                        fill={color}
                        opacity={n.costBearing ? 0.95 : 0.85}
                        stroke={n.isCenter ? "#ffffff" : "none"}
                        strokeWidth={n.isCenter ? 0.35 : 0}
                      />
                      {family && (
                        <g transform={`translate(${n.x} ${n.y})`}>
                          <Glyph family={family} size={glyphSize} stroke="#05070f" />
                        </g>
                      )}
                      <text
                        x={n.x}
                        y={n.y + n.radius + 3}
                        textAnchor="middle"
                        fontSize={2.1}
                        fill={n.costBearing ? "#aab4d8" : "#c4b5fd"}
                        className="font-mono"
                      >
                        {n.displayLabel.length > 22
                          ? `${n.displayLabel.slice(0, 20)}…`
                          : n.displayLabel}
                      </text>
                      {!n.costBearing && (
                        <text
                          x={n.x}
                          y={n.y + n.radius + 5.4}
                          textAnchor="middle"
                          fontSize={1.6}
                          fill="#8b7cc4"
                          className="font-mono"
                        >
                          {n.kindLabel}
                        </text>
                      )}
                    </g>
                  );
                })}
              </>
            ) : (
              <>
                {relationLines.map((l) => (
                  <line
                    key={l.key}
                    x1={l.x1}
                    y1={l.y1}
                    x2={l.x2}
                    y2={l.y2}
                    stroke="#3d4a7a"
                    strokeWidth={0.15}
                    strokeDasharray="0.6,0.6"
                  />
                ))}
                {visible.map((r) => {
                  const idlePulsing = (r.idle?.idle_days ?? 0) >= IDLE_PULSE_THRESHOLD_DAYS;
                  const unknownIdle = !r.idle;
                  const unknownData = !r.idle || !r.cost;
                  const color = unknownIdle ? COLOR_UNKNOWN : idlePulsing ? COLOR_IDLE : COLOR_ACTIVE;
                  const glyphSize = Math.max(1.3, r.radius * 1.05);
                  const isSelected = r.id === selectedId;
                  // Drag-aware render position (see getRenderPos above) --
                  // falls back to the deterministic golden-angle layout
                  // position (r.x/r.y) if this star hasn't been dragged
                  // this session. Never mutates `r`/`positioned` itself.
                  const pos = getRenderPos(r.id, r.x, r.y);
                  return (
                    <g
                      key={r.id}
                      className={idlePulsing ? "galaxy-star-idle" : ""}
                      style={{ cursor: "grab", touchAction: "none" }}
                      onPointerDown={(e) => handleStarPointerDown(e, r.id)}
                      onPointerMove={(e) => handleStarPointerMove(e, r.id)}
                      onPointerUp={(e) => handleStarPointerUp(e, r.id)}
                      onPointerCancel={(e) => handleStarPointerCancel(e, r.id)}
                    >
                      <circle cx={pos.x} cy={pos.y} r={r.radius * 2.1} fill={color} opacity={0.14} />
                      <circle
                        cx={pos.x}
                        cy={pos.y}
                        r={r.radius}
                        fill={color}
                        opacity={unknownData ? 0.55 : 0.95}
                        stroke={isSelected ? "#ffffff" : "none"}
                        strokeWidth={isSelected ? 0.35 : 0}
                      />
                      <g transform={`translate(${pos.x} ${pos.y})`}>
                        <Glyph family={familyFor(r.type)} size={glyphSize} stroke="#05070f" />
                      </g>
                      <text
                        x={pos.x}
                        y={pos.y + r.radius + 3}
                        textAnchor="middle"
                        fontSize={2.1}
                        fill="#aab4d8"
                        className="font-mono"
                      >
                        {r.name.length > 22 ? `${r.name.slice(0, 20)}…` : r.name}
                      </text>
                    </g>
                  );
                })}
              </>
            )}
          </svg>

          {loading && !scan && (
            <div className="absolute inset-0 z-10 flex items-center justify-center text-sm text-muted">
              Scanning {region}… ({elapsedSeconds}s)
            </div>
          )}

          {!clusterActive && scan && visible.length === 0 && (
            <div className="absolute inset-0 z-10 flex items-center justify-center px-8 text-center text-sm text-muted">
              {scan.resources.length === 0
                ? `No supported resources found in ${scan.region}.`
                : "All resource families are filtered out -- toggle the legend to bring them back."}
            </div>
          )}

          {/* detail side panel -- non-modal, keeps the galaxy/cluster canvas
              visible underneath in either mode */}
          <div
            className="absolute right-0 top-0 z-30 h-full w-full overflow-y-auto border-l border-border bg-[#0a0e1f]/97 backdrop-blur sm:w-96"
            style={{
              transform: selected ? "translateX(0)" : "translateX(100%)",
              transition: "transform 0.3s ease",
            }}
          >
            {selected && (
              <DetailPanel
                resource={selected}
                region={scan?.region ?? region}
                onClose={() => setSelectedId(null)}
                onViewConnections={viewConnections}
              />
            )}
          </div>
        </div>
      )}
    </div>
  );
}

