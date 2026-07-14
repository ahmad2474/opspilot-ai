"""Lambda business logic. No boto3 calls anywhere else in the app.

See LambdaFunctionSummary's docstring (app/models/dashboard.py) for why
there's deliberately no get_function()-populated creation timestamp --
Lambda's API exposes no creation time at all, only LastModified (which is
not a safe creation-time proxy, since it changes on every deploy).
"""
from __future__ import annotations

from app.aws.client import get_lambda_client
from app.models.dashboard import LambdaCard, LambdaFunctionSummary


def _role_name_from_arn(arn: str | None) -> str | None:
    """Strips a role ARN down to just its trailing name segment (e.g.
    "arn:aws:iam::123456789012:role/my-lambda-role" -> "my-lambda-role") --
    security: keeps the AWS account ID embedded in the ARN out of
    LambdaFunctionSummary.role_name, matching this app's existing
    precedent of scrubbing the account ID from every other caller-facing
    field. None in, None out.
    """
    if not arn:
        return None
    return arn.rsplit("/", 1)[-1]


def list_functions(region: str | None = None) -> LambdaCard:
    client = get_lambda_client(region=region)
    paginator = client.get_paginator("list_functions")
    functions: list[LambdaFunctionSummary] = []
    for page in paginator.paginate():
        for raw in page.get("Functions", []):
            vpc_config = raw.get("VpcConfig") or {}
            functions.append(
                LambdaFunctionSummary(
                    name=raw["FunctionName"],
                    runtime=raw.get("Runtime"),
                    last_modified=raw.get("LastModified"),
                    memory_size_mb=raw.get("MemorySize"),
                    role_name=_role_name_from_arn(raw.get("Role")),
                    security_group_ids=list(vpc_config.get("SecurityGroupIds", []) or []),
                    subnet_ids=list(vpc_config.get("SubnetIds", []) or []),
                    vpc_id=vpc_config.get("VpcId") or None,
                )
            )
    return LambdaCard(functions=functions, count=len(functions))


def get_function(
    function_name: str, region: str | None = None
) -> LambdaFunctionSummary | None:
    """List-then-filter, same convention every other service module in
    this app uses for its get_*() (ec2_service.get_instance(),
    rds_service.get_instance(), etc.) -- keeps idle_service/cost_service
    able to treat every resource type's lookup the same way, and avoids a
    one-off ResourceNotFoundException handler per service."""
    result = list_functions(region=region)
    for function in result.functions:
        if function.name == function_name:
            return function
    return None
