"""API Gateway models. Mirrors app/models/ebs.py's shape/style.

REST APIs only (apigateway v1 client) -- see api_gateway_service.py's
module docstring for why HTTP APIs (apigatewayv2) are a documented gap in
this build step, not silently mishandled.
"""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class ApiGatewayRestApi(BaseModel):
    api_id: str
    name: str = Field(description="Also the CloudWatch 'ApiName' dimension value for this API.")
    created_date: datetime | None = None


class ApiGatewayRestApiList(BaseModel):
    apis: list[ApiGatewayRestApi]
    count: int
