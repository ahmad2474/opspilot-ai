"""AWS Compute Optimizer rightsizing recommendations (roadmap phase 2
Section 1.3, "Batch B"). See app/models/compute_optimizer.py's module
docstring for how this differs from every other check in this app.

Field names below (instanceRecommendations/recommendationOptions/
savingsOpportunity/estimatedMonthlySavings; volumeRecommendations/
currentConfiguration/volumeRecommendationOptions; lambdaFunctionRecommendations/
memorySizeRecommendationOptions; ecsServiceRecommendations/
currentServiceConfiguration/serviceRecommendationOptions) and the `finding`
enum values per resource type were verified against the actual installed
boto3 (1.35.x) compute-optimizer service model before writing this -- not
guessed (same discipline this batch applied to Cost Explorer's commitment
APIs in commitment_service.py).

Pagination note, also verified rather than assumed: of the 4
Get*Recommendations calls used here, only get_lambda_function_recommendations
actually supports a boto3 paginator (`client.can_paginate(...)` is False for
the EC2/EBS/ECS variants) -- so this module hand-rolls the shared
`nextToken`-in/`nextToken`-out pagination loop for all 4 uniformly, rather
than depending on `get_paginator` for some and not others.

Every Get*Recommendations call raises `OptInRequiredException` for an
account that hasn't opted in to Compute Optimizer -- caught explicitly and
turned into a graceful `enrolled=False` result, never an unhandled
exception (same pattern already established for Redshift/Kinesis and, in
this same batch, ECS Container Insights).
"""
from __future__ import annotations

import logging
from typing import Any, Callable

from app.aws.client import get_compute_optimizer_client
from app.models.compute_optimizer import RightsizingFinding, RightsizingReport

logger = logging.getLogger("app.services.compute_optimizer")

COMPUTE_OPTIMIZER_OPT_IN_NOTE = (
    "This AWS account is not enrolled in Compute Optimizer. Enable it once "
    "(AWS console -> Compute Optimizer -> Get started, or `aws "
    "compute-optimizer update-enrollment-status --status Active`) to unlock "
    "rightsizing recommendations -- a one-time, account-level opt-in, same "
    "shape as Redshift/Kinesis's service activation elsewhere in this app."
)

_ALREADY_OPTIMAL_NOTE = "Every checked resource is already Optimized -- no rightsizing findings."

_SUPPORTED_RESOURCE_TYPES = {"ec2", "ebs", "lambda", "ecs"}


class UnsupportedRightsizingResourceTypeError(ValueError):
    """Raised when get_rightsizing_recommendations is asked about a type
    outside Compute Optimizer's EC2/EBS/Lambda/ECS-on-Fargate coverage."""


def _paginate(client_method: Callable[..., dict], list_key: str, **kwargs: Any) -> list[dict]:
    """Manual `nextToken` pagination -- see module docstring for why this
    isn't `client.get_paginator(...)` uniformly across all 4 operations."""
    items: list[dict] = []
    next_token: str | None = None
    while True:
        call_kwargs = dict(kwargs)
        if next_token:
            call_kwargs["nextToken"] = next_token
        response = client_method(**call_kwargs)
        items.extend(response.get(list_key, []))
        next_token = response.get("nextToken")
        if not next_token:
            return items


def _top_recommendation_option(
    options: list[dict], config_formatter: Callable[[dict], str]
) -> tuple[str | None, float | None]:
    """AWS ranks recommendationOptions by `rank` (1 = best); this app
    surfaces only the top-ranked option to keep the finding shape flat and
    comparable across resource types, rather than the full ranked list."""
    if not options:
        return None, None
    top = min(options, key=lambda o: o.get("rank", 999))
    savings = (top.get("savingsOpportunity") or {}).get("estimatedMonthlySavings") or {}
    return config_formatter(top), savings.get("value")


def _get_ec2(client: Any) -> RightsizingReport:
    raw_items = _paginate(client.get_ec2_instance_recommendations, "instanceRecommendations")
    findings: list[RightsizingFinding] = []
    for raw in raw_items:
        if raw.get("finding") == "Optimized":
            continue
        recommended, savings = _top_recommendation_option(
            raw.get("recommendationOptions", []), lambda o: o.get("instanceType", "unknown")
        )
        findings.append(
            RightsizingFinding(
                resource_id=raw["instanceArn"],
                finding=raw.get("finding", "Unknown"),
                current_configuration=raw.get("currentInstanceType", "unknown"),
                recommended_configuration=recommended,
                estimated_monthly_savings_usd=savings,
                lookback_period_days=raw.get("lookBackPeriodInDays"),
            )
        )
    return RightsizingReport(
        resource_type="ec2",
        enrolled=True,
        findings=findings,
        total_checked=len(raw_items),
        note=_ALREADY_OPTIMAL_NOTE if raw_items and not findings else None,
    )


def _format_ebs_config(cfg: dict) -> str:
    return f"{cfg.get('volumeType', 'unknown')} {cfg.get('volumeSize', '?')}GiB"


def _get_ebs(client: Any) -> RightsizingReport:
    raw_items = _paginate(client.get_ebs_volume_recommendations, "volumeRecommendations")
    findings: list[RightsizingFinding] = []
    for raw in raw_items:
        if raw.get("finding") == "Optimized":
            continue
        recommended, savings = _top_recommendation_option(
            raw.get("volumeRecommendationOptions", []),
            lambda o: _format_ebs_config(o.get("configuration", {})),
        )
        findings.append(
            RightsizingFinding(
                resource_id=raw["volumeArn"],
                finding=raw.get("finding", "Unknown"),
                current_configuration=_format_ebs_config(raw.get("currentConfiguration", {})),
                recommended_configuration=recommended,
                estimated_monthly_savings_usd=savings,
                lookback_period_days=raw.get("lookBackPeriodInDays"),
            )
        )
    return RightsizingReport(
        resource_type="ebs",
        enrolled=True,
        findings=findings,
        total_checked=len(raw_items),
        note=_ALREADY_OPTIMAL_NOTE if raw_items and not findings else None,
    )


def _get_lambda(client: Any) -> RightsizingReport:
    raw_items = _paginate(
        client.get_lambda_function_recommendations, "lambdaFunctionRecommendations"
    )
    findings: list[RightsizingFinding] = []
    for raw in raw_items:
        if raw.get("finding") == "Optimized":
            continue
        recommended, savings = _top_recommendation_option(
            raw.get("memorySizeRecommendationOptions", []),
            lambda o: f"{o.get('memorySize', '?')}MB",
        )
        findings.append(
            RightsizingFinding(
                resource_id=raw["functionArn"],
                finding=raw.get("finding", "Unknown"),
                current_configuration=f"{raw.get('currentMemorySize', '?')}MB",
                recommended_configuration=recommended,
                estimated_monthly_savings_usd=savings,
                lookback_period_days=raw.get("lookbackPeriodInDays"),
            )
        )
    return RightsizingReport(
        resource_type="lambda",
        enrolled=True,
        findings=findings,
        total_checked=len(raw_items),
        note=_ALREADY_OPTIMAL_NOTE if raw_items and not findings else None,
    )


def _format_ecs_config(cfg: dict) -> str:
    return f"{cfg.get('cpu', '?')} CPU units / {cfg.get('memory', '?')}MiB"


def _get_ecs(client: Any) -> RightsizingReport:
    raw_items = _paginate(client.get_ecs_service_recommendations, "ecsServiceRecommendations")
    findings: list[RightsizingFinding] = []
    for raw in raw_items:
        if raw.get("finding") == "Optimized":
            continue
        recommended, savings = _top_recommendation_option(
            raw.get("serviceRecommendationOptions", []), _format_ecs_config
        )
        findings.append(
            RightsizingFinding(
                resource_id=raw["serviceArn"],
                finding=raw.get("finding", "Unknown"),
                current_configuration=_format_ecs_config(
                    raw.get("currentServiceConfiguration", {})
                ),
                recommended_configuration=recommended,
                estimated_monthly_savings_usd=savings,
                lookback_period_days=raw.get("lookbackPeriodInDays"),
            )
        )
    return RightsizingReport(
        resource_type="ecs",
        enrolled=True,
        findings=findings,
        total_checked=len(raw_items),
        note=_ALREADY_OPTIMAL_NOTE if raw_items and not findings else None,
    )


_DISPATCH: dict[str, Callable[[Any], RightsizingReport]] = {
    "ec2": _get_ec2,
    "ebs": _get_ebs,
    "lambda": _get_lambda,
    "ecs": _get_ecs,
}


def get_rightsizing_recommendations(
    resource_type: str, region: str | None = None
) -> RightsizingReport:
    if resource_type not in _SUPPORTED_RESOURCE_TYPES:
        raise UnsupportedRightsizingResourceTypeError(
            f"get_rightsizing_recommendations for resource_type={resource_type!r} is not "
            f"supported -- only {sorted(_SUPPORTED_RESOURCE_TYPES)!r} (Compute Optimizer's "
            "EC2/EBS/Lambda/ECS-on-Fargate coverage) are implemented."
        )
    client = get_compute_optimizer_client(region=region)
    try:
        return _DISPATCH[resource_type](client)
    except client.exceptions.OptInRequiredException:
        logger.info("compute_optimizer_not_enrolled resource_type=%s", resource_type)
        return RightsizingReport(
            resource_type=resource_type,
            enrolled=False,
            findings=[],
            total_checked=0,
            note=COMPUTE_OPTIMIZER_OPT_IN_NOTE,
        )
