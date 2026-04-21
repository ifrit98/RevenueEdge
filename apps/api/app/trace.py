"""Trace-ID middleware. Propagates an `X-Trace-ID` header end-to-end.

Every webhook → enqueued job → worker → internal API call carries the same
trace_id, making cross-service log correlation trivial.
"""

from __future__ import annotations

import contextvars
import uuid
from typing import Callable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

TRACE_HEADER = "x-trace-id"
_trace_id_var: contextvars.ContextVar[str] = contextvars.ContextVar("trace_id", default="")


def current_trace_id() -> str:
    return _trace_id_var.get() or ""


def new_trace_id() -> str:
    return str(uuid.uuid4())


class TraceMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        incoming = request.headers.get(TRACE_HEADER) or ""
        trace_id = incoming.strip() or new_trace_id()
        token = _trace_id_var.set(trace_id)
        try:
            response = await call_next(request)
        finally:
            _trace_id_var.reset(token)
        response.headers[TRACE_HEADER] = trace_id
        return response
