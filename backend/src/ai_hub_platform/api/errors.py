from __future__ import annotations

from typing import Any, cast

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse


class ApiError(Exception):
    def __init__(
        self,
        status_code: int,
        error_code: str,
        message: str,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.error_code = error_code
        self.message = message
        self.details = details or {}


def error_payload(
    request: Request, error_code: str, message: str, details: dict[str, Any]
) -> dict[str, Any]:
    return {
        "error_code": error_code,
        "message": message,
        "details": details,
        "request_id": request.state.request_id,
    }


def register_error_handlers(application: FastAPI) -> None:
    @application.exception_handler(ApiError)
    async def api_error_handler(request: Request, error: ApiError) -> JSONResponse:
        return JSONResponse(
            status_code=error.status_code,
            content=error_payload(request, error.error_code, error.message, error.details),
        )

    @application.exception_handler(RequestValidationError)
    async def validation_error_handler(
        request: Request, error: RequestValidationError
    ) -> JSONResponse:
        # Pydantic may embed non-JSON-serializable objects (e.g. ValueError) in
        # error contexts; coerce them so clients always receive a 422 payload.
        serializable_errors: list[dict[str, Any]] = []
        for item in error.errors():
            cleaned = dict(item)
            context = cleaned.get("ctx")
            if isinstance(context, dict):
                typed_context = cast(dict[str, Any], context)
                safe_ctx: dict[str, Any] = {}
                for key, value in typed_context.items():
                    if isinstance(value, (str, int, float, bool, type(None))):
                        safe_ctx[key] = value
                    else:
                        safe_ctx[key] = str(value)
                cleaned["ctx"] = safe_ctx
            serializable_errors.append(cleaned)
        return JSONResponse(
            status_code=422,
            content=error_payload(
                request,
                "request_validation_failed",
                "Request validation failed",
                {"errors": serializable_errors},
            ),
        )

    _ = api_error_handler, validation_error_handler
