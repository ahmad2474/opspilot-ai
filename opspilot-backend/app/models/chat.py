from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel


class ChatRequest(BaseModel):
    message: str


class TraceStep(BaseModel):
    type: Literal["tool_call", "tool_result", "message"]
    tool: str | None = None
    arguments: Any = None
    output: Any = None
    text: str | None = None


class ChatResponse(BaseModel):
    reply: str
    provider_used: str
    trace: list[TraceStep] = []
