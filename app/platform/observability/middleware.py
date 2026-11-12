"""Pure-ASGI request middleware: request id, HTTP metrics, access + slow-request logs.

Metrics are labelled with the matched ROUTE TEMPLATE (``/v1/deployments/{deployment_id}``),
never the raw path, so cardinality stays bounded; unmatched paths use ``<unmatched>``.
"""

from __future__ import annotations

import logging
import re
import time
import uuid

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.platform.observability.context import RequestContext, pop_context, push_context
from app.platform.observability.metrics import HTTP_LATENCY, HTTP_REQUESTS

log = logging.getLogger("app.platform.http")

REQUEST_ID_HEADER = "x-request-id"
_VALID_REQUEST_ID = re.compile(r"^[A-Za-z0-9._:\-]{1,128}$")
UNMATCHED = "<unmatched>"
#: Requests slower than this are logged at WARNING. Assumption: no setting exists for it.
SLOW_REQUEST_MS = 1000.0


def route_template(scope: Scope) -> str:
    route = scope.get("route")
    path = getattr(route, "path_format", None) or getattr(route, "path", None)
    return path if isinstance(path, str) else UNMATCHED


def _incoming_request_id(scope: Scope) -> str | None:
    for name, value in scope.get("headers", []):
        if name == REQUEST_ID_HEADER.encode():
            candidate = value.decode("latin-1").strip()
            # Untrusted input lands in logs and headers: accept only a safe charset.
            return candidate if _VALID_REQUEST_ID.match(candidate) else None
    return None


class ObservabilityMiddleware:
    def __init__(self, app: ASGIApp, *, metrics_enabled: bool = True, slow_ms: float = SLOW_REQUEST_MS) -> None:
        self.app = app
        self.metrics_enabled = metrics_enabled
        self.slow_ms = slow_ms

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        request_id = _incoming_request_id(scope) or uuid.uuid4().hex
        ctx = RequestContext(request_id=request_id)
        token = push_context(ctx)
        status = 500
        started = time.perf_counter()

        async def send_wrapper(message: Message) -> None:
            nonlocal status
            if message["type"] == "http.response.start":
                status = int(message["status"])
                headers = [(k, v) for k, v in message.get("headers", []) if k.lower() != REQUEST_ID_HEADER.encode()]
                headers.append((REQUEST_ID_HEADER.encode(), request_id.encode()))
                message = {**message, "headers": headers}
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        except Exception:
            status = 500
            log.exception("unhandled error", extra={"method": scope.get("method"), "path": scope.get("path")})
            raise
        finally:
            elapsed = time.perf_counter() - started
            method = str(scope.get("method", ""))
            route = route_template(scope)
            if self.metrics_enabled:
                HTTP_REQUESTS.labels(method=method, route=route, status=str(status)).inc()
                HTTP_LATENCY.labels(method=method, route=route).observe(elapsed)
            fields = {
                "method": method,
                "route": route,
                "status": status,
                "duration_ms": round(elapsed * 1000, 2),
            }
            if elapsed * 1000 >= self.slow_ms:
                log.warning("slow request", extra={**fields, "slow_threshold_ms": self.slow_ms})
            else:
                log.info("request", extra=fields)
            pop_context(token)
