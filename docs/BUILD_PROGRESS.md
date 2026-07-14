# OpsPilot AI — Build Progress

Tracks roadmap `docs/opspilot-ai-roadmap.md` Section 6 build order across sessions. Update
this file every time a step changes state so work can resume without re-deriving context.

| # | Step | Owner | Status |
|---|---|---|---|
| 1 | Login-based auth (NextAuth + FastAPI session validation) | auth-agent | done |
| 2 | Idle detection + cost calc — EC2 only | backend-agent | done |
| 3 | Extend idle + cost calc to remaining 14 resource types | backend-agent | done |
| 4 | Region-wide scanning | backend-agent | done |
| 5 | Galaxy UI wired to real data + refresh/cache + icon/legend system + connected-resources cluster view (3.7), **plus roadmap Section 3.8 chat tools** (folded in per user decision, 2026-07-11 — 3.8 had no assigned step number in the original build order) | frontend-agent (UI + 3.7 cluster view) + backend-agent (3.8 tools + 3.7 relations data) | done |
| 6 | MCP token-based auth | mcp-agent | done |
| 7 | Security hardening pass + SECURITY.md | security-reviewer (audit) + owning agents | done |
| 8 | Write-action/approval layer | TBD — confirm UX with user before delegating | not started |

## Decisions made
- **Auth method (Step 1)**: email/password, single admin user via env vars (not OAuth). User
  choice, 2026-07-11.
- **MCP token storage (Step 6)**: roadmap Section 3.6 literally says "stored hashed in
  Postgres" — this app has no Postgres anywhere (no driver, no connection string, no ORM;
  the only persistent datastore in the whole project is DynamoDB, used for investigation
  memory). Stopped and asked the user rather than silently deviating from the roadmap's
  literal wording or introducing a brand-new database dependency unprompted. User asked which
  option was cost-free; DynamoDB is (reuses the already-connected AWS account, stays inside
  DynamoDB's permanent free tier at this scale — one token row + a handful of audit-log rows
  for a single-admin app) where Postgres would require either a non-deployable local-only
  Docker instance or a real hosted instance with ongoing cost. **Decision: DynamoDB**, not
  Postgres — a new `mcp_tokens` table (hashed token, created_at, revoked_at) and a minimal
  `audit_log` table (token generate/revoke events only, for now — Section 4/Step 7 will
  formalize/extend it to cover every action type). User choice, 2026-07-11.
- **Static AWS IAM keys vs. assumed-role sessions (Step 7)**: roadmap Section 4 says "no
  long-lived credentials stored anywhere... assumed-role sessions short-lived (15-60 min)."
  This app has used a static IAM user's `AWS_ACCESS_KEY_ID`/`AWS_SECRET_ACCESS_KEY` (in
  `opspilot-backend/.env`, read by `app/aws/client.py`) since Step 1, re-flagged as deferred in
  every security review since. Stopped and asked rather than either silently fixing it
  (real infrastructure change requiring the user to create a new IAM role) or silently leaving
  it undocumented. **Decision: keep the static key, document it as an accepted limitation, do
  not fix now** — this is currently a local, single-admin, not-internet-facing demo, and
  short-lived assumed-role sessions add real AWS-account setup complexity for a threat model
  that doesn't yet apply. Explicit condition attached: must be upgraded before this app is ever
  hosted anywhere reachable by anyone other than its single operator. Documented plainly (not
  overclaimed) in `docs/SECURITY.md` Section 3. User choice, 2026-07-12.

## Step 1 — Login-based auth
- Status: review in progress (security-reviewer done, code-reviewer pending)
- Built by `auth-agent`: NextAuth (Credentials provider, single admin via `ADMIN_EMAIL` +
  bcrypt `ADMIN_PASSWORD_HASH`), `middleware.ts` gating every frontend route, FastAPI
  `require_session` dependency (HS256 JWT, `AUTH_SHARED_SECRET` shared secret, fails closed)
  wired onto every router except `/health`. Verified locally: 46/46 backend tests, frontend
  typecheck/lint/build clean, manual curl checks for unauth/expired/garbage-token rejection.
- `security-reviewer` findings (no blockers): (1) no rate limiting/lockout on login — flagged
  as a follow-up before any internet-facing deploy, not a Step-1 requirement; (2) audit
  logging doesn't cover login yet — correctly deferred to Section 3.6/4; (3) minor config-error
  message leaks a misconfiguration fact (not a secret) to unauthenticated clients; (4)
  out-of-scope carryover — `opspilot-backend/.env` has static long-lived AWS keys, flagged for
  `backend-agent`/Section 4 hardening, not this diff. No committed secrets confirmed via
  `git ls-files`; JWT algorithm pinned + expiry enforced + fails closed; bcrypt constant-time
  compare confirmed.
- `code-reviewer` findings, all fixed and re-verified (51/51 tests pass, ruff clean):
  (1) FastAPI's `/docs`/`/redoc`/`/openapi.json` bypassed `require_session` entirely since
  they're registered on the app object, not a router — fixed by gating them behind
  `OPSPILOT_APP_ENV=local` (404 otherwise), with a new test proving it; (2) JWT decode didn't
  require `exp`/`sub` claims to be present — added `options={"require": ["exp", "sub"]}` +
  tests; (3) the fail-closed-on-missing-secret 503 branch had no test — added one.
- Deferred (not this step, tracked for later): stale-cache-plus-error-banner UX on a 401
  instead of a bare error string (Section 3.4, backend/frontend-agent); `authHeaders()` calling
  `getSession()` per request, a minor extra round trip (perf nit, low priority); no rate
  limiting/lockout on login (flag before any internet-facing deploy); audit logging doesn't
  cover login yet (Section 3.6/4); pre-existing static long-lived AWS keys in
  `opspilot-backend/.env` — not this diff's scope, flagged for the Section 4 hardening pass.
- **Status: done.** Verify locally: set `ADMIN_EMAIL`/`ADMIN_PASSWORD_HASH`/`AUTH_SHARED_SECRET`
  in both `.env`/`.env.local` (see `.env.example` files), run both dev servers, confirm
  visiting any page redirects to `/login` when signed out, and that valid credentials sign in
  and reach the dashboard.

## Step 2 — Idle detection + cost calc, EC2 only
- **Status: done.** Built generic `check_idle(resource_type, resource_id, days)` and
  `estimate_cost(resource_type, resource_id, date_range)` (EC2-only dispatch for now, other
  types raise `UnsupportedResourceTypeError` until Step 3 extends them), a reusable
  `cloudwatch_service.get_daily_datapoints` helper, and Pricing API cost lookup. Wired into
  `/resources/ec2`, MCP server, and the chat agent — all three call the same service functions.
  73/73 tests pass, ruff clean.
- Reviews: security-reviewer clean (no findings). code-reviewer found a stale `is_idle`
  description in the `data-schema` skill (fixed directly — clarified `is_idle` is a
  whole-requested-window property, distinct from the UI's `idle_days >= 7` amber-pulse
  threshold) plus 3 test-coverage gaps, all fixed by `backend-agent`: graceful-degradation test
  for `/resources/ec2` (plus wrapped the previously-unguarded `get_cpu_utilization` call in the
  same try/except for consistency), a future-dated `date_range.end` cap test, and direct unit
  tests for `get_daily_datapoints`'s boto3-call shape.
- Noted for Step 3, not blocking: `get_daily_datapoints` only supports a single dimension
  name/value pair — fine for EC2, some later types (e.g. ELB) may need composite dimensions.
- IAM: EC2 cost calc requires `pricing:GetProducts`/`pricing:DescribeServices` on the AWS
  account — user informed, add before live (non-mocked) testing. Full consolidated policy saved to
  `docs/iam-policy.json` (2026-07-11) — replaces the IAM info that used to live in the deleted
  `AWS_ZeroSpend_Setup_Guide.md`; keep this file in sync as later steps add new AWS actions.

## Step 3 — Extend idle + cost calc to remaining 14 types
- Status: in progress. Split into two batches per the roadmap's own ordering: batch A
  (EBS, RDS, EIP, ELB — the already-scoped types) then batch B (the other 10), each reviewed
  separately rather than one large diff.
- **Batch A (EBS, RDS, EIP, ELB): done and reviewed.** Extended `check_idle`/`estimate_cost`
  dispatch with 4 new resource types, new services (`ebs_service`, `eip_service`,
  `elb_service`), new AWS clients (`get_elbv2_client`, `get_elb_client`), Pricing API cost
  lookups per type. 111/111 tests, ruff clean.
  - `security-reviewer`: clean. Two notes, not this batch's fault: MCP server still has no
    token auth (pre-existing gap, correctly Step 6's territory, just re-flagged since surface
    grew); IAM policy doc was missing `elasticloadbalancing:Describe*`/`pricing:GetProducts` —
    fixed directly (note: `AWS_ZeroSpend_Setup_Guide.md` was subsequently deleted entirely at
    the user's request, 2026-07-11 — this IAM info is no longer written down anywhere, just
    flagged verbally to the user).
  - `code-reviewer` findings, all fixed: (1, high) chat agent's system prompt still said "only
    resource_type='ec2' supported" even though MCP/dashboard fully supported all 5 types —
    genuine two-front-doors-disagree bug, fixed; (2, medium-high) EIP/EBS-unattached
    `idle_since`/`idle_days` claimed the full requested window as a verified streak with no
    signal to back it — added `idle_since_is_estimated: bool` to `IdleCheckResult`
    (documented in the `data-schema` skill now); (3, low) stale `resource_type` Field
    descriptions in `models/idle.py`/`models/cost.py`, fixed.
  - Review cadence note: agreed with the user to combine batch B's review with a
    re-confirmation pass over batch A rather than reviewing every batch separately, since
    batch A's pattern is now established. Single combined review after batch B, before Step 4.
- **Batch B (Lambda, NAT Gateway, DynamoDB, ElastiCache, SageMaker, Redshift, API Gateway,
  CloudFront, OpenSearch, Kinesis): built, all 15 roadmap types now supported.** 189/189 tests,
  ruff clean. New `zero_fill_missing_days` param added to `_check_idle_via_metrics` — sparse
  activity metrics (Lambda/DynamoDB/API Gateway/CloudFront/Kinesis) publish no datapoint at all
  during zero-activity periods, not a zero-valued one; caught and fixed by backend-agent itself
  mid-build. Notable scope decisions (all documented inline): API Gateway is REST-only (v1),
  HTTP APIs (v2) are a documented gap; Lambda/CloudFront have no creation timestamp at all
  (younger_than_window always False, cost defaults to trailing-7-day window not "since
  creation"); DynamoDB prices PROVISIONED vs PAY_PER_REQUEST differently; CloudFront prices
  requests only, not data transfer (documented undercount).
  New IAM actions needed (batch B, on top of batch A's ELB/Pricing additions):
  `ec2:DescribeNatGateways`, `elasticache:DescribeCacheClusters`, `sagemaker:ListEndpoints`,
  `sagemaker:DescribeEndpoint`, `sagemaker:DescribeEndpointConfig`,
  `redshift:DescribeClusters`, `apigateway:GET`, `cloudfront:ListDistributions`,
  `es:ListDomainNames`, `es:DescribeDomain`, `kinesis:ListStreams`,
  `kinesis:DescribeStreamSummary` — user informed, add before live (non-mocked) testing. Full consolidated policy saved to
  `docs/iam-policy.json` (2026-07-11) — replaces the IAM info that used to live in the deleted
  `AWS_ZeroSpend_Setup_Guide.md`; keep this file in sync as later steps add new AWS actions.
  `app/mcp/server.py`'s tool docstrings were stale ("one of ec2, ebs, rds, eip, elb") — fixed
  directly (same as the batch-A docstring cleanup) before the combined review ran.
- **Combined review (batch A + B together): security-reviewer done, no blockers.** Two
  pre-existing items re-confirmed still open, both already tracked elsewhere, not this diff's
  fault: static long-lived AWS IAM keys (no assumed-role path) — Section 4/Step 7; MCP server
  still has no token auth, now covering all 15 types through the same unauthenticated
  stdio-only surface — Step 6. `code-reviewer` found the same "orchestrator.py system prompt
  went stale" bug a second time (fixed directly by the coordinator, 189/189 tests re-verified)
  and one real test-coverage gap (missing direct test for `get_daily_datapoints`'s new
  `extra_dimensions`/`region` params — fixed by `backend-agent`, 191/191 tests, ruff clean).
  Two low-severity items accepted, not fixed: dashboard REST API intentionally still
  EC2-only (Step 5 will extend it); API Gateway's generic "not found" error doesn't distinguish
  "genuinely missing" from "you passed an HTTP API id into the REST-only path."
- **Status: done.** All 15 roadmap resource types now support `check_idle`/`estimate_cost`,
  consistently through the dashboard API, MCP server, and chat agent.

## Step 4 — Region-wide scanning
- **Status: done.** New `app/services/scan_service.py` aggregates all 15 types for one region
  into the `data-schema` skill's top-level scan response (region, last_updated, resources[],
  totals). Per-region in-process cache with a 45s cooldown on explicit refresh, graceful
  degradation (one type/resource failing doesn't blank the scan; AWS failure serves stale
  cache with `error` set). `GET /resources/scan?region=...&force=...` and
  `GET /resources/regions`, plus matching `scan_region`/`list_regions` MCP + chat tools.
  224/224 tests, ruff clean.
  - `code-reviewer` (deep pass, warranted since this introduced genuinely new
    concurrency/caching architecture, not a repetitive extension): 5 real findings, all fixed —
    (1, high) orchestrator's "list all resources" instruction still enumerated only the
    original 7 tools, omitting 11 of 15 types now only reachable via `scan_region` (third
    occurrence of this bug class in this file); (2, moderate-high) stopped RDS / paused
    Redshift got priced at full rate and could be misreported idle — EC2's
    `skip_idle_cost` guard wasn't replicated for them; (3, moderate) a plain `force=False`
    request could race into a 429 on a brand-new never-cached region, contradicting the
    documented contract; (4, moderate) no test coverage for region-threading or per-region
    cache isolation, the two actually-new pieces of architecture; (5, minor) MCP/dashboard
    disagreed on whether cached data is returned during a cooldown.
  - `security-reviewer`: 1 **critical** finding, fixed — the region cache/cooldown was keyed
    on the raw unnormalized string, so `us-east-1`/`US-EAST-1`/`"us-east-1 "` each bypassed the
    cooldown as a "new" region, defeating the anti-billed-API-spam protection entirely and
    growing the cache unbounded; also unvalidated against the account's actual enabled regions.
    Fixed with `_validate_region()`/`InvalidRegionError`, called first thing in `scan_region()`
    before any cache/lock/AWS-call logic — independently re-verified by reading the code
    directly, not just trusting the report. Plus 1 medium finding, fixed: raw AWS exception
    text (can embed account ID via IAM ARNs) was leaked into 502 responses and MCP error text.
  - Re-flagged, pre-existing, not this diff's fault: MCP still has no token auth (Step 6); the
    app still uses static long-lived IAM user keys rather than assumed-role sessions (Step 7).
  - Known accepted gap: CloudFront is a global service, so scanning multiple regions
    double-counts its distributions if a caller sums totals client-side — flagged for
    `frontend-agent`.

## Step 5 — Galaxy UI wired to real data + refresh/cache + icons/legend + cluster view (3.7) + 3.8 chat tools
- **Status: done.** Resumed a session where frontend-agent's UI half and backend-agent's
  3.8-tools half had both already been substantially built (untracked files found in the
  working tree). Assessed actual completeness against the roadmap by reading the code
  directly rather than trusting the file-existence signal — found two real gaps beyond what
  the prior session had flagged as in-progress, both closed this session, sequenced backend
  first then frontend (frontend's cluster view is content-dependent on backend's relations
  output, so not run in parallel despite no file overlap).
- **Already solid on resume, verified by reading the code (not just re-trusting the prior
  session):** `GalaxyView.tsx` — real `scanRegion`/`getRegions` data, full refresh/cooldown/
  stale-cache-warning UX matching 3.4's contract exactly (never blanks on failure, debounced
  refresh, "last updated Nm ago"), per-family SVG glyphs + toggleable legend (Section 5's
  icon table), deterministic golden-angle layout. `app/services/resource_query_service.py` +
  `app/models/resource_query.py` + `app/tools/resource_query_tools.py` — all four 3.8 tools
  (`list_resources`, `get_resource_health`, `get_resource_age`, `estimate_instance_cost`)
  implemented and wired into both the orchestrator's system prompt and the MCP tool list
  (two-front-doors-agree, verified by grep, not just self-report).
- **Gap found #1: roadmap 3.7 (connected-resources cluster view) was not done on either
  side**, despite being in this step's scope. `scan_service.py` hardcoded `relations=[]` for
  every resource (confirmed at the model/service level, not just inferred), and
  `GalaxyView.tsx` had an explicit code comment stating the cluster view was "out of scope
  for this build." Neither half of 3.7 existed yet.
- **Gap found #2: zero test coverage for the new 3.8 service code** — no
  `tests/test_resource_query_service.py` existed at all, and `test_cost_service.py` had no
  case for `estimate_instance_cost`, breaking this codebase's established one-test-file-
  per-service-module convention.
- **Backend (`backend-agent`), first pass:** populated `relations` in `scan_service.py` via a
  new `_relations_for()` helper, fed by new fields added to per-type models (security group
  IDs, subnet, VPC, IAM role/instance-profile, attached EBS volumes) that were already present
  in each type's existing `Describe*`/`List*` response — no new AWS calls added (independently
  verified by both the coordinator's review-gate agents, not just the building agent's claim).
  Populated for 10 of 15 types (ec2, ebs, rds, elb, lambda, eip, nat_gateway, elasticache,
  redshift, opensearch); genuinely not populatable without a new AWS call for the other 5
  (dynamodb, sagemaker, api_gateway, cloudfront, kinesis — no VPC/SG/IAM linkage exposed by
  their existing list/describe calls), documented as an accepted gap, not a shortcut. Added
  `tests/test_resource_query_service.py` (21 tests) + 3 new `estimate_instance_cost` cases in
  `test_cost_service.py`. 248/248 tests, ruff clean at this checkpoint.
  - `code-reviewer` findings, all fixed: (1) stale `GalaxyResource` class docstring still said
    "relations always [] for now," contradicting the correct module docstring 15 lines above
    it — same stale-docstring bug class that's recurred multiple times this build; (2)
    `RelationLink.label`/`.kind` were plain `str` despite this codebase's own established
    `Literal` convention for every other closed-set string field (`CostEstimate.method`,
    `idle_data_source`, etc.) — changed to `Literal[...]`, `kind`'s derived directly from
    `TYPE_CODES` via unpacking so it can't itself drift; (3) `_relations_for()` itself — the
    function deciding every kind/label pairing — had zero direct tests, and 6 of 7 modified
    service files' new field-extraction code had no test fixtures exercising the new raw AWS
    response keys at all (reviewer hand-verified correctness against boto3 shapes for this
    pass, but flagged there was no regression protection) — added 17 direct `_relations_for()`
    tests plus extended fixtures/assertions in all 7 service test files.
  - `security-reviewer` finding, fixed (moderate): `LambdaFunctionSummary.role_arn` and
    `EC2Instance.iam_instance_profile_arn` were full IAM ARNs (embedding the 12-digit AWS
    account ID) — this app has otherwise consistently scrubbed the account ID from all
    caller-facing text (error messages, etc.), and these two fields newly exposed it through
    two **pre-existing** routes (`GET /dashboard`, `GET /resources/ec2`) plus the new
    `list_resources` chat tool, meaning the account ID would now reach the LLM provider as
    ordinary tool output. Coordinator judged this a straightforward consistency fix against
    the app's own established precedent (not a scope tradeoff needing user input) and sent it
    back for a fix rather than just noting it: both fields renamed to `role_name`/
    `iam_instance_profile_name`, stripped to the bare trailing path segment at parse time so
    the account ID never enters the model at all. Confirmed via repo-wide grep no other code
    depended on the full ARN.
  - Re-verified independently by the coordinator after fixes (not just re-trusting the
    agent's report): 277/277 tests, ruff clean, `Literal` types and ARN-stripping present in
    the actual code.
- **Frontend (`frontend-agent`), run only after backend's relations landed (sequenced, not
  parallel — content-dependent even though file scopes don't overlap):** built the "View
  connections" cluster view in `GalaxyView.tsx` — button in `DetailPanel` (shown only when
  `relations.length > 0`), re-centers into a cluster layout, cost-bearing relation targets
  looked up in the current scan and sized/colored exactly like the main galaxy, infra targets
  (security_group/subnet/vpc/iam_role) rendered smaller in violet with their bare id (never an
  ARN, per the backend fix above) plus a small kind caption, labeled dashed edges for all 5
  relation-label values, cluster-spend HUD (center + connected cost-bearing, nulls/infra
  skipped), hoppable re-centering on clicking a connected cost-bearing node (infra nodes
  correctly non-clickable), "Back to galaxy" button. Removed the now-stale "out of scope"
  comment. Updated `lib/api.ts`'s `RelationLink` typing to match backend's new `Literal`
  types. `npx tsc --noEmit` clean, `next lint` clean, `next build` clean (independently
  re-run by the coordinator, not just trusted from the report).
  - `code-reviewer`: no blocking findings. Two non-blocking notes: the "View connections (N)"
    button label can slightly overcount vs. the cluster's actual rendered node count if a
    cost-bearing relation target genuinely isn't in the current scan (cosmetic, the in-cluster
    HUD count is accurate); several relations are backend-declared one-directional by design
    (e.g. `eip -> ec2` has no reciprocal edge), so "hopping back" isn't always symmetric — a
    backend data-shape characteristic, not a frontend bug, worth knowing for QA. Both accepted
    as-is, not fixed.
  - `security-reviewer`: clean, no findings. Specifically confirmed the frontend doesn't
    reintroduce account-ID-bearing data the backend deliberately stripped (no `console.log` of
    raw resources, infra node ids never reach a URL/router.push since only cost-bearing
    resources ever become `selectedId`), no new fetch/route surface beyond the already-reviewed
    `scanRegion`/`getRegions`, no raw-HTML rendering, `/galaxy` stays behind the existing
    NextAuth middleware gate, no secrets. Noted as a side observation (improvement, not a
    finding): this diff's `authHeaders()` change also added the bearer token to several
    previously-unauthenticated-at-the-fetch-layer calls (`getEc2Resources`,
    `getDashboardOverview`, etc.) — a net positive, not flagged as needing follow-up.
- **Final combined verification (coordinator, independent of both agents' self-reports):**
  277/277 backend tests, backend ruff clean, frontend `npm run build` succeeds with `/galaxy`
  present in the route manifest.
- Verify locally: sign in, load `/galaxy`, confirm the star field renders from a real scan
  (not mock data), toggle the legend, click a star with relations (e.g. an EC2 instance with
  a security group/subnet/VPC/attached EBS volume) to open its detail panel, click "View
  connections," confirm the cluster re-centers with labeled edges and violet infra nodes,
  click a connected cost-bearing node to re-center again, then "Back to galaxy."

### Post-ship fix (2026-07-11) — layout width, nav theme, scan hang/timeout
User live-tested `/galaxy` after Step 5 was marked done and found three problems (screenshot
review, not new roadmap scope — Step 5 stays **done**, this is a bug-fix pass on it).
- **Problem 1 (not full width) + Problem 2 (nav bar didn't match the galaxy theme):** root
  cause was `opspilot-frontend/app/layout.tsx`'s shared `<main className="mx-auto max-w-6xl
  px-6 py-8">` clamping every page, including `/galaxy`, to a centered column, plus
  `NavBar.tsx` being a flat generic `bg-surface` bar. `frontend-agent` moved the `max-w-6xl
  px-6 py-8` constraint out of the root layout into each individual page's own wrapper div
  (`chat`, `resources`, `investigations`, `mcp`, `settings`, `login` — all visually unchanged)
  so `/galaxy/page.tsx` alone renders full-bleed; `GalaxyView.tsx` itself was untouched (its
  internal canvas was already `w-full`, just previously starved of width by the parent).
  `NavBar.tsx` restyled to reuse `GalaxyView.tsx`'s existing tokens (deep-space radial
  gradient, `backdrop-blur`, accent-bordered pill nav tabs, confirmed `text-accent` already
  equals `GalaxyView`'s `COLOR_IDLE` hex before reusing it — no new palette invented), inner
  `max-w-5xl` constraint removed to match.
- **Problem 3 (scan stuck indefinitely on "Scanning...", Monthly Spend stuck on "—"):**
  `backend-agent` traced this live against the real AWS account in `.env` (not from reading
  code alone) rather than assuming a permissions problem. Findings: no AWS call actually
  hangs (13/15 collectors succeed; Redshift/Kinesis fail fast with `OptInRequired`/
  `SubscriptionRequiredException` — account-level "never activated this service" errors, not
  IAM policy gaps; `docs/iam-policy.json` needed no changes). The real bug: `GET
  /resources/scan` (`app/api/routes/resources.py`) was `async def` but called the fully
  synchronous, sequential `scan_service.scan_region()` directly with no `await`/threadpool
  offload — measured at ~125s for a first-ever scan of this account (only 5 real resources;
  this environment has unusually high ~2-4.6s per-call boto3 latency) — which froze the
  entire single-worker ASGI event loop for the full duration, so a concurrent duplicate
  request (e.g. React 18 StrictMode's double-mount-effect) queued up behind it instead of
  being served, compounding the wait. Fixed via `starlette.concurrency.run_in_threadpool`,
  with a regression test (`test_scan_route.py`) proving a concurrent fast request no longer
  waits behind a slow in-flight scan (live-verified by reverting the fix and watching the test
  fail, then restoring it). Did **not** add speculative boto3 client timeouts
  (`app/aws/client.py`) or fix `_run_scan`'s theoretical "all 15 collectors fail silently"
  gap — neither was reachable/observed with this account's real credentials, so neither was
  touched, per instruction not to fabricate fixes for unobserved failure modes.
  `frontend-agent` added the matching frontend half: `lib/api.ts`'s `scanRegion` now has a
  4-minute `AbortController` timeout (~115s headroom over the observed ~125s real latency, so
  a legitimately slow scan isn't misclassified as a hang) that flows into the pre-existing
  `hardError`/`warning` UI — no new error-display pattern added; `GalaxyView.tsx`'s loading
  state now shows a ticking `Scanning {region}… (Ns)` elapsed-time counter so a slow-but-working
  scan is distinguishable from a frozen one.
- **User-facing IAM note (not a code/policy fix):** Redshift and Kinesis need a one-time
  manual account-level service opt-in in the AWS Console (unrelated to `docs/iam-policy.json`,
  which is already correct) before those two resource types will show up in a scan — flagged
  to the user, not actioned in code.
- **Review (code-reviewer + security-reviewer, combined pass over the full diff):** two real
  findings, both fixed by `backend-agent` and independently re-verified (308/308 tests, ruff
  clean): (1, confirmed via live reproduction) the no-cache `ScanCooldownActive` 429 branch
  set `Retry-After` on the injected `Response` object but then `raise`d `HTTPException`,
  which builds a fresh response and silently drops that header — fixed by passing
  `headers={"Retry-After": ...}` directly on the `HTTPException`; (2, found independently by
  both reviewers) `_get_valid_regions()`'s module-level cache (`_valid_regions_cache`/
  `_valid_regions_cache_at`) had no lock — harmless while the route ran fully on the event
  loop, newly race-prone (redundant free `DescribeRegions` calls, not a cooldown/billing
  bypass — both reviewers rated it low severity) now that `run_in_threadpool` lets
  `scan_region()` run on real OS threads — fixed with a dedicated `_valid_regions_guard`
  lock, deliberately not sharing `_region_locks_guard` (would've serialized unrelated
  regions' scans behind an allowlist refresh); re-review confirmed no deadlock risk (the two
  locks are never held simultaneously by the same thread) and confirmed the new race test
  (`threading.Barrier`-synchronized, real threads) is genuine, not a rubber stamp.
  Non-blocking items explicitly deferred, not fixed: `app/aws/client.py`'s single cached
  `boto3.Session` under genuine multi-thread concurrency (currently a non-issue — static env
  var credentials, no refresh cycle to race; both reviewers agreed this only matters once
  assumed-role/refreshable-credential work lands); six frontend pages hand-duplicating the
  same `max-w-6xl px-6 py-8` wrapper div (DRY smell, not a bug — worth a shared
  `PageContainer` component later); superseded scan requests aren't actually `abort()`'d when
  a newer request supersedes them (harmless given the existing `requestIdRef` guard, just a
  missed optimization); the Refresh button is clickable during the initial-load spinner
  (handled gracefully server-side by the existing lock/wait logic, not a bug).
- Verify locally: load `/galaxy` at desktop width and confirm the starfield reaches both
  viewport edges with no visible page background on either side; confirm every other page
  (`/chat`, `/resources`, `/investigations`, `/mcp`, `/settings`) still renders in its
  original centered column; confirm the nav bar visually reads as continuous with the
  starfield on `/galaxy` and still looks clean on the other pages; force a fresh (uncached)
  region scan and confirm the "Scanning {region}… (Ns)" counter ticks up instead of a static
  message, resolving to real data well before the 4-minute timeout.

### Post-ship fix (2026-07-11) — galaxy canvas still not truly full-bleed
User compared the live `/galaxy` page (screenshot) directly against the locked-in reference
prototype `docs/aws-galaxy-dashboard.jsx` and found it still didn't match, even after the prior
post-ship fix above (which solved page *width* but not this). Coordinator diffed the two
directly before delegating — root cause confirmed by reading `GalaxyView.tsx` directly, not
inferred from the screenshot alone (Step 5 stays **done**, this is another bug-fix pass on it).
- **Root cause**: the prototype's root element is a truly full-bleed `w-full h-screen` scene
  with no title text and no border/frame — the cosmic scene IS the page. The built version had
  (1) an `<h1>Galaxy</h1>` heading + description paragraph rendered above the canvas, not in the
  prototype at all, and (2) the starfield canvas wrapped in a `rounded-lg border border-border`
  box budgeted to `h-[calc(100vh-14rem)]` — a bounded, bordered panel sitting inside a normal
  dashboard page, rather than an edge-to-edge immersive scene.
- **Fix (`frontend-agent`)**: removed the `<h1>`/description block from `GalaxyView.tsx` (grepped
  first to confirm this title+description pattern isn't shared/reused elsewhere — it wasn't, so
  nothing else needed touching); removed `rounded-lg border border-border` from the canvas
  wrapper; removed `app/galaxy/page.tsx`'s `px-4 py-6` padding wrapper entirely (page now
  returns a bare `<GalaxyView />`) since it was also eating into the full-bleed goal even after
  the canvas's own border/rounding was removed. Deliberately did not touch `NavBar.tsx` or
  `layout.tsx` — both already correct/deliberate from the prior post-ship fix. Deep-space
  gradient + twinkling-stars layers already used `absolute inset-0`/`h-full w-full`, so they
  auto-filled the new full-bleed container with no changes needed. `npx tsc --noEmit`, `next
  lint`, `next build` all clean.
- **Review**: `security-reviewer` clean, no findings (confirmed no new data exposure, auth
  gate/middleware untouched, no raw-HTML injection, no new fetch surface — pure CSS/JSX
  structure change). `code-reviewer` found two non-blocking items:
  (1) the canvas height constant (`calc(100vh-5rem)`, first pass) was derived by flawed analogy
  to `chat`/`login` pages' `calc(100vh-9rem)` budgets, which also count their own page padding/
  heading — a direct read of `NavBar.tsx`'s actual classes put its real height at ~67px
  (~4.2rem), not 5rem, leaving a ~13px unstyled sliver at the bottom of the viewport (low visual
  severity, near-black colors on both sides, but it undercut the fix's actual goal) — **sent back
  to `frontend-agent` and fixed**: recomputed the constant directly from `NavBar.tsx`'s classes
  (header `border-b` 1px + row `py-4` 32px + tallest nav-`Link` content 34px = 67px), changed to
  `h-[calc(100vh-67px)]`, comment rewritten with the real per-class derivation; independently
  re-verified by the coordinator via direct grep of the final code, not just the agent's
  self-report. Re-ran `tsc`/`lint`/`build`, all clean.
  (2) `/galaxy` now has zero heading landmarks anywhere in its render tree (the only page in the
  app in that state) — a real deviation from this codebase's one-heading-per-page convention and
  a screen-reader/document-outline regression, but it's faithful to the prototype (which also has
  no heading) and is literally what the fix was asked to do ("no page title... above the
  canvas"). **Accepted as-is, not fixed** — coordinator judged this an intentional design
  tradeoff explicit in both the user's request and the locked-in prototype, not a bug; flagged
  here as a known gap if a11y work is ever prioritized (an `sr-only` `<h1>Galaxy</h1>` would close
  it without reintroducing visible chrome, if wanted later).
- Verify locally: load `/galaxy`, confirm the starfield reaches all four viewport edges with no
  border/frame around it and no visible gap/scrollbar at the bottom, confirm no page title/
  description renders above the canvas, and confirm every other page still renders its own
  heading/title as before.

### Post-ship fix (2026-07-12) — chat as floating launcher, nav bar seam
User compared the live app directly against both the locked-in reference prototype
`docs/aws-galaxy-dashboard.jsx` and roadmap Section 5's own wording (screenshots/preview
reviewed side by side, not new roadmap scope — Step 5 stays **done**, this is a third
bug-fix pass on it, same category as the two post-ship fixes above).
- **Fix 1 — Chat was a top nav tab, not a floating launcher (confirmed spec violation).**
  Roadmap Section 5 says explicitly: "Floating chat launcher (bottom-right), available from
  every tab, slides open a panel — not a top-level tab." `NavBar.tsx` had `/chat` sitting
  directly in its `TABS` array, rendered like every other tab. `frontend-agent` removed it and
  built `components/ChatLauncherProvider.tsx` (plain React Context — `isOpen`/`scope`/
  `openChat(scope?)`/`closeChat()`, no new state library) + `components/ChatLauncher.tsx` (fixed
  bottom-right toggle button + slide-in panel reusing `GalaxyView.tsx`'s own detail-panel
  `translateX`/`backdrop-blur`/translucent-surface visual pattern, `fixed` instead of `absolute`
  since it lives in the root layout, not inside GalaxyView's own `relative` canvas). Reused
  `ChatPanel.tsx` as-is (no chat logic rebuilt) — gave it a new optional `initialAbout` prop that
  takes precedence over its pre-existing `?about=/&label=` URL-param reading, so both entry
  points (floating panel and the still-live standalone route) work. `GalaxyView.tsx`'s "Ask
  about this resource" button now calls `openChat({id, label})` from `useChatLauncher()` instead
  of `router.push('/chat?about=...')`. `Providers` (`app/providers.tsx`) wraps
  `SessionProvider` → `ChatLauncherProvider` → children; `ChatLauncher` renders once in
  `app/layout.tsx`, sibling to `<main>`.
  - **`/chat` route decision** (coordinator call, not a user-input-required tradeoff):
    `app/chat/page.tsx` left completely untouched as a working deep-link/bookmark fallback —
    confirmed via grep it's the only remaining reference to `/chat` in the codebase once NavBar
    and GalaxyView stopped linking it. Zero risk, zero maintenance burden, preserves the
    pre-scoping entry point without deleting/redirecting/repurposing anything.
- **Fix 2 — nav bar read as a separate stacked panel, not embedded in the galaxy scene.** The
  reference prototype has no persistent nav bar at all, just small floating translucent overlay
  cards with no hard edge-to-edge border. `NavBar.tsx` had its own `border-b border-border` plus
  an independently-centered `radial-gradient(...)` background — a hard seam against
  `GalaxyView.tsx`'s continuous starfield even though the color values were already matched in
  an earlier fix. `frontend-agent` dropped both, letting the nav sit in the page's own `bg-bg`
  using the same floating-card language (`rounded-lg border border-border bg-surface/90
  backdrop-blur`) GalaxyView's own region-selector/HUD/legend overlays already use. Kept in
  normal document flow (not fixed/absolute); verified every other page's `max-w-6xl px-6 py-8`
  wrapper still reads correctly. NavBar's rendered height dropped 67px → 66px (lost its 1px
  border), so `GalaxyView.tsx`'s hardcoded `h-[calc(100vh-67px)]` full-bleed starfield container
  was updated to `h-[calc(100vh-66px)]` to match — layout math tied directly to this restyle, not
  scan/refresh logic.
- **Review (code-reviewer + security-reviewer, run in parallel against the same diff):** both
  independently flagged the same low-severity finding — `ChatLauncher` rendered unconditionally
  in the root layout, so the floating button/panel was visible and clickable on `/login` (the one
  route `middleware.ts` deliberately excludes from its auth-redirect matcher), contradicting
  `app/providers.tsx`'s own comment that claimed it rendered "behind the exact same NextAuth
  session gate as everything else." Not a data-exposure bug — the backend's `require_session`
  still independently gated the actual `sendChatMessage` call, an unauthenticated click just hit
  the existing "Couldn't reach the agent" error path — but the comment overstated a guarantee
  that wasn't actually enforced. Coordinator judged this a cheap, clearly-scoped fix (not
  something to defer) and sent it back rather than triaging it away: `frontend-agent` added an
  explicit `useSession()` status check in `ChatLauncher.tsx` (`status !== "authenticated"` →
  render `null`, correctly covering both the `"unauthenticated"` and `"loading"` interstitial
  states so it never flashes on then off), corrected both files' comments to describe the real
  mechanism (explicit client-side check, not `SessionProvider`/middleware alone; backend
  `require_session` remains the actual enforcement boundary either way). Independently
  re-verified by the coordinator via direct code read, not just the agent's report.
  - `security-reviewer`, full findings: confirmed `ChatLauncher` reuses the exact same
    `sendChatMessage`/`authHeaders()` path as before (no new fetch call, no parallel auth), no
    `console.log`/`dangerouslySetInnerHTML`/client-side-only "auth" check anywhere in the new
    files, `ChatScope` (`{id, label}`) carries nothing more sensitive than the old `?about=/
    &label=` URL params it replaces, NavBar's CSS-only restyle didn't affect the sign-out
    button/session-email markup. Otherwise clean.
  - `code-reviewer`, full findings: the pre-login exposure item above (fixed), plus one
    documentation-precision nit accepted as-is (a code comment slightly overstated visual parity
    with GalaxyView's detail panel without calling out that the chat panel, being `fixed` at the
    viewport level, additionally overlaps the NavBar itself — arguably correct per the roadmap's
    "available from every tab" framing, not a bug). Verified sound: Context memoization/provider
    placement, `ChatPanel`'s dual prop/URL-param precedence (no stale-state risk given
    `ChatLauncher` only mounts `ChatPanel` while `isOpen`, so every open is a fresh mount), no
    z-index/click-through conflicts between the new fixed elements and GalaxyView's existing
    absolute-positioned overlays, no dead code left from the removed `router.push` import, no
    established frontend test convention being skipped (this codebase has no frontend test
    runner at all yet, confirmed via `package.json`).
- **Final verification (coordinator, independent of the agent's self-reports, run twice — before
  and after the pre-login fix):** `npx tsc --noEmit` clean, `npx next lint` clean (`No ESLint
  warnings or errors`), `npx next build` clean (all 10 app routes still build, including `/chat`
  and `/galaxy`).
- Verify locally: confirm no "Chat" tab appears in the nav on any page; confirm a floating 💬
  button appears bottom-right once signed in, on every page, and does NOT appear on `/login`;
  click it and confirm the panel slides in from the right without navigating away; from
  `/galaxy`, click a resource then "Ask about this resource" and confirm the same floating panel
  opens pre-filled with that resource's question instead of navigating to `/chat`; confirm
  `/chat` still loads directly if visited by URL; confirm the nav bar on `/galaxy` no longer has
  a visible hard-edged strip separating it from the starfield, and still looks clean (no gap/
  seam) on `/resources`, `/investigations`, `/mcp`, `/settings`, `/login`.

### Post-ship fix (2026-07-12) — raw AWS traceback leaking through 3 read routes
User live-tested against the real AWS account tonight and hit `AccessDeniedException` on
`GET /mcp/token/status` (a real missing-IAM-permission situation, being fixed on the AWS side
separately) — confirmed via `backend_dev.log`, not a guess. This surfaced a genuine bug, not new
roadmap scope: Step 5 stays **done**, this is a bug-fix pass on it (audit-log/investigations/mcp
routes and their `lib/api.ts`/panel consumers are all part of this app's Section 5 tab surface).
- **Root cause**: `GET /mcp/token/status` and `GET /audit-log` had no try/except around their
  DynamoDB-backed service calls, so a raw `botocore.exceptions.ClientError` (embedding the full
  IAM caller ARN + 12-digit account ID) propagated to an unhandled 500 with a full Python
  traceback in the HTTP response — a first-time violation, in an HTTP route, of this codebase's
  own repeatedly-enforced precedent (Step 4's `scan_service.py` fix, Step 5's `role_arn`/
  `iam_instance_profile_arn` fix) that raw AWS exception text must never reach a response body.
- **Systemic check (before delegating, not after)**: grepped for the same gap elsewhere and found
  two more instances beyond what the user hit: `GET /investigations` had the identical unguarded
  DynamoDB-scan gap, and `mcp_auth_service.generate_token()`/`revoke_token()`'s *primary* DynamoDB
  mutation calls were unguarded too (only the audit-log write that follows them was already
  guarded, per the Step 6 review fix). All fixed together in one pass rather than patching only
  the two routes the user happened to hit.
- **Backend (`backend-agent`)**: new `opspilot-backend/app/core/aws_errors.py` —
  `aws_error_to_http_exception(exc, *, logger, context)` maps a caught exception to one of four
  fixed, literal detail strings (AccessDenied → permission error; ResourceNotFoundException →
  "resource/table may not exist yet"; `BotoCoreError` → "AWS unreachable"; anything else →
  generic fallback), decided only by exception *type*/`ClientError.response["Error"]["Code"]` —
  never `str(exc)` or any exception attribute. Always status `502`, matching this codebase's
  existing `list_available_regions`/`ScanFailedNoCacheError` convention in
  `app/api/routes/resources.py` (not a new 503 convention). The real exception is still logged
  server-side in full (`exc_info=True`). Wired into 5 call sites: `audit_log.py`'s
  `get_audit_log`, `mcp_auth.py`'s `get_mcp_token_status`/`generate_mcp_token`/`revoke_mcp_token`
  (guarding the primary mutation, separate from the pre-existing audit-write guard), and
  `investigations.py`'s `list_investigations`. Service-layer functions themselves still raise on
  failure, unchanged (routes translate, services don't) — matches this codebase's existing
  service-raises/route-translates split. Added `tests/test_audit_log_route.py` and
  `tests/test_investigations_route.py` (new files, no route-level tests existed for either
  before), extended `tests/test_mcp_auth_route.py` with 3 new primary-mutation-failure cases —
  each asserting 502, that the response `detail` contains none of the account ID/ARN/error-code/
  IAM-username substrings, and that the real exception was logged. 339/339 tests pass, ruff clean
  (independently re-run by the coordinator, not just trusted).
- **Frontend (`frontend-agent`), run after backend landed**: found `lib/api.ts`'s
  `getMcpTokenStatus()` and `getInvestigations()` were the only two fetch wrappers in the file
  not following the established `body?.detail ?? fallback` pattern every sibling function already
  uses (`getAuditLog`, `generateMcpToken`, `revokeMcpToken`, `getConnectedAccount`, `getRegions`,
  `scanRegion`) — they threw a generic `Request failed with status ${res.status}` and never read
  the response body, so even after the backend fix the user would still have seen a generic
  message instead of the new clean detail text. Fixed both to match the established pattern
  exactly (verified directly, not just trusted: both now do
  `const body = await res.json().catch(() => null); throw new ApiError(body?.detail ?? ..., res.status);`).
  Checked `SettingsPanel.tsx`'s `loadStatus()` and `InvestigationsPanel.tsx`'s load handler (plus
  `AuditLogPanel.tsx` incidentally) — all three already correctly do
  `err instanceof Error ? err.message : "..."`, so no further changes were needed there; they now
  automatically surface the backend's real detail text. Deliberately left `getEc2Resources`/
  `getDashboardOverview`/`getMcpServerInfo` alone (same generic-message gap, but not part of
  tonight's reported failure and backed by AWS calls that already have their own per-resource
  graceful degradation elsewhere) — not fixed opportunistically, out of this fix's scope.
  `npx tsc --noEmit` clean, `npx next lint` clean, `npx next build` clean (independently re-run
  by the coordinator for typecheck/lint; build trusted from the agent's report plus direct code
  read of the two changed functions).
- **Review (code-reviewer + security-reviewer, run in parallel against the backend diff)**: both
  clean, no blocking findings.
  - `security-reviewer`: confirmed directly (not from the docstring) that `_detail_for()` only
    returns fixed literal strings, never derived from the caught exception; confirmed
    `raise ... from exc` only affects server-side traceback chaining, not the JSON response body;
    confirmed `FastAPI()` never sets `debug=True` anywhere in this app (so the fix holds
    regardless of env misconfiguration, not just in the happy path); confirmed `/investigations`
    was never actually missing `require_session` (gated at router-registration level in
    `main.py`, same mechanism as every other protected router — the per-route
    `Depends(require_session)` visible in `mcp_auth.py` exists only because those handlers need
    `SessionUser` for `actor_email`, not because other routes lack the gate); confirmed the new
    tests assert on the *absence* of account-ID/ARN/error-code substrings, not just status code;
    confirmed `context` (the short server-log label) never reaches the response body; confirmed
    no overlap with the two known pre-existing tracked gaps (static IAM keys; MCP auth) — neither
    touched nor worsened.
  - `code-reviewer`: independently ran the full suite + ruff live (339/339, clean) rather than
    trusting the self-report; confirmed the 502 choice matches `list_available_regions`/
    `ScanFailedNoCacheError` precedent exactly; confirmed no leftover `str(exc)`/f-string-of-
    exception reaches any `HTTPException(detail=...)` in the 5 touched call sites or elsewhere in
    the diff; confirmed service-layer functions still raise (routes translate) unchanged. Two
    non-blocking notes, not fixed: a test helper's log-assertion clause is redundant (the second
    of two `or`-joined conditions is always true given `exc_info=True` is always passed, so the
    first clause is dead weight — doesn't invalidate the test's real assertions); `investigations.py`'s
    module docstring doesn't mention the `require_session` gate the way `audit_log.py`/
    `mcp_auth.py`'s docstrings do (pure doc-consistency nit, no functional gap).
- **Final verification (coordinator, independent of every agent's self-report)**: re-ran
  `python -m pytest -q` (339 passed) and `python -m ruff check .` (clean) from `opspilot-backend`;
  re-ran `npx tsc --noEmit` (clean) and `npx next lint` (clean, "No ESLint warnings or errors")
  from `opspilot-frontend`; directly read the final `aws_errors.py`, all 3 modified route files,
  and the 2 modified `lib/api.ts` functions rather than relying on either agent's summary.
- Verify locally: with the real AWS IAM user still missing `dynamodb:GetItem`/`Scan` on
  `opspilot-mcp-tokens`/`opspilot-audit-log`/`opspilot-investigations` (or by temporarily revoking
  those permissions), load `/settings`, `/settings` (Audit Log tab), and `/investigations` and
  confirm each shows a clean, readable error message (e.g. "AWS permission error -- the backend's
  IAM credentials don't have access to this resource.") instead of a generic "Failed to fetch" or
  a raw traceback; confirm `backend_dev.log` still shows the full real exception server-side.
  Once the user's separate AWS-side IAM fix lands, confirm all three load normally again.

### Post-ship fix (2026-07-12) — nav restructure: Settings + user icons instead of tab/inline text
User requested a frontend-only nav restructure, confirmed via a clarifying question before
delegating (user picked "two separate icons," not a combined dropdown) — not new roadmap scope,
Step 5 stays **done**, this is another bug-fix/UX pass on it, same category as the prior
post-ship fixes above. Scope confined to `opspilot-frontend/components/NavBar.tsx`, no other
file touched.
- **Change**: (1) removed `Settings` from the `TABS` array — five tabs remain (Galaxy, Idle
  Resources, Investigations, Cost Overview, Audit Log), completely untouched otherwise; (2)
  added a standalone gear-icon `<Link href="/settings">` top-right, reusing the existing
  `SlidersIcon` (no new gear glyph invented) — direct navigation, no dropdown, same behavior the
  Settings tab had, just relocated + icon-only; (3) replaced the inline
  `{session.user.email}` text + "Sign out" button with a new `UserIcon` (hand-rolled SVG,
  built from this file's existing `ICON_PROPS` convention — same `viewBox`/`stroke`/
  `strokeWidth` as every other icon here) that toggles a popover showing the email + a "Sign
  out" button calling the unchanged `signOut({ callbackUrl: "/login" })`; (4) both new icons
  gated behind the exact same pre-existing `session?.user?.email` truthy check that gated the
  old inline block — no new/different condition.
- **Outside-click-to-close**: confirmed via repo-wide grep (`addEventListener`/`onBlur`/
  `useOnClickOutside`, zero matches) that no such pattern existed anywhere in this frontend
  before this fix — `GalaxyView.tsx`'s region-selector dropdown is a visual analog only, it
  closes solely via each option's own `onClick`, not on outside click. `frontend-agent` added a
  self-contained `useRef` + `document.addEventListener("mousedown", ...)` effect local to
  `NavBar.tsx` (no new shared hook file, per instruction to keep it minimal) — closes on
  outside click and on clicking "Sign out."
- Also updated the file's stale "six tabs" doc comment to describe the new five-tabs-plus-two-
  icons layout, so the comment doesn't contradict the code (same stale-comment class of issue
  flagged multiple times earlier in this build).
- **Review**: `security-reviewer` (light-touch, per instruction — this is UI relocation of
  already-reviewed auth UI, not new auth logic): clean, no findings. Confirmed the auth gate is
  bit-for-bit the same condition as before, email is rendered as plain JSX text interpolation
  (React-escaped, no `dangerouslySetInnerHTML`/`innerHTML` anywhere in the diff), the new
  mousedown listener only does a `.contains()` DOM check (no injection surface), and the
  settings gear link is a static hardcoded route gated behind the same session check.
  `code-reviewer`: no correctness/gating/dead-code findings (outside-click logic sound, listener
  correctly scoped and cleaned up, `setUserMenuOpen(false)` runs before `signOut()`, `SlidersIcon`
  still in use via the gear link so not orphaned). Two low-severity a11y nits, both fixed:
  missing `aria-expanded` on the toggle button, missing `type="button"` on the two `<button>`
  elements. Deliberately **not** added, explicitly deferred (coordinator judgment — beyond what
  was asked for in this narrow icon-relocation task): full ARIA menu semantics (`role="menu"`/
  `menuitem`), Escape-to-close, focus-return-to-trigger on close.
  - Note on the code-reviewer's diff comparison: it initially flagged what looked like a much
    larger change than described (comparing against committed git `HEAD`, which predates several
    prior sessions' worth of still-uncommitted work on this file — nothing has been committed
    since `552eb71`, so a `HEAD` diff includes all of it, not just this fix). Not a real
    discrepancy in this fix's actual scope — confirmed directly by reading `NavBar.tsx`'s
    pre-fix working-tree state myself before delegating, which already matched the six-tab +
    inline-email/sign-out description given to `frontend-agent`.
- **Final verification (coordinator, independent of the agent's self-reports, re-run after the
  a11y fixes)**: `npx tsc --noEmit` clean, `npx next lint` clean ("No ESLint warnings or
  errors"), `npx next build` clean — all 13 app routes present in the manifest, including all 5
  remaining tabs (`/galaxy`, `/idle-resources`, `/investigations`, `/cost-overview`,
  `/audit-log`) and `/settings` (now reachable only via the gear icon, page itself untouched).
  Confirmed both `aria-expanded={userMenuOpen}` and `type="button"` present via direct diff read,
  not just the agent's report.
- Verify locally: confirm the top nav shows exactly five tabs (no Settings tab); confirm a gear
  icon and a user icon appear top-right when signed in; click the gear icon and confirm it
  navigates directly to `/settings`; click the user icon and confirm a popover opens showing the
  signed-in email and a "Sign out" button; confirm clicking outside the popover closes it;
  confirm "Sign out" still signs out and redirects to `/login`.

### Post-ship fix (2026-07-12) — wide markdown table forced whole chat panel to scroll sideways
User reported (screenshot `chatui.png` at repo root) that asking the chat agent a question whose
answer includes a markdown table (e.g. "What's idle in this account?" → Resource ID/Name/Type
table) produced a table wider than the ~384px chat panel; instead of the table itself scrolling
horizontally, the entire chat panel required horizontal scroll, cutting off columns at the panel
edge. Root cause pre-confirmed by direct code read before delegating (no investigation agent
needed) — not new roadmap scope, Step 5 stays **done**, this is a bug-fix pass on it.
- **Root cause**: `opspilot-frontend/components/ChatPanel.tsx` (assistant-message branch, then
  lines ~176-179) rendered `<ReactMarkdown remarkPlugins={[remarkGfm]}>{m.content}</ReactMarkdown>`
  with no `components` override, inside a `prose prose-invert prose-sm max-w-none ...` wrapper.
  `remark-gfm` emits plain `<table>` elements with no scoped `overflow-x-auto` container, so a
  wide table expanded its containing block (the bubble/panel) instead of scrolling internally.
- **Fix (`frontend-agent`)**: added a `components={{ table: ... }}` prop to the `ReactMarkdown`
  call that wraps rendered `<table>` elements in their own `<div className="overflow-x-auto">`,
  scoped tightly to the table only. `react-markdown@9.0.1`'s documented override shape —
  `node` destructured out of the props object (not spread onto the DOM `<table>`, avoiding a
  React unknown-prop warning). Checked whether fenced code blocks (`<pre>`) had the same
  unbounded-width risk: confirmed via direct read of `@tailwindcss/typography`'s source
  (`node_modules/@tailwindcss/typography/src/styles.js`) that the `prose` plugin already sets
  `pre: { overflowX: 'auto', ... }` by default — `pre`/inline `code` were deliberately left
  untouched, already safe. Nothing else in the file touched by this fix itself.
  `npx tsc --noEmit`, `npx next lint`, `npx next build` all clean.
- **Verification method (stated plainly, same honesty standard as prior post-ship fixes)**: no
  browser automation available, so verified via direct code/CSS reasoning, not a live screenshot
  — traced the DOM ancestry from the new `overflow-x-auto` div up through the `prose max-w-none`
  div, the `max-w-4xl` message bubble, and the `overflow-y-auto` message list, confirming no
  ancestor forces `overflow: visible` back onto the table's own scroll container (overflow is a
  non-inherited CSS property, and `max-w-none` on the prose div only lifts prose's own
  typography max-width, it doesn't touch a descendant's overflow behavior).
- **Review (`code-reviewer` only — light-touch per instruction, no `security-reviewer` pass since
  this is a pure CSS/rendering change with no auth/data-exposure surface)**: no blocking findings
  on the table-wrap hunk itself (override shape correct for the installed react-markdown version,
  no a11y/semantic-HTML concern with wrapping `<table>` in a plain `<div>`). Two non-blocking
  notes: (1) `components/InvestigationsPanel.tsx` renders LLM-generated markdown
  (`inv.conclusion`) through the identical `ReactMarkdown`+`remarkGfm`+`prose` shape with *no*
  scroll container at all — same bug class, unfixed, flagged as a same-shape follow-up candidate
  but out of this fix's scope, not actioned now; (2) reviewer's initial `git diff HEAD` comparison
  looked larger than "just the table fix" — same non-issue already documented in the prior
  Step-5 post-ship fix above (nothing committed since `552eb71`, so a `HEAD` diff includes several
  prior sessions' worth of still-uncommitted work on this file, e.g. the chat-launcher `initialAbout`
  prop/deep-linking feature — not something this fix introduced; confirmed by reading the agent's
  actual reported diff, which was exactly the `components={{ table: ... }}` hunk).
- **Follow-up noted, not actioned**: `InvestigationsPanel.tsx`'s markdown rendering has the same
  unscoped-wide-table gap; worth the same fix (or a small shared `ReactMarkdown` wrapper component)
  if/when that panel's output is observed to include wide tables in practice.
- Verify locally: ask the chat agent a question that returns a markdown table (e.g. "What's idle
  in this account?" with 3+ resources), confirm the table gets its own horizontal scrollbar
  bounded to the table's width and the surrounding chat panel/page never scrolls sideways; confirm
  a normal short assistant reply and a fenced-code-block reply both still render unchanged.

### Post-ship fix (2026-07-12) — chat bubble width overflow, structural root cause (supersedes/
completes the table-overflow fix above)
User reported the table-overflow fix above was real but incomplete: plain prose text (no table
involved) still overflowed the chat panel, cutting off mid-word with a visible gap before the
bubble's true off-screen edge — described as looking like "two boxes, one inside another." Not
new roadmap scope, Step 5 stays **done**, this is a follow-on bug-fix pass completing the same
overflow issue the entry above only partially closed.
- **Root cause (confirmed by direct code read before delegating)**: `ChatPanel.tsx` now only ever
  renders inside `ChatLauncher.tsx`'s floating slide-in panel (`w-full sm:w-96` — 384px on desktop,
  ~352px after `p-4` padding; confirmed by reading `ChatLauncher.tsx` directly, not assumed) —
  the old full-width standalone `/chat` page usage is gone as of an earlier fix this session. Each
  message row used `self-end`/`self-start` inside a `flex flex-col` container, which opts the row
  OUT of flex-stretch sizing (content-based `fit-content` instead), with no width ceiling anywhere
  above the bubble div. The bubble divs then carried absolute pixel max-widths — `max-w-xl` (576px)
  for user/error, `max-w-4xl` (896px) for assistant — both larger than the actual ~352-384px panel,
  a dead-context leftover from when `ChatPanel` used to render full-width. Wide content (long
  unbroken prose, inline code spans, tables) pushed the bubble past the panel's real edge, so the
  panel scrolled horizontally to reveal it while text got cut off mid-word at the visible boundary
  — the earlier table-specific `overflow-x-auto` wrap only fixed the table case, not this
  structural cause, which is why plain-text overflow was still visible after that fix landed.
- **Fix (`frontend-agent`)**: reworked the full width chain, not just the bubble.
  - Row wrapper: `self-end`/`self-start` → `flex w-full justify-end`/`justify-start` (now
    explicitly spans the panel's real width instead of sizing to content).
  - New inner "column" div (didn't exist before), wrapping the bubble together with its metadata
    footers (recalled-from/provider/trace): `flex min-w-0 max-w-[85%] flex-col
    items-end`/`items-start`. Needed because making the row itself `display:flex` would otherwise
    lay the bubble and its metadata siblings out side-by-side instead of stacked; `min-w-0`
    overrides the column's default flex-item `min-width:auto`, which is what actually lets
    `max-w-[85%]` shrink it below its content's intrinsic width (e.g. a wide table) instead of
    being overridden by that content.
  - Bubble div: dropped `max-w-xl`/`max-w-4xl` entirely, added `min-w-0`. All three message types
    (user/assistant/error) now share the same panel-relative `max-w-[85%]` cap via the column
    wrapper, fixed consistently rather than only on the visibly-broken assistant case — user/error
    had the identical latent defect, just usually masked by shorter message content.
  - The table-specific `overflow-x-auto` wrap from the entry above was left completely untouched —
    still correct: once the bubble itself is properly capped, a table still wider than that
    (now-correct) bubble width legitimately scrolls internally within itself.
  - `npx tsc --noEmit`, `npx next lint`, `npx next build` all clean.
- **Verification method (stated plainly, same standard as the entry above and every prior post-ship
  fix)**: no browser automation tool available, so verified by reasoning through the full CSS/width
  chain rather than a live screenshot — traced from `ChatLauncher.tsx`'s fixed panel width (384px
  desktop `sm:w-96`, full viewport width on mobile) through its `p-4` padding, `ChatPanel.tsx`'s own
  `p-6` message-list padding, the `flex-col` message list, the row's `w-full`, the column's
  `min-w-0 max-w-[85%]`, down to the bubble's `min-w-0`. At the 384px desktop breakpoint this
  resolves to a message-list content area of 384 − 32 (panel padding) − 48 (list padding) = 304px,
  capped at ≤ 258.4px (85%) for the column/bubble — comfortably inside the panel at every level, for
  all three message roles identically. Same proportional math holds at mobile's `w-full` breakpoint
  since the padding values don't change with breakpoint. `prose max-w-none` (unchanged) means
  Typography's own `65ch` default doesn't compete — sizing is fully deferred to the bubble/column.
- **Review (`code-reviewer` only — light-touch per instruction, pure CSS/layout fix, no
  `security-reviewer` pass since there's no data flow or auth surface in this diff)**: confirmed the
  diff matched what was reported (direct file read, not just trusting the summary); confirmed the
  `min-w-0` + `max-w-[85%]` + `flex-col` chain is sound, standard flexbox for shrink-to-fit chat
  bubbles with wrapping prose, no `items-end`/`min-w-0` conflict; confirmed the new inline comment
  explaining the column wrapper is accurate to what the code does; no dead classes or stale comments
  left behind. One real, non-blocking finding: `ReasoningTrace.tsx`'s root div (a sibling of the
  bubble inside the same new column) didn't itself carry `min-w-0`, and its tool-call line
  (`→ {step.tool}({formatArgs(step.arguments)})`, rendering an unbroken `JSON.stringify`'d args
  string) had no `break-words` — pre-existing code, not introduced by this diff, but the panel's new
  narrower-only context made it more likely to actually trigger the same overflow bug class this fix
  had just closed everywhere else.
- **Follow-up fix (`frontend-agent`, same session, sent back rather than deferred — cheap, directly
  on-topic, same bug class)**: added `min-w-0` to `ReasoningTrace.tsx`'s root div (`mt-2` →
  `mt-2 min-w-0`) and `break-words` to the tool-call line's className (`text-accent` →
  `break-words text-accent`). Re-ran `npx tsc --noEmit`, `npx next lint`, `npx next build` — all
  clean. Diff independently re-verified by the coordinator via direct file read, not just the
  agent's self-report.
- **Final verification (coordinator, independent of every agent's self-report)**: directly read the
  live `ChatPanel.tsx` message-rendering block after the primary fix and confirmed the described
  diff was actually present before sending it to `code-reviewer`; directly read the live
  `ReasoningTrace.tsx` after the follow-up fix and confirmed both changes (`min-w-0`,
  `break-words`) were actually present.
- Verify locally: open the floating chat launcher (desktop and a narrow/mobile-width viewport),
  send a question likely to produce a long unbroken prose reply and one likely to include inline
  code spans (not just a table), confirm the assistant bubble wraps within the visible panel with
  no horizontal scroll and no cut-off text at the panel edge; confirm short user messages still
  right-align correctly; confirm a markdown-table reply still scrolls only the table internally
  (unchanged from the entry above); expand a reasoning trace with a tool call that has a long/complex
  arguments object and confirm the trace text wraps instead of overflowing.

## Step 6 — MCP token-based auth
- **Status: done.** Reviewed this session (2026-07-12) — see "Review (this session)" below.
- **Storage (both DynamoDB, per the "Decisions made" entry above, 2026-07-11):**
  `opspilot-mcp-tokens` — one fixed-key item (`id="current"`; `token_hash` bcrypt, `created_at`,
  `revoked` bool, `revoked_at`). "Generate" always overwrites this single item wholesale,
  which both mints a new token and invalidates whatever token existed before (revoked or not)
  — true single-admin, single-active-token scope, no history of prior hashes kept here (that
  history lives in the audit log instead). `opspilot-audit-log` — one item per
  generate/revoke event (`id` uuid, `action`, `actor_email`, `created_at`, optional `detail`),
  scan + sort-by-created_at read path, same shape as `investigation_service.py`'s pattern. New
  `app/services/mcp_auth_service.py` (generate/revoke/get_status/is_token_valid, bcrypt via the
  `bcrypt` package — same primitive as the frontend's `bcryptjs` `ADMIN_PASSWORD_HASH`, added to
  `requirements.txt`) and `app/services/audit_log_service.py` (write_entry/list_recent_entries —
  the one write path Step 7 should extend for full Section 4 coverage, not duplicate).
- **HTTP routes** (`app/api/routes/mcp_auth.py`, wired into `main.py` behind `require_session`
  like every other router): `POST /mcp/token/generate` (returns the plaintext token once, never
  again), `POST /mcp/token/revoke` (404 no-op if nothing active, no audit entry written for a
  no-op), `GET /mcp/token/status` (has_active_token/created_at/revoked_at only — never the token
  or its hash). Both generate and a successful revoke write an audit log entry automatically
  (roadmap 3.6's explicit requirement), tagged with the real signed-in admin email from
  `require_session`'s `SessionUser`.
- **MCP transport enforcement** (`app/mcp/server.py`): stdio JSON-RPC has no per-request header
  channel, so the token is passed via the `OPSPILOT_MCP_TOKEN` environment variable, set in
  whatever launches the server process (Claude Desktop's `claude_desktop_config.json` "env"
  block; this repo's own `.env` for local testing, picked up via the `load_dotenv()` this app's
  config module already calls at import time). Read fresh from `os.environ` on *every* tool
  call (not cached at process start), and re-validated against DynamoDB each time — so revoking
  a token takes effect immediately without restarting whatever spawned the MCP process, closer
  to the roadmap's "every connection handshake" language than a startup-only check would be.
  Enforced via a `_AuthenticatedFastMCP(FastMCP)` subclass overriding `call_tool` — has to be a
  subclass override, not a post-construction monkeypatch, since `FastMCP.__init__` registers
  `self.call_tool` as the JSON-RPC handler immediately; independently verified end-to-end by
  invoking the real lowlevel JSON-RPC `CallToolRequest` handler (not just the test suite's
  direct `mcp.call_tool()` shortcut) with no token set — confirmed a clean
  `CallToolResult(isError=True, ...)` with no AWS call and no crash, not a raw exception. `list_tools`
  is deliberately NOT gated (tool discovery must stay reachable for `GET /mcp/tools`'s
  in-process introspection, which is already behind `require_session` at the HTTP layer and has
  no stdio token context) — only `call_tool` (and therefore every AWS-touching tool body) is
  gated, matching roadmap 3.6's "before any tool call or AWS role assumption" wording exactly.
- **Rate limiting / logging parity with the HTTP API**: grepped the codebase first — the HTTP
  API currently has **no rate-limiting middleware at all** (only `RequestIdMiddleware` for
  structured request logging, no `slowapi`/similar anywhere in `requirements.txt`). Per the
  explicit instruction not to invent a heavier scheme than what already exists, MCP got the
  logging half (`app.mcp.server` logger emits one line per tool call attempt — accepted or
  rejected — mirroring `RequestIdMiddleware`'s start/end HTTP access log) but no new
  rate-limiting mechanism, since matching "the same as the HTTP API" means matching its current
  (none) posture, not adding a first-of-its-kind limiter to only one of the two front doors.
  Flagged as a pre-existing gap on both surfaces for `security-reviewer` to weigh in on, not
  silently worked around.
- **Settings tab UI (frontend, new)**: added "Settings" as a 6th nav tab
  (`opspilot-frontend/components/NavBar.tsx`), `app/settings/page.tsx` +
  `components/SettingsPanel.tsx` (mirrors `McpPanel.tsx`'s load/error/loading state pattern and
  Tailwind conventions). Built the MCP Access section only, per the roadmap's own Section 4
  wrap-up ordering — "Connected account + IAM role ARN" and "Security posture summary" are
  explicitly Step 7's territory, not built here. Generate/Revoke buttons call the new
  `lib/api.ts` functions (`generateMcpToken`/`revokeMcpToken`/`getMcpTokenStatus`, same
  `authHeaders()`/fetch-wrapper pattern as every other call in that file); the plaintext token
  is held only in local component state (never re-fetched, never in `McpTokenStatus`) with a
  "copy this now" warning and a Copy-to-clipboard button, cleared on navigation/refresh; Revoke
  has a native `confirm()` guard. Exposed-tool list is **not** duplicated here — the section
  links to the existing `/mcp` page (`McpPanel.tsx`/`getMcpServerInfo()`) instead, per the
  instruction not to rebuild that fetch from scratch. MCP itself still is not a top-level tab —
  only its token lifecycle lives under Settings, matching roadmap 3.6's "doesn't get a top-level
  tab" line.
- **New IAM actions needed** (`docs/iam-policy.json`, new `OpspilotMcpTokenAndAuditLog`
  statement): `dynamodb:PutItem`, `dynamodb:GetItem`, `dynamodb:UpdateItem`, `dynamodb:Query`,
  `dynamodb:Scan` scoped to two new table ARNs —
  `arn:aws:dynamodb:us-east-1:<YOUR_ACCOUNT_ID>:table/opspilot-mcp-tokens` and
  `.../opspilot-audit-log` — user informed, add before live (non-mocked) testing. Both tables
  (partition key `id`, type String) also need to actually be created in DynamoDB by the user
  first, same as `opspilot-investigations` was for Step 8's memory feature — the app does not
  auto-create tables.
- **Testing**: 29 new backend tests across `tests/test_mcp_auth_service.py` (13),
  `tests/test_audit_log_service.py` (4), `tests/test_mcp_auth_route.py` (8), and 4 new
  auth-gate-specific tests appended to `tests/test_mcp_server.py` (plus an autouse fixture
  bypassing the gate for the pre-existing 19 tool-dispatch tests, which are about each tool's
  own behavior, not the gate). 306/306 backend tests pass, backend ruff clean. Frontend:
  `npx tsc --noEmit` clean, `npx eslint` clean on every changed file — `next lint`/`next build`
  themselves could not be run this session (tool-layer "temporarily unavailable" classifier
  error on every retry, unrelated to the code), flagged for the coordinator to re-run before
  merge rather than silently skipped.
- Verify locally: generate `AUTH_SHARED_SECRET`-signed session, visit `/settings`, click
  "Generate token," copy it, set it as `OPSPILOT_MCP_TOKEN` in `opspilot-backend/.env`, run
  `python -m app.mcp.server` (or point Claude Desktop's config at it) and confirm tool calls
  succeed; click "Revoke" in Settings and confirm the next MCP tool call is rejected without
  restarting the MCP process; confirm two new rows appear in DynamoDB's `opspilot-audit-log`
  table after generate/revoke.
- **Review (this session, 2026-07-12):** `code-reviewer` and `security-reviewer` run in
  parallel (read-only, no file overlap) against the full Step 6 diff.
  - `security-reviewer`: **clean, no blocking findings.** Explicitly confirmed rather than
    assumed: `bcrypt.checkpw` used for the token compare (constant-time, not a manual string
    comparison, wrapped in `except ValueError` to fail closed on a corrupt stored hash); no
    plaintext/hash ever reaches a log line, an audit-log `detail` field, or an error message
    (checked `mcp_auth_service.py`, `mcp/server.py`, the route, and `RequestIdMiddleware`
    directly); `require_session` actually wired on `mcp_auth.router` in `main.py` (not just
    assumed from convention) and backed by 401 tests; frontend `SettingsPanel.tsx` never
    puts the token in a URL/`console.log`/`localStorage`; `docs/iam-policy.json`'s new
    `OpspilotMcpTokenAndAuditLog` statement is scoped to the two exact new table ARNs, not
    `"*"`; this diff doesn't touch or worsen the pre-existing static-IAM-key gap (confirmed
    left untouched for Step 7). One low/informational note, not a finding requiring action:
    `is_token_valid`'s early-return path (`no token generated` vs. `wrong token`) is a
    theoretical timing distinguisher, but since the compared secret is a 256-bit
    `secrets.token_urlsafe` value, distinguishing those two states gives an attacker no
    practical advantage — noted for completeness, no fix needed. `list_tools` staying
    ungated and the no-new-rate-limiting decision were both independently re-evaluated (not
    just re-stated) and judged sound given the trust boundary and token entropy.
  - `code-reviewer`: also ran the full verification suite live (not just read the diff) —
    308/308 backend tests, backend ruff clean, frontend `npx tsc --noEmit` clean, `npx
    eslint` clean, and — closing last session's open item — `npx next lint` and `npx next
    build` both ran clean this time (`/settings` present in the route manifest), so the
    "couldn't run these last session" gap noted above is now resolved. Three findings:
    1. **(Moderate, fixed)** `app/api/routes/mcp_auth.py`'s `generate_mcp_token`/
       `revoke_mcp_token` called the state-changing service function first, then
       `audit_log_service.write_entry(...)` with no `try`/`except` — since `write_entry`
       deliberately does not swallow DynamoDB failures, a transient failure on the audit
       write turned an *already-successful* mutation into an unhandled 500: on generate,
       the new token was already persisted (invalidating the prior one) but the caller would
       never receive the one-time plaintext; on revoke, a retry after such a failure would
       hit `revoke_token()` returning `False` and get a misleading 404 ("nothing to revoke")
       for a revoke that had, in fact, already succeeded. **Sent back to `mcp-agent`,
       fixed**: both routes now wrap the audit write in `try/except Exception`, log
       `audit_log_write_failed action=...` via a new module logger (matching
       `app/api/routes/resources.py`'s existing `# noqa: BLE001` catch-all-and-log
       convention) on failure, and still return the normal success response either way,
       since the mutation itself is the source of truth. Two new regression tests added
       (`test_generate_token_still_returns_token_when_audit_write_fails`,
       `test_revoke_token_still_succeeds_when_audit_write_fails`), each mocking
       `audit_log_service.write_entry` to raise and asserting the route still returns 200
       with the correct body — independently re-read by the coordinator (not just trusted),
       confirmed genuine (they assert on the actual HTTP response, not just that no
       exception propagated).
    2. **(Low, fixed)** `McpTokenGenerateResponse.warning` was returned over the wire and
       typed in `lib/api.ts`, but `SettingsPanel.tsx` ignored it and hardcoded its own copy
       of the identical sentence — harmless today, but a dead field that could silently
       drift if either side were edited independently. **Fixed**: `SettingsPanel.tsx` now
       renders `result.warning` (via a new `freshTokenWarning` state, set alongside
       `freshToken` in `handleGenerate` and cleared alongside it in `handleRevoke`) instead
       of a separately-maintained string.
    3. **(Low, deferred, not fixed)** the four new auth-gate tests in `test_mcp_server.py`
       call `call_tool`/the `_call_tool` test helper directly rather than exercising the
       real lowlevel JSON-RPC `CallToolRequest` dispatch path end-to-end — so nothing in the
       committed suite would catch a future refactor of FastMCP's handler-registration
       wiring silently routing around the `_AuthenticatedFastMCP` override. The subclass
       wiring itself was manually verified end-to-end this build (see the MCP transport
       enforcement note above) but that verification isn't captured as a regression test.
       **Coordinator judgment: deferred, not delegated.** Low severity (would require a
       wiring regression in a third-party-adjacent layer to matter, and the manual
       verification already happened once), and closing it properly needs standing up a
       real lowlevel-dispatch test harness rather than a one-line assertion — a
       meaningfully larger unit of work than the other two findings, not a "cheap while
       you're in the area" fix. Tracked here as an accepted gap for whenever MCP-transport
       test infrastructure is revisited, not silently dropped.
  - Fixes independently re-verified by the coordinator directly (not just re-trusting
    `mcp-agent`'s self-report): read both changed files end to end, confirmed the
    `try/except`+log-then-return-success shape in `mcp_auth.py` and the `freshTokenWarning`
    plumbing in `SettingsPanel.tsx`; re-ran the full suite from scratch: **310/310 backend
    tests pass, backend ruff clean, frontend `npx tsc --noEmit` clean, `npx eslint` clean on
    the changed files, `npx next build` clean with `/settings` in the route manifest.**
- Step 6 is now fully closed out — demoable end to end (Settings UI generate/revoke, MCP
  process picking up `OPSPILOT_MCP_TOKEN`, audit log rows) and clean on both review passes,
  with one explicitly-accepted low-severity test-coverage gap noted above.

## Step 7 — Security hardening pass + SECURITY.md
- **Status: in progress.** Whole-app audit run first (`security-reviewer`, read-only, before
  any implementation), per roadmap Section 4 and the instruction to actually resolve (not
  re-defer a sixth time) the static-AWS-IAM-keys item that's been flagged through every prior
  step. Two independent tracks opened off the audit; a third item is paused on a user decision.
- **Audit findings (`security-reviewer`, full-repo pass, 2026-07-12):**
  1. **(High) Static long-lived AWS IAM user access keys** — `app/aws/client.py`'s `_session()`
     is a plain `boto3.Session(region_name=...)` that silently resolves
     `AWS_ACCESS_KEY_ID`/`AWS_SECRET_ACCESS_KEY` from the environment; these are non-expiring
     IAM user keys, re-flagged as "Step 4/Step 7's territory" in the Step 1, Step 3, Step 4, and
     Step 5 security reviews and never actually addressed. Reviewer's own read on the roadmap's
     scoping: the "cross-account role assumption + external ID" bullet is genuinely tied to the
     deferred multi-account work (Section 8), but the adjacent "no long-lived credentials stored
     anywhere" bullet is a separate, general principle that doesn't require multi-account at
     all — a same-account self-assumed IAM role (`sts:AssumeRole` within the one connected
     account, 15-60 min sessions, refreshed on expiry) would satisfy it without touching
     Section 8's deferred scope. Reviewer flagged this explicitly as needing a human decision
     (fix now via same-account role assumption vs. explicitly document as an accepted
     single-account/local-only-scope limitation in `SECURITY.md`) rather than picking one — also
     warned `SECURITY.md` must not claim "no stored credentials" if the code still uses static
     keys, whichever way this is decided.
     **Coordinator: stopped and asked the user rather than picking either option** — this
     changes what the user needs to set up in their AWS account (a new IAM role + trust policy)
     and is exactly the kind of scope/infrastructure tradeoff the build's own ground rules say
     needs a human call, not a guess. Awaiting user decision — see "Decisions made" section /
     this file's top for the answer once given, this entry will be updated.
  2. **(Medium) Audit log coverage gap** — `audit_log_service.write_entry` is called from
     exactly one file (`mcp_auth.py`), for exactly two actions, since Step 6. No login
     success/failure record exists anywhere despite this being a single-admin app's actual
     authentication boundary. Reviewer's scoped recommendation, weighing signal value against
     DynamoDB write volume for a single-admin app: add login success/failure now (cheap, high
     value); do **not** persist every MCP tool call to DynamoDB (already covered by
     `app.mcp.server`'s existing per-call logger lines, and "tied to a real logged-in user" can't
     literally apply to a single shared MCP token anyway — document that honestly rather than
     implying per-user MCP attribution exists); do **not** persist every dashboard GET (read-only,
     already session-gated, no side effects worth an audit trail until Step 8's write/approval
     layer exists — `write_entry` is designed to be extended there, not duplicated); don't build
     a new "agent flags" audit path since `opspilot-investigations` already durably records
     agent findings.
     **Coordinator: this is a scoped technical decision I can make without the user (not an
     infra/deployment tradeoff)** — agreed with the reviewer's recommendation exactly as given.
     Delegated to `auth-agent` (in progress, see below), sequenced independently of the IAM-keys
     question above since it doesn't touch `app/aws/client.py` at all.
  3. **(Low) No rate limiting/lockout anywhere** (login path, MCP `call_tool` path) —
     re-confirmed still present, correctly deferred at Steps 1 and 6 ("before any
     internet-facing deploy"). Roadmap Section 4 itself doesn't mandate rate limiting; this is a
     reviewer-raised hardening item. **Decision: document as an accepted limitation in
     `SECURITY.md` with an explicit "must add before public deploy" callout, not a Step 7
     blocker** — matches the coordinator's own read, no user input needed.
  4. **(Low) Config-error messages leak a misconfiguration fact** — `app/core/security.py`
     ("Auth is not configured on the server.") and `lib/auth.ts` ("ADMIN_EMAIL /
     ADMIN_PASSWORD_HASH are not configured on the server.") both return which specific env var
     is missing to an unauthenticated client; no secret leaked, but a free fix. Re-confirmed
     unchanged since Step 1's "no blocker" verdict. **Decision: fix now, cheap** — delegated to
     `auth-agent` alongside the audit-log work (same file, `security.py`, already in scope).
  5. **Confirmed clean, no action needed:** secrets hygiene (`git ls-files` + full history sweep
     — only the two `.env.example` files ever tracked, no secret ever committed, live or
     historical); `docs/iam-policy.json` least-privilege (every action `Describe*`/`List*`/
     `Get*`/`pricing:*`/`apigateway:GET`, zero write/mutate permission against any real monitored
     AWS resource; the two write-capable DynamoDB statements are ARN-scoped to exactly this
     app's own 3 bookkeeping tables, not `"*"`; no `ce:GetCostAndUsage` present — cost estimation
     already uses only the cheaper/more-conservative Pricing API, already labeled as such);
     MCP token handling (re-confirmed sound, matches Step 6's own review); cross-account
     readiness (correctly not started, nothing to flag); request logging (`RequestIdMiddleware`
     logs method/path/status/duration only, no headers/body/token/cookie ever logged).
     **Needs user action, not code** (can't be resolved by an agent): GitHub push protection
     status couldn't be verified from an unauthenticated API call (repo confirmed **public**, so
     secret scanning is auto-on and free, but push-protection's on/off state needs a repo owner
     with an authenticated token to check under Settings -> Code security) — flagged to the user,
     not blocking Step 7's other work.
  - Reviewer's draft `SECURITY.md` outline (not written yet, captured for whoever writes it):
    overview/posture statement; authentication (NextAuth + FastAPI session verification);
    **AWS access model** (must state plainly whichever way the IAM-keys decision lands — not
    overstate); least privilege / IAM policy summary; secrets handling; MCP token auth; audit
    logging (honest current-coverage description, not aspirational); known limitations/accepted
    gaps (rate limiting, config-error granularity, multi-account, write/approval layer); a real
    responsible-disclosure contact (currently missing entirely, needs an actual value, not a
    placeholder — will need the user's email/contact preference).
- **Built by `auth-agent` (2026-07-12), review paused mid-gate — resume here:** item 2 (login
  success/failure audit logging) + item 4 (generic config-error messages), built together since
  both are auth-surface-only and don't touch AWS credential handling.
  - New `POST /auth/login-audit` (`opspilot-backend/app/api/routes/auth_events.py`), deliberately
    registered **outside** the `require_session`-gated router group in `main.py` (there is no
    session yet at the point `authorize()` calls it) — instead gated by a new HMAC-SHA256
    signature check (`app/core/security.py`'s `sign_login_event`/`verify_login_event_signature`,
    signing `f"{action}:{email}:{ts}"` with the existing shared `AUTH_SHARED_SECRET`, verified
    with `hmac.compare_digest` + a 60s timestamp freshness window). `AuditAction` widened to add
    `login_success`/`login_failed`. `opspilot-frontend/lib/auth.ts`'s `authorize()` now calls
    `recordLoginAudit(...)` on every exit path (missing creds, email mismatch, password mismatch,
    success), non-blocking (3s `AbortController` timeout, try/catch that only `console.error`s,
    never throws into the login flow). Audit write itself is also non-blocking on the backend
    (try/except around `audit_log_service.write_entry`, mirrors Step 6's `mcp_auth.py` pattern).
  - Config-error fix: both `security.py`'s `require_session` 503 and `lib/auth.ts`'s
    `authorize()` config-check now throw/return a shared generic `AUTH_UNAVAILABLE_MESSAGE`
    instead of naming the specific missing env var; the specific detail still goes to a
    server-side log line only.
  - New `opspilot-backend/tests/test_auth_events_route.py` (12 tests). auth-agent reported
    320/320 backend tests passing, ruff clean, `tsc`/`eslint` clean.
  - **Coordinator's own read of the code (done before dispatching review)**: read
    `security.py`, `auth_events.py`, and `lib/auth.ts` directly. Mechanism looks sound at a
    glance — secret itself is never transmitted (only an HMAC derived from it), constant-time
    comparison used, replay window is short and this endpoint can only ever write an audit-log
    row (no session-minting, no state-changing action beyond that), non-blocking on both sides.
    **This is still just my own read, not a substitute for the review gate** — flagging that
    explicitly since this file's own rules require independent review before a step counts as
    reviewed, not just a coordinator skim.
  - **Review (resumed and completed, 2026-07-12):** both `code-reviewer` and `security-reviewer`
    re-run to completion (fresh full passes, not continuations) after the earlier interruption.
    - `security-reviewer`: **no blocking findings.** Confirmed the deliberate `require_session`
      bypass is sound — the route's only side effect is an `audit_log` write (no session
      minting, no auth-bypass), `hmac.compare_digest` used correctly, config-error fix verified
      consistent on both sides, `recordLoginAudit` confirmed unable to throw into `authorize()`.
      Two low findings: (1) the HMAC canonical string's unescaped `:` delimiter over an
      attacker-controlled `email` field is theoretically ambiguous (a crafted email containing
      `:<digits>` could in principle reproduce another (email, ts) pair's signature) but **not
      currently exploitable** — the signature never leaves trusted server-to-server
      infrastructure and isn't echoed anywhere reachable pre-auth; (2) no length bound on
      `email`, cheap fix. Also flagged, not a defect: `actor_email` now carries two different
      trust levels depending on `action` (verified identity for MCP-token actions, raw
      unauthenticated input for login actions) with nothing documenting the distinction.
    - `code-reviewer`: ran the full verification suite live — 320/320 backend tests, ruff clean,
      `tsc`/`eslint` clean. One **moderate, real** finding: the one test not mocking
      `audit_log_service.write_entry` (`test_login_audit_does_not_require_a_session_bearer_token`)
      was making a genuine boto3 DynamoDB `put_item` call against this checkout's real `.env`
      credentials on every test run (confirmed via a `datetime.utcnow()` SigV4-signing
      deprecation warning unique to that test) — silently writing live rows to the real
      `opspilot-audit-log` table and adding real network latency/flakiness in a
      credential-less CI environment, masked because the route's own try/except swallows the
      failure either way. Plus three low findings: test-count reporting said 12, actually 10
      (correcting the record here — see below); two stale docstrings describing Step 7's own
      audit-log extension as future work; `recordLoginAudit` being `await`ed on every
      `authorize()` exit path meant every login (success or failure) waited up to the 3s fetch
      timeout for a DynamoDB-adjacent round trip before resolving, contradicting its own
      "must never block login" framing (didn't block on *failure*, but did block on *latency*).
    - **All five actionable findings sent back to `auth-agent`, fixed, independently
      re-verified by the coordinator (read the actual diffs, not just the report):**
      (1) added the missing `@patch` to the one unmocked test, confirmed by reading
      `test_auth_events_route.py` directly — 8 of its 10 tests now mock `write_entry` and
      the other 2 legitimately never reach it (422 missing-signature validation, 503
      secret-not-configured fail-closed, both confirmed by test name/behavior, no mock needed);
      (2) `LoginAuditRequest.email` now `Field(max_length=320)`, mirrored client-side in
      `lib/auth.ts` via `.slice(0, MAX_LOGIN_AUDIT_EMAIL_LENGTH)`; (3) both docstrings rewritten
      to past/present tense reflecting the actual current write set; (4) `authorize()`'s four
      `recordLoginAudit(...)` calls changed from `await`ed to `void`-prefixed genuine
      fire-and-forget — `auth-agent` checked deployment target first (`next start`, a
      long-running Node process per `package.json`, README confirms local-only/Docker-Compose
      deployment, no serverless/edge target where an unawaited promise risks early
      termination) before choosing fire-and-forget over just shortening the timeout, and
      documented the reasoning + revisit condition inline; (5) added a `actor_email` trust-level
      comment to `AuditLogEntry` in `models/audit_log.py`. **Deferred, not fixed:** the HMAC
      delimiter ambiguity (security-reviewer's finding 1) — real but not currently exploitable,
      closing it properly needs an unambiguous encoding (length-prefixing or signing
      `json.dumps([...])`), a heavier change than this fix batch; tracked here as a follow-up.
      Re-verified independently by the coordinator: read `auth_event.py`, `audit_log.py`,
      `audit_log_service.py`'s docstring, `lib/auth.ts`'s `void recordLoginAudit(...)` call
      sites, and `test_auth_events_route.py`'s test names directly (not just the agent's
      self-report); re-ran the full suite from scratch: **320/320 backend tests pass, ruff
      clean, frontend `npx tsc --noEmit` clean, `npx eslint lib/auth.ts` clean.**
  - **This sub-item (login audit logging + config-error fix) is now done and fully reviewed.**
- **User decision on item 1 (static AWS IAM keys), received 2026-07-12: document as an accepted
  limitation, do not fix now.** Rationale given: this is currently a local, single-admin,
  not-internet-facing demo, and short-lived assumed-role sessions would require real setup
  complexity (a new IAM role + trust policy in the user's AWS account) for a threat model that
  doesn't yet apply. Explicit condition attached to this decision, not an open-ended pass: **must
  be upgraded to short-lived assumed-role sessions before this app is ever hosted anywhere
  reachable by anyone other than its single operator.** `docs/SECURITY.md` (below) states this
  plainly — long-lived credentials are in use, exactly where in code/config
  (`opspilot-backend/.env`'s `AWS_ACCESS_KEY_ID`/`AWS_SECRET_ACCESS_KEY`, read by
  `app/aws/client.py`'s `_session()` with no role-assumption/refresh logic), and the upgrade
  condition above — not a claim of a stronger posture than the code actually has.
- GitHub push-protection on/off status still needs manual verification by the user in repo
  settings (Settings -> Code security) — not verifiable or fixable from the codebase itself, not
  blocking Step 7's other work, tracked in `docs/SECURITY.md` Section 8's gap table too.
- **`docs/SECURITY.md` written** (2026-07-12), covering: posture overview; authentication
  (mechanism, login-audit-logging, the two known auth gaps); the AWS access model (item 1's
  decision, stated honestly per above); least-privilege IAM policy summary; secrets handling;
  MCP token auth; audit logging (real current coverage: `mcp_token_generated`/
  `mcp_token_revoked`/`login_success`/`login_failed`, what's deliberately not covered and why,
  the `actor_email` trust-level distinction); a known-limitations summary table; a responsible-
  disclosure contact (the project owner's real email, not a placeholder, since this is a
  single-admin personal project — `zaryabbaloch04@gmail.com`).
  - **`security-reviewer` accuracy pass (2026-07-12):** verified every factual claim in the
    document against the actual code, section by section (JWT TTL/claims, the AWS-access-model
    section's code-level claims, the full `docs/iam-policy.json` contents, `.gitignore`
    coverage + full git-history secret sweep + public-repo confirmation via `git remote -v`,
    MCP token hashing/entropy/fail-closed behavior, and a repo-wide grep confirming
    `audit_log_service.write_entry` has exactly the three call sites the document claims and no
    others). **Verdict: "unusually accurate... no real credential leak, no mutating IAM action
    against a monitored resource, no materially false security claim."** Two precision findings,
    both fixed directly (documentation-only, not delegated — same as this file's own maintenance
    pattern): (1) Section 3's "the project owner decided" framing implied a decision already
    logged in this file when it hadn't been yet — fixed by adding this very decision record
    above, so the document's own "kept in sync with BUILD_PROGRESS.md" claim is actually true;
    (2) Section 2 claimed both frontend and backend "fail closed with a 503" on a missing
    `AUTH_SHARED_SECRET" — accurate for the backend (`require_session`/
    `verify_login_event_signature` genuinely raise HTTP 503) but not the frontend, where
    NextAuth's `authorize()` throws a plain `Error` with no HTTP status involved at all (it's
    not a REST response) — reworded to describe each side's actual mechanism instead of implying
    a shared status code. One non-blocking side-note surfaced (not a `SECURITY.md` inaccuracy,
    a small frontend/backend inconsistency worth a future cleanup): `GalaxyView.tsx` has UI
    code for a `cost.method === "billed"` label the backend can never actually produce (no
    `ce:GetCostAndUsage` call exists anywhere) — dead code implying a capability that doesn't
    exist, left as-is, not in this step's scope.
- **Step 7 is now done.** All items closed: static-IAM-keys question resolved by explicit user
  decision and honestly documented (not fixed in code, as decided); login audit logging +
  config-error fix built and fully reviewed (code-reviewer + security-reviewer, all findings
  fixed and re-verified); `docs/SECURITY.md` written and verified accurate against the real code
  by `security-reviewer`, both precision findings fixed. Remaining non-blocking follow-up,
  explicitly not this step's job: GitHub push-protection manual check (user action, tracked in
  `docs/SECURITY.md` Section 8), the HMAC-delimiter-ambiguity hardening item (deferred earlier
  this step, tracked), and the dead `cost.method === "billed"` frontend branch noted just above.
- Verify locally: read `docs/SECURITY.md` end to end and confirm it matches the running app's
  actual behavior (no rate limiting on login, static AWS keys in `opspilot-backend/.env`, MCP
  token required even on localhost, audit log covering exactly the four action types listed);
  trigger a failed login and a successful login and confirm both produce a `login_failed`/
  `login_success` row in the `opspilot-audit-log` DynamoDB table without any added delay to the
  sign-in flow itself.

### Post-ship fix (2026-07-12) — nav/Settings finish Section 5's locked UI spec + scan-request race condition
Two confirmed, previously-scoped gaps found via direct investigation this session (not new roadmap
scope — Step 5 stays **done**, this is another post-ship pass on it, same category as the three
above; the concurrency fix also touches `scan_service.py`, Step 4's territory, logged here to keep
both halves of this session's work together).

**Task 1 — Nav tabs + Settings sections didn't match roadmap Section 5's locked spec.** Confirmed
gap: `NavBar.tsx`'s `TABS` was `Galaxy, Resources, Investigations, MCP Server, Settings` against the
roadmap's locked `Galaxy (default) · Idle Resources · Investigations · Cost Overview · Audit Log ·
Settings` — Idle Resources/Cost Overview/Audit Log were missing entirely, and `Resources`/`MCP
Server` were live as top-level tabs despite `MCP Server` not being one per the roadmap (this
codebase's own prior notes already said as much). `SettingsPanel.tsx` had only the MCP Access
section built (Step 6); "Connected account + IAM role ARN" and "Security posture summary" were
deferred at Step 6 but never actually assigned to a build step since.
- **Backend (`backend-agent`), done and reviewed:**
  1. `GET /audit-log` (`app/api/routes/audit_log.py`) — thin route onto `audit_log_service.list_recent_entries()`, which already existed (built Step 6, no route ever wired to it). `limit` query param, `ge=1, le=200`, default 50. Gated by `require_session` in `main.py`.
  2. `GET /aws/account` (`app/api/routes/aws_account.py`, `app/services/account_service.py`, `app/models/account.py`) — new `AccountIdentity{account_id, region}` via `sts:GetCallerIdentity` (new `get_sts_client()` in `app/aws/client.py`), `@lru_cache`d (account identity never changes for a static-key process). Deliberately **excludes** the IAM ARN/UserId `GetCallerIdentity` also returns — this app has an established precedent (Step 5) of stripping ARNs everywhere specifically to keep the account ID out of caller-facing text; showing the bare `account_id` is the explicit roadmap ask, no reason to also surface the raw principal ARN on top of it. **Coordinator-identified constraint before delegating**: this new account-ID-exposing surface must be dashboard-only — never wired into `app/mcp/server.py` or the chat agent's tool list (`app/agent/orchestrator.py`), since MCP/chat tool output reaches the LLM provider, exactly the leak vector Step 5's ARN-stripping fix existed to prevent. Both `code-reviewer` and `security-reviewer` independently grepped (not trusted the docstrings) and confirmed zero references in either surface.
  3. `docs/iam-policy.json`: added `sts:GetCallerIdentity` to `OpspilotReadOnly` (`Resource: "*"`, correct shape — this action has no resource-level restriction). **User needs to apply this before live (non-mocked) testing of `GET /aws/account`.**
  4. New tests: `tests/test_audit_log_route.py`, `tests/test_account_route.py`, `tests/test_account_service.py`. 330/330 tests, ruff clean.
- **Review (code-reviewer + security-reviewer, parallel, backend diff only):** both clean, no blocking findings.
  - `security-reviewer`: independently confirmed the account-ID boundary holds (zero MCP/agent references via grep), `GET /audit-log` exposes nothing beyond existing `AuditLogEntry` fields with a bounded `limit` param (no injection surface), `require_session` wired on both new routers, `docs/iam-policy.json`'s new grant correctly scoped with nothing broader added alongside it, no new secrets, no existing `docs/SECURITY.md` claim contradicted. One non-blocking observation, accepted: the new account/audit-log surfaces add no new SECURITY.md-documented behavior yet (expected — doc sync happens after the frontend Settings UI lands, not this half).
  - `code-reviewer`: independently re-ran the full suite (330/330, ruff clean — confirmed real, not just trusted). One non-blocking finding: `GET /aws/account` and `GET /audit-log` have no `try/except` around their AWS/DynamoDB calls, so a genuine STS/DynamoDB failure would 500 via FastAPI's default handler rather than a clean sanitized message, unlike the rest of this codebase's AWS-touching routes. **Coordinator triage: deferred, not fixed** — FastAPI's default unhandled-exception handler returns a generic "Internal Server Error" with no exception detail when not in debug mode (confirmed `app/main.py` doesn't enable debug), so this isn't an actual information-leak risk today, just an inconsistency with this codebase's usual defensive-wrapping convention on two low-traffic Settings/Audit pages. Worth closing if debug mode is ever considered, or just for consistency — tracked here, not blocking.
- **Frontend (`frontend-agent`), done and reviewed:**
  - `NavBar.tsx` `TABS` now exactly Galaxy, Idle Resources, Investigations, Cost Overview, Audit
    Log, Settings (roadmap Section 5 order). `Resources` and `MCP Server` removed from the nav —
    both routes/components left completely untouched and reachable by direct URL (same treatment
    as `/chat`'s prior removal): `/resources` (`ResourcesPanel.tsx`) kept because it provides real,
    non-duplicated value (EC2-only CPU-sparkline deep dive + account-overview cards, neither
    replicated by the scan-derived all-15-types views below); `/mcp` kept because roadmap 3.6 says
    outright it's not a top-level tab, only its token lifecycle belongs under Settings. Top-of-file
    comment rewritten to describe the final state instead of the stale "Step 7" placeholder text.
  - New tab **Idle Resources** (`/idle-resources`, `IdleResourcesPanel.tsx`) — filters
    `scanRegion()`'s existing resources on `idle.idle_days >= 7`, matching `GalaxyView.tsx`'s own
    `IDLE_PULSE_THRESHOLD_DAYS` display convention (confirmed by `code-reviewer`: not `is_idle`
    directly, per the data-schema skill's documented distinction). No new backend endpoint — reuses
    the existing scan response.
  - New tab **Cost Overview** (`/cost-overview`, `CostOverviewPanel.tsx`) — `scan.totals` HUD +
    client-computed per-type breakdown; resources with `cost: null` are excluded and counted
    separately ("cost lookup failed for N of M"), never silently treated as $0 (confirmed by
    `code-reviewer`). Explicit "list-price estimate, not billed cost" disclosure shown near the
    total per the data-schema skill's requirement to label the cost method.
  - New tab **Audit Log** (`/audit-log`, `AuditLogPanel.tsx`) — new `getAuditLog()` +
    `AuditLogEntry`/`AuditLogEntryList` types added to `lib/api.ts` (same `authHeaders()` pattern as
    every other function there, confirmed by both reviewers), backed by the new `GET /audit-log`
    route above. UI states the real four-action-type coverage honestly (per `docs/SECURITY.md`
    Section 7) rather than implying broader coverage, and surfaces a trust-level badge
    distinguishing verified-admin vs. unverified-login-attempt `actor_email` (matching
    `AuditLogEntry`'s own doc-comment framing). `security-reviewer` confirmed `actor_email`/`detail`
    (attacker-controllable on `login_failed` rows) render as plain JSX text, not
    `dangerouslySetInnerHTML` — no injection risk from that free-text field.
  - **Settings**: added "Connected account" (new `getConnectedAccount()`/`AccountIdentity` in
    `lib/api.ts`, renders only `account_id`+`region`, nothing fabricated) and "Security posture
    summary" (hand-authored, not a live backend call, sourced directly from `docs/SECURITY.md` —
    both reviewers independently checked it side-by-side with the real `SECURITY.md` and confirmed
    it plainly states the static/long-lived AWS IAM key limitation rather than softening it, and
    doesn't overstate audit-log coverage). Code comment flags the doc-drift risk if `SECURITY.md`
    changes later without this component being updated in tandem (self-documented, mirroring
    `SECURITY.md`'s own "kept in sync with BUILD_PROGRESS.md" discipline).
  - New shared infra: `lib/useRegionScan.ts`, `lib/format.ts`, `components/RegionScanToolbar.tsx`
    (region/refresh/cooldown state + formatting helpers reused by the two new scan-derived tabs).
  - **Review (code-reviewer + security-reviewer, parallel, frontend diff only):** both clean, no
    blocking findings.
    - `security-reviewer`: confirmed auth consistency (new `lib/api.ts` functions use the identical
      pattern, both new backend routes gated by `require_session`, `middleware.ts`'s matcher
      naturally covers the three new pages with no exclusion introduced), no unsafe rendering, no
      new leak surface (no `console.log` of sensitive data, no token in a URL, no new
      unauthenticated fetch), standard secrets sweep clean.
    - `code-reviewer`: independently re-ran `tsc`/`lint`/`build`, confirmed real. One **non-blocking
      finding, deferred by the coordinator**: `lib/useRegionScan.ts` was built as an extraction
      target but `GalaxyView.tsx` was never actually refactored to consume it — there are now two
      independently-maintained copies of the same region/scan/in-flight-dedupe/cooldown logic
      (both currently agree byte-for-byte, both self-document the tradeoff in their own comments).
      **Coordinator triage: deferred, not fixed this round** — not an active bug today, and closing
      it means refactoring the exact file (`GalaxyView.tsx`) that just went through two careful
      rounds of bug-fixing and live Playwright verification this same session; taking on that
      refactor risk for a non-blocking finding wasn't judged worth it right now. Tracked here as a
      real follow-up: the next time `GalaxyView.tsx`'s scan/refresh logic needs a fix (like this
      session's in-flight-dedupe fix), it currently has to be hand-applied to both copies.
  - **Final independent verification (coordinator, both halves of this fix, re-run from scratch
    after both review passes):** backend **330/330 tests pass, ruff clean**; frontend `npx tsc
    --noEmit` clean, `npx next lint` clean ("No ESLint warnings or errors").
  - Verify locally: sign in, confirm the nav reads exactly Galaxy / Idle Resources / Investigations
    / Cost Overview / Audit Log / Settings in that order, with no Resources/MCP Server tabs (both
    still load fine by direct URL); load `/idle-resources` and confirm only resources with
    `idle_days >= 7` appear; load `/cost-overview` and confirm the total matches Galaxy's HUD figure
    and the list-price disclosure is visible; load `/audit-log` and confirm recent
    generate/revoke/login rows appear newest-first; load `/settings` and confirm "Connected account"
    shows a real account ID + region and "Security posture summary" reads consistently with
    `docs/SECURITY.md` (including the static-IAM-key disclosure).
- **Task 1 status: done.**

**Task 2 — scan-request race condition, confirmed via live backend log this session.** Reproduced:
two `GET /resources/scan?region=us-east-1` requests fired ~52ms apart on page load. First took ~130s
(Redshift/Kinesis fail slow with real `OptInRequired`/`SubscriptionRequired` errors in this account —
an account characteristic, not a bug, 13/15 collectors still succeed fast). Second request raced
`scan_service.py`'s old fixed 60s no-cache wait, lost, and — since this was the first scan since a
server restart (empty cache) — surfaced as a 502 to the user despite the first request succeeding
shortly after.
- **Backend root cause + fix (`backend-agent`), done and reviewed:** the old design had a second
  no-cache caller race a fixed `_NO_CACHE_WAIT_TIMEOUT_SECONDS = 60` against the first caller's real
  completion time — a design flaw independent of how long a legitimate scan takes, not just a
  too-low constant. **Fixed properly** (per instruction not to just raise the cap): replaced with a
  per-region `concurrent.futures.Future` in a new `_in_flight_scans` dict (guarded by
  `_in_flight_scans_guard`). `_get_or_create_in_flight_future(region)` atomically elects one "winner"
  caller; the winner runs `_do_scan()` and resolves the future (`set_result`/`set_exception`) in a
  `try/except BaseException/else/finally` (cleanup always runs); every other concurrent caller for
  that never-cached region blocks on `future.result(timeout=_NO_CACHE_WEDGED_SCAN_TIMEOUT_SECONDS)`
  (renamed, now 600s) — now purely a "this looks wedged" backstop, not the normal success/failure
  decider, so a legitimately slow 130s+ scan is no longer punished. The `force=True`-against-
  existing-cache cooldown path is untouched. New regression test
  (`test_no_cache_concurrent_callers_both_get_winners_result_past_old_fixed_cap`,
  `tests/test_scan_service.py`) uses two real `threading.Thread`s synchronized with
  `threading.Event`s (not `time.sleep`), proving both callers get the identical result object, only
  one real scan runs, and the second caller survives well past what the old 60s cap would have
  allowed.
  - `security-reviewer`: confirmed the Step 4 region-normalization fix isn't reintroduced/weakened
    (`_validate_region` still runs before any cache/lock/`_in_flight_scans` access, so the dict can
    only ever hold one entry per real enabled AWS region, not per arbitrary input string); confirmed
    exception propagation from winner to losers never leaks a raw AWS exception (only the
    already-sanitized `ScanFailedNoCacheError` crosses the future, raw cause stays server-log-only).
    One non-blocking observation: the 600s backstop means a waiting caller can now hold a
    `run_in_threadpool` worker thread up to 10x longer than before under a genuinely wedged scan —
    an accepted tradeoff per the module's own "don't punish a legitimately slow scan" reasoning, flagged for awareness if this app ever moves beyond single-admin scale.
  - `code-reviewer`: traced the full winner/loser lifecycle directly — cleanup-always-runs confirmed,
    no double-winner race window confirmed (atomic get-or-create under one lock), losers'
    `FutureTimeoutError` fallback correctly re-checks the cache before giving up. Confirmed the new
    concurrency test is genuine (real threads, not a shallow mock). No blocking findings.
- **Frontend root cause + fix (`frontend-agent`), done and reviewed:** confirmed root cause by
  direct code read: `GalaxyView.tsx`'s `region` state initializes hardcoded (`useState("us-east-1")`,
  line 493), and the `[region]`-keyed effect (line ~618) fires `runScan(region, false)` on mount.
  `next.config.mjs` has `reactStrictMode: true` (confirmed) -- in dev this double-invokes the effect
  (mount -> cleanup -> mount again), firing two near-simultaneous `scanRegion()` calls, exactly the
  two `GET /resources/scan` requests ~52ms apart reproduced live this session. The pre-existing
  `requestIdRef` guard only ever prevented a stale *response* from overwriting fresher state -- it
  never stopped the redundant *request* from being sent, which is what raced the backend's
  now-fixed concurrency path in the first place.
  - **Fix**: added `inFlightRef` (`useRef<Map<string, Promise<ScanResponse>>>`) inside `runScan`,
    keyed by `` `${targetRegion}:${force}` `` -- a second caller for the same in-flight key reuses
    the first caller's still-pending promise instead of issuing a new `scanRegion()` call, with a
    `.finally()` identity-checked cleanup so a later, genuinely new re-scan of the same target is
    never wrongly deduped. Coexists correctly with the untouched `requestIdRef` guard -- the two
    solve different problems (dedupe the request vs. gate which response applies) and don't
    conflict, confirmed by `code-reviewer` reading the full interaction directly.
  - **Live end-to-end verification** (real dev server, real logged-in session, cross-checked
    against the backend's own request logs, not just client-side counts): mount fired exactly 1
    `GET /resources/scan` across 4 separate page loads; manual force-refresh fired exactly 1
    request (165.5s round trip for a genuine uncached rescan in this environment, not a bug).
    Region-switching not live-observable (this AWS account currently exposes only one enabled
    region) but the fix's key structure structurally guarantees a region switch always produces a
    fresh, never-deduped key.
  - **Review (code-reviewer + security-reviewer)**: both clean, no blocking findings.
    `code-reviewer` verified line-by-line: the `.catch(() => {})` on the bookkeeping chain only
    guards the `Map`-cleanup promise against an unhandled-rejection warning, it does not consume
    the rejection for real callers (each caller's own `await scanPromise` inside its `try` block
    still sees the rejection and hits the existing error-handling path); no `Map`-entry leak on any
    code path (identity-checked delete in `finally`, runs on both success and failure).
    `security-reviewer` confirmed this is pure client-side request-coalescing -- no new fetch
    surface, no new params, no bypass of the cooldown/auth logic, reuses the same authenticated
    `scanRegion()` call GalaxyView already made.
- **Task 2 status: done.** Both the backend design flaw (fixed timeout race -> shared-future
  coalescing) and the frontend duplicate-fire trigger (StrictMode double-invoke with no request
  dedupe) are now closed -- previously only the backend side of this exact bug class had been
  patched (see the first post-ship fix above, Problem 3, which fixed the threadpool-blocking
  symptom but explicitly left the frontend double-fire trigger unaddressed).
- Verify locally: with the backend freshly restarted (empty cache) and `reactStrictMode: true`
  still set, load `/galaxy` and confirm via the backend's request log that exactly one
  `GET /resources/scan` fires on initial mount, not two; confirm the "Refresh" button and switching
  regions both still work normally afterward.

### Post-ship fix (2026-07-12) — chat empty-state copy, nav icons, floating-launcher hint, scan parallelization
Resumed after a session-limit interruption. The coordinator handoff described four items: two
"landed but review cut off" (`ChatPanel.tsx` copy, `NavBar.tsx` icons) and two "never started,
zero diff" (`ChatLauncher.tsx` redesign, `scan_service.py` parallelization). That framing for
the second pair turned out to be wrong methodology, not wrong facts on disk: both files are
untracked in git (never committed), so `git diff`/`git status` show nothing for them regardless
of actual content — absence of a tracked diff isn't evidence of "untouched." Caught this by
reading the real file contents directly (not trusting `git diff`) before delegating anything,
confirmed both were already fully built by an earlier session whose background agents had
apparently completed the work but reported "failed" at the wrap-up/reporting stage only, so
neither `docs/BUILD_PROGRESS.md` nor the coordinator's own handoff notes ever recorded it. No
duplicate build work was dispatched — this was caught before any `backend-agent`/`frontend-agent`
call went out for items 3/4; only read-only `code-reviewer`/`security-reviewer` agents were run
against all four items this session, per the standard review gate. Step 5 stays **done**; this
is a fourth post-ship pass on it, same category as the three above (item 4 also touches
`scan_service.py`, Step 4's territory, logged here to keep the whole sweep together, same
precedent as the "scan-request race condition" entry just above).

**Item 1 — `ChatPanel.tsx` empty-state copy + suggested prompts.** Replaced stale "read-only
access to EC2 and CloudWatch" intro text and three EC2-only suggested prompts with copy
reflecting the agent's actual full tool coverage (all 15 resource types via
`check_idle`/`estimate_cost`/`scan_region`/`list_resources`, per `orchestrator.py`) — the same
stale-copy-vs-actual-coverage bug class that's recurred in `orchestrator.py`'s own system prompt
multiple times this build. Also added a small decorative orbit/planet SVG glyph to the empty
state, ties the panel to the galaxy view's visual language.
- Review (`code-reviewer` + `security-reviewer`, parallel, read-only): both clean, no blocking
  findings. Confirmed no new fetch/data logic introduced (copy + a static decorative SVG only).
- **Status: done.**

**Item 2 — `NavBar.tsx` nav-tab icons.** Added small inline stroke-based SVG glyphs before each
of the 6 nav tab labels (Galaxy/globe, Idle Resources/filter, Investigations/clock, Cost
Overview/trending-up, Audit Log/document, Settings/sliders — the last two are a judgment call,
the user's mockup was cut off before showing them). Also restyled the nav bar itself (dropped
hard border/background per the earlier "nav bar seam" post-ship fix's continuation) and added a
session-email + sign-out control.
- `code-reviewer`: no blocking findings. One non-blocking finding, **fixed directly by the
  coordinator** (cheap, documentation-only, same precedent as `SECURITY.md`'s precision fixes
  in Step 7): the new icon convention's comment claimed to match "the same viewBox/stroke
  pattern" as `GalaxyView.tsx`'s `Glyph`/`StandaloneGlyph` icons, but those actually use a
  different, relative `-5 -5 10 10` viewBox, not `NavBar.tsx`'s fixed `0 0 24 24` — reworded to
  cite `GalaxyView.tsx` accurately as prior art for "no icon library, inline SVG only," not an
  exact viewBox match. Two non-blocking notes accepted as-is: the session-email/sign-out
  addition is scope creep beyond "icon work" but doesn't duplicate/conflict with any other
  sign-out control (grepped `signOut` repo-wide, this is the only call site); `ReactNode`-typed
  `icon` field on `TABS` is fine, no looseness concern.
- `security-reviewer`: clean, no blocking findings. Confirmed `session.user.email` display is
  the same value already available client-side via `useSession()` elsewhere (no new exposure),
  `signOut({ callbackUrl: "/login" })` behaves correctly and is the only sign-out call site, no
  raw-HTML/`dangerouslySetInnerHTML` introduced by the SVG additions (plain inline JSX), no
  weakening of the NextAuth middleware gate or session handling.
- **Status: done.**

**Item 3 — `ChatLauncher.tsx`/`ChatLauncherProvider.tsx` icon + discoverability hint.** Already
fully built on resume: `ChatBubbleIcon`/`CloseIcon` (inline SVG replacing the emoji button,
confirmed `lucide-react` is not in `package.json`) plus a one-time "Need help? Ask me anything"
hint (`sessionStorage` key `opspilot.chatHintSeen`, auto-fades after 5s, hidden once the panel
opens). No pill-shaped icon+text button was built (per `nav.jpeg`'s optional suggestion) — stayed
a plain 56px circle + fade-out hint, which was explicitly acceptable per the original ask if a
circle+tooltip combo already looked good.
- `code-reviewer`: no blocking findings. Confirmed live: `npx tsc --noEmit` and `npx eslint`
  both clean on both files. Verified the hint logic is sound (SSR guard, timeout cleanup on
  unmount, no flash-then-hide) and the `useSession()` gate correctly excludes both
  `"unauthenticated"` and `"loading"` states. One non-blocking finding, **fixed directly by the
  coordinator** (same class of fix as item 2's): a comment citing `GalaxyView.tsx`'s icons as
  the matching-convention source was imprecise in the same way `NavBar.tsx`'s was before that
  fix — reworded to cite `NavBar.tsx`'s icons (which do share the 24x24 viewBox) instead, with
  `GalaxyView.tsx` kept only as "prior art for no-icon-library," not an exact match claim.
  Reviewer's own opinion, requested and recorded: the circle + one-time hint is a reasonable
  first pass, not a defect — flagged only as a low-priority future idea (an always-visible label
  on first-ever login, not per-session) if discoverability data ever shows users missing it.
- `security-reviewer`: clean, no blocking findings. Confirmed the `useSession()` gate matches
  the exact fix already recorded in this file's own "chat as floating launcher" entry above (not
  regressed), `sessionStorage` holds nothing sensitive, no new fetch/data call beyond the
  pre-existing `ChatPanel`/`sendChatMessage` path, no raw-HTML SVG strings, and
  `ChatLauncherProvider`'s context carries nothing more sensitive than `{id, label}`.
- **Status: done.**

**Item 4 — `scan_service.py` region-scan parallelization.** Already fully built on resume: the
15 per-type collectors, previously run one at a time in `_run_scan` (measured ~175s+ per full
scan), now run through a shared `_run_collectors_concurrently()` helper bounded by a
`ThreadPoolExecutor(max_workers=_SCAN_MAX_WORKERS)` (`_SCAN_MAX_WORKERS = 6`, justified inline as
sitting in a 5-8 "sweet spot" — the slowest 1-2 collectors dominate wall-clock regardless of
width). Both `_run_scan` (the full idle/cost-inclusive scan) and `list_lite_resources` (roadmap
3.8's cheap identity-only listing, same sequential-loop pattern flagged as worth checking) route
through the same helper, with `lite` threaded through correctly to each collector. Graceful
degradation preserved exactly: each future's exception is caught individually, logged, and
contributes 0 resources for that type only — never blanks the other 14. Result order is
reassembled by `type_codes` order, not thread-completion order (confirmed nothing downstream
actually depends on this, both `GalaxyView.tsx` and `resource_query_service.list_resources`
re-sort independently, verified by direct grep, not just trusted from a comment).
`app/aws/client.py` was also proactively hardened with a dedicated `_client_creation_lock`,
scoped to client *construction* only and deliberately kept independent of `scan_service.py`'s
own locks to avoid nesting/deadlock — a real fix for a genuine boto3 first-use thread-safety gap
this parallelization would otherwise have introduced.
- New test coverage in `tests/test_scan_service.py` proves, with real `ThreadPoolExecutor`/
  `threading`/`time.sleep` (not mocked-away timing): all 15 collectors get called
  (`test_run_collectors_concurrently_calls_every_type`,
  `test_list_lite_resources_calls_every_type`); one failing collector doesn't exclude the others
  (`test_run_collectors_concurrently_one_failure_keeps_other_fourteen`,
  `test_list_lite_resources_one_failure_keeps_other_fourteen`); wall-clock time is bounded by the
  slowest collector, not the sum (`test_run_collectors_concurrently_runs_in_parallel_not_serially`,
  `test_list_lite_resources_runs_in_parallel_not_serially` — 15 collectors each sleeping 0.1s
  finish in well under 1.5s). Also confirmed the parallelization coexists correctly with the
  pre-existing per-region single-flight future/lock mechanism (`_get_or_create_in_flight_future`)
  from the earlier "scan-request race condition" fix — the executor only ever runs inside the
  sole winner's `_do_scan()` call, re-verified by the existing
  `test_no_cache_concurrent_callers_both_get_winners_result_past_old_fixed_cap` two-thread test
  still passing with the executor now nested inside it.
- `code-reviewer`: no blocking findings. Independently re-ran the full suite live (337 passed)
  and `ruff check .` — found one real lint failure (`tests/test_scan_service.py:733`, a line over
  100 chars in a new test helper's signature), **fixed directly by the coordinator** (mechanical
  reflow, no behavior change), re-verified ruff clean after. Two non-blocking notes accepted, not
  fixed: no test drives `lite=True` through a real (non-fake) collector to confirm `cost`/`idle`
  end up `None` end-to-end (existing lite tests use fake collectors that ignore `lite`; code
  reading confirms correctness but a regression here wouldn't be caught by CI) — tracked as a
  coverage gap, not urgent; `_SCAN_MAX_WORKERS` bounds one region's scan only, concurrent scans of
  multiple regions have no global cap (fine at single-admin/demo scale, noted as an implicit
  assumption).
- `security-reviewer`: no blocking findings. Confirmed the prior "single cached `boto3.Session`
  under genuine multi-thread concurrency" note (tracked since the earlier scan-race fix) still
  holds given the new `_client_creation_lock` addition; confirmed 6 concurrent Describe*/List*
  calls is a reasonable, justified bound, not a throttling risk; confirmed a failing collector's
  exception never leaks raw AWS exception text (account ID/IAM ARN) to the scan response, only to
  the server-side log; confirmed no interaction that could bypass the per-region cooldown/cache
  mechanism or double-run a scan; no new secrets/hardcoded values/unsafe logging.
- **Status: done.**

**Final independent verification (coordinator, after all four items' fixes, re-run from
scratch):** backend **337/337 tests pass, ruff clean**; frontend `npx tsc --noEmit` clean, `npx
next lint` clean ("No ESLint warnings or errors"), `npx next build` clean (all 13 app routes
build, including `/chat` and `/galaxy` — one earlier build attempt hit a stale-`.next`-cache
`PageNotFoundError` for `/chat`, resolved by clearing `.next` and rebuilding, not a code issue).
- Verify locally: open the floating chat panel (bottom-right circle) and confirm the empty state
  shows the new copy/glyph and four updated suggested prompts, not the old EC2-only ones; confirm
  the nav bar shows a small icon before each of the six tab labels; confirm a one-time "Need
  help? Ask me anything" hint appears near the floating chat button shortly after sign-in and
  fades after ~5s (check in a fresh browser session / cleared `sessionStorage` to see it again);
  with the backend freshly restarted (empty cache), force a region scan and confirm it completes
  in roughly the time of the single slowest collector rather than the sum of all 15 (expect well
  under the old ~175s, on the order of whatever the slowest individual collector call takes in
  this environment).

### Post-ship fix (2026-07-12) — nav popover z-index bug + draggable galaxy stars
User reported one bug live and requested one new interaction feature, both frontend-only — not
new roadmap scope, Step 5 stays **done**, this is another post-ship pass on it, same category as
the entries above. `security-reviewer` was deliberately not run this pass (user's explicit call,
confirmed reasonable by the coordinator, see below) — `code-reviewer` was.

**Item 1 (bug) — account-menu popover rendered under the galaxy HUD.** Root cause was already
confirmed before delegating: `NavBar.tsx`'s account-menu popover (email + "Sign out") and several
of `GalaxyView.tsx`'s own overlay cards (region selector, Monthly Spend HUD, warning banner,
legend) were all `z-20`; `ChatLauncher.tsx`'s button/hint/panel are `z-40`. On `/galaxy` this let
the HUD card visually bury "Sign out," making it unclickable. `frontend-agent` bumped the
popover's z-index to `z-50` (`NavBar.tsx` ~line 258) — nav chrome now strictly outranks every
page-level overlay instead of tying with them. Confirmed via grep across `components/`/`app/`
that `z-50` is now the highest z-index anywhere in the app, and that `app/layout.tsx`'s flat
`NavBar`/`<main>`/`ChatLauncher` sibling structure (no intervening `transform`/`filter`/
`isolation` ancestor) means the popover correctly renders above all of it, chat panel open or
not. `GalaxyView.tsx`'s own region-selector dropdown (also `z-20`) was confirmed correctly out of
scope — it's self-contained inside the canvas, not global nav chrome, so it never needs to
outrank anything outside itself.

**Item 2 (feature) — draggable galaxy stars.** Added click-and-drag to stars in the main galaxy
view only (`GalaxyView.tsx`; the "View connections" cluster view deliberately untouched, keeps
its own click-to-recenter semantics). Confirmed no drag library is installed
(`package.json` — only next/react/next-auth/react-markdown deps) before building, per instruction
not to add one. Built with plain pointer events (`onPointerDown`/`Move`/`Up`/`Cancel`, with
`setPointerCapture`) for cross-input consistency: `clientToSvgPoint()` converts screen coords to
the `viewBox="0 0 100 100"` space via `getScreenCTM().inverse()` (correct for a non-square
responsive container holding a square viewBox under `preserveAspectRatio="xMidYMid meet"`, unlike
a naive width/height ratio); session-local `dragPositions` (`Map<string, {x,y}>`, keyed by
resource id) overlays but never mutates the deterministic `layoutResources()` output; a single
shared `getRenderPos()` helper is read by the star circles, glyph, label, AND `relationLines`'
endpoints, so constellation lines visibly follow a dragged star in real time rather than only on
release; click vs. drag is disambiguated by a `DRAG_CLICK_THRESHOLD_PX = 5` (screen pixels)
movement threshold, with `handleStarPointerUp` (not a separate `onClick`) the only thing that ever
opens the detail panel, so a completed drag can never also fire a stray click. Positions are
session-local by design (no backend field, no localStorage) — `setDragPositions(new Map())` runs
inside `runScan`'s success branch right after `setScan(res)`, covering both a region switch and a
manual refresh, so a fresh scan always returns stars to the deterministic layout rather than
fighting leftover drag state against possibly-different resource data.
- `code-reviewer` findings: one real bug, fixed by `frontend-agent`, independently re-verified by
  the coordinator via direct code read (not just the agent's report) — `dragSessionRef` was a
  single shared ref, not keyed per pointer, so a second concurrent pointer (multi-touch, or a
  stray second pointer — reachable since `touchAction: "none"` was deliberately set for touch
  input) landing on a *different* star while a first drag was active would silently corrupt both
  gestures: the first star would freeze mid-drag (its `move` events failing an id check against
  the now-overwritten ref), and the first star's eventual `pointerup` would null out the *second*
  star's still-active session, killing it prematurely. Fixed by keying `dragSessionRef` as a
  `Map<number, {...}>` by `pointerId` (verified directly: all four handlers now `.get`/`.set`/
  `.delete` by `e.pointerId`, no single shared value left). Two optional nits from the same
  finding, both also fixed opportunistically: `pointerdown` now gates on `e.button !== 0` so
  right/middle-click no longer hijacks the browser's default handling for those buttons; a
  `pointercancel` (e.g. OS hands the gesture to scroll/zoom mid-drag) now reverts the star to its
  pre-drag position (a new `dragPositionsRef` mirror lets the cancel handler read that
  synchronously) instead of leaving it stranded at its last partial position as if the drag had
  completed normally. Re-verified clean: `npx tsc --noEmit`, `npx next lint`
  ("No ESLint warnings or errors"), `npx next build` (all 13 routes, `/galaxy` present) — run
  independently by the coordinator both before and after this fix, not just trusted from either
  agent's report.
  - Two non-blocking notes accepted as-is, not fixed: `relationLines` is built from `positioned`
    (all resources) not `visible` (family-filtered), so toggling a family off via the legend hides
    a star but not a constellation line still pointing at its former position — pre-existing
    behavior, not caused by this diff, flagged for awareness only; `cursor: "grab"` never switches
    to `"grabbing"` during an active drag, a cosmetic nit.
  - Process note, addressed directly rather than re-running anything: the reviewer flagged that
    `GalaxyView.tsx`/`ChatLauncher.tsx` are untracked in git (never committed — a pre-existing
    repo-hygiene characteristic of this project, already noted in an earlier post-ship entry
    above) so it had no real diff to review, only full-file content — not a defect in this pass,
    just a review-mechanics limitation to keep in mind for future passes on these files.
- **On skipping `security-reviewer` this pass**: user's explicit instruction, with a judgment
  caveat attached ("use judgment"). Coordinator's read: sound. Item 2 adds zero new fetch/data
  calls, zero new data exposure, and touches only client-side rendering/interaction state
  (`dragPositions`, pure UI). Item 1 changes one CSS z-index value on `NavBar.tsx`'s account-menu
  popover — `code-reviewer` separately noted that popover's *surrounding* file content includes
  `useSession()`-gated rendering and a `signOut()` call, but that auth-adjacent logic is
  pre-existing, untouched by this diff, and was already independently reviewed by
  `security-reviewer` in the earlier "nav restructure: Settings + user icons" post-ship entry
  above (confirmed clean then: same auth-gate condition, plain JSX text interpolation, no
  `dangerouslySetInnerHTML`). A z-index-only change to already-reviewed markup doesn't reopen that
  review. No new security-relevant surface in either item.
- **Manual verification**: `frontend-agent` could not literally perform a pointer-drag gesture in
  a live browser (no browser-automation/screenshot tool available to it, or to the coordinator) —
  disclosed honestly rather than claiming a click-through it didn't do. What was actually done
  instead, by both the agent and independently by the coordinator: full line-by-line trace of the
  pointer-event/coordinate-conversion/click-vs-drag/reset-on-scan logic (confirmed internally
  consistent — single shared `getRenderPos` for stars and lines, threshold-gated click
  suppression, single reset point covering both region switch and refresh, now also
  per-pointer-isolated sessions post-fix); confirmed `/galaxy` compiles and server-renders under a
  real authenticated session with no runtime errors; `tsc`/`lint`/`build` clean, independently
  re-run by the coordinator (not just trusted), both before and after the pointer-session fix.
  **Not yet done: an actual hands-on browser drag test.** Flagged here explicitly, matching this
  file's own standard for interaction features that can't be automated — recommend doing this
  before treating item 2 as fully field-verified: load `/galaxy`, drag a star and confirm it
  tracks the pointer smoothly and a constellation line attached to it follows in real time during
  the drag (not just after release); click a star with no/minimal movement and confirm the detail
  panel still opens; drag a star with clear movement and confirm the detail panel does NOT open;
  refresh (or switch region and back) and confirm stars return to the deterministic golden-angle
  layout, not leftover dragged positions; confirm "View connections," the legend toggle, and the
  region switcher all still work normally; separately, confirm the account-menu popover on
  `/galaxy` now renders above the Monthly Spend HUD and "Sign out" is clickable.

### Post-ship fix (2026-07-12) — duplicate chat UI on /chat direct visit
User reported: visiting `/chat` directly showed two overlapping chat UIs stacked on each other.
Not new roadmap scope — Step 5 stays **done**, this is another post-ship bug-fix pass on it, same
category as the entries above. Root cause was confirmed by the coordinator via direct code read
before delegating (no investigation-agent needed): `app/chat/page.tsx` still rendered its own
standalone `<ChatPanel />` (a leftover from before the "chat as floating launcher" post-ship fix
above), while `ChatLauncher.tsx` — mounted globally in `app/layout.tsx`, present on every page
including `/chat` — independently renders its own `<ChatPanel initialAbout={scope} />` overlay
once signed in. The earlier floating-launcher fix correctly de-linked `/chat` from the nav but
left the page's own inline `ChatPanel` in place as a "harmless deep-link fallback," without
accounting for the now-global launcher also being present on that same page — so two independent
`ChatPanel` mounts existed simultaneously on `/chat` for any signed-in user.

- **Fix (`frontend-agent`), scoped to `app/chat/page.tsx` only:** removed the standalone
  `<ChatPanel />`/`<h1>Chat</h1>` render entirely. The page now mounts a `ChatRedirect` client
  component (`Suspense`-wrapped, since it still needs `useSearchParams` — same pattern as
  `app/login/page.tsx`) that, on mount, reads the existing `?about=`/`?label=` deep-link params,
  calls `openChat({ id: about, label: label ?? about })` (mirroring `ChatPanel.tsx`'s own
  `label ?? id` fallback precedence) or `openChat()` with no scope if `about` is absent — using
  the same `useChatLauncher()` hook and call shape `GalaxyView.tsx`'s "Ask about this resource"
  button already uses — then `router.replace("/galaxy")` so the user lands on a normal page with
  the launcher's panel now open, rather than a bare page with nothing of its own. Effect runs
  once (empty dep array, `openChat` is `useCallback`-stable). Deliberately did not touch
  `ChatLauncher.tsx`, `ChatPanel.tsx`, `ChatLauncherProvider.tsx`, or `layout.tsx`. Coordinator
  confirmed via repo-wide grep before delegating that nothing else links/pushes to `/chat`
  (comments only), so the deep-link contract (`/chat?about=X&label=Y` still opens chat
  pre-scoped) is preserved with no other call site needing an update. `npx tsc --noEmit`, `npx
  next lint`, `npx next build` all clean (route manifest still lists `/chat`), independently
  re-verified by the coordinator via direct read of the final file, not just the agent's report.
- **Review (`code-reviewer` only — `security-reviewer` skipped per this task's explicit
  instruction, reasonable: pure client-side redirect/context-call logic reusing already-reviewed
  auth/fetch paths, zero new fetch surface, zero new data exposure).** No blocking findings.
  Verified sound: provider ordering (`ChatLauncherProvider` wraps `<main>` in `app/providers.tsx`,
  so `useChatLauncher()` always has a live context value when `ChatRedirect`'s effect runs — no
  mount-timing risk); the `openChat()`-then-`router.replace()` sequencing is not a real race,
  since `ChatLauncherProvider`/`ChatLauncher` live in the root layout and are never unmounted by
  the `/chat` → `/galaxy` client-side navigation, so the panel reliably ends up open after
  landing; `/galaxy` is a valid redirect target gated by the identical `middleware.ts` matcher
  `/chat` was already behind; no dead imports/exports left in the changed file.
  - **Two non-blocking items, triaged, neither fixed:** (1) `ChatPanel.tsx` (lines ~53-63,
    untouched — out of scope for this fix) now has a dead URL-reading fallback and a stale
    comment describing the old `/chat` behavior, since `/chat` no longer renders `ChatPanel`
    directly and nothing else ever set `?about=`/`?label=` in the URL — confirmed via grep no
    other caller exists. Explicitly deferred rather than scope-creeped into this fix: causes no
    visible bug (unreachable branch, not incorrect behavior), and the instruction for this task
    was a light, single-file fix. Flagged here as a small follow-up cleanup for whoever next
    touches `ChatPanel.tsx`. (2) Brief blank-page flash between `/chat` mounting and the redirect
    firing (both `ChatRedirect` and its `Suspense` fallback render `null`) — a minor UX
    regression versus the old version's immediate visible content, but accepted as-is for what's
    now purely a rarely-hit deep-link/bookmark fallback route, not a primary UI surface.
- **Verification honesty note:** no browser-automation tool is available to either the building
  agent or the coordinator, so this was verified by direct code/logic trace only (provider tree
  position, effect dependency correctness, redirect-target validity, `tsc`/`lint`/`build`
  clean), not a live click-through. **Not yet done: an actual hands-on browser check.** Recommend
  before treating this as fully field-verified: sign in, visit `/chat` directly with no query
  params and confirm exactly one chat panel appears (the floating launcher's, slid open) after
  landing on `/galaxy`, not two stacked UIs and not a blank dead page; visit
  `/chat?about=<some-resource-id>&label=<name>` directly and confirm the same panel opens
  pre-scoped with the input prefilled ("What's the status of `<name>` (`<id>`)?"), same as the
  old inline behavior; confirm clicking "Ask about this resource" from `/galaxy` still works
  unchanged (it never touched `/chat` in the first place, per the earlier floating-launcher fix).

### Post-ship fix (2026-07-14) — full rewrite of the floating chat UI (supersedes the three
incremental CSS patches from earlier today)
User had three rounds of incremental CSS patches on the chat UI earlier today (documented above:
the 2026-07-12 "wide markdown table forced whole chat panel to scroll sideways" fix, the
2026-07-12 "chat bubble width overflow, structural root cause" fix, plus a third round of
structural flex fixes made in this session that patched the same overflow bug class again) and
was still unhappy with the result — each patch fixed one overflow bug and revealed the next
because they were patches on an accumulating structure, not a coherent design. Explicit
instruction: "remove the existing floating chat UI completely without removing the functions,
and create a clean superb UI again." Not new roadmap scope — Step 5 stays **done**, this
supersedes and closes out the three prior incremental patches with a from-scratch rewrite rather
than a fourth patch.
- **Scope**: `opspilot-frontend/components/ChatPanel.tsx` and
  `opspilot-frontend/components/ChatLauncher.tsx` rewritten from scratch (fresh JSX/markup, not
  layered onto the prior structure). Explicitly out of scope, confirmed untouched:
  `ChatLauncherProvider.tsx` (pure state plumbing), `app/chat/page.tsx` (redirect-only fallback),
  `GalaxyView.tsx`, `lib/api.ts`. `ReasoningTrace.tsx` got minor in-file restyling only
  (`min-w-0` on its root, `break-words` on the tool-call line) — its exported prop contract
  (`steps: TraceStep[]` as the only prop) is unchanged.
- **Functional contract preserved byte-for-byte** (verified directly by the coordinator before
  delegating, and again by `code-reviewer` after): `ChatPanelAbout` prop precedence over
  `?about=`/`?label=` URL params, input prefill when scoped, the exact 4 `SUGGESTIONS` strings
  unchanged, `extractRecall()`'s past-investigation lookup logic, `handleSend`'s
  trim/no-op/append/error-message/`finally` sequence including the exact
  `Couldn't reach the agent: ${detail}. Confirm the backend is running on port 8000.` error text,
  auto-scroll-on-new-message, markdown-for-assistant/plain-text-for-user-and-error rendering,
  `ChatLauncher`'s `useSession().status === "authenticated"` gate (covering both
  `"unauthenticated"` and the `"loading"` interstitial, not regressing the 2026-07-12
  pre-login-exposure fix), the one-time-per-session hint, and the `Suspense` boundary around
  `<ChatPanel initialAbout={scope} />`.
- **Root-cause structural fix** (the actual point of the rewrite, not just restyling): the prior
  `ChatPanel.tsx` still carried vestigial "standalone page" framing (`h-[calc(100vh-9rem)]`
  height math plus its own `rounded-lg border` box) left over from before `/chat` became a
  launcher-redirect-only route — but `ChatPanel` is now *only* ever mounted inside
  `ChatLauncher`'s own fixed panel (confirmed via repo-wide grep), so the two components were
  disagreeing about who owns the outer chrome, which is a real structural cause of the recurring
  overflow bugs. `frontend-agent` gave `ChatLauncher` sole ownership of the fixed right-edge
  panel (`h-screen`, a new proper header region with an "Ask OpsPilot_AI" title + close button,
  not just a floating X) and made `ChatPanel` a plain height-filling flex child (`h-full min-h-0
  flex-col`, no border, no vh-based height of its own) that trusts its parent for sizing.
  Panel width widened from `sm:w-96` (384px, the exact width every prior overflow bug occurred
  at) to `sm:w-[420px]`. `min-w-0`/`min-h-0` applied through the full flex chain; GFM tables,
  `pre`/code blocks, and links each get their own scoped `overflow-x-auto`/`break-all` handling
  via a custom `ReactMarkdown` `components` map, and plain-text bubbles get `break-words`.
- **Color/icon discipline**: reused the app's existing Tailwind tokens only (`bg`, `surface`,
  `surfacealt`, `border`, `text`, `muted`, `accent`, `status-bad`) — no new palette invented, and
  specifically no cyan pulled in from `GalaxyView.tsx`'s canvas-only `COLOR_ACTIVE` constant
  (that's a resource-status dot color for the starfield, not a general UI token; this app's chat
  UI has always used a single `accent` amber for both CTAs and informational elements, confirmed
  by reading `tailwind.config.ts` directly before delegating rather than trusting a
  cyan-vs-amber split that isn't actually backed by a token). Icons stayed hand-rolled inline SVG
  (`lucide-react` confirmed still absent from `package.json`) — new `SendIcon`/`RecallIcon`/
  `OrbitMark` glyphs added, matching the existing stroke/`currentColor` convention.
- **`code-reviewer` findings**: one blocking, fixed — `bg-[#0a0e1f]/97` on the new panel
  container was a hardcoded hex (pre-existing from before this rewrite, not newly invented, but
  left in place by the rewrite; coordinator judged fixing it now — rather than deferring — the
  right call since token discipline was the explicit point of this task), swapped for the actual
  `bg` token (`bg-bg/97`, visually indistinguishable from `#0a0e1f` at this near-black end of the
  palette). One non-blocking a11y nit, also fixed since cheap: the panel's header (including its
  close button) stayed permanently mounted and merely slid off-screen while closed, so a keyboard
  user tabbing through the page could still reach the invisible close button — fixed with
  `tabIndex={isOpen ? 0 : -1}` on the close button. Everything else — functional contract,
  the full width/height overflow chain across five traced scenarios (wide GFM table, long
  unbroken string in both plain and markdown content, a fenced code block, deeply nested JSON
  inside an open reasoning trace, and the single-scroll-container check), icon convention, and
  out-of-scope file boundaries — checked out clean. `security-reviewer` not run for this step per
  the user's own instruction (pure UI/CSS rewrite, no new data/auth/fetch surface — consistent
  with this build's existing precedent of light-touch/skipped security review for pure UI
  relocations, e.g. the 2026-07-12 nav-icons fix).
- **Five content-scenario walkthroughs** (from `frontend-agent`'s report, independently
  spot-checked by the coordinator via direct code read, not just trusted):
  (a) *wide GFM table in an assistant reply* — wrapped in its own `overflow-x-auto` div via a
  custom `table` renderer in `ReactMarkdown`'s `components` map; scrolls internally, never
  widens the bubble/panel.
  (b) *long unbroken string (ARN/URL) with no spaces* — user/error bubbles get
  `whitespace-pre-wrap break-words`; markdown assistant content gets `break-words` on the
  `prose` container (inherited by `<p>`/`<li>`/`<td>`) plus GFM-autolinked URLs specifically get
  `break-all` via a custom `a` renderer, since `break-words` alone doesn't reliably break truly
  unbroken runs inside an anchor.
  (c) *empty state* — unaffected by any of the width work; renders full-height, centered,
  independent of message-list overflow handling.
  (d) *scoped view ("Ask about this resource")* — banner text and the `aboutLabel` span both get
  `break-words`/`break-all` so a long resource label can't itself force the panel wider.
  (e) *open reasoning trace with deep JSON* — `ReasoningTrace.tsx`'s own `<pre>` already had
  `overflow-x-auto whitespace-pre-wrap`; the rewrite additionally added `min-w-0` to its root so
  it correctly receives a width-constrained box from its new parent structure instead of
  potentially escaping it.
- **Verification (coordinator, independent of both agent runs' self-reports, re-run after the
  two review fixes landed)**: `npx tsc --noEmit` clean, `npx next lint` clean ("No ESLint
  warnings or errors"), `npx next build` clean — all app routes present, including `/chat` and
  `/galaxy` (one transient `MODULE_NOT_FOUND` on a first re-run was traced to a stale `.next`
  cache directory from two build runs racing on the same output folder, not a code defect —
  resolved by deleting `.next` and rebuilding clean, confirmed reproducible-clean on the clean
  rebuild). Confirmed via direct grep that `bg-bg/97` (not the old hardcoded hex) and
  `tabIndex={isOpen ? 0 : -1}` are actually present in the final `ChatLauncher.tsx`, not just
  claimed by the agent.
- **Final design description** (for a from-code sanity check, since the coordinator can't see it
  rendered): `ChatLauncher.tsx` now owns a single fixed right-edge panel (`h-screen`,
  `sm:w-[420px]`, `bg-bg/97` + `backdrop-blur`, slides in via `translateX`) split into a
  `shrink-0` header (orbit-glyph brand mark + "Ask OpsPilot_AI" title, left-aligned, close button
  right-aligned) and a `min-h-0 flex-1` body that mounts `ChatPanel` only while open. `ChatPanel`
  itself is a borderless, full-height flex column: an optional scoped-banner strip (amber-tinted,
  only when pre-scoped from the galaxy), a single scrolling message region
  (`overflow-y-auto`/`overflow-x-hidden`) containing either the empty-state (orbit/planet SVG +
  heading + description + the 4 suggestion pills) or the message list (rounded chat bubbles,
  amber-filled for the user with a sharp top-right corner, dark bordered `surfacealt` for
  assistant/error with a sharp top-left corner, each followed by its recall/provider badges and
  collapsible reasoning trace), and a `shrink-0` footer with a pill-shaped input and an amber
  pill Send button with a paper-plane icon. Every visible color is one of the app's existing
  tokens (`bg`/`surface`/`surfacealt`/`border`/`text`/`muted`/`accent`/`status-bad`) — no new hex
  values anywhere in the final diff.
- Verify locally: open the floating chat launcher, ask a question that returns a wide markdown
  table (e.g. "What's idle in this account?") and confirm only the table scrolls horizontally,
  not the panel; ask something whose reply contains a long ARN/URL and confirm it wraps/breaks
  inside the bubble instead of pushing the panel wider; open a reasoning trace on a
  tool-call-heavy answer and confirm the JSON output stays contained; click "Ask about this
  resource" from `/galaxy` and confirm the scoped banner + prefilled input still work; confirm
  the panel still opens/closes via both the floating button and the in-panel close button, and
  that a keyboard Tab from a closed state doesn't land on the hidden close button.

### Post-ship fix (2026-07-14) — nav-icon pop-in delay + chat panel still clipping content
(supersedes item 2 of this entry against the "full rewrite of the floating chat UI" entry
immediately above — that rewrite claimed the overflow problem was fully fixed; it wasn't.) Two
real bugs, both root-caused by direct code read before delegating, no investigation phase
needed. Not new roadmap scope — Step 5 stays **done**, this is a bug-fix pass on it.

- **Item 1 — nav icons (settings gear, user avatar, chat launcher) popped in 4-5s after the
  rest of the page, on every load.** Root cause: `opspilot-frontend/app/providers.tsx` mounted
  `<SessionProvider refetchInterval={5*60}>` with no `session` prop, so `useSession()` always
  started in client-side `"loading"` and had to complete a round trip to `/api/auth/session`
  before the icons (gated on `session?.user?.email` / `status === "authenticated"`) could
  render. `frontend-agent` fixed it with the standard next-auth@4 App Router pattern: made
  `app/layout.tsx`'s `RootLayout` `async`, added `const session = await
  getServerSession(authOptions)` (reusing the existing `authOptions` export from `lib/auth.ts`),
  passed it into `<Providers session={session}>` → `<SessionProvider session={session}
  refetchInterval={5*60}>`. Font loaders kept at module scope (not moved into the now-async
  function). `middleware.ts` untouched — confirmed by both reviewers this doesn't change any
  gating behavior, only how fast `useSession()` resolves client-side; `/login` still correctly
  resolves to a `null` session and the icons still don't render there.
- **Item 2 — chat panel still clipped content with no scrollbar, despite today's earlier "full
  rewrite" claiming this was fixed.** Two parts:
  1. **Floor fix**: `ChatPanel.tsx`'s message scroll region had `overflow-x-hidden` as an
     explicit "safety net" — backwards, since `hidden` makes any uncontained content silently
     unreachable instead of just scrollable. Changed to `overflow-x-auto`.
  2. **Actual root cause, traced for real rather than guess-patched a fourth time**: coordinator
     read the actual installed `node_modules/@tailwindcss/typography/src/styles.js` (v0.5.15)
     and confirmed zero `word-break`/`overflow-wrap`/`white-space` rules anywhere in the file —
     ruling out, with direct evidence, the theory that typography's heading styles reset
     wrapping. Also read `node_modules/tailwindcss/src/corePlugins.js` and confirmed Tailwind's
     `break-words` utility compiles to exactly `overflow-wrap: break-word` and nothing else — so
     the property genuinely was reaching the heading via inheritance, ruling out a
     specificity/cascade bug too. The real mechanism: **`overflow-wrap: break-word` is excluded
     from a box's min-content/intrinsic-size calculation per the CSS Text spec — only
     `overflow-wrap: anywhere` counts toward it.** The chat bubble is sized via flexbox
     shrink-to-fit (`items-end`/`items-start`, not `stretch`, confirmed present in the actual
     JSX by both the coordinator and `code-reviewer` independently), so a heading with several
     separately-unbreakable runs could produce a min-content width exceeding the available
     85%-of-420px panel space and overflow without `break-word`'s line-breaking ever getting a
     chance to apply below that floor. Fixed by `frontend-agent`: replaced `break-words` with
     the Tailwind arbitrary-value utility `[overflow-wrap:anywhere]` at all five text-bearing
     spots in `ChatPanel.tsx` — the user/error plain-text bubble, the `prose` wrapper around
     `ReactMarkdown` output, the inline `code` renderer, and (added after `code-reviewer` caught
     it as a missed spot in the same shrink-to-fit column) the "recalled past investigation"
     badge span. Deliberately did not switch to `break-all` (`word-break: break-all`), which
     breaks more aggressively than necessary.
  - **Confidence note, stated plainly rather than overclaimed**: neither the coordinator nor
    `frontend-agent` has a browser available, so this fix is verified by direct reading of the
    installed Tailwind/typography source plus the documented CSS Text spec distinction between
    `break-word` and `anywhere`, and by confirming the described flex chain actually matches the
    real JSX (independently re-confirmed by `code-reviewer`) — not by rendering the page. If
    this is still visibly broken after this fix, the next debugging step should be an actual
    browser/computed-style check, not a fifth CSS-class guess.
- **Review**: `code-reviewer` found one real gap (the recall-badge span above, missed on the
  first pass) — fixed and re-verified. Also noted, as a non-blocking scope observation, that
  the working tree's `ChatPanel.tsx`/`layout.tsx` diffs are larger than "two small bugfixes"
  purely because today's earlier full-rewrite entry above is still uncommitted — not something
  this fix introduced, no action taken. `security-reviewer` run against item 1 only (judged not
  needed for item 2 — pure CSS/wrapping, no new data/auth/fetch surface, consistent with this
  build's precedent for pure-UI fixes): clean, no findings — confirmed `getServerSession`
  forces dynamic rendering (no static-cache cross-user leak risk), confirmed the `apiToken`/
  email reaching the client via server-rendered HTML is the same data already reaching it via
  `/api/auth/session` today (only the timing changed, not the audience), confirmed
  `middleware.ts`'s gate is untouched and still the real UX-layer boundary, confirmed the
  `Session` type import matches the existing `next-auth.d.ts` augmentation with no duplicate
  shape introduced.
- **Verification (coordinator, independent of the agent's self-reports)**: `npx tsc --noEmit`,
  `npx next lint` (`No ESLint warnings or errors`), `npx next build` (all 14 routes) all
  re-confirmed clean after the final recall-badge fix.
- Verify locally (visual confirmation still outstanding — no browser was available for this
  session): load any authenticated page and confirm the settings gear + user avatar + floating
  chat launcher render immediately with the rest of the page, not 4-5s later; open the chat
  launcher, ask a question that returns a markdown heading with inline code (e.g. anything that
  triggers a `scan_region`-style heading in the reply), and confirm the heading wraps within the
  panel instead of being cut off with no scrollbar; also check a long recalled-investigation
  badge doesn't overflow the panel.

### Post-ship fix (2026-07-14) — chat multi-resource answers: table -> paragraph-per-resource
Prompt-engineering change only, confirmed with the user beforehand (design discussion already
had, exact format agreed) — not new roadmap scope, Step 5 stays **done**, this is another
bug-fix pass on it in the same category as the CSS-layer chat-panel fixes above. Scope confined
to `opspilot-backend/app/agent/orchestrator.py`'s `AGENT_INSTRUCTIONS` string; no tools, data
schema, `ChatPanel.tsx`, or CSS touched (the earlier CSS-layer `overflow-x-auto` table safety
net stays in place as defense in depth, deliberately not removed).
- **Problem**: the chat panel is a narrow (~420px) floating sidebar. `AGENT_INSTRUCTIONS`
  explicitly told the model to "render a table" for list-style questions, and was also
  observed (screenshot, an idle-resources question — not a literal "list them" question)
  producing wide 6-7 column markdown tables (Resource ID/Name/Type/Region/Idle since/Idle
  days/Monthly waste) even without an explicit table instruction for that case, since the
  underlying multi-field-per-resource data shape naturally suggests a table to the model.
  Six-plus columns structurally doesn't fit a 420px sidebar at readable size — this is a
  prompt-shape problem, not fixable with more CSS (confirmed by this session's earlier CSS
  work already having tried and hit that wall).
- **Fix (`backend-agent`)**: replaced table-rendering guidance with a paragraph-per-resource
  default — one resource per line, bold name leading (or raw ID via the existing
  untagged-resource fallback), key facts inline/terse via dashes rather than repeated
  "Field: value" prose, worked two-line example embedded matching this prompt's existing
  example-driven style. Added as one general rule (inserted right after the "simple lookup"
  paragraph) plus reinforcing sentences in the 4 other paragraphs that touch multi-resource
  data (broad inventory, single-resource idle/cost when it turns out multi-resource,
  scan_region's broad idle/cost listing, count/list question), plus a rewritten closing
  "Formatting" paragraph. Tables remain available when the user explicitly asks for one — not
  a hard ban, a changed default. 339/339 tests pass, ruff clean. Grepped `tests/` for any
  assertion on `AGENT_INSTRUCTIONS` content/the word "table" in that context — none existed,
  so no test needed updating.
- **Review**: `security-reviewer` skipped per the user's own scoping call (prompt text only, no
  new data/tool surface) — **this skip decision was independently re-verified, not just
  trusted**, after `code-reviewer` initially flagged it as a blocking process concern (see
  below). `code-reviewer` (light pass, as instructed): formatting guidance internally
  consistent across all 5 touched spots, new instruction text clear/unambiguous and matches
  the prompt's existing density, untagged-resource fallback behavior preserved. Confirmed
  339/339 tests and ruff clean by running both itself.
  - `code-reviewer` also raised what it labeled a **blocking** finding: that the diff wasn't
    actually prompt-only, since a full `git diff` against the last commit showed 8 tools
    (`check_idle`, `estimate_cost`, `scan_region`, `list_regions`, `list_resources`,
    `get_resource_health`, `get_resource_age`, `estimate_instance_cost`) wired into `TOOLS`,
    backed by untracked tool-module files — new surface that would need `security-reviewer`.
    **Investigated and found to be a false positive**, not acted on as a real finding: this
    repo's whole roadmap build has been accumulating as one large uncommitted working tree
    since before this session (last commit `552eb71`, well before Steps 2-6), so a bare `git
    diff`/`git status` at any point in this build shows the *entire* unstaged build, not just
    the change actually being reviewed in isolation. Confirmed directly (not just asserted) by
    diffing the file's imports (lines 1-36) and `TOOLS` list (lines 180-200) as read by the
    coordinator immediately before delegating this task against the same lines read
    immediately after `backend-agent`'s edit: byte-for-byte identical content and order in
    both — those 8 tools were already present (Steps 2-6, already reviewed and marked done
    above) and `backend-agent` touched only the `AGENT_INSTRUCTIONS` string, nothing else.
    `security-reviewer` was correctly skipped; no re-review triggered.
  - Two non-blocking findings from `code-reviewer`, both triaged and accepted as-is
    (deferred, not fixed, noted here rather than silently dropped): (1) the untagged-resource
    fallback is described with slightly different verbosity in the general rule ("lead with
    raw ID, no separate callout needed") vs. the list-question paragraph ("note it's
    untagged") — same underlying behavior, minor wording drift, not a functional gap;
    (2) the hypothetical/exploratory cost paragraph (`estimate_instance_cost`, comparing 2-3
    reference instance types) wasn't included in the reinforcement pass and isn't listed among
    the general rule's example contexts — left ambiguous whether the new default applies
    there, and arguably a small side-by-side comparison table reads *better* than prose for
    that specific case anyway. Out of the scope the user actually specified (four named
    paragraphs), so not addressed; worth a decision later if it's observed producing a table
    in practice.
- **Verification note, stated plainly rather than overclaimed**: this change is not verifiable
  by execution — there is no way to run the LLM and observe a live response in this
  environment. Verification here is limited to: the new instruction text read cleanly and
  unambiguously by direct inspection (confirmed by both the coordinator and `code-reviewer`,
  independently), it's consistent with the rest of `AGENT_INSTRUCTIONS`'s existing
  example-driven style, the backend test suite and ruff are clean, and the MCP server /
  dashboard REST views are confirmed unaffected since neither renders this LLM-generated
  markdown text at all — this prompt change only shapes the chat agent's own natural-language
  replies.
- Verify locally (visual/live confirmation still outstanding — no way to run the LLM here):
  ask the chat agent a question covering multiple resources (e.g. "what's idle in this
  account?") and confirm the reply renders as one bold-name-led paragraph per resource instead
  of a markdown table; separately ask for "a table" explicitly and confirm the agent still
  produces one (the CSS `overflow-x-auto` safety net should still contain it if so).

### Post-ship fix (2026-07-14) — restore narrative open/close framing around the paragraph-per-resource list
Refines the "chat multi-resource answers: table -> paragraph-per-resource" entry directly
above (same session, same day). User live-reviewed a screenshot of the new format and
confirmed the layout/wrapping problem was genuinely fixed, but the response *content* had
regressed into a bare fact-dump: it jumped straight into the resource list with zero intro
and ended with a flat "All other resources in the account are currently active or have no
idle data" instead of a real summary. This framing existed *before* the table-format change
(narrative opening — "I scanned every AWS region that's enabled for this account..." — plus a
closing summary with a combined total waste figure and an offer to dig deeper) and was
dropped as an unintended side effect of that earlier fix, not something anyone deliberately
removed. Not new roadmap scope — Step 5 stays **done**, this is a second bug-fix pass in the
same category as the CSS-layer chat-panel fixes and the table-format fix above it. Scope
confined to `opspilot-backend/app/agent/orchestrator.py`'s `AGENT_INSTRUCTIONS` string only —
`ChatPanel.tsx`, CSS, and the paragraph-per-resource list format itself (bold name leading,
terse inline facts) were explicitly out of scope and untouched, confirmed by direct read
after the edit.
- **Fix (`backend-agent`)**: every multi-resource chat answer now targets a three-part shape —
  (1) a one-line opening stating what was checked/scanned (region, scope), (2) the
  paragraph-per-resource list, unchanged, (3) a closing summary with a real total (combined
  monthly waste added up across idle/flagged resources — for `scan_region` specifically, the
  instruction now says to quote its own real `idle_monthly_waste` field rather than have the
  model re-sum manually) plus a natural offer to go deeper on any specific one, matching the
  existing "offer to narrow down further" pattern already used in this prompt for the
  hypothetical-cost-question paragraph. Explicitly framed as mirroring this prompt's own
  hypothesis-then-evidence-then-conclusion shape already established for the single-resource
  investigation protocol ("always land on a real conclusion, don't just enumerate raw facts
  and stop").
  - Six edits, all reviewed by the coordinator directly against the live file (not just the
    agent's self-report): (1) a new general-rule paragraph inserted right after the existing
    table-vs-paragraph explanation, stating the three-part shape once as the umbrella rule;
    (2) broad-inventory paragraph — added open + close; (3) idle/cost-about-a-specific-
    resource paragraph — added open + close for its "if the question covers more than one
    resource" branch; (4) `scan_region` paragraph — added open + close, close pointed at the
    tool's own real `idle_monthly_waste` field; (5) list-question half of the count/list
    paragraph — added open + close (count question, which already ends in a real
    total+breakdown, deliberately left alone); (6) closing "Formatting" paragraph — one
    sentence appended reinforcing that the list-format fix didn't remove the need for framing.
  - 339/339 tests pass, ruff clean (verified twice — once by `backend-agent`, once
    independently re-run by the coordinator). No test asserts on `AGENT_INSTRUCTIONS` literal
    content (re-confirmed via grep), so no test changes were needed.
- **Review**: `security-reviewer` skipped, same call and same reasoning as the parent entry
  above (prompt-text-only, no new tool/data surface) — this time the coordinator independently
  re-confirmed that call itself before delegating (checked imports and the `TOOLS` list are
  unchanged) rather than just repeating the precedent. `code-reviewer` (light pass, as
  instructed): **pass, no blocking findings.** Proactively briefed on the exact
  git-diff-shows-the-whole-uncommitted-build false-positive from the parent entry so it
  wouldn't need to rediscover it; it independently re-verified that caveat anyway (confirmed
  imports/`TOOLS` list match the tools the prompt text actually invokes) rather than taking
  the briefing on faith, then re-ran the full suite and ruff itself (339 passed, clean).
  Confirmed the new general-rule paragraph is correctly reflected in all four specific
  paragraphs below it, and specifically confirmed the scan_region-quotes-its-own-field vs.
  other-paths-manually-sum distinction is deliberate, not drift. Two low-severity findings,
  both triaged by the coordinator and accepted as-is (not sent back — same
  cosmetic/documentation-nit tier as findings triaged this way earlier in this build): (1)
  "go deeper" (general rule, broad-inventory paragraph) vs. "dig deeper" (specific-resource,
  scan_region, list-question paragraphs) — cosmetic wording inconsistency only; (2) the
  general-rule paragraph doesn't explicitly carve out the count-question sub-case (which
  intentionally skips the three-part treatment since its answer is already a self-contained
  total+breakdown) as an exception — a documentation-clarity gap, not a functional one.
- **Verification note, stated plainly rather than overclaimed (same honesty caveat as the
  parent entry)**: this cannot be verified against real model output without actually running
  the LLM — no such run happened in this environment. What was actually verified: the edited
  text reads cleanly and unambiguously (confirmed independently by the coordinator and by
  `code-reviewer`, not just by `backend-agent`'s own report), it's internally consistent with
  itself and with the rest of `AGENT_INSTRUCTIONS`, the paragraph-per-resource list format
  itself is provably untouched (direct before/after line read), and the backend test suite
  and ruff are clean. It is not verified that the model will actually produce the three-part
  shape in a live reply.
- Verify locally once the LLM can actually be exercised: ask the chat agent a multi-resource
  question (e.g. "what's idle in this account?" or "list all resources") and confirm the reply
  (1) opens with a one-line statement of what was checked/scanned, (2) still renders the
  paragraph-per-resource list intact (bold name leading, terse inline facts — unchanged from
  the parent entry's fix), and (3) closes with a real combined-waste total figure (not a vague
  "some resources are idle") plus a natural offer to dig into a specific resource further.

### Post-ship fix (2026-07-14) — stale Gemini fallback model default (404, deprecated for new users)
User live-reproduced a real failure in tonight's `backend_dev.log`, not a guess: every chat
request that fell through to the Gemini fallback provider (`opspilot_llm_primary_provider`
chain groq -> gemini -> nvidia) failed with a 404 from Google — `"This model
models/gemini-2.5-flash is no longer available to new users."` This mattered more than usual
tonight because the primary provider (Groq) was separately rate-limited (daily token quota
exhausted from heavy testing this session), so a broken Gemini fallback meant zero working chat
providers. Not new roadmap scope — this is a bug-fix pass on the chat feature, which Step 5
folded in (roadmap Section 3.8 chat tools, "folded in per user decision, 2026-07-11"). **Step 5
stays done**, same category as the other post-ship fixes logged under it.
- **Root cause**: `opspilot-backend/app/core/config.py`'s `gemini_model` setting defaulted to
  `"gemini-2.5-flash"`, a dated snapshot model Google has since deprecated for new API access.
- **Live-verified before delegating (not just documentation)**: using the real `GEMINI_API_KEY`
  already present in `opspilot-backend/.env`, made real calls against the OpenAI-compatible
  endpoint (`gemini_base_url`). Reproduced the exact 404 with `model: "gemini-2.5-flash"`,
  matching tonight's log byte-for-byte on the error message. Fetched the live
  `/v1beta/openai/models` list with the same key and confirmed `models/gemini-flash-latest` is
  a real, currently-listed model (alongside `gemini-flash-lite-latest`/`gemini-pro-latest` as
  other alias options, and dated snapshots like `gemini-2.5-pro`/`gemini-2.0-flash`/
  `gemini-2.5-flash-lite`). Then made a real chat-completion call with
  `model: "gemini-flash-latest"` and got back a genuine successful completion
  (`"content":"OK"`, `finish_reason":"stop"`). Chose the alias name over another dated snapshot
  deliberately — less likely to hit this same deprecation-404 class of bug again later.
- **Fix (`backend-agent`)**: two-line change, nothing else touched — `gemini_model` default in
  `app/core/config.py` (`"gemini-2.5-flash"` -> `"gemini-flash-latest"`) and the matching
  `GEMINI_MODEL=` line in `.env.example`, so a fresh clone's documented default matches the
  fixed code default. Deliberately left `gemini_embedding_model` (`"gemini-embedding-001"`,
  used for investigation-memory RAG) untouched — not reported broken, out of scope. Confirmed
  via grep neither the model string nor a hardcoded duplicate appears anywhere else in the repo
  (code, tests, docs) before finishing. 339/339 tests pass, ruff clean.
- **Review**: `security-reviewer` skipped deliberately — this is a model-name config string, no
  secrets/auth surface touched. `code-reviewer` (light pass): no findings. Independently
  confirmed the default is isolated and fully propagated (config default -> `.env.example` ->
  dynamically consumed in `app/agent/providers.py` via `settings.gemini_model`, not hardcoded
  elsewhere), zero remaining references to the old model string anywhere in the tree, and no
  test asserts on the literal old string (would've been asserting now-broken behavior).
- **Verification honesty note**: this one is externally verifiable in a way most recent
  prompt-only chat fixes weren't, and it *was* live-verified end-to-end against the real Gemini
  API with real credentials — both the reproduction of the bug and the success of the fix are
  real API responses captured above, not inferred from documentation.
- Verify locally: with `OPSPILOT_LLM_PRIMARY_PROVIDER` set (or Groq otherwise unavailable, e.g.
  rate-limited) so a chat request actually falls through to Gemini, send a chat message and
  confirm a real reply comes back instead of a 404 in `backend_dev.log`.

## Step 8 — Write-action/approval layer
- **Status: not started, explicitly paused.** Per this build's own ground rules, Step 8 is the
  most sensitive step (first AWS-mutating calls in this project) and must not be delegated on
  autopilot — the approval/dry-run UX needs to be confirmed with the user before any agent is
  assigned to it. Do not start this step until that conversation happens.
