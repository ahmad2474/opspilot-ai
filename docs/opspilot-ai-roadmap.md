# OpsPilot AI — Development Roadmap & Spec

Compiled reference for continuing development. This document protects the existing
core concept while scoping every new addition discussed. Nothing here replaces
existing functionality — everything is additive.

---

## 1. Core concept (already built — do not remove or rearchitect)

- Agentic reasoning loop: hypothesis → tool call → confirm/contradict → adjust → conclude
- Investigation reasoning trace shown to the user, not just a final answer
- RAG-based investigation memory (embeds past investigations, recalls by cosine similarity — no vector DB needed)
- MCP server exposing the same tools externally (e.g. Claude Desktop) via stdio JSON-RPC
- Layered architecture: `tools/` → `services/` → `aws/`
  - Investigation logic is unit-testable by mocking one function, independent of LLM availability
  - Dashboard, MCP server, and agent tools all call the same service layer — guaranteed to agree

**Rule for all new work below: every new capability is a new tool/service slotting into
this existing layering, not a parallel system.**

---

## 2. Scope decision: single account for now

- Multi-account (cross-account `sts:AssumeRole`, `accounts` table, account switcher) is
  **deferred**, not removed from the plan — architecture should stay parameterizable so it
  can be added later without a rewrite.
- Build and demo against one connected AWS account.

### 2a. Scope decision: 15 supported resource types

**Tier 1 — in scope, build all 15 using the same pattern (CloudWatch metric window +
Pricing/Cost Explorer for cost):**

| Service | Idle signal |
|---|---|
| EC2 | `CPUUtilization` ≈ 0% AND `NetworkIn`/`NetworkOut` ≈ 0 for every day in window |
| EBS | Not attached, or attached with `VolumeReadOps`/`VolumeWriteOps` ≈ 0 |
| RDS | `DatabaseConnections` = 0 for every day in window |
| Elastic IP | Not associated with a running instance/ENI |
| Load Balancer (ELB) | `RequestCount` = 0 for every day in window |
| Lambda | Invocation count = 0 over window |
| NAT Gateway | `BytesOutToDestination`/`BytesInFromSource` ≈ 0 |
| DynamoDB | Consumed read/write capacity ≈ 0 (provisioned tables) |
| ElastiCache | `CurrConnections` ≈ 0 |
| SageMaker endpoint | Invocation count = 0 (often the biggest silent cost — runs 24/7 by default) |
| Redshift cluster | `DatabaseConnections` ≈ 0 |
| API Gateway | `Count` (requests) = 0 |
| CloudFront distribution | `Requests` = 0 |
| OpenSearch domain | Search/index rate ≈ 0 |
| Kinesis stream | `IncomingRecords` ≈ 0 |

Each new type is a per-service checklist item (idle signal + cost calc), not a redesign —
same `check_idle`/`estimate_cost` tool pattern from Section 3.1/3.2, parameterized per type.

**Tier 2 — deferred, genuinely different shape, not just "harder":**
- S3 (storage, not compute — "waste" means lifecycle/no-access-in-N-days, not CPU-idle)
- ECS/EKS (container-level granularity, needs per-task not per-cluster idle signal)
- SQS/SNS (near-zero cost regardless, low payoff for the complexity)

These need their own idle *definition* before they're worth building, so they're explicitly
phase 2, not part of the 15.

---

## 3. New capabilities to build (additive)

### 3.1 Idle detection
- New tool: `check_idle(resource, days)`
- Full 15-resource-type idle-signal table is in Section 2a — this tool is parameterized
  per type, not rewritten per type.
- Require **every** daily datapoint in the window below threshold (not just the average) —
  an average can hide a burst on day 3 followed by idle days after.
- "Idle since" date = walk backward through daily datapoints to the first day that breaks
  the idle condition, +1 day.
- Edge case: if a resource is younger than the requested window (e.g. launched 5 days ago),
  report "idle since launch," never a fabricated longer window.
- Build order: **EC2 first**, prove the concept, then extend across the remaining 14 types
  in Section 2a — each one is an incremental addition to the same tool, not a new system.

### 3.1a Star/bubble sizing rule (applies to galaxy + cluster views)
- Two distinct numbers exist per resource: **projected monthly cost** (rate × full month,
  based on instance type/size) vs. **cost incurred so far** (actual billed amount since
  creation or since going idle).
- **Size the star/bubble by projected monthly cost, not cost-incurred-so-far.** A large,
  expensive instance created 2 hours ago must render as a large star — sizing by
  incurred-so-far would hide it as a tiny star and understate the risk.
- Detail panel shows both numbers side by side, e.g.
  `"Projected: $210/mo · Incurred so far: $2.10 (created 6 hours ago)"` — never collapse
  them into one misleading figure.
- Reinforces the existing edge case above: a resource younger than the idle window is
  never reported as "idle N days" beyond its actual age.

### 3.2 Cost calculation
- New tool: `estimate_cost(resource, date_range)`
- Two approaches:
  - **Pricing API** (`get_products`) — list price × hours in window. Free, no tagging
    required, but an estimate (ignores savings plans/reserved pricing). Good enough for demo.
  - **Cost Explorer API** (`get_cost_and_usage`) — actual billed cost, filtered by resource
    ID via cost allocation tags. More accurate, needed for a production-honest number.
  - Verify current Cost Explorer API request pricing before relying on exact figures.
- Combine with idle detection: `"idle since Jun 20 (20 days) · $34.20 incurred in that window"`
- Label in the UI which method (list price vs. billed) is being shown, so it's never misleading.

### 3.3 Region-wide scanning
- Loop the existing per-resource service calls across all enabled regions
  (`describe_regions`), not just one.
- Cache per-region results with a TTL — don't rescan live on every load (see refresh/caching below).

### 3.4 Refresh & caching behavior
- No background polling. Data lives in frontend state; a manual **refresh button** triggers
  one backend call.
- Backend caches the last successful scan (per region) with a `last_updated` timestamp.
- On refresh: rescan, overwrite cache, return new data + timestamp.
- On failure (rate limit, AWS error): **keep serving last good cached data**, show an error
  toast — never blank the dashboard.
- Show staleness in UI ("Last updated 4 minutes ago").
- Debounce/cooldown on the refresh button to prevent accidental over-calling billed APIs
  (`GetMetricData`, Cost Explorer).
- Refresh only rescans the **currently selected** region, not all regions.

### 3.5 Login-based authentication — build FIRST, gates everything else
- Every route checks for a valid session on load; no session → redirect to `/login` before
  any dashboard, data, or API call happens (Next.js `middleware.ts`).
- FastAPI mirrors this: every API route requires a valid session token server-side too —
  the frontend redirect is not the only protection.
- NextAuth.js (Auth.js) for session handling; email/password or OAuth (Google/GitHub).
- Single admin-style login is fine while scope is one account.
- Rationale for building this first: every feature built afterward is already behind the
  wall, instead of retrofitting auth around finished features.

### 3.6 MCP server — secure token auth (build now, not deferred)
- MCP is a **parallel exposure surface** to the same `services/`/`aws/` layer, not a
  dashboard feature — it doesn't get a top-level tab.
- Every new tool (idle check, cost estimate) must be exposed through **both** the
  dashboard's API and the MCP server, since they're two front doors to one backend.
- **Token required even on localhost** — build the check in now so nothing changes later
  if hosted elsewhere:
  - Settings → MCP Access section: "Generate token" (shown once, stored **hashed** in
    Postgres, never plaintext) and "Revoke" button
  - MCP server checks the token on every connection handshake before allowing any tool
    call or AWS role assumption — reject immediately if missing/invalid
  - Token generation/revocation writes an entry to the Audit Log automatically
  - Rate-limit and log the MCP path the same as the HTTP API
- Localhost-only note: while it's just you testing locally, the connection never leaves
  the machine, so this is already safe — the token requirement is there so nothing needs
  to change when/if this is ever hosted for other users to connect their own Claude Desktop.

### 3.7 Connected-resources bubble map ("View connections") — built in prototype
- New field per resource: `relations: [{ id, label, kind }]` — populated from data already
  returned by existing `Describe*` calls (security group IDs, subnet ID, VPC, attached
  volumes, IAM role) — **no new AWS calls required**, just data-shaping in the `aws/` layer.
- Entry point: "View connections" button in the resource detail panel (alongside "Ask
  about this resource"), shown only when the resource has relations data.
- Behavior: re-focuses the galaxy view into a cluster layout — selected resource centered,
  related nodes (attached volumes, security groups, subnets, VPC, load balancer, IAM role)
  orbiting around it, connected by labeled lines (`attached`, `secured by`, `in`, `routed by`,
  `assumes`).
- Sizing consistency: connected **resources** (cost-bearing) size the same way as the main
  galaxy (by projected cost); connected **infrastructure nodes** (security group, subnet,
  VPC, IAM role — non-cost-bearing) render smaller and in a distinct color (violet) so cost
  vs. non-cost nodes are never visually confused.
- Cluster spend readout: sums the center resource + connected cost-bearing resources.
- Clicking a connected resource jumps to *that* resource's own detail panel — clusters are
  hoppable, not a dead-end view.
- "Back to galaxy" returns to the full star field.
- Real value beyond visuals: answers "if I terminate this, what else is affected?" — a
  security group or subnet shared across many resources becomes visually obvious as a hub.

### 3.8 Chat capability & scope contract
The chat (floating launcher) uses the same reasoning loop and tool layer as the rest of
the app — these are new tools it needs, not a separate system.

**New tools required**
- `list_resources(filters?)` — full inventory with Name tags; powers both count queries
  ("how many resources are running?") and list queries ("list them")
- `get_resource_health(id)` — status checks, CloudWatch health signals, running/stopped state
- `get_resource_age(id)` — days running, from `LaunchTime` (EC2) or equivalent creation
  timestamp per resource type
- `estimate_instance_cost(instance_type, region)` — hypothetical cost lookup via Pricing
  API, independent of any existing resource (for "what would a big EC2 cost" style questions)

**Required behaviors**
- **Count queries** ("how many resources running?"): call `list_resources`, return a real
  number with a useful breakdown, not a bare count — e.g. "15 total — 11 active, 4 idle."
- **List queries** ("list them"): table using each resource's **Name tag** (not just the
  AWS resource ID), grouped by type, sorted alphabetically within each group. Edge case:
  if a resource has no Name tag, fall back to showing its resource ID with a note — never
  silently omit untagged resources.
- **Single-resource health/uptime queries**: pull real signals (status checks, CPU, launch
  time) via `get_resource_health`/`get_resource_age` — reuses the same investigation-trace
  pattern as the galaxy detail panel, just triggered from chat.
- **Hypothetical cost queries** (e.g. "how much would a big EC2 machine cost"): don't stall
  on ambiguity — give 2–3 concrete reference instance types with their monthly on-demand
  rate, and offer to narrow down further if given a specific size/workload.
- **Out-of-scope queries**: check scope *before* attempting to reason. If the question isn't
  about this AWS account's resources, cost, or health, respond calmly and redirect — no cold
  refusal. E.g. "That's outside what I can help with here — I'm set up to answer questions
  about your AWS resources, costs, and health. Ask me anything about what's running in this
  account!"

---

## 4. Security hardening (wraps all of the above)

- **Least privilege**: every IAM role is read-only (`Describe*`/`List*`/`Get*` only) — no
  write access exists until the write-action/approval layer is explicitly built later.
- **Cross-account role assumption** (when multi-account is eventually built): IAM role +
  randomly generated **external ID** per connection, never a fixed value in code.
- **No long-lived credentials stored anywhere.** Assumed-role sessions short-lived
  (15–60 min), re-assumed per request/session.
- **Secrets never in the repo**:
  - Only `.env.example` with placeholders committed
  - Real `.env*` files gitignored
  - GitHub secret scanning + push protection enabled (free for public repos)
  - Anything ever accidentally committed is rotated immediately — `git revert` does not
    remove it from history
- **Audit log** of every action (dashboard actions, agent flags, MCP calls, token
  generation/revocation) tied to a real logged-in user.
- **`SECURITY.md`** documenting all of the above publicly — read-only by default, no
  stored credentials, per-user MCP tokens, external-ID-scoped role assumption, and a
  responsible-disclosure contact. A stated security model is a pitch asset, not just protection.

---

## 5. UI — locked in

Reference prototype: `aws-galaxy-dashboard.jsx` (React artifact already built).

**Layout**
- Top bar: region selector (left) · nav tabs (center) · user icon (right)
- Tabs: **Galaxy** (default) · **Idle Resources** · **Investigations** · **Cost Overview** ·
  **Audit Log** · **Settings**
- Floating chat launcher (bottom-right), available from every tab, slides open a panel —
  not a top-level tab
- Contextual scoping: clicking a star opens the resource detail panel (with the existing
  investigation trace) → "Ask about this resource" button opens chat pre-scoped to that resource

**Galaxy view specifics**
- Star size = **projected** monthly cost (see 3.1a — never sized by incurred-so-far),
  color/pulse = idle status (cyan = active, pulsing amber = idle ≥7 days)
- Dashed constellation lines connect related resources (e.g. EC2 ↔ its attached EBS volume)
- HUD (top-right in galaxy view): total monthly spend + idle waste found
- Click-to-inspect side panel, not a modal — keeps spatial/galaxy context visible
- "View connections" button (when relations data exists) → cluster/bubble-map view (3.7),
  with its own back button and cluster-spend HUD

**Per-resource-type visual identity (needed now that scope is 15 types, not 5)**
- Idle/active color coding (amber pulse vs. cyan) stays the universal status signal across
  all 15 types — don't overload color with type too.
- Distinguish *type* via a small icon/glyph rendered inside or beside each star, plus a
  legend toggle so the galaxy doesn't become illegible at higher resource counts:

  | Family | Suggested glyph |
  |---|---|
  | Compute (EC2, Lambda, SageMaker) | small chip/cpu icon |
  | Storage (EBS) | disk icon |
  | Database (RDS, DynamoDB, ElastiCache, Redshift, OpenSearch) | cylinder/db icon |
  | Networking (ELB, NAT Gateway, EIP, API Gateway, CloudFront) | node/link icon |
  | Streaming (Kinesis) | wave/stream icon |

- A **legend / filter panel** (toggle families on/off) becomes worth adding once real
  accounts return dozens of resources across 15 types — treat as part of this same UI pass,
  not a separate feature.

**Settings tab**
- Connected account + IAM role ARN
- Security posture summary
- MCP Access section: generate/revoke token, list of exposed tools

**Refresh**
- Manual refresh button + "last updated" timestamp + graceful fallback to cached data on failure

---

## 6. Suggested build order (each step fully working/demoable before the next)

1. Login-based auth (NextAuth + FastAPI session validation) — gates the whole app
2. Idle detection + cost calc for EC2 only
3. Extend idle + cost calc across the remaining 14 resource types (Section 2a) —
   EBS, RDS, EIP, ELB first (already-scoped types), then Lambda, NAT Gateway, DynamoDB,
   ElastiCache, SageMaker, Redshift, API Gateway, CloudFront, OpenSearch, Kinesis
4. Region-wide scanning
5. Galaxy UI wired to real data (replacing mock array) + refresh/cache behavior +
   per-type icon/legend system (needed once 15 types are live)
6. MCP token-based auth wired in
7. Security hardening pass + `SECURITY.md`
8. Write-action/approval layer (stop/terminate idle resource, dry-run + explicit
   confirmation, tied to logged-in user via audit log) — build last, most sensitive

---

## 7. Suggested subagents for building this (Claude Code)

Create these under `.claude/agents/` so the build order in Section 6 can actually be
delegated, not just read:

| Subagent | Tools | Job |
|---|---|---|
| `auth-agent` | Read, Edit, Bash | Step 1 — NextAuth + FastAPI session middleware |
| `backend-agent` | Read, Edit, Bash | Steps 2–4 — idle detection, cost calc, region scanning tools/services |
| `frontend-agent` | Read, Edit, Bash | Step 5 — wires galaxy UI to real API responses, refresh/cache UI states |
| `mcp-agent` | Read, Edit, Bash | Step 6 — token auth on the MCP server, Settings UI wiring |
| `security-reviewer` | Read, Grep, Glob, Bash | Runs after every step above — least privilege, no leaked secrets, `SECURITY.md` |
| `code-reviewer` | Read, Grep, Glob, Bash | Runs after every step above — style, error handling, test coverage |

Suggested skill to add: `data-schema` — documents the resource/relations JSON shape (as
used in the galaxy + cluster views) so every agent writes to the same contract without
you repeating it per prompt.

Each subagent should only touch its own layer (`tools/`/`services/`/`aws/` for backend,
components for frontend) so they can run without stepping on each other's files — worktrees
if you want to try any of them in parallel later.

---

## 8. Deferred (documented so it isn't lost, not being built now)

- Multi-account support (cross-account role assumption, `accounts` table, account switcher)
- Write-action/approval layer (scoped above, but intentionally last)
