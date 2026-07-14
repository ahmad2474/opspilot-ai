"""Kinesis Data Streams business logic. No boto3 calls anywhere else in
the app. Mirrors ebs_service.py's shape/style.

ListStreams' own StreamSummaries don't include shard count or retention
(needed for idle/cost calc) -- one DescribeStreamSummary call per stream
fills those in, same N+1 shape as sagemaker_service/opensearch_service.
"""
from __future__ import annotations

from app.aws.client import get_kinesis_client
from app.models.kinesis import KinesisStream, KinesisStreamList


def list_streams(region: str | None = None) -> KinesisStreamList:
    client = get_kinesis_client(region=region)
    paginator = client.get_paginator("list_streams")
    stream_names: list[str] = []
    for page in paginator.paginate():
        stream_names.extend(page.get("StreamNames", []))

    streams: list[KinesisStream] = []
    for name in stream_names:
        detail = client.describe_stream_summary(StreamName=name)["StreamDescriptionSummary"]
        streams.append(
            KinesisStream(
                stream_name=name,
                stream_arn=detail.get("StreamARN"),
                status=detail.get("StreamStatus", "unknown"),
                open_shard_count=detail.get("OpenShardCount", 0),
                retention_period_hours=detail.get("RetentionPeriodHours"),
                creation_timestamp=detail.get("StreamCreationTimestamp"),
            )
        )
    return KinesisStreamList(streams=streams, count=len(streams))


def get_stream(stream_name: str, region: str | None = None) -> KinesisStream | None:
    result = list_streams(region=region)
    for stream in result.streams:
        if stream.stream_name == stream_name:
            return stream
    return None
