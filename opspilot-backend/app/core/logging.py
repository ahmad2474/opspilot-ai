"""Structured logging with request-ID propagation.

Every log line emitted anywhere in the app during a single HTTP request
carries the same request_id — including inside tool calls and the agent
orchestrator — because the filter reads from a contextvar rather than
something passed explicitly down the call stack. This is what lets you
grep one request's full story (UI call -> agent run -> tool calls -> AWS
calls) out of the logs by request_id alone.
"""
from __future__ import annotations

import contextvars
import logging
import time
import uuid
from typing import Awaitable, Callable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

request_id_var: contextvars.ContextVar[str] = contextvars.ContextVar("request_id", default="-")


class RequestIdFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = request_id_var.get()
        return True


def configure_logging(level: int = logging.INFO) -> None:
    handler = logging.StreamHandler()
    handler.addFilter(RequestIdFilter())
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s [req:%(request_id)s] %(name)s: %(message)s")
    )
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level)


class RequestIdMiddleware(BaseHTTPMiddleware):
    """Assigns a request_id (from X-Request-ID if the client sent one,
    otherwise a fresh one), makes it available to every logger for the
    duration of the request, and echoes it back in the response header.
    """

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        req_id = request.headers.get("x-request-id") or uuid.uuid4().hex[:12]
        token = request_id_var.set(req_id)
        logger = logging.getLogger("app.request")
        start = time.perf_counter()
        logger.info("start %s %s", request.method, request.url.path)
        try:
            response = await call_next(request)
            duration_ms = (time.perf_counter() - start) * 1000
            logger.info(
                "end %s %s %d %.1fms",
                request.method,
                request.url.path,
                response.status_code,
                duration_ms,
            )
            response.headers["X-Request-ID"] = req_id
            return response
        except Exception:
            duration_ms = (time.perf_counter() - start) * 1000
            logger.exception(
                "error %s %s after %.1fms", request.method, request.url.path, duration_ms
            )
            raise
        finally:
            request_id_var.reset(token)
