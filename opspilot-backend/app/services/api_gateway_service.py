"""API Gateway business logic. No boto3 calls anywhere else in the app.
Mirrors ebs_service.py's shape/style.

REST APIs only (apigateway v1 client, GetRestApis) -- HTTP APIs
(apigatewayv2) are a documented gap in this build step. Reasoning: the
CloudWatch dimension REST APIs publish under is `ApiName` (this app's
resource_id for the type), which is stable and human-chosen; HTTP APIs
publish under `ApiId` instead (there is no ApiName CloudWatch dimension
for v2), which would mean check_idle/estimate_cost's `resource_id` means
something different depending on API type in the same "api_gateway"
`resource_type` bucket -- a REST-only scope keeps that contract
unambiguous for this build step. Revisit with a separate `resource_type`
(e.g. "api_gateway_http") if/when HTTP APIs need coverage.
"""
from __future__ import annotations

from app.aws.client import get_apigateway_client
from app.models.api_gateway import ApiGatewayRestApi, ApiGatewayRestApiList


def list_apis(region: str | None = None) -> ApiGatewayRestApiList:
    client = get_apigateway_client(region=region)
    paginator = client.get_paginator("get_rest_apis")
    apis: list[ApiGatewayRestApi] = []
    for page in paginator.paginate():
        for raw in page.get("items", []):
            apis.append(
                ApiGatewayRestApi(
                    api_id=raw["id"],
                    name=raw.get("name", raw["id"]),
                    created_date=raw.get("createdDate"),
                )
            )
    return ApiGatewayRestApiList(apis=apis, count=len(apis))


def get_api(
    api_id_or_name: str, region: str | None = None
) -> ApiGatewayRestApi | None:
    """Matches on either api_id or name -- callers (idle/cost tools) pass
    whichever they have; the CloudWatch dimension value this app uses is
    `name` (see module docstring), but resources are just as often
    referenced by their AWS-assigned id."""
    result = list_apis(region=region)
    for api in result.apis:
        if api.api_id == api_id_or_name or api.name == api_id_or_name:
            return api
    return None
