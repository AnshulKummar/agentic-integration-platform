"""Fixtures: vendor simulators on real sockets + connectors wired to the secret store."""

from __future__ import annotations

import secrets
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator
from contextlib import ExitStack
from dataclasses import dataclass, field
from typing import Any

import httpx
import pytest
from fastapi import FastAPI

from app.platform.config import PlatformSettings
from app.platform.connectors.http import HttpConnector, shared_ssl_context
from app.platform.core_models import Tenant
from app.platform.db import Database
from app.platform.interfaces import AuthConfig, ConnectorConfig
from app.platform.secrets.store import DbSecretStore
from app.platform.simulators.billing import create_billing_app
from app.platform.simulators.common import ADMIN_HEADER, SimState
from app.platform.simulators.crm import TOKEN_PATH, create_crm_app
from app.platform.simulators.esign import create_esign_app
from app.platform.simulators.harness import run_simulator


@dataclass
class RunningSim:
    name: str
    base_url: str
    app: FastAPI
    admin_token: str
    creds: dict[str, str] = field(default_factory=dict)
    http: httpx.Client = field(default_factory=lambda: httpx.Client(verify=shared_ssl_context()))

    @property
    def state(self) -> SimState:
        state: SimState = self.app.state.sim
        return state

    def admin(self, method: str, path: str, json: dict[str, Any] | None = None) -> httpx.Response:
        r = self.http.request(method, self.base_url + path, json=json, headers={ADMIN_HEADER: self.admin_token})
        r.raise_for_status()
        return r

    def faults(self, **faults: Any) -> None:
        self.admin("PUT", "/_sim/faults", faults)

    def spec(self) -> dict[str, Any]:
        r = self.http.get(self.base_url + "/openapi.json")
        r.raise_for_status()
        spec: dict[str, Any] = r.json()
        return spec


SimFactory = Callable[..., RunningSim]


def _track(stack: ExitStack, sim: RunningSim) -> RunningSim:
    stack.callback(sim.http.close)
    return sim


@pytest.fixture
def _sim_stack() -> Iterator[ExitStack]:
    with ExitStack() as stack:
        yield stack


@pytest.fixture
def make_crm(_sim_stack: ExitStack) -> SimFactory:
    def _make(**kwargs: Any) -> RunningSim:
        creds = {"client_id": "crm-" + secrets.token_hex(4), "client_secret": secrets.token_urlsafe(24)}
        admin = secrets.token_urlsafe(16)
        kwargs.setdefault("rate_limit", None)
        app = create_crm_app(admin_token=admin, **creds, **kwargs)
        return _track(_sim_stack, RunningSim("crm", _sim_stack.enter_context(run_simulator(app)), app, admin, creds))

    return _make


@pytest.fixture
def make_billing(_sim_stack: ExitStack) -> SimFactory:
    def _make(**kwargs: Any) -> RunningSim:
        creds = {"token": secrets.token_urlsafe(24)}
        admin = secrets.token_urlsafe(16)
        kwargs.setdefault("rate_limit", None)
        app = create_billing_app(api_token=creds["token"], admin_token=admin, **kwargs)
        return _track(
            _sim_stack, RunningSim("billing", _sim_stack.enter_context(run_simulator(app)), app, admin, creds)
        )

    return _make


@pytest.fixture
def make_esign(_sim_stack: ExitStack) -> SimFactory:
    def _make(**kwargs: Any) -> RunningSim:
        creds = {"api_key": secrets.token_urlsafe(24), "webhook_secret": secrets.token_urlsafe(24)}
        admin = secrets.token_urlsafe(16)
        kwargs.setdefault("rate_limit", None)
        app = create_esign_app(admin_token=admin, **creds, **kwargs)
        return _track(_sim_stack, RunningSim("esign", _sim_stack.enter_context(run_simulator(app)), app, admin, creds))

    return _make


@pytest.fixture
def secret_store(db: Database, settings: PlatformSettings) -> DbSecretStore:
    return DbSecretStore(db, settings)


@pytest.fixture
def sleeps() -> list[float]:
    return []


@pytest.fixture
def fake_sleep(sleeps: list[float]) -> Callable[[float], Awaitable[None]]:
    async def _sleep(seconds: float) -> None:
        sleeps.append(seconds)

    return _sleep


async def store_sim_secrets(store: DbSecretStore, tenant_id: str, sim: RunningSim) -> AuthConfig:
    """Store the simulator's credentials as tenant secrets; return the matching AuthConfig (names only)."""
    if sim.name == "crm":
        await store.put(tenant_id, "crm_client_id", sim.creds["client_id"])
        await store.put(tenant_id, "crm_client_secret", sim.creds["client_secret"])
        return AuthConfig(
            kind="oauth2_client_credentials",
            secrets={"client_id": "crm_client_id", "client_secret": "crm_client_secret"},
            token_url=TOKEN_PATH,
            scopes=("crm.read",),
        )
    if sim.name == "billing":
        await store.put(tenant_id, "billing_token", sim.creds["token"])
        return AuthConfig(kind="bearer", secrets={"token": "billing_token"})
    await store.put(tenant_id, "esign_api_key", sim.creds["api_key"])
    return AuthConfig(kind="api_key", secrets={"api_key": "esign_api_key"}, header_name="X-API-Key")


@pytest.fixture
async def make_connector(
    secret_store: DbSecretStore,
    settings: PlatformSettings,
    tenant: Tenant,
    fake_sleep: Callable[[float], Awaitable[None]],
) -> AsyncIterator[Callable[..., Awaitable[HttpConnector]]]:
    """await make_connector(sim, system_id=..., max_retries=..., real_sleep=False, clock=..., transport=...)"""
    made: list[HttpConnector] = []

    async def _make(
        sim: RunningSim,
        *,
        system_id: str | None = None,
        max_retries: int = 3,
        real_sleep: bool = False,
        **kwargs: Any,
    ) -> HttpConnector:
        auth = await store_sim_secrets(secret_store, tenant.id, sim)
        config = ConnectorConfig(
            system_id=system_id or f"{sim.name}-{secrets.token_hex(3)}",
            base_url=sim.base_url,
            spec=sim.spec(),
            auth=auth,
            tool_prefix=sim.name,
        )
        local = settings.model_copy(update={"connector_max_retries": max_retries, "connector_timeout_s": 5.0})
        if not real_sleep:
            kwargs.setdefault("sleep", fake_sleep)
        connector = HttpConnector(config, secret_store, tenant.id, local, **kwargs)
        made.append(connector)
        return connector

    yield _make
    for connector in made:
        await connector.aclose()
