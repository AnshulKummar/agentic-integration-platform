"""Fixtures for simulator tests (in-process ASGI clients; the harness is used where sockets matter)."""

from __future__ import annotations

import secrets
from collections.abc import AsyncIterator, Callable
from contextlib import AsyncExitStack
from typing import Any

import httpx
import pytest
from fastapi import FastAPI

from app.platform.connectors.http import shared_ssl_context
from app.platform.simulators.common import ADMIN_HEADER


@pytest.fixture
def admin_token() -> str:
    return secrets.token_urlsafe(16)


@pytest.fixture
def admin_headers(admin_token: str) -> dict[str, str]:
    return {ADMIN_HEADER: admin_token}


@pytest.fixture
def random_secret() -> Callable[[], str]:
    return lambda: secrets.token_urlsafe(24)


@pytest.fixture
async def asgi_client() -> AsyncIterator[Callable[[FastAPI], httpx.AsyncClient]]:
    """asgi_client(app) -> AsyncClient bound to the app in-process."""
    async with AsyncExitStack() as stack:

        def _make(app: FastAPI, **kwargs: Any) -> httpx.AsyncClient:
            client = httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://sim.test",
                verify=shared_ssl_context(),
                **kwargs,
            )
            stack.push_async_callback(client.aclose)
            return client

        yield _make
