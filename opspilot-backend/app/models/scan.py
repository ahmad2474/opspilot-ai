"""Response models for region-wide scanning (roadmap Section 3.3/3.4).

Field names/shape match the `data-schema` skill's `GalaxyResource` and
top-level "scan response" exactly -- this is the first place that
top-level shape (region, last_updated, resources[], totals) actually gets
built, per that skill's own note. frontend-agent/mcp-agent consume this
shape as-is.

Two deliberate, documented extensions beyond the skill's illustrative
JSONC example (flagged for the skill file itself to be updated in the
same change, since these are real contract decisions, not drift):

1. `GalaxyResource.cost`/`.idle` are Optional here, defaulting to `None`.
   The canonical example shows both blocks always populated, but the
   roadmap's graceful-degradation rule ("one resource type failing must
   not take down the whole scan") extends one level deeper in practice:
   a single resource's idle/cost lookup can fail (CloudWatch throttled,
   Pricing API miss) without that resource's identity/listing being
   dropped from the scan -- exactly the precedent GET /resources/ec2
   already set per-instance for cpu/idle/cost. `cost`/`idle` being `None`
   means "this particular lookup failed for this particular resource,"
   not "this resource has no cost/idle status at all."
2. `ScanResponse.error` -- present (`None` normally) so a rescan that
   failed and fell back to the last good cache (roadmap 3.4) can signal
   that fact to the caller alongside the (untouched) stale data, per
   "signal the failure separately... never return an empty/blank result."

`relations` (roadmap 3.7) is populated by `scan_service._relations_for()`
-- see `RelationLink`'s own docstring below for exactly which types/
fields. Not part of the two numbered deviations above since it isn't a
deviation from the skill's example, just the (now-built) feature the
skill's example already showed.
"""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from app.models.cost import CostEstimate
from app.models.idle import IdleCheckResult

# The 15 roadmap-scoped resource type codes (roadmap Section 2a / TYPE_CODES
# in the data-schema skill). Order matches the skill's table.
TYPE_CODES: tuple[str, ...] = (
    "ec2",
    "ebs",
    "rds",
    "eip",
    "elb",
    "lambda",
    "nat_gateway",
    "dynamodb",
    "elasticache",
    "sagemaker",
    "redshift",
    "api_gateway",
    "cloudfront",
    "opensearch",
    "kinesis",
)

# Closed sets for RelationLink.label/.kind (roadmap 3.7) -- Literal rather
# than plain str for the same reason every other closed-set string field in
# this codebase already is (ChatEvent.type, CostEstimate.method,
# LoadBalancerType, ResourceListResponse.idle_data_source): a typo'd kind
# (e.g. "ebs_volume"/"load_balancer" instead of the TYPE_CODES-exact
# "ebs"/"elb") fails fast at construction time in
# scan_service._relations_for() instead of silently reaching the
# frontend/MCP caller as an unrecognized node kind. RelationKind is derived
# directly from TYPE_CODES (plus the 4 infra kinds) rather than a second,
# hand-typed list of the same 15 codes -- one source of truth, so this
# can't itself drift from TYPE_CODES the way the bug class it's guarding
# against did.
RelationLabel = Literal["attached", "secured_by", "in", "routed_by", "assumes"]
RelationKind = Literal[*TYPE_CODES, "security_group", "subnet", "vpc", "iam_role"]


class RelationLink(BaseModel):
    """One entry in `GalaxyResource.relations` (roadmap 3.7). Populated by
    `scan_service._relations_for()` purely by shaping fields already
    returned by each type's existing list_*()/get_*() Describe* call --
    no new AWS calls. Populated for: ec2 (attached EBS volume(s), security
    group(s), subnet, VPC, IAM instance profile), ebs (reverse "attached"
    to its EC2 instance), rds/elb/opensearch (security group(s), subnet(s),
    VPC), lambda (IAM role, and VPC/security group/subnet when the
    function is VPC-attached), eip (attached EC2 instance), nat_gateway
    (subnet, VPC), elasticache/redshift (security group(s); redshift also
    VPC). Always `[]` for dynamodb, sagemaker, api_gateway, cloudfront,
    kinesis -- their existing list/describe responses carry no VPC/
    security-group/IAM linkage (see `_relations_for()`'s docstring for why,
    per type). Also `[]` whenever the underlying resource itself simply
    has no such linkage (e.g. an EC2 instance with no attached volumes, or
    a Lambda function outside a VPC).
    """

    id: str
    label: RelationLabel = Field(
        description="Edge semantics: attached | secured_by | in | routed_by | assumes"
    )
    kind: RelationKind = Field(
        description="Target node's type -- one of TYPE_CODES (cost-bearing) or "
        "security_group | subnet | vpc | iam_role (infra, non-cost-bearing)."
    )


class ResourceHealth(BaseModel):
    """`health` block on `GalaxyResource`.

    `primary_metric_value` is always `None` in this build step -- populating
    it with the *current* live value of a type's primary idle-signal metric
    would need its own CloudWatch call per resource, independent of the
    pass/fail daily-window check `check_idle` already does. That's the
    natural home for roadmap 3.8's `get_resource_health` tool (a separate,
    not-yet-built step), not duplicated here just for the galaxy view.
    `status` and `primary_metric` (the metric *name*, not its value) are
    always populated, from each resource type's own state/status field and
    the roadmap 2a idle-signal table respectively.
    """

    primary_metric: str
    primary_metric_value: float | None = None
    status: str


class GalaxyResource(BaseModel):
    """One entry in a scan response's `resources[]` -- see module
    docstring for the two documented deviations from the data-schema
    skill's illustrative example (cost/idle nullable, relations shaped by
    `scan_service._relations_for()`). `relations` is now populated for 10 of
    the 15 TYPE_CODES -- ec2, ebs, rds, elb, lambda, eip, nat_gateway,
    elasticache, redshift, opensearch (see `RelationLink`'s own docstring
    for exactly which fields per type) -- and always `[]` for the 5
    documented-gap types (dynamodb, sagemaker, api_gateway, cloudfront,
    kinesis) that carry no VPC/security-group/IAM linkage in their existing
    list/describe response, as well as for any individual resource that
    simply has no such linkage regardless of type."""

    id: str
    name: str = Field(
        description="Name tag if present, else the resource's own AWS ID/human "
        "identifier -- never silently omitted for an untagged resource "
        "(roadmap 3.8 list_resources rule)."
    )
    type: str = Field(description="One of the 15 TYPE_CODES.")
    region: str

    cost: CostEstimate | None = None
    idle: IdleCheckResult | None = None
    health: ResourceHealth
    created_at: datetime | None = None
    relations: list[RelationLink] = Field(default_factory=list)


class ScanTotals(BaseModel):
    monthly_spend: float = Field(
        description="Sum of every resource's cost.projected_monthly (resources with "
        "no cost block contribute 0, not a fabricated estimate)."
    )
    idle_count: int = Field(description="Count of resources where idle.is_idle is True.")
    idle_monthly_waste: float = Field(
        description="Sum of cost.projected_monthly for resources where idle.is_idle "
        "is True -- same 'projected, not incurred-so-far' rule as monthly_spend "
        "(roadmap 3.1a)."
    )


class ScanResponse(BaseModel):
    region: str
    last_updated: datetime = Field(
        description="ISO timestamp this scan actually ran. On a failed rescan served "
        "from cache, this is the ORIGINAL successful scan's timestamp, untouched "
        "(roadmap 3.4) -- never bumped just because a rescan was attempted."
    )
    resources: list[GalaxyResource]
    totals: ScanTotals
    error: str | None = Field(
        default=None,
        description="Set only when this payload is stale cache served after a failed "
        "rescan attempt (roadmap 3.4) -- None on every normal fresh/cached response. "
        "The caller (dashboard/MCP) should surface this as a non-blocking warning "
        "('showing data from N minutes ago, refresh failed'), never treat a non-null "
        "error as 'no data' -- resources/totals are still the last good scan.",
    )
