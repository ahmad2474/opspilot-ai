# OpsPilot AI — Security Model

This document describes the security posture of OpsPilot AI **as the code actually behaves
today**, not an aspirational target. It exists per `docs/opspilot-ai-roadmap.md` Section 4
("a stated security model is a pitch asset, not just protection") and is kept in sync with
`docs/BUILD_PROGRESS.md`, which has the step-by-step build/review history behind every claim
made here. Where the current implementation falls short of the roadmap's ideal, that's stated
explicitly below, not glossed over.

## 1. Overview / posture statement

OpsPilot AI is currently built and intended to run as a **single-admin, local-only tool**: one
person, running the frontend (`opspilot-frontend`) and backend (`opspilot-backend`) on their own
machine (or a private Docker Compose deployment — see the repo README), pointed at their own AWS
account. It is **not currently deployed anywhere internet-facing**, and several controls below
are explicitly scoped to "acceptable because this is local-only" — each one is called out with
what would need to change before this could be safely exposed to untrusted networks or multiple
independent users.

Read access to AWS is read-only by design (Section 2 below). No AWS resource can be modified,
stopped, or deleted by this app today — the roadmap's write-action/approval layer (Section 6,
build-order Step 8) is intentionally not built yet.

## 2. Authentication

- **Frontend**: NextAuth.js (Auth.js), Credentials provider, single hardcoded admin account
  (`ADMIN_EMAIL` + bcrypt `ADMIN_PASSWORD_HASH` in `opspilot-frontend/.env.local`). No OAuth, no
  multi-user support — this was a deliberate scope choice for a single-account build (see
  `docs/BUILD_PROGRESS.md`'s "Decisions made" section). `middleware.ts` redirects any
  unauthenticated request to `/login` for every route except `/login` itself.
- **Backend**: the frontend redirect above is a UX nicety, **not** the security boundary — every
  FastAPI route (except `GET /health`) independently verifies a session token server-side via the
  `require_session` dependency (`opspilot-backend/app/core/security.py`), wired once per router in
  `app/main.py` rather than annotated per-route, so nothing can accidentally ship unprotected.
- **Mechanism**: on sign-in, NextAuth mints a short-lived (1 hour, auto-refreshed while the tab
  stays open) HS256 JWT signed with a secret (`AUTH_SHARED_SECRET`) shared between the two
  services, sent as `Authorization: Bearer <token>` on every backend call. FastAPI verifies the
  signature and expiry independently, with no shared database/session store needed. The JWT
  decode requires both `exp` and `sub` claims to be present (closes a gap where a validly-signed
  token that simply omitted them would otherwise pass). If `AUTH_SHARED_SECRET` is unset on
  either side, the affected service fails closed (503 with a generic message — see Section 5) —
  it never silently allows unauthenticated access.
- **Login audit logging**: every login attempt (success or failure) writes an entry to the Audit
  Log (Section 7) via a dedicated `POST /auth/login-audit` endpoint. This one endpoint is the
  single deliberate exception to "every route requires a session" above — it's called by
  NextAuth's `authorize()` *before* any session exists, so it can't use the normal bearer-token
  check. It is not left unauthenticated: the request is HMAC-SHA256 signed (over
  `action:email:timestamp`) using the same `AUTH_SHARED_SECRET`, verified server-side with a
  constant-time comparison and a 60-second freshness window, so only this app's own trusted
  Next.js process can write a login-audit entry. The raw secret is never transmitted, only a
  signature derived from it. Its only possible side effect is writing one audit-log row — it
  cannot mint a session or bypass any other check.
- **Known limitation — no rate limiting or lockout**: neither the login form nor the MCP token
  path (Section 6) has brute-force rate limiting today (confirmed: no rate-limiting dependency
  anywhere in `requirements.txt`). This is an accepted gap for a local-only tool and **must be
  added before any internet-facing deployment**.
- **Known limitation — config-error message granularity**: if `AUTH_SHARED_SECRET` (or
  `ADMIN_EMAIL`/`ADMIN_PASSWORD_HASH`) is unset, both sides fail closed with the same fully
  generic message ("Authentication is unavailable — please try again later.") rather than naming
  the missing variable to the client. The specific mechanism differs by side, since they're
  different runtimes: the backend (`require_session`, `verify_login_event_signature`) returns an
  actual HTTP 503 with that message; the frontend's `authorize()` throws a plain `Error` with the
  same message text, which NextAuth surfaces through its own sign-in error path (there's no HTTP
  status code involved on that side, since it isn't a REST response). Either way, the specific
  missing-variable detail is logged server-side only (`logger.error`/`console.error`), never in
  anything the client receives.

## 3. AWS access model — the honest current state

**This app currently uses a static, long-lived AWS IAM user access key pair**
(`AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY`), set in `opspilot-backend/.env` and picked up
automatically by boto3 from the environment. The only place a boto3 client is ever constructed is
`opspilot-backend/app/aws/client.py`'s `_session()` — a single `boto3.Session(region_name=...)`
call, with no role-assumption or credential-refresh logic. This is a **known, explicitly accepted
gap against the roadmap's own stated goal** ("no long-lived credentials stored anywhere ...
assumed-role sessions short-lived (15–60 min), re-assumed per request/session") — not something
this document is going to paper over.

**This was a deliberate decision, not an oversight left unaddressed.** As of 2026-07-12, the
project owner decided: *keep the static IAM user key for now*, because this app is currently a
local, single-admin, not-internet-facing demo, and short-lived assumed-role sessions add real
setup complexity (a new IAM role + trust policy in the connected AWS account) for a threat model
that doesn't yet apply. **This must be upgraded to short-lived assumed-role sessions before this
app is ever hosted anywhere reachable by anyone other than its single operator**, or before it's
used against an AWS account where the operator isn't also the sole person with access to the
machine running it.

What partially mitigates this in the meantime:
- The IAM policy attached to that user is scoped to least privilege (Section 4) — even if the
  static keys were ever exposed, they grant only read access to your AWS resources plus write
  access to three of this app's own bookkeeping DynamoDB tables, not the ability to modify,
  stop, or delete anything in your account.
- Real `.env` files are never committed to the repository (Section 5) — the static keys living on
  one operator's own machine is a materially smaller exposure surface than the same keys sitting
  in a shared or hosted environment.

What does **not** mitigate it: this is still a standing, non-expiring credential. If the machine,
its `.env` file, a backup, or a shell history containing it were ever compromised, whoever
obtained it would have read access to the connected AWS account (and write access to this app's
own three DynamoDB tables) until the keys are manually rotated in the IAM console — there is no
automatic expiry.

## 4. Least privilege / IAM policy

The full policy is maintained in `docs/iam-policy.json` and kept in sync as new resource types are
added — reproduced in summary here:

- **Read-only statement (`OpspilotReadOnly`)**: every action is `Describe*`/`List*`/`Get*` (or the
  read-shaped equivalent — `apigateway:GET`, `cloudwatch:GetMetricData`, `pricing:GetProducts`,
  etc.) against `Resource: "*"`. **No write, modify, stop, terminate, or delete permission against
  any monitored AWS resource exists anywhere in this policy.** This directly implements the
  roadmap's "every IAM role is read-only ... no write access exists until the write-action/
  approval layer is explicitly built later" rule (that layer, build-order Step 8, is not built —
  see Section 8 below).
- **Two write-capable statements**, both narrowly ARN-scoped to this app's own bookkeeping
  DynamoDB tables, never to a monitored AWS resource:
  - `OpspilotInvestigationMemoryWrite` — `PutItem`/`Scan` on `opspilot-investigations` only (the
    RAG-based investigation-memory table).
  - `OpspilotMcpTokenAndAuditLog` — `PutItem`/`GetItem`/`UpdateItem`/`Query`/`Scan` on exactly
    `opspilot-mcp-tokens` and `opspilot-audit-log`, and no other resource.
- Cost estimation deliberately uses only the Pricing API (`pricing:GetProducts`), not Cost
  Explorer's `ce:GetCostAndUsage` — this is both the cheaper choice (Cost Explorer bills per
  request) and the more conservative one from a scope perspective (list-price estimates, not
  billed-cost lookups tied to your actual spend), and is labeled as such in the UI (see roadmap
  Section 3.2).
- **Cross-account role assumption** (a second, separate control from Section 3's IAM keys — the
  roadmap's "external ID per connection" requirement) does not apply yet: multi-account support is
  explicitly deferred (roadmap Section 2/8), so there is no cross-account trust relationship to
  secure. Nothing to flag here beyond "correctly not started."

## 5. Secrets handling

- Only `opspilot-backend/.env.example` and `opspilot-frontend/.env.local.example` (placeholder
  values only) are committed. Real `.env`/`.env.local` files are excluded by `.gitignore` at every
  directory depth.
- Confirmed via `git ls-files` and a full `git log --all` history sweep (regex-checked for AWS key
  patterns, private-key blocks, bearer tokens, inline passwords): **no secret has ever been
  committed to this repository, live or historical.**
- This repository is public on GitHub, so GitHub's secret scanning is automatically enabled at no
  cost. **Push protection's on/off state has not been verified** — it requires the repo owner to
  check it directly under GitHub → Settings → Code security with an authenticated session; this is
  a manual follow-up, not something verifiable or fixable from the codebase itself (tracked in
  `docs/BUILD_PROGRESS.md`).
- Standing policy if a secret is ever accidentally committed: rotate it immediately in the
  provider's console. `git revert`/history rewriting does not, on its own, remove a secret's
  exposure — anyone who already cloned or viewed the commit has it.
- Request logging (`opspilot-backend/app/core/logging.py`'s `RequestIdMiddleware`) logs only HTTP
  method, path, status code, and duration — never headers, request/response bodies, bearer tokens,
  or cookie values.

## 6. MCP server — token authentication

The MCP server (`opspilot-backend/app/mcp/server.py`) is a second, parallel front door to the same
`services/`/`aws/` layer the dashboard uses (roadmap Section 1/3.6) — every tool it exposes is the
same function the dashboard REST API calls, guaranteed to agree.

- **Token required on every tool call, including from localhost** — this is intentional per the
  roadmap ("build the check in now so nothing changes later if hosted elsewhere"), not overkill
  for a local-only tool.
- Settings → MCP Access (`opspilot-frontend/app/settings`) has "Generate token" (shown exactly
  once, never retrievable again) and "Revoke."
- **Storage**: DynamoDB (`opspilot-mcp-tokens` table), not Postgres — the roadmap's literal
  wording says Postgres, but this app has no Postgres infrastructure anywhere; DynamoDB was
  chosen as the free, already-connected alternative (see `docs/BUILD_PROGRESS.md`'s "Decisions
  made" section for the full reasoning). The token is stored **bcrypt-hashed only** — the
  plaintext is generated with `secrets.token_urlsafe(32)` (256 bits of entropy), returned to the
  caller exactly once, and never written to DynamoDB, a log line, an audit-log `detail` field, or
  anywhere else.
- **Single-admin, single-active-token model**: generating a new token always invalidates whichever
  token was previously active (revoked or not). There is no multi-token support — this is a
  deliberate single-operator scope choice, not a partial implementation.
- **Transport**: stdio JSON-RPC has no per-request header channel, so the token is passed via the
  `OPSPILOT_MCP_TOKEN` environment variable set on whatever process launches the MCP server
  (Claude Desktop's config, or this repo's own `.env` for local testing). It is read fresh from the
  environment and re-validated against DynamoDB **on every tool call**, not cached at process
  start — so revoking a token takes effect immediately without restarting the MCP process.
- **Fails closed on every branch**: missing token, no token ever generated, revoked token, wrong
  token, a DynamoDB lookup error, or a corrupt stored hash all reject the call — none of these
  states are treated as "allow."
- Token comparison uses `bcrypt.checkpw` (constant-time), not a manual string comparison.
- Token generation and revocation each write an Audit Log entry automatically (Section 7).
- **Known limitation**: `list_tools` (tool discovery) is deliberately *not* gated — only
  `call_tool` (and therefore every AWS-touching tool body) is. `GET /mcp/tools`, the dashboard's
  in-process introspection of the same tool list, is separately protected by the normal
  `require_session` check. The raw stdio `list_tools` handler being reachable without a token only
  matters to whoever can already spawn/pipe to the local MCP process — the same trust boundary as
  being able to set `OPSPILOT_MCP_TOKEN` in its environment in the first place.
- **Known limitation — no rate limiting** on the `call_tool` path, matching the HTTP API's current
  (also-none) posture (see Section 2). Given the token's 256-bit entropy, brute force is
  infeasible regardless, but this should be revisited alongside the HTTP API's own rate limiting
  before any multi-user hosting.

## 7. Audit logging

**Current real coverage** (not aspirational — this is everything `audit_log_service.write_entry`
is actually called with today, across the whole codebase):
- `mcp_token_generated` / `mcp_token_revoked` — written from `POST /mcp/token/generate` and
  `POST /mcp/token/revoke`, tagged with the real signed-in admin's email (a cryptographically
  verified identity, from a validated session JWT).
- `login_success` / `login_failed` — written from `POST /auth/login-audit`, tagged with the
  *attempted* email typed into the login form.

**Important trust-level distinction**: `AuditLogEntry.actor_email` does **not** always mean "a
verified identity." For the two `mcp_token_*` actions it is a cryptographically verified admin
email. For `login_failed` specifically, it is raw, unauthenticated, attacker-controllable input —
intentionally recorded as-is, because "someone attempted to log in as X and failed" is useful
signal for a single-admin app, but it must never be treated as proof that X actually attempted
anything. Any future consumer of this table (an Audit Log UI, an alerting rule, this document)
needs to account for that per-action difference rather than assuming uniform trust.

**What is deliberately *not* persisted to this table, and why**:
- **Individual MCP tool calls** are not written here — they're covered by `app.mcp.server`'s own
  per-call log lines (accepted/rejected, with the tool name) instead. Since MCP authenticates via
  one shared token rather than per-user identity, "tied to a real logged-in user" (the roadmap's
  Section 4 audit-log requirement) can't be literally satisfied on this surface at single-admin
  scale — stating that honestly here rather than implying per-user MCP attribution exists that
  doesn't.
- **Dashboard read actions** (scans, resource lists, etc.) are not audit-logged — they're
  read-only, already gated by session auth, and have no side effect worth a durable trail yet.
  This becomes genuinely meaningful once the write-action/approval layer (Section 8) exists, and
  `write_entry` is designed to be extended then, not duplicated into a second mechanism now.
- **Agent investigation findings** are not duplicated into this table — `opspilot-investigations`
  (the RAG-based investigation-memory store) already durably records what the agent found and
  when, which is a more complete record for that purpose than a short audit-log line would be.

A write failure on this table (e.g. a transient DynamoDB error) never blocks or fails the action
it's trying to record — every call site wraps the write in a try/except that logs the failure
server-side and still lets the primary action (token generate/revoke, login) complete normally.
This is an intentional choice: an audit-log outage should not become a denial-of-service against
the feature it's auditing.

## 8. Known limitations / accepted gaps (summary)

| Gap | Status | Required before... |
|---|---|---|
| Static, long-lived AWS IAM user keys (Section 3) | Accepted for now | Hosting for/by anyone other than the single local operator |
| No rate limiting/lockout (login, MCP `call_tool`) | Accepted for now | Any internet-facing deployment |
| GitHub push-protection on/off status unverified | Needs manual check | N/A — repo-owner action, not code |
| Write-action/approval layer not built | Not started, deferred | Any AWS-mutating feature (stop/terminate, etc.) — build-order Step 8, explicitly last and gated on a separate UX decision |
| Multi-account / cross-account role assumption | Not started, deferred | Supporting more than one connected AWS account (roadmap Section 2/8) |
| MCP tool calls not individually audit-logged | Accepted, covered by app logs instead | Per-user MCP attribution (not meaningful until MCP moves beyond one shared token) |

## 9. Responsible disclosure

If you find a security issue in this project, please report it directly rather than opening a
public issue: **zaryabbaloch04@gmail.com**. This is a personal/portfolio project without a formal
bug-bounty program, but reports are genuinely welcome and will be looked at promptly.
