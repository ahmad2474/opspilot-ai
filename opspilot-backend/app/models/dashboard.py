from __future__ import annotations

from pydantic import BaseModel

from app.models.cloudtrail import CloudTrailEvent


class LambdaFunctionSummary(BaseModel):
    name: str
    runtime: str | None = None
    last_modified: str | None = None


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
