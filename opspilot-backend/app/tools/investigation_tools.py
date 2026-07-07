"""Agent-facing tool for investigation memory. Stays thin on purpose — all
the real logic lives in app.services.investigation_service.
"""
from __future__ import annotations

import json
import logging
from typing import Annotated

from agents import function_tool

from app.services import investigation_service

logger = logging.getLogger("app.tools.investigation")


@function_tool
def find_similar_past_investigations(
    query: Annotated[str, "The current question or issue to search past investigations for."],
    top_k: Annotated[int, "Maximum number of past investigations to return."] = 3,
) -> str:
    """Search past chat investigations for ones semantically similar to the
    current question. Use this when a user's question sounds like something
    that may have come up before (a recurring issue, a repeated question)."""
    logger.info("tool_call find_similar_past_investigations query=%s", query)
    try:
        results = investigation_service.find_similar_past_investigations(query, top_k=top_k)
    except Exception as exc:  # noqa: BLE001 - degrade gracefully, never break the chat turn
        logger.warning("find_similar_past_investigations failed: %s", exc)
        return json.dumps({"results": [], "error": "investigation memory unavailable"})

    logger.info("tool_result find_similar_past_investigations count=%d", len(results))
    return json.dumps({"results": [r.model_dump() for r in results]})
