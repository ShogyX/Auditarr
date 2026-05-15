"""Global FastAPI exception handlers."""

from __future__ import annotations

import uuid

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.exceptions import AuditarrError
from app.core.logging import get_logger
from app.schemas import ErrorResponse

log = get_logger("auditarr.api.errors", category="api")


def _request_id(request: Request) -> str:
    rid = request.headers.get("x-request-id")
    return rid or uuid.uuid4().hex


def install_error_handlers(app: FastAPI) -> None:
    """Attach uniform JSON error handlers to *app*."""

    @app.exception_handler(AuditarrError)
    async def _domain_handler(
        request: Request, exc: AuditarrError
    ) -> JSONResponse:
        rid = _request_id(request)
        log.warning(
            "api.domain_error",
            code=exc.code,
            status=exc.status_code,
            message=exc.message,
            request_id=rid,
            path=request.url.path,
        )
        return JSONResponse(
            status_code=exc.status_code,
            content=ErrorResponse(
                code=exc.code,
                message=exc.message,
                details=exc.details or None,
                request_id=rid,
            ).model_dump(exclude_none=True),
        )

    @app.exception_handler(RequestValidationError)
    async def _validation_handler(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        rid = _request_id(request)
        log.info(
            "api.validation_error",
            errors=len(exc.errors()),
            request_id=rid,
            path=request.url.path,
        )
        return JSONResponse(
            status_code=422,
            content=ErrorResponse(
                code="validation_error",
                message="Request validation failed",
                details={"errors": exc.errors()},
                request_id=rid,
            ).model_dump(exclude_none=True),
        )

    @app.exception_handler(StarletteHTTPException)
    async def _http_handler(
        request: Request, exc: StarletteHTTPException
    ) -> JSONResponse:
        rid = _request_id(request)
        return JSONResponse(
            status_code=exc.status_code,
            content=ErrorResponse(
                code=f"http_{exc.status_code}",
                message=str(exc.detail),
                request_id=rid,
            ).model_dump(exclude_none=True),
        )

    @app.exception_handler(Exception)
    async def _fallback_handler(request: Request, exc: Exception) -> JSONResponse:
        rid = _request_id(request)
        log.error(
            "api.unhandled_exception",
            error=str(exc),
            request_id=rid,
            path=request.url.path,
            exc_info=True,
        )
        return JSONResponse(
            status_code=500,
            content=ErrorResponse(
                code="internal_error",
                message="An unexpected error occurred",
                request_id=rid,
            ).model_dump(exclude_none=True),
        )
