import { getSession } from "next-auth/react";

const API_BASE_URL = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";

// Every call to the FastAPI backend must carry the short-lived apiToken
// minted by NextAuth (see lib/auth.ts) — FastAPI independently verifies it
// on every route (see opspilot-backend/app/core/security.py). The
// middleware-based /login redirect is a UX nicety only; this header is the
// actual credential the backend checks.
async function authHeaders(): Promise<Record<string, string>> {
  const session = await getSession();
  return session?.apiToken ? { Authorization: `Bearer ${session.apiToken}` } : {};
}

export interface TraceStep {
  type: "tool_call" | "tool_result" | "message";
  tool?: string;
  arguments?: unknown;
  output?: unknown;
  text?: string;
}

export interface ChatResponse {
  reply: string;
  provider_used: string;
  trace: TraceStep[];
}

export interface Ec2Instance {
  instance_id: string;
  instance_type: string;
  state: string;
  availability_zone: string;
  public_ip: string | null;
  private_ip: string | null;
  launch_time: string | null;
  tags: Record<string, string>;
}

export interface MetricDatapoint {
  timestamp: string;
  average: number | null;
  maximum: number | null;
  unit: string;
}

export interface CpuUtilizationSummary {
  instance_id: string;
  lookback_hours: number;
  datapoints: MetricDatapoint[];
  average_cpu_percent: number | null;
  max_cpu_percent: number | null;
  breached_80_percent: boolean;
}

export interface Ec2ResourceCard {
  instance: Ec2Instance;
  cpu: CpuUtilizationSummary | null;
}

export interface ResourcesResponse {
  ec2: Ec2ResourceCard[];
}

export interface CloudTrailEvent {
  event_name: string;
  event_time: string;
  username: string | null;
}

export interface LambdaCard {
  functions: { name: string; runtime: string | null; last_modified: string | null }[];
  count: number;
}

export interface S3Card {
  buckets: { name: string; creation_date: string | null }[];
  count: number;
}

export interface DynamoCard {
  tables: { name: string; status: string; item_count: number | null }[];
  count: number;
}

export interface SnsCard {
  topics: { topic_arn: string; name: string }[];
  count: number;
}

export interface RdsCard {
  instances: { identifier: string; engine: string; instance_class: string; status: string }[];
  count: number;
}

export interface DashboardOverview {
  lambda_functions: LambdaCard;
  s3: S3Card;
  dynamodb: DynamoCard;
  sns: SnsCard;
  rds: RdsCard;
  cloudtrail: { events: CloudTrailEvent[] };
}

export interface Investigation {
  id: string;
  question: string;
  trace_summary: string;
  conclusion: string;
  created_at: string;
}

export interface InvestigationList {
  investigations: Investigation[];
}

export interface SimilarInvestigationResult extends Investigation {
  similarity: number;
}

export interface McpToolInfo {
  name: string;
  description: string | null;
}

export interface McpServerInfo {
  server_name: string;
  transport: string;
  tool_count: number;
  tools: McpToolInfo[];
}

// MCP access-token lifecycle (roadmap 3.6, Settings -> MCP Access). The
// plaintext `token` only ever appears in McpTokenGenerateResponse, the
// one-time response to POST /mcp/token/generate — never persisted client-
// side, never present in McpTokenStatus.
export interface McpTokenGenerateResponse {
  token: string;
  created_at: string;
  warning: string;
}

export interface McpTokenRevokeResponse {
  revoked: boolean;
  message: string;
}

export interface McpTokenStatus {
  has_active_token: boolean;
  created_at: string | null;
  revoked_at: string | null;
}

// Audit Log tab (roadmap Section 5) -- mirrors
// opspilot-backend/app/models/audit_log.py field-for-field, snake_case.
// `action` is intentionally a plain string on the backend (not a Literal
// union) so new action types can be added there without a model change --
// mirrored the same way here rather than typing a closed union that would
// silently go stale. See docs/SECURITY.md Section 7 for the actual current
// coverage (four action types) before building any UI around this.
export interface AuditLogEntry {
  id: string;
  action: string;
  // Trust level depends on `action` -- verified admin identity for the two
  // mcp_token_* actions, raw unauthenticated form input for login_failed
  // specifically. See app/models/audit_log.py's docstring (backend) and
  // components/AuditLogPanel.tsx (frontend) for the full framing. Do not
  // assume this is always a verified identity.
  actor_email: string;
  created_at: string;
  detail: string | null;
}

export interface AuditLogEntryList {
  entries: AuditLogEntry[];
}

// Connected-account identity (roadmap Section 5's Settings tab). Mirrors
// opspilot-backend/app/models/account.py exactly -- deliberately just
// account_id + region, no IAM role ARN (this app has no assumed-role
// concept to show one for; see docs/SECURITY.md Section 3).
export interface AccountIdentity {
  account_id: string;
  region: string;
}

// ---------------------------------------------------------------------------
// Galaxy dashboard (roadmap Section 5 / 3.3 / 3.4) — field names below mirror
// the `data-schema` skill and opspilot-backend/app/models/scan.py exactly,
// snake_case end to end. Do not rename these; extend the skill file first if
// a new field is ever needed (see skill's "who produces/consumes what").
// ---------------------------------------------------------------------------

export interface CostDateRange {
  start: string;
  end: string;
}

export interface CostEstimate {
  resource_id: string;
  resource_type: string;
  date_range: CostDateRange;
  // "list_price" = AWS Pricing API on-demand rate. "billed" = Cost Explorer
  // actual billed cost (not yet implemented backend-side). Always label
  // which one is shown (roadmap 3.2) — never present list price as billed.
  method: "list_price" | "billed";
  hourly_rate: number | null;
  // Drives star/bubble sizing (roadmap 3.1a) — NEVER incurred_so_far.
  projected_monthly: number;
  incurred_so_far: number;
}

export interface IdleCheckResult {
  resource_id: string;
  resource_type: string;
  window_days: number;
  is_idle: boolean;
  idle_since: string | null;
  idle_days: number;
  younger_than_window: boolean;
  idle_since_is_estimated: boolean;
}

export interface ResourceHealth {
  primary_metric: string;
  // Always null from scan_region() today — populating the live value is a
  // separate future tool (get_resource_health, roadmap 3.8), not this scan.
  primary_metric_value: number | null;
  status: string;
}

// Edge semantics shown on the connecting line (roadmap 3.7).
export type RelationLabel = "attached" | "secured_by" | "in" | "routed_by" | "assumes";

// The 15 TYPE_CODES (data-schema skill) -- cost-bearing relation targets,
// also present in this scan's `resources[]` array, looked up by id.
export type CostBearingKind =
  | "ec2"
  | "ebs"
  | "rds"
  | "eip"
  | "elb"
  | "lambda"
  | "nat_gateway"
  | "dynamodb"
  | "elasticache"
  | "sagemaker"
  | "redshift"
  | "api_gateway"
  | "cloudfront"
  | "opensearch"
  | "kinesis";

// Non-cost-bearing infra relation targets -- never present in `resources[]`,
// carry only an id (data-schema skill's INFRA_KINDS).
export type InfraKind = "security_group" | "subnet" | "vpc" | "iam_role";

export type RelationKind = CostBearingKind | InfraKind;

export interface RelationLink {
  id: string;
  label: RelationLabel;
  // Target node's type — one of the 15 TYPE_CODES (cost-bearing, looked up
  // in this scan's `resources[]`) or security_group | subnet | vpc |
  // iam_role (non-cost-bearing infra, id-only).
  kind: RelationKind;
}

export interface GalaxyResource {
  id: string;
  name: string;
  type: string;
  region: string;
  // Nullable at the per-resource level: a single resource's cost/idle
  // lookup can fail without dropping the resource from the scan. null
  // means "this lookup failed for this resource," not "not applicable."
  cost: CostEstimate | null;
  idle: IdleCheckResult | null;
  health: ResourceHealth;
  created_at: string | null;
  // Roadmap 3.7. Populated by scan_region() for ec2, ebs, rds, elb, lambda,
  // eip, nat_gateway, elasticache, redshift, opensearch. Always [] for
  // dynamodb, sagemaker, api_gateway, cloudfront, kinesis (no VPC/SG/IAM
  // linkage available without an extra call -- a documented gap, not a
  // bug) and for any individual resource with no such linkage.
  relations: RelationLink[];
}

export interface ScanTotals {
  monthly_spend: number;
  idle_count: number;
  idle_monthly_waste: number;
}

export interface ScanResponse {
  region: string;
  last_updated: string;
  resources: GalaxyResource[];
  totals: ScanTotals;
  // null on every normal fresh/cached response. Set only when this payload
  // is stale cache served after a failed rescan (roadmap 3.4) — surface as
  // a non-blocking warning, never treat as "no data."
  error: string | null;
}

export interface RegionsResponse {
  regions: string[];
}

class ApiError extends Error {
  constructor(message: string, public status?: number) {
    super(message);
    this.name = "ApiError";
  }
}

// Distinct from ApiError: a 429 from GET /resources/scan means the
// roadmap 3.4 refresh debounce/cooldown rejected an explicit force=true
// rescan, not a real failure. The response still carries the last-good
// cached scan in its body when one exists (Retry-After header has the
// seconds to wait) — callers should keep showing that cached data rather
// than treating this like a hard error.
export class ScanCooldownError extends Error {
  constructor(public retryAfterSeconds: number, public cached: ScanResponse | null) {
    super(`Refresh is cooling down — retry in ${retryAfterSeconds}s.`);
    this.name = "ScanCooldownError";
  }
}

export async function sendChatMessage(message: string): Promise<ChatResponse> {
  const res = await fetch(`${API_BASE_URL}/chat`, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...(await authHeaders()) },
    body: JSON.stringify({ message }),
  });

  if (!res.ok) {
    const body = await res.json().catch(() => null);
    throw new ApiError(body?.detail ?? `Request failed with status ${res.status}`, res.status);
  }

  return res.json();
}

export async function getEc2Resources(): Promise<ResourcesResponse> {
  const res = await fetch(`${API_BASE_URL}/resources/ec2`, {
    cache: "no-store",
    headers: await authHeaders(),
  });

  if (!res.ok) {
    throw new ApiError(`Request failed with status ${res.status}`, res.status);
  }

  return res.json();
}

export async function getDashboardOverview(): Promise<DashboardOverview> {
  const res = await fetch(`${API_BASE_URL}/resources/overview`, {
    cache: "no-store",
    headers: await authHeaders(),
  });

  if (!res.ok) {
    throw new ApiError(`Request failed with status ${res.status}`, res.status);
  }

  return res.json();
}

export async function getInvestigations(): Promise<InvestigationList> {
  const res = await fetch(`${API_BASE_URL}/investigations`, {
    cache: "no-store",
    headers: await authHeaders(),
  });

  if (!res.ok) {
    const body = await res.json().catch(() => null);
    throw new ApiError(body?.detail ?? `Request failed with status ${res.status}`, res.status);
  }

  return res.json();
}

export async function getMcpServerInfo(): Promise<McpServerInfo> {
  const res = await fetch(`${API_BASE_URL}/mcp/tools`, {
    cache: "no-store",
    headers: await authHeaders(),
  });

  if (!res.ok) {
    throw new ApiError(`Request failed with status ${res.status}`, res.status);
  }

  return res.json();
}

// Audit Log tab (roadmap Section 5), backed by GET /audit-log --
// app/api/routes/audit_log.py caps `limit` server-side at 200 (default 50);
// no client-side cap duplicated here, the backend's Query(..., le=200)
// already rejects an out-of-range value.
export async function getAuditLog(limit?: number): Promise<AuditLogEntryList> {
  const params = limit != null ? `?${new URLSearchParams({ limit: String(limit) }).toString()}` : "";
  const res = await fetch(`${API_BASE_URL}/audit-log${params}`, {
    cache: "no-store",
    headers: await authHeaders(),
  });

  if (!res.ok) {
    const body = await res.json().catch(() => null);
    throw new ApiError(body?.detail ?? `Request failed with status ${res.status}`, res.status);
  }

  return res.json();
}

// Connected-account identity (roadmap Section 5, Settings -> Connected
// account), backed by GET /aws/account.
export async function getConnectedAccount(): Promise<AccountIdentity> {
  const res = await fetch(`${API_BASE_URL}/aws/account`, {
    cache: "no-store",
    headers: await authHeaders(),
  });

  if (!res.ok) {
    const body = await res.json().catch(() => null);
    throw new ApiError(body?.detail ?? `Request failed with status ${res.status}`, res.status);
  }

  return res.json();
}

// --- MCP access token lifecycle (roadmap 3.6, Settings -> MCP Access) ------

export async function getMcpTokenStatus(): Promise<McpTokenStatus> {
  const res = await fetch(`${API_BASE_URL}/mcp/token/status`, {
    cache: "no-store",
    headers: await authHeaders(),
  });

  if (!res.ok) {
    const body = await res.json().catch(() => null);
    throw new ApiError(body?.detail ?? `Request failed with status ${res.status}`, res.status);
  }

  return res.json();
}

export async function generateMcpToken(): Promise<McpTokenGenerateResponse> {
  const res = await fetch(`${API_BASE_URL}/mcp/token/generate`, {
    method: "POST",
    headers: await authHeaders(),
  });

  if (!res.ok) {
    const body = await res.json().catch(() => null);
    throw new ApiError(body?.detail ?? `Request failed with status ${res.status}`, res.status);
  }

  return res.json();
}

export async function revokeMcpToken(): Promise<McpTokenRevokeResponse> {
  const res = await fetch(`${API_BASE_URL}/mcp/token/revoke`, {
    method: "POST",
    headers: await authHeaders(),
  });

  if (!res.ok) {
    const body = await res.json().catch(() => null);
    throw new ApiError(body?.detail ?? `Request failed with status ${res.status}`, res.status);
  }

  return res.json();
}

// Enabled-region list for the galaxy view's region selector (roadmap 3.3),
// backed by ec2:DescribeRegions. Cheap/infrequent — not cached client-side.
export async function getRegions(): Promise<RegionsResponse> {
  const res = await fetch(`${API_BASE_URL}/resources/regions`, {
    cache: "no-store",
    headers: await authHeaders(),
  });

  if (!res.ok) {
    const body = await res.json().catch(() => null);
    throw new ApiError(body?.detail ?? `Request failed with status ${res.status}`, res.status);
  }

  return res.json();
}

// Region-wide scan behind the galaxy view (roadmap 3.3/3.4).
// force=false -> cache-or-scan (cheap, used on load/region switch).
// force=true  -> explicit user refresh, subject to the backend's
// COOLDOWN_SECONDS debounce (see ScanCooldownError below).
//
// Response codes per the data-schema skill / app/api/routes/resources.py:
//  200 -> normal (check `.error`: non-null means stale-but-usable cache
//         served after a failed rescan — caller shows a non-blocking
//         warning, keeps rendering `.resources`).
//  429 -> cooldown/in-progress rejection of a force=true refresh; body is
//         either the still-good cached ScanResponse or a plain {detail}
//         if no cache exists yet for this region. Retry-After header has
//         the seconds to wait.
//  400 -> region not recognized as one of this account's enabled regions.
//  502 -> scan failed and there is truly no prior cache for this region
//         (the one case with nothing to show).
// Bounded client-side timeout for the scan fetch (roadmap Section 5 bug
// fix). Without this, a genuine backend hang (crash, network partition,
// etc.) leaves the caller sitting on loading=true forever with no way to
// reach the existing hardError/warning UI, since nothing ever
// rejects/resolves. Measured live in this environment: a first-ever,
// never-cached scan of a region with only 5 resources took ~125s (15
// sequential resource-type collectors, each doing list + per-resource
// CloudWatch/Pricing calls, at ~2-4.6s per boto3 call in this environment's
// unusually high AWS latency) -- and that's now non-blocking server-side
// (run_in_threadpool) but still genuinely takes that long end to end. 4
// minutes gives ~115s of headroom above that observed figure so a normal
// slow-but-working scan (or a slightly larger one) doesn't falsely trip the
// timeout, while still bounding a truly stuck request.
const SCAN_TIMEOUT_MS = 4 * 60 * 1000;

export async function scanRegion(region: string, force = false): Promise<ScanResponse> {
  const params = new URLSearchParams({ region, force: String(force) });
  const headers = await authHeaders();

  const controller = new AbortController();
  const timeoutId = setTimeout(() => controller.abort(), SCAN_TIMEOUT_MS);

  let res: Response;
  try {
    res = await fetch(`${API_BASE_URL}/resources/scan?${params.toString()}`, {
      cache: "no-store",
      headers,
      signal: controller.signal,
    });
  } catch (err) {
    if (err instanceof DOMException && err.name === "AbortError") {
      throw new ApiError(
        `Scan of ${region} timed out after ${Math.round(SCAN_TIMEOUT_MS / 1000)}s -- the backend may be stuck. Try again.`
      );
    }
    throw err;
  } finally {
    clearTimeout(timeoutId);
  }

  if (res.status === 429) {
    const retryAfterHeader = res.headers.get("Retry-After");
    const retryAfterSeconds = retryAfterHeader ? Number(retryAfterHeader) : 45;
    const body = await res.json().catch(() => null);
    const cached = body && Array.isArray(body.resources) ? (body as ScanResponse) : null;
    throw new ScanCooldownError(
      Number.isFinite(retryAfterSeconds) ? retryAfterSeconds : 45,
      cached
    );
  }

  if (!res.ok) {
    const body = await res.json().catch(() => null);
    throw new ApiError(body?.detail ?? `Request failed with status ${res.status}`, res.status);
  }

  return res.json();
}
