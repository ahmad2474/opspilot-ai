"""A deterministic, zero-network stand-in for
investigation_service._embed (roadmap phase 2 Section 2.3's recall-
accuracy case).

Investigation memory's real embedding call (`_embed` in
app/services/investigation_service.py) hits Gemini's real embedContent
endpoint over HTTPS -- exactly the kind of real, uncontrolled network
call the deterministic eval tier must never require (this build step's
own non-negotiable: "must never require [GEMINI_API_KEY] to be set").
This module exists so eval/test_deterministic_cases.py's
test_recall_accuracy can exercise the *real* ranking logic in
investigation_service.find_similar_past_investigations (real cosine
similarity, real DynamoDB scan-and-rank, both against a real moto-backed
table) without needing that one real network call.

This is a hashing-trick bag-of-words vector, not a real semantic
embedding -- it has no notion of synonyms or meaning, only shared
tokens. That's a real, deliberate scope limit, not a hidden one: it's
good enough to prove "a question sharing the same resource ID/keywords as
a past investigation ranks above an unrelated one" (exactly what
recall_accuracy.yaml's rubric asks for), but it is NOT a substitute for
verifying the real Gemini embedding call actually works end-to-end --
that was verified separately and live, with real credentials, outside
this deterministic suite (see this build step's own final report for
what that verification found).
"""
from __future__ import annotations

import hashlib
import re

_DIMENSIONS = 64
_TOKEN_RE = re.compile(r"[a-z0-9]+")


def deterministic_embedding(text: str) -> list[float]:
    """A fixed-dimension hashing-trick bag-of-words vector: each lowercased
    alphanumeric token increments the count in one of `_DIMENSIONS` fixed
    buckets (its token hashed into that bucket, so the same token always
    lands in the same bucket across calls). Two texts sharing many tokens
    (e.g. the same EC2 instance ID) end up with a high cosine similarity;
    two texts sharing none end up near-orthogonal -- exactly the property
    investigation_service._cosine_similarity's ranking needs to be
    exercised meaningfully.
    """
    vector = [0.0] * _DIMENSIONS
    for token in _TOKEN_RE.findall(text.lower()):
        bucket = int(hashlib.sha256(token.encode("utf-8")).hexdigest(), 16) % _DIMENSIONS
        vector[bucket] += 1.0
    return vector


__all__ = ["deterministic_embedding"]
