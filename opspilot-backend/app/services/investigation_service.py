"""Investigation memory — persists each chat investigation to DynamoDB and
supports semantic recall via brute-force cosine similarity over Gemini
embeddings.

No dedicated vector DB: at this scale (a handful of investigations from a
demo account) a full table scan plus in-process cosine similarity is far
simpler to operate than a real vector index, and costs nothing extra.

Requires the `opspilot-app` IAM user to have dynamodb:PutItem and
dynamodb:Scan on the investigations table. Without it, save/find calls
raise botocore.ClientError, which callers should treat as non-fatal
(log and continue the chat turn).
"""
from __future__ import annotations

import json
import logging
import math
import uuid
from datetime import datetime, timezone

import httpx

from app.aws.client import get_dynamodb_client
from app.core.config import get_settings
from app.models.investigation import Investigation, SimilarInvestigation

logger = logging.getLogger("app.services.investigation")

EMBEDDING_TIMEOUT_SECONDS = 10.0


def _embed(text: str) -> list[float]:
    settings = get_settings()
    if not settings.gemini_api_key:
        raise RuntimeError("GEMINI_API_KEY not configured — required for investigation embeddings")

    # Auth via header, not a `?key=` query param — the query-param form ends
    # up embedded in httpx's exception messages (and thus in logs) on any
    # non-2xx response, which would leak the API key.
    url = (
        "https://generativelanguage.googleapis.com/v1beta/models/"
        f"{settings.gemini_embedding_model}:embedContent"
    )
    response = httpx.post(
        url,
        headers={"x-goog-api-key": settings.gemini_api_key},
        json={"content": {"parts": [{"text": text}]}},
        timeout=EMBEDDING_TIMEOUT_SECONDS,
    )
    response.raise_for_status()
    return response.json()["embedding"]["values"]


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    if len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return dot / (norm_a * norm_b)


def save_investigation(question: str, trace_summary: str, conclusion: str) -> Investigation:
    """Embed and persist one chat investigation. Raises on failure (missing
    Gemini key, DynamoDB access denied, etc.) — callers decide whether that
    should interrupt the chat turn or just get logged."""
    settings = get_settings()
    client = get_dynamodb_client()

    embedding = _embed(f"{question}\n{trace_summary}\n{conclusion}")
    investigation = Investigation(
        id=str(uuid.uuid4()),
        question=question,
        trace_summary=trace_summary,
        conclusion=conclusion,
        created_at=datetime.now(timezone.utc),
    )

    client.put_item(
        TableName=settings.opspilot_investigations_table,
        Item={
            "id": {"S": investigation.id},
            "question": {"S": investigation.question},
            "trace_summary": {"S": investigation.trace_summary},
            "conclusion": {"S": investigation.conclusion},
            "created_at": {"S": investigation.created_at.isoformat()},
            "embedding": {"S": json.dumps(embedding)},
        },
    )
    logger.info("investigation_saved id=%s", investigation.id)
    return investigation


def find_similar_past_investigations(query: str, top_k: int = 3) -> list[SimilarInvestigation]:
    """Brute-force cosine similarity over every stored investigation."""
    settings = get_settings()
    client = get_dynamodb_client()

    query_embedding = _embed(query)

    scored: list[tuple[float, dict]] = []
    paginator = client.get_paginator("scan")
    for page in paginator.paginate(TableName=settings.opspilot_investigations_table):
        for raw in page.get("Items", []):
            embedding_json = raw.get("embedding", {}).get("S")
            if not embedding_json:
                continue
            similarity = _cosine_similarity(query_embedding, json.loads(embedding_json))
            scored.append((similarity, raw))

    scored.sort(key=lambda pair: pair[0], reverse=True)

    return [
        SimilarInvestigation(
            id=raw["id"]["S"],
            question=raw["question"]["S"],
            trace_summary=raw["trace_summary"]["S"],
            conclusion=raw["conclusion"]["S"],
            created_at=raw["created_at"]["S"],
            similarity=round(similarity, 4),
        )
        for similarity, raw in scored[:top_k]
    ]


def list_recent_investigations(limit: int = 20) -> list[Investigation]:
    """Every persisted investigation, newest first — powers the read-only
    Investigations page. No embedding field in the response; it's large
    and irrelevant to a human reader."""
    settings = get_settings()
    client = get_dynamodb_client()

    items: list[dict] = []
    paginator = client.get_paginator("scan")
    for page in paginator.paginate(TableName=settings.opspilot_investigations_table):
        items.extend(page.get("Items", []))

    items.sort(key=lambda raw: raw["created_at"]["S"], reverse=True)

    return [
        Investigation(
            id=raw["id"]["S"],
            question=raw["question"]["S"],
            trace_summary=raw["trace_summary"]["S"],
            conclusion=raw["conclusion"]["S"],
            created_at=raw["created_at"]["S"],
        )
        for raw in items[:limit]
    ]
