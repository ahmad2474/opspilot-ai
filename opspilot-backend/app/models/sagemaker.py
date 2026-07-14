"""SageMaker endpoint models. Mirrors app/models/ebs.py's shape/style.

`instance_type`/`instance_count` come from the endpoint's *config*
(DescribeEndpointConfig), not DescribeEndpoint itself -- SageMaker splits
"what's deployed" (endpoint) from "what instance type/count it runs on"
(endpoint config) across two API calls. `variant_name` is needed because
the AWS/SageMaker `Invocations` CloudWatch metric requires both
EndpointName AND VariantName dimensions together (unlike most other
services in this batch, which need only one dimension) -- see
sagemaker_service.py and idle_service.py's SageMaker branch.

Simplifying assumption: only the endpoint's first production variant is
used for cost/idle purposes. Multi-variant (e.g. blue/green or A/B
canary) endpoints are a documented gap -- the dominant single-variant case
is what this build step covers.
"""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class SageMakerEndpoint(BaseModel):
    endpoint_name: str
    status: str = Field(description="e.g. InService, Creating, Updating, Failed")
    creation_time: datetime | None = None
    variant_name: str | None = Field(
        default=None, description="First production variant's name, if any."
    )
    instance_type: str | None = Field(
        default=None, description="First production variant's instance type, e.g. ml.m5.xlarge."
    )
    instance_count: int = 0


class SageMakerEndpointList(BaseModel):
    endpoints: list[SageMakerEndpoint]
    count: int
