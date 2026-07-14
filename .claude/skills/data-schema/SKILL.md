---
name: data-schema
description: Use before writing or consuming galaxy/cluster resource data in OpsPilot AI — defines the canonical resource + relations JSON shape shared by backend-agent (producer), frontend-agent (consumer), and mcp-agent (exposer). Read this before adding a field to a resource card, wiring the galaxy dashboard to real data, building check_idle/estimate_cost responses, or building the "View connections" cluster view. Triggers on: galaxy schema, resource shape, relations field, idle/cost tool response shape, star sizing, cluster view data.
---

# OpsPilot AI — galaxy/cluster resource data schema

This is the one contract all of `backend-agent`, `frontend-agent`, and `mcp-agent` write to,
per roadmap `docs/opspilot-ai-roadmap.md` Section 7. Don't invent a new shape per prompt —
extend this one, and update this file in the same change if you do.

## Wire convention: snake_case, always

The existing backend (`app/models/*.py`, e.g. `EC2Instance.instance_id`, `.launch_time`) and
existing frontend (`lib/api.ts`, e.g. `instance_id`, `launch_time`, `average_cpu_percent`) are
consistently **snake_case** end to end — Pydantic models serialize snake_case, TS interfaces
mirror them field-for-field.

The prototype `docs/aws-galaxy-dashboard.jsx` mock data uses **camelCase** (`idleDays`, `x`,
`y`) purely because it's static in-component sample data with no backend behind it yet. Do
**not** carry `idleDays`/`x`/`y` into the real API contract — `frontend-agent` maps the
snake_case response below into whatever local view-model shape the component wants, the same
way it already would for any other API response. The API itself stays snake_case.

## Canonical resource shape (`GalaxyResource`)

One entry per resource, returned by the region-scan endpoint that backs the galaxy view
(roadmap 3.3/3.4) and by `check_idle`/`estimate_cost` tool responses (3.1/3.2):

```jsonc
{
  "id": "i-0a3f",                 // AWS resource ID
  "name": "web-server-prod-01",   // Name tag; falls back to id if untagged (roadmap 3.8 list_resources rule) — never silently omitted
  "type": "ec2",                  // one of the 15 TYPE_CODES below
  "region": "us-east-1",

  "cost": {
    "projected_monthly": 68.0,    // rate x full month — THIS drives star/bubble sizing (3.1a), never incurred_so_far
    "incurred_so_far": 2.10,      // actual billed since creation or since going idle
    "method": "list_price"        // "list_price" (Pricing API) | "billed" (Cost Explorer) — UI must label which one it's showing (3.2)
  },

  "idle": {
    "is_idle": false,             // true only if EVERY daily datapoint across the whole requested window is below threshold (roadmap 3.1 anti-averaging rule) — NOT the same thing as "idle_days >= 7"
    "idle_since": null,           // ISO date; walk back from the most recent day to compute the current trailing idle streak, +1 day past the break (3.1)
    "idle_days": 0,               // length of that trailing streak — can be SHORTER than the requested window (e.g. a burst 3 days into a 10-day window makes is_idle=false for that window while idle_days still reports the streak since the burst)
    "younger_than_window": false, // true => report "idle since launch", never a fabricated longer window (3.1 edge case)
    "idle_since_is_estimated": false  // true only when there's no real timestamp/metric to verify idle_since/idle_days against — currently EIP (always, no creation timestamp exists) and EBS-unattached-with-no-create_time-signal. In that case idle_days/idle_since is a worst-case "known idle for at least the requested window" assumption, not a CloudWatch-verified streak — a resource that just became idle and one idle for the full window both report the same idle_days. False everywhere else, including every CloudWatch-verified branch and EBS-unattached-but-younger-than-window (has a real create_time anchor).
  },

  "health": {
    "primary_metric": "cpu_percent",  // which signal this type's idle check uses (see TYPE_CODES table)
    "primary_metric_value": 42.0,     // null if not applicable (e.g. EIP has no CPU) -- ALSO always null from scan_region() today (see note below)
    "status": "running"               // running/stopped, ok/impaired, available, etc. — type-appropriate
  },

  "created_at": "2026-07-04T10:00:00Z",  // launch/creation timestamp, null if unknown

  "relations": [                  // roadmap 3.7 — omit or [] when no relations data; no new AWS calls, shaped from existing Describe* output
    { "id": "vol-77aa", "label": "attached", "kind": "ebs" }
  ]
}
```

Don't conflate `is_idle` with the UI's amber-pulse threshold (roadmap Section 5: "pulsing amber = idle ≥ 7 days") — those are two different concepts that happen to coincide today only because the dashboard route currently calls `check_idle` with a fixed 7-day window. `is_idle` is a property of *the window you asked for*; the UI's `>= 7 days` rule is `frontend-agent`'s own display threshold applied to `idle_days`, independent of whatever window a given caller requested.

`relations[].label` is the **edge semantics** shown on the connecting line — one of `attached`,
`secured_by`, `in`, `routed_by`, `assumes` (roadmap 3.7). `relations[].kind` is the **target
node's type** and is what `frontend-agent` uses to decide sizing/color in the cluster view:

- `kind` in `COST_BEARING_KINDS` (any of the 15 `TYPE_CODES` below **exactly as spelled there**,
  e.g. `ebs`, `elb` — not `ebs_volume`/`load_balancer`; `kind` and `type` share the same code
  space so a frontend `COST_BEARING_KINDS.has(kind)` check can just test set membership against
  `TYPE_CODES`) → render sized by that resource's own `projected_monthly` cost, same rule as the
  main galaxy.
- `kind` in `INFRA_KINDS` = `security_group | subnet | vpc | iam_role` → render smaller, in a
  distinct violet color, non-cost-bearing. These come from `Describe*` fields (SG IDs, subnet
  ID, VPC, IAM role) that have no cost of their own — never give them a `cost` block.
  `kind: "iam_role"`'s `id` is deliberately the bare role/instance-profile **name** (e.g.
  `my-lambda-role`), never the full ARN — this app otherwise keeps the AWS account ID out of
  every caller-facing field (it's scrubbed from error messages for the same reason), and an ARN
  embeds the account ID (`arn:aws:iam::<account-id>:role/...`). `LambdaFunctionSummary.role_name`
  and `EC2Instance.iam_instance_profile_name` are the already-stripped source fields — don't
  reintroduce the full ARN into either the API response or a relation `id`.

## Top-level scan response

```jsonc
{
  "region": "us-east-1",
  "last_updated": "2026-07-10T09:15:00Z",   // for "Last updated N minutes ago" (3.4)
  "resources": [ /* GalaxyResource[] */ ],
  "totals": {
    "monthly_spend": 622.0,
    "idle_count": 4,
    "idle_monthly_waste": 149.0
  },
  "error": null   // see below
}
```

On a failed rescan, the backend returns the **last good cached payload** for that region with
its original `last_updated` timestamp untouched — never an empty `resources` array (3.4).
`error` (`GET /resources/scan`, `app/models/scan.py::ScanResponse`) is `null` on every normal
fresh/cached response, and set to a short human-readable string **only** when this payload is
stale cache served after a failed rescan attempt — the caller surfaces it as a non-blocking
warning ("showing data from N minutes ago, refresh failed"), never as "no data." A rescan
rejected by the debounce/cooldown (roadmap 3.4, `scan_service.COOLDOWN_SECONDS = 45`) is a
distinct case at the HTTP layer, not `error`: `GET /resources/scan` returns **429** with a
`Retry-After` header and the still-good cached body (or a plain error `detail` if there is no
cache at all yet for that region) — this can only happen on an explicit `force=true` refresh,
never on a plain load. A scan failure with **no prior cache at all** for a region returns
**502** — the one case the roadmap says has genuinely nothing to fall back to.

Built by `GET /resources/scan?region=...&force=...` (`app/services/scan_service.py`, one
region per call — see roadmap 3.3's "region list is for the selector, not for scanning every
region on every call"). `GET /resources/regions` (backed by `ec2:DescribeRegions`, no new IAM
needed) lists enabled regions for that selector. Both routes, and the `scan_region`/
`list_regions` MCP tools that call the same `scan_service.scan_region()`/
`list_available_regions()`, sit behind `require_session` like every other route.

Two deliberate extensions **`GalaxyResource`** carries beyond the per-field illustration above,
both introduced by `scan_service` (not by `check_idle`/`estimate_cost`, which still always
populate `cost`/`idle` for a resource that resolves at all):
- `cost`/`idle` are **nullable at the per-resource level**. A single resource's idle or cost
  lookup failing (CloudWatch throttled, no Pricing API match) must not drop that resource from
  the scan (same graceful-degradation principle as one whole *type* failing to list, one level
  deeper) — `null` means "this lookup failed for this resource," not "not applicable."
- `relations` (roadmap 3.7) is populated in `scan_region()`/`list_lite_resources()`/
  `get_lite_resource()` alike, purely by shaping fields each type's existing `list_*()`/`get_*()`
  call already returns — no new AWS calls. Populated for: `ec2` (attached EBS volume(s),
  security group(s), subnet, VPC, IAM instance profile — the roadmap's example set), `ebs`
  (reverse `attached` back to its EC2 instance), `rds`/`elb`/`opensearch` (security group(s),
  subnet(s), VPC), `lambda` (IAM role always; VPC/security group(s)/subnet(s) only when the
  function is VPC-attached), `eip` (attached EC2 instance), `nat_gateway` (subnet, VPC),
  `elasticache` (security group(s)), `redshift` (security group(s), VPC). Always `[]` for
  `dynamodb`, `sagemaker`, `api_gateway`, `cloudfront`, `kinesis` — their existing list/describe
  responses carry no VPC/security-group/IAM linkage without an extra call this step doesn't make
  (see `app/services/scan_service.py::_relations_for()`'s docstring for the per-type reasoning);
  also `[]` for any individual resource that simply has no such linkage (e.g. a Lambda function
  outside a VPC, an EC2 instance with no attached volumes).
- `health.primary_metric_value` is always `null` from `scan_region()` today — populating the
  *current live value* of a type's primary metric needs its own CloudWatch call per resource,
  independent of `check_idle`'s pass/fail daily-window check. That's roadmap 3.8's
  `get_resource_health` tool (not built in this step) — `scan_region()` doesn't duplicate it.

One more scan-specific note: CloudFront is a **global** service (one distribution list, not
per-region) — `scan_region(region=X)` attributes every CloudFront distribution to whichever
region `X` happens to be, so scanning two regions back to back currently double-counts
CloudFront distributions in `totals`. A known, documented gap (see
`cloudfront_service.list_distributions`'s docstring), not yet deduplicated across regions.

## TYPE_CODES (the 15 in-scope types, roadmap Section 2a)

| `type` code | Family (icon, roadmap Section 5) | Primary idle metric |
|---|---|---|
| `ec2` | Compute | `cpu_percent` (+ network in/out ≈ 0) |
| `ebs` | Storage | attachment state / `volume_io_ops` |
| `rds` | Database | `database_connections` |
| `eip` | Networking | association state |
| `elb` | Networking | `request_count` |
| `lambda` | Compute | invocation count |
| `nat_gateway` | Networking | bytes in/out |
| `dynamodb` | Database | consumed read/write capacity |
| `elasticache` | Database | `curr_connections` |
| `sagemaker` | Compute | invocation count |
| `redshift` | Database | `database_connections` |
| `api_gateway` | Networking | request count |
| `cloudfront` | Networking | request count |
| `opensearch` | Database | search/index rate |
| `kinesis` | Streaming | `incoming_records` |

Non-galaxy types already live on the existing Overview dashboard (`s3`, `sns`, `cloudtrail`)
are **not** part of this schema — S3/SNS are Tier 2 deferred (Section 2a), CloudTrail is an
event log, not a cost-bearing resource. Don't add them to `TYPE_CODES`.

## Who produces/consumes what

- **`backend-agent`** is the sole producer: every new tool/service (`check_idle`,
  `estimate_cost`, region scan, and the chat tools in 3.8) returns data conforming to
  `GalaxyResource`/the scan response above — extend this file first if a new field is needed,
  don't let two tools drift into slightly different shapes for the same concept.
- **`frontend-agent`** is the sole consumer for the galaxy/cluster UI — maps this schema into
  whatever local rendering shape (`x`/`y` layout, etc.) the component needs, but the
  fetch/response boundary matches this file exactly.
- **`mcp-agent`** exposes the same `services/` functions over MCP — the tool's return value is
  this same schema, not a reshaped copy, since dashboard and MCP are two front doors to one
  backend (roadmap Section 1/3.6).
