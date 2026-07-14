from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from app.models.cloudtrail import CloudTrailEvent


class LambdaFunctionSummary(BaseModel):
    name: str
    runtime: str | None = None
    last_modified: str | None = None
    memory_size_mb: int | None = Field(
        default=None,
        description=(
            "GetFunctionConfiguration's MemorySize -- used by cost_service's "
            "Lambda GB-second estimate. None only if lambda_service.get_function "
            "wasn't used to populate this (list_functions still fills it in, "
            "since ListFunctions' response includes MemorySize too)."
        ),
    )

    # Lambda's API exposes no creation timestamp anywhere (GetFunction/
    # GetFunctionConfiguration only return LastModified, which changes on
    # every code/config deploy -- using it as a creation-time proxy would
    # be actively misleading, e.g. a 2-year-old function redeployed
    # yesterday would falsely report as "younger than window"). No
    # created_at field exists here at all, on purpose -- same documented
    # gap as ElasticIp (app/models/eip.py) and CloudFrontDistribution.

    role_name: str | None = Field(
        default=None,
        description=(
            "Just the trailing path segment of ListFunctions' Role ARN (e.g. "
            "'my-lambda-role', not 'arn:aws:iam::123456789012:role/"
            "my-lambda-role') -- roadmap 3.7 'assumes' relation, no new call. "
            "Deliberately not the full ARN: this app otherwise keeps the AWS "
            "account ID out of every caller-facing field (it's scrubbed from "
            "error messages for the same reason), and the relation only ever "
            "needs an identifier to display, never the full ARN."
        ),
    )
    security_group_ids: list[str] = Field(
        default_factory=list,
        description="VpcConfig.SecurityGroupIds -- empty for non-VPC functions. Roadmap 3.7.",
    )
    subnet_ids: list[str] = Field(
        default_factory=list,
        description="VpcConfig.SubnetIds -- empty for non-VPC functions. Roadmap 3.7.",
    )
    vpc_id: str | None = Field(
        default=None,
        description="VpcConfig.VpcId -- None for non-VPC functions. Roadmap 3.7.",
    )


class LambdaCard(BaseModel):
    functions: list[LambdaFunctionSummary]
    count: int


class S3BucketSummary(BaseModel):
    name: str
    creation_date: str | None = None


class S3Card(BaseModel):
    buckets: list[S3BucketSummary]
    count: int


class DynamoTableSummary(BaseModel):
    name: str
    status: str
    item_count: int | None = None
    creation_date_time: datetime | None = Field(
        default=None,
        description=(
            "DescribeTable's CreationDateTime -- plays the same role "
            "EC2Instance.launch_time plays for idle_service's "
            "younger-than-window check and cost_service's elapsed-hours calc."
        ),
    )
    billing_mode: str = Field(
        default="PROVISIONED",
        description=(
            "'PROVISIONED' or 'PAY_PER_REQUEST' (on-demand), from "
            "BillingModeSummary.BillingMode -- absent in DescribeTable's "
            "response entirely for tables that have always been PROVISIONED "
            "and never switched modes, hence the 'PROVISIONED' default "
            "rather than an Optional[None] here."
        ),
    )
    read_capacity_units: int = Field(
        default=0,
        description="ProvisionedThroughput.ReadCapacityUnits -- 0 for PAY_PER_REQUEST tables.",
    )
    write_capacity_units: int = Field(
        default=0,
        description="ProvisionedThroughput.WriteCapacityUnits -- 0 for PAY_PER_REQUEST tables.",
    )


class DynamoCard(BaseModel):
    tables: list[DynamoTableSummary]
    count: int


class SnsTopicSummary(BaseModel):
    topic_arn: str
    name: str


class SnsCard(BaseModel):
    topics: list[SnsTopicSummary]
    count: int


class RdsInstanceSummary(BaseModel):
    identifier: str
    engine: str
    instance_class: str
    status: str
    instance_create_time: datetime | None = Field(
        default=None,
        description=(
            "From DescribeDBInstances' InstanceCreateTime -- plays the same "
            "role EC2Instance.launch_time plays for idle_service's "
            "younger-than-window check and cost_service's elapsed-hours calc."
        ),
    )
    vpc_security_group_ids: list[str] = Field(
        default_factory=list,
        description="From VpcSecurityGroups -- roadmap 3.7 relation-shaping, no new call.",
    )
    subnet_ids: list[str] = Field(
        default_factory=list,
        description="From DBSubnetGroup.Subnets -- roadmap 3.7 relation-shaping, no new call.",
    )
    vpc_id: str | None = Field(
        default=None,
        description="From DBSubnetGroup.VpcId -- roadmap 3.7 relation-shaping, no new call.",
    )


class RdsCard(BaseModel):
    instances: list[RdsInstanceSummary]
    count: int


class CloudTrailCard(BaseModel):
    events: list[CloudTrailEvent]


class DashboardOverview(BaseModel):
    """One combined payload for the Resources page's breadth section —
    single round trip instead of 6, since none of these need to be
    fetched independently of each other."""

    lambda_functions: LambdaCard
    s3: S3Card
    dynamodb: DynamoCard
    sns: SnsCard
    rds: RdsCard
    cloudtrail: CloudTrailCard
