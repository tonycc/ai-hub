from __future__ import annotations

import json
import logging
import time
from typing import cast
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


class PortalAuditMiddleware(BaseHTTPMiddleware):
    """Persist successful portal API calls without reading request bodies or secrets."""

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        response = await call_next(request)
        if not request.url.path.startswith("/portal-api/") or response.status_code >= 400:
            return response
        principal = getattr(request.state, "portal_principal", None)
        database = getattr(request.app.state, "database", None)
        if principal is None or database is None:
            return response
        from ai_hub_platform.modules.audit.service import AuditRecord, AuditService
        from ai_hub_platform.modules.portal.service import PortalPrincipal
        from ai_hub_platform.shared.database import Database

        typed_principal = cast(PortalPrincipal, principal)
        application_id = request.path_params.get("application_id")
        try:
            await AuditService().append_committed(
                cast(Database, database),
                AuditRecord(
                    request_id=str(request.state.request_id),
                    trace_id=getattr(request.state, "trace_id", None),
                    action="platform.portal.api.call",
                    result="SUCCESS",
                    actor_type="user",
                    actor_id=typed_principal.subject,
                    application_id=(application_id if isinstance(application_id, str) else None),
                    target_type="management_api_path",
                    target_id=request.url.path,
                    authorization_version=typed_principal.authorization_version,
                    metadata={"method": request.method},
                ),
            )
        except Exception:
            LOGGER.exception(
                "portal API audit failed",
                extra={"request_id": str(request.state.request_id)},
            )
        return response
