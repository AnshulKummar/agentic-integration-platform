"""Shared simulator machinery: fault injection, rate limiting, admin API, pagination.

Every simulator app gets:

* ``/_sim/faults`` (GET/PUT/DELETE) and ``/_sim/state`` (GET), protected by the
  ``X-Sim-Admin-Token`` header and hidden from the OpenAPI document, so connectors
  never generate tools for them;
* a middleware applying faults to every vendor route (``/health`` included):
  ``outage`` -> 503, ``latency_ms`` -> delay, ``throttle_next`` -> the next N
  requests get 429 + ``Retry-After``, ``error_rate`` -> random ``error_status``;
* a fixed-window rate limiter (429 + ``Retry-After``) on API routes (not /health).
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import hmac
import math
import random
import threading
import time
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import asdict, dataclass, field
from typing import Any

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

ADMIN_HEADER = "X-Sim-Admin-Token"
_UNMETERED_PREFIXES = ("/_sim", "/openapi.json", "/docs", "/redoc")


@dataclass
class Faults:
    error_rate: float = 0.0
    error_status: int = 503
    latency_ms: int = 0
    outage: bool = False
    throttle_next: int = 0
    retry_after_s: int = 1


class FaultsIn(BaseModel):
    """Partial update of the fault configuration."""

    model_config = ConfigDict(extra="forbid")

    error_rate: float | None = Field(default=None, ge=0.0, le=1.0)
    error_status: int | None = Field(default=None, ge=500, le=599)
    latency_ms: int | None = Field(default=None, ge=0, le=60_000)
    outage: bool | None = None
    throttle_next: int | None = Field(default=None, ge=0, le=10_000)
    retry_after_s: int | None = Field(default=None, ge=0, le=3600)


@dataclass
class SimState:
    """Mutable simulator state shared by the middleware and the admin API."""

    name: str
    admin_token: str
    rate_limit: int | None
    rate_window_s: float
    rng: random.Random
    faults: Faults = field(default_factory=Faults)
    counters: dict[str, int] = field(default_factory=dict)
    extra_state: Callable[[], dict[str, Any]] | None = None
    lock: threading.Lock = field(default_factory=threading.Lock)
    _window_start: float = 0.0
    _window_count: int = 0

    def bump(self, counter: str, n: int = 1) -> None:
        with self.lock:
            self.counters[counter] = self.counters.get(counter, 0) + n

    def take_rate_slot(self) -> int | None:
        """None if allowed, else seconds until the window resets (>= 1)."""
        if self.rate_limit is None:
            return None
        now = time.monotonic()
        with self.lock:
            if now - self._window_start >= self.rate_window_s:
                self._window_start = now
                self._window_count = 0
            if self._window_count < self.rate_limit:
                self._window_count += 1
                return None
            return max(1, math.ceil(self.rate_window_s - (now - self._window_start)))

    def take_throttle(self) -> int | None:
        with self.lock:
            if self.faults.throttle_next > 0:
                self.faults.throttle_next -= 1
                return self.faults.retry_after_s
            return None


def _error(status: int, code: str, message: str, headers: dict[str, str] | None = None) -> JSONResponse:
    return JSONResponse({"detail": message, "code": code}, status_code=status, headers=headers)


def create_sim_app(
    *,
    name: str,
    title: str,
    version: str,
    description: str,
    admin_token: str,
    rate_limit: int | None,
    rate_window_s: float,
    seed: int | None,
) -> tuple[FastAPI, SimState]:
    if not admin_token:
        raise ValueError("admin_token is required")
    app = FastAPI(title=title, version=version, description=description, docs_url="/docs", redoc_url=None)
    state = SimState(
        name=name,
        admin_token=admin_token,
        rate_limit=rate_limit,
        rate_window_s=rate_window_s,
        rng=random.Random(seed),
    )
    app.state.sim = state

    @app.middleware("http")
    async def _faults(request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
        path = request.url.path
        if path.startswith(_UNMETERED_PREFIXES):
            return await call_next(request)
        state.bump("requests")
        faults = state.faults
        if faults.outage:
            state.bump("faults_injected")
            return _error(503, "outage", f"{title} is unavailable (simulated outage)")
        if faults.latency_ms:
            await asyncio.sleep(faults.latency_ms / 1000)
        throttle = state.take_throttle()
        if throttle is not None:
            state.bump("throttled")
            return _error(429, "rate_limited", "too many requests", {"Retry-After": str(throttle)})
        if path != "/health":
            wait = state.take_rate_slot()
            if wait is not None:
                state.bump("throttled")
                return _error(429, "rate_limited", "rate limit exceeded", {"Retry-After": str(wait)})
        if faults.error_rate and state.rng.random() < faults.error_rate:
            state.bump("faults_injected")
            return _error(faults.error_status, "injected_fault", "simulated upstream failure")
        return await call_next(request)

    def admin_routes(admin: APIRouter) -> None:
        @admin.get("/faults")
        async def get_faults() -> dict[str, Any]:
            return asdict(state.faults)

        @admin.put("/faults")
        async def set_faults(body: FaultsIn) -> dict[str, Any]:
            with state.lock:
                for key, value in body.model_dump(exclude_none=True).items():
                    setattr(state.faults, key, value)
            return asdict(state.faults)

        @admin.delete("/faults")
        async def clear_faults() -> dict[str, Any]:
            with state.lock:
                state.faults = Faults()
            return asdict(state.faults)

        @admin.get("/state")
        async def get_state() -> dict[str, Any]:
            out: dict[str, Any] = {"name": state.name, "counters": dict(state.counters), "faults": asdict(state.faults)}
            if state.extra_state is not None:
                out.update(state.extra_state())
            return out

    add_admin_routes(app, state, admin_routes)

    @app.get("/health", operation_id="health", summary="Service health", tags=["meta"])
    async def health() -> dict[str, str]:
        return {"status": "ok", "service": name}

    return app, state


def add_admin_routes(app: FastAPI, state: SimState, routes: Callable[[APIRouter], None]) -> None:
    """Register admin routes under ``/_sim`` (admin token required, hidden from OpenAPI)."""

    async def require_admin(request: Request) -> None:
        supplied = request.headers.get(ADMIN_HEADER, "")
        if not supplied or not hmac.compare_digest(supplied.encode(), state.admin_token.encode()):
            raise HTTPException(401, "invalid simulator admin token")

    router = APIRouter(prefix="/_sim", include_in_schema=False, dependencies=[Depends(require_admin)])
    routes(router)
    app.include_router(router)


def add_openapi_extensions(app: FastAPI, extensions: dict[str, Any]) -> None:
    """Add top-level ``x-*`` keys to the generated OpenAPI document."""
    original = app.openapi

    def openapi() -> dict[str, Any]:
        schema = original()
        schema.update(extensions)
        return schema

    app.openapi = openapi  # type: ignore[method-assign]


# ------------------------------------------------------------------ pagination
class Page[T](BaseModel):
    """Cursor page: pass ``next_cursor`` back as ``cursor`` until it is null."""

    items: list[T]
    next_cursor: str | None = None


def encode_cursor(offset: int) -> str:
    return base64.urlsafe_b64encode(f"o:{offset}".encode()).decode().rstrip("=")


def decode_cursor(cursor: str | None) -> int:
    if not cursor:
        return 0
    try:
        raw = base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)).decode()
        if not raw.startswith("o:"):
            raise ValueError(raw)
        offset = int(raw[2:])
        if offset < 0:
            raise ValueError(raw)
        return offset
    except (ValueError, binascii.Error, UnicodeDecodeError) as exc:
        raise HTTPException(400, "invalid cursor") from exc


def paginate[T](items: Sequence[T], cursor: str | None, limit: int) -> tuple[list[T], str | None]:
    offset = decode_cursor(cursor)
    page = list(items[offset : offset + limit])
    nxt = offset + limit
    return page, (encode_cursor(nxt) if nxt < len(items) else None)
