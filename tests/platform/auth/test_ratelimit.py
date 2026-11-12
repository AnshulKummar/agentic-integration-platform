"""Login rate limiting: sliding window per (email, client IP)."""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable

import httpx
import pytest
from fastapi import FastAPI

from app.platform.app import create_app
from app.platform.auth.ratelimit import SlidingWindowLimiter
from app.platform.config import PlatformSettings
from app.platform.db import Database
from app.platform.errors import RateLimited
from app.platform.observability.metrics import REGISTRY


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def test_sliding_window_blocks_then_recovers() -> None:
    clock = FakeClock()
    limiter = SlidingWindowLimiter(3, 60.0, clock=clock)
    for _ in range(3):
        limiter.hit("a|1.2.3.4")
        clock.now += 10
    with pytest.raises(RateLimited) as err:
        limiter.hit("a|1.2.3.4")
    assert err.value.retry_after_s == 30  # oldest hit at t=1000 leaves the window at t=1060; now=1030
    limiter.hit("b|1.2.3.4")  # other keys are independent
    clock.now = 1060.5  # first hit slid out of the window
    limiter.hit("a|1.2.3.4")
    with pytest.raises(RateLimited):
        limiter.hit("a|1.2.3.4")


def test_prunes_idle_keys_when_full() -> None:
    clock = FakeClock()
    limiter = SlidingWindowLimiter(1, 60.0, clock=clock, max_keys=2)
    limiter.hit("k1")
    limiter.hit("k2")
    clock.now += 61
    limiter.hit("k3")
    assert set(limiter._hits) == {"k3"}


def test_rejects_nonpositive_limit() -> None:
    with pytest.raises(ValueError):
        SlidingWindowLimiter(0)


@pytest.fixture
async def strict_client(settings: PlatformSettings, db: Database) -> AsyncIterator[httpx.AsyncClient]:
    app: FastAPI = create_app(settings.model_copy(update={"login_rate_limit_per_minute": 3}))
    app.state.db = db
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://platform.test") as c:
            yield c


async def test_login_endpoint_returns_429_with_retry_after(
    strict_client: httpx.AsyncClient, password: str
) -> None:
    r = await strict_client.post(
        "/v1/auth/signup",
        json={"org_name": "Acme", "org_slug": "acme", "email": "a@acme.io", "password": password},
    )
    assert r.status_code == 201
    before = REGISTRY.get_sample_value("pmaip_auth_events_total", {"event": "login", "outcome": "rate_limited"}) or 0
    # Signup shares the (email, IP) bucket (it verifies passwords of existing accounts),
    # so signup + 2 failed logins exhaust the limit of 3.
    for _ in range(2):
        r = await strict_client.post("/v1/auth/login", json={"email": "a@acme.io", "password": "wrong password!!"})
        assert r.status_code == 401
    r = await strict_client.post("/v1/auth/login", json={"email": "A@acme.io", "password": password})
    assert r.status_code == 429
    assert r.json()["error"]["code"] == "rate_limited"
    assert 1 <= int(r.headers["Retry-After"]) <= 60
    after = REGISTRY.get_sample_value("pmaip_auth_events_total", {"event": "login", "outcome": "rate_limited"})
    assert after == before + 1
    # A different email from the same IP is a different bucket.
    r = await strict_client.post("/v1/auth/login", json={"email": "b@acme.io", "password": password})
    assert r.status_code == 401


async def test_default_limiter_uses_settings(
    client: httpx.AsyncClient, app: FastAPI, signup: Callable[..., Awaitable[dict[str, object]]]
) -> None:
    await signup("acme", "a@acme.io")  # signup also passes through the limiter
    assert app.state.auth_login_limiter.limit == 1000
