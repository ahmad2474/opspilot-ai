from __future__ import annotations

from fastapi import APIRouter, HTTPException

from app.agent.orchestrator import run_chat_turn
from app.models.chat import ChatRequest, ChatResponse

router = APIRouter()


@router.post("/chat", response_model=ChatResponse)
async def chat(request: ChatRequest) -> ChatResponse:
    try:
        reply, provider = await run_chat_turn(request.message)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    return ChatResponse(reply=reply, provider_used=provider)
