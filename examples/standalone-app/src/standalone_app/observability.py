from __future__ import annotations

import json
import logging
import time
from uuid import uuid4

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint

LOGGER = logging.getLogger("standalone_app.access")
SECURITY_LOGGER = logging.getLogger("standalone_app.security")


def request_id_from(request: Request) -> str:
    return str(request.state.request_id)


def log_security_event(
    request: Request,
    *,
    action: str,
    result: str,
    actor_id: str | None = None,
    target_type: str | None = None,
    target_id: str | None = None,
    reason: str | None = None,
) -> None:
    SECURITY_LOGGER.info(
        json.dumps(
            {
                "event": "security_decision",
                "request_id": request_id_from(request),
                "trace_id": getattr(request.state, "trace_id", None),
                "action": action,
                "result": result,
                "actor_id": actor_id,
                "target_type": target_type,
                "target_id": target_id,
                "reason": reason,
            },
            separators=(",", ":"),
        )
    )


class RequestContextMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        supplied_request_id = request.headers.get("X-Request-ID", "").strip()
        request_id = supplied_request_id if 0 < len(supplied_request_id) <= 128 else str(uuid4())
        supplied_trace_id = request.headers.get("X-Trace-ID", "").strip()
        trace_id = supplied_trace_id[:128] or None
        request.state.request_id = request_id
        request.state.trace_id = trace_id
        started_at = time.monotonic()
        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        if trace_id:
            response.headers["X-Trace-ID"] = trace_id
        LOGGER.info(
            json.dumps(
                {
                    "event": "http_request",
                    "request_id": request_id,
                    "trace_id": trace_id,
                    "method": request.method,
                    "path": request.url.path,
                    "status_code": response.status_code,
                    "duration_ms": round((time.monotonic() - started_at) * 1000, 3),
                },
                separators=(",", ":"),
            )
        )
        return response
