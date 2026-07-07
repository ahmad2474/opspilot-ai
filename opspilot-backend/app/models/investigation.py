from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class Investigation(BaseModel):
    id: str
    question: str
    trace_summary: str
    conclusion: str
    created_at: datetime


class SimilarInvestigation(BaseModel):
    id: str
    question: str
    trace_summary: str
    conclusion: str
    created_at: str
    similarity: float


class SimilarInvestigationList(BaseModel):
    query: str
    results: list[SimilarInvestigation]
