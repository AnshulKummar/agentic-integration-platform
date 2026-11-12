"""Error types and the uniform JSON error envelope: {"error": {"code", "message"}}."""

from __future__ import annotations

import logging

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.platform.db import TenantViolation

log = logging.getLogger(__name__)


class PlatformError(Exception):
    status = 400
    code = "bad_request"

    def __init__(self, message: str, *, code: str | None = None) -> None:
        super().__init__(message)
        self.message = message
        if code is not None:
            self.code = code


class Unauthorized(PlatformError):
    status = 401
    code = "unauthorized"


class Forbidden(PlatformError):
    status = 403
    code = "forbidden"


class NotFound(PlatformError):
    status = 404
    code = "not_found"


class Conflict(PlatformError):
    status = 409
    code = "conflict"


class ValidationFailed(PlatformError):
    status = 422
    code = "validation_failed"


class RateLimited(PlatformError):
    status = 429
    code = "rate_limited"

    def __init__(self, message: str, *, retry_after_s: int = 60, code: str | None = None) -> None:
        super().__init__(message, code=code)
        self.retry_after_s = retry_after_s


class UpstreamError(PlatformError):
    status = 502
    code = "upstream_error"


def _envelope(code: str, message: str) -> dict[str, dict[str, str]]:
    return {"error": {"code": code, "message": message}}


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(PlatformError)
    async def _platform(_: Request, exc: PlatformError) -> JSONResponse:
        headers = {"Retry-After": str(exc.retry_after_s)} if isinstance(exc, RateLimited) else None
        if exc.status == 401:
            headers = {**(headers or {}), "WWW-Authenticate": "Bearer"}
        return JSONResponse(_envelope(exc.code, exc.message), status_code=exc.status, headers=headers)

    @app.exception_handler(TenantViolation)
    async def _tenant(_: Request, exc: TenantViolation) -> JSONResponse:
        log.error("tenant violation blocked: %s", exc)
        return JSONResponse(_envelope("not_found", "not found"), status_code=404)
