from __future__ import annotations

from app.aws.client import get_sns_client
from app.models.dashboard import SnsCard, SnsTopicSummary


def list_topics() -> SnsCard:
    client = get_sns_client()
    paginator = client.get_paginator("list_topics")
    topics: list[SnsTopicSummary] = []
    for page in paginator.paginate():
        for raw in page.get("Topics", []):
            arn = raw["TopicArn"]
            topics.append(SnsTopicSummary(topic_arn=arn, name=arn.split(":")[-1]))
    return SnsCard(topics=topics, count=len(topics))
