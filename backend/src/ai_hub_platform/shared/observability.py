from __future__ import annotations

import json
import logging
import time
from uuid import uuid4

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint

LOGGER = logging.getLogger("ai_hub_platform.access")


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
