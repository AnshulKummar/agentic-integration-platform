"""End to end, with nothing faked except the vendors themselves.

Real login and roles -> encrypted secrets -> connections whose tools are generated
from each simulator's OpenAPI spec -> loop-1 integration drafted from the KG ->
engineer edits -> dual approval -> staged deployment driven by the worker, where
the approved code runs in the subprocess sandbox and calls the vendor simulators
over real sockets -> production -> KG learns from the live version.

Second scenario: a real vendor outage during canary makes the health checks fail
and the platform rolls back on its own.
"""

from __future__ import annotations

import secrets
from collections.abc import AsyncIterator, Iterator
from contextlib import ExitStack
from dataclasses import dataclass
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import func, select

from app.demo.mocks import MockWorld, create_tax_app
from app.platform.config import PlatformSettings
from app.platform.core_models import ExecutionRecord, KGNodeRecord
from app.platform.db import Database
from app.platform.deployments.clock import ManualClock
from app.platform.deployments.models import DeploymentStatus
from app.platform.runtime import build_deployment_deps
from app.platform.simulators.billing import create_billing_app
from app.platform.simulators.crm import TOKEN_PATH, create_crm_app
from app.platform.simulators.esign import create_esign_app
from app.platform.simulators.harness import run_simulator
from app.platform.worker import Worker

pytestmark = pytest.mark.slow

PASSWORD = "correct-horse-battery-staple"
LOOP1_INTENT = (
    "Connect our CRM to the billing system so every closed-won account becomes a billing customer, "
    "including tax calculations."
)
ENGINEER_EDITS = [
    {"target": "Customer.currency_code", "transform": "to_iso4217()"},
    {"target": "Customer.terms_code", "transform": "TERMS_MAP lookup"},
    {"target": "TaxQuote.jurisdiction", "source": "Account.shipping_address", "transform": "geocode(ship-to)"},
]


@dataclass
class Vendors:
    urls: dict[str, str]
    specs: dict[str, dict[str, Any]]
    creds: dict[str, str]
    billing_admin: str


@pytest.fixture
def settings(database_url: str) -> PlatformSettings:
    from app.platform.config import generate_master_key

    return PlatformSettings(
        env="test",
        database_url=database_url,
        jwt_secret="e2e-jwt-secret-" + "x" * 32,  # type: ignore[arg-type]
        master_key=generate_master_key(),  # type: ignore[arg-type]
        sandbox_backend="subprocess",
        sandbox_timeout_s=20.0,
        log_json=False,
        login_rate_limit_per_minute=1000,
        canary_percent=50,
        canary_min_events=3,
        post_deploy_watch_s=60,
        health_check_interval_s=30,
        connector_max_retries=0,
    )


@pytest.fixture
def vendors() -> Iterator[Vendors]:
    creds = {
        "crm_client_id": "crm-" + secrets.token_hex(4),
        "crm_client_secret": secrets.token_urlsafe(24),
        "billing_token": secrets.token_urlsafe(24),
        "esign_api_key": secrets.token_urlsafe(24),
    }
    billing_admin = secrets.token_urlsafe(16)
    apps: dict[str, FastAPI] = {
        "crm": create_crm_app(
            client_id=creds["crm_client_id"],
            client_secret=creds["crm_client_secret"],
            admin_token=secrets.token_urlsafe(16),
            rate_limit=None,
        ),
        "billing": create_billing_app(api_token=creds["billing_token"], admin_token=billing_admin, rate_limit=None),
        "esign": create_esign_app(
            api_key=creds["esign_api_key"],
            webhook_secret=secrets.token_urlsafe(24),
            admin_token=secrets.token_urlsafe(16),
            rate_limit=None,
        ),
        "tax": create_tax_app(MockWorld()),
    }
    with ExitStack() as stack:
        urls = {name: stack.enter_context(run_simulator(app)) for name, app in apps.items()}
        yield Vendors(
            urls=urls, specs={n: a.openapi() for n, a in apps.items()}, creds=creds, billing_admin=billing_admin
        )


@pytest.fixture
def clock() -> ManualClock:
    return ManualClock()


@pytest.fixture
async def platform(
    client: httpx.AsyncClient, app: FastAPI, db: Database, settings: PlatformSettings, clock: ManualClock
) -> AsyncIterator[Worker]:
    deps = build_deployment_deps(app.state.settings, db)
    deps.clock = clock
    app.state.deployment_deps = deps
    yield Worker(db, app.state.settings, deps)


async def _signup_team(client: httpx.AsyncClient, slug: str) -> dict[str, str]:
    """Admin signs up the org and adds a PM and an engineer; returns bearer tokens per role."""
    r = await client.post(
        "/v1/auth/signup",
        json={
            "org_name": slug.title(),
            "org_slug": slug,
            "email": f"admin@{slug}.test",
            "password": PASSWORD,
        },
    )
    assert r.status_code == 201, r.text
    tokens = {"admin": r.json()["access_token"]}
    for role in ("pm", "engineer"):
        email = f"{role}@{slug}.test"
        r = await client.post(
            "/v1/members",
            headers=_auth(tokens["admin"]),
            json={
                "email": email,
                "roles": [role],
                "initial_password": "temporary-" + PASSWORD,
            },
        )
        assert r.status_code == 201, r.text
        r = await client.post("/v1/auth/login", json={"email": email, "password": "temporary-" + PASSWORD})
        assert r.status_code == 200, r.text
        r = await client.post(
            "/v1/auth/password",
            headers=_auth(r.json()["access_token"]),
            json={
                "current_password": "temporary-" + PASSWORD,
                "new_password": PASSWORD,
            },
        )
        assert r.status_code == 200, r.text
        tokens[role] = r.json()["access_token"]
    return tokens


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def _connect_vendors(client: httpx.AsyncClient, admin: str, vendors: Vendors) -> None:
    for name, value in vendors.creds.items():
        r = await client.put(f"/v1/secrets/{name}", headers=_auth(admin), json={"value": value})
        assert r.status_code == 200, r.text
        assert value not in r.text  # write-only
    auth: dict[str, dict[str, Any]] = {
        "crm": {
            "kind": "oauth2_client_credentials",
            "token_url": TOKEN_PATH,
            "scopes": ["crm.read"],
            "secrets": {"client_id": "crm_client_id", "client_secret": "crm_client_secret"},
        },
        "billing": {"kind": "bearer", "secrets": {"token": "billing_token"}},
        "esign": {"kind": "api_key", "secrets": {"api_key": "esign_api_key"}, "header_name": "X-API-Key"},
        "tax": {"kind": "none"},
    }
    for system, url in vendors.urls.items():
        r = await client.put(
            f"/v1/connections/{system}",
            headers=_auth(admin),
            json={
                "base_url": url,
                "spec": vendors.specs[system],
                "auth": auth[system],
                "tool_prefix": system,
            },
        )
        assert r.status_code == 200, r.text
        r = await client.post(f"/v1/connections/{system}/health", headers=_auth(admin))
        assert r.status_code == 200 and r.json()["ok"], r.text


async def _approved_loop1(client: httpx.AsyncClient, tokens: dict[str, str]) -> tuple[str, str]:
    r = await client.post("/v1/kg/bootstrap", headers=_auth(tokens["admin"]))
    assert r.status_code == 200, r.text
    r = await client.post(
        "/v1/integrations",
        headers=_auth(tokens["admin"]),
        json={
            "intent": LOOP1_INTENT,
            "sourceSystem": "sys-crm",
            "targetSystem": "sys-billing",
            "trigger": "proc-closedwon",
        },
    )
    assert r.status_code == 201, r.text
    iid, vid = r.json()["integration"]["id"], r.json()["version"]["id"]
    base = f"/v1/integrations/{iid}/versions/{vid}"
    r = await client.patch(f"{base}/mappings", headers=_auth(tokens["engineer"]), json={"edits": ENGINEER_EDITS})
    assert r.status_code == 200, r.text
    assert "def run(event, tools)" in r.json()["code"]
    for role, who in (("pm", "pm"), ("eng", "engineer")):
        r = await client.post(f"{base}/approvals", headers=_auth(tokens[who]), json={"role": role, "comment": "ok"})
        assert r.status_code == 200, r.text
    assert r.json()["status"] == "approved"
    return iid, vid


async def _send_events(client: httpx.AsyncClient, token: str, iid: str, n: int, start: int = 0) -> list[int]:
    codes = []
    for i in range(start, start + n):
        r = await client.post(
            f"/v1/integrations/{iid}/events",
            headers=_auth(token),
            json={
                "event_id": f"evt-{i}",
                "payload": {"opportunity_id": f"opp-00{1 + i % 3}"},
            },
        )
        codes.append(r.status_code)
    return codes


async def _deployment(client: httpx.AsyncClient, token: str, dep_id: str) -> dict[str, Any]:
    r = await client.get(f"/v1/deployments/{dep_id}", headers=_auth(token))
    assert r.status_code == 200, r.text
    body: dict[str, Any] = r.json()
    return body


async def test_loop1_ships_through_real_sandbox_and_vendor_apis(
    client: httpx.AsyncClient, db: Database, vendors: Vendors, platform: Worker, clock: ManualClock
) -> None:
    tokens = await _signup_team(client, "acme")
    await _connect_vendors(client, tokens["admin"], vendors)
    iid, vid = await _approved_loop1(client, tokens)

    r = await client.post(
        f"/v1/integrations/{iid}/deployments", headers=_auth(tokens["engineer"]), json={"version_id": vid}
    )
    assert r.status_code == 202, r.text
    dep_id = r.json()["id"]

    await platform.drain()  # sandbox + staging: synthetic runs in the sandbox against the simulators
    dep = await _deployment(client, tokens["admin"], dep_id)
    assert dep["current_stage"] == "canary", [
        (c["stage"], c["check"] if "check" in c else c.get("name"), c.get("ok"), c.get("detail"))
        for c in dep.get("health_checks", [])
    ] or dep

    codes = await _send_events(client, tokens["engineer"], iid, 12)
    assert codes.count(200) >= 3, codes  # canary share served; the rest have no live version yet (409)
    clock.advance(31)
    await platform.drain()
    for _ in range(4):  # post-deploy watch window
        clock.advance(30)
        await platform.drain()

    dep = await _deployment(client, tokens["admin"], dep_id)
    assert dep["status"] == DeploymentStatus.SUCCEEDED, dep
    r = await client.get(f"/v1/integrations/{iid}", headers=_auth(tokens["admin"]))
    assert r.json()["activeVersionId"] == vid, r.text

    # Production traffic now runs the approved code against the real vendor APIs.
    assert await _send_events(client, tokens["engineer"], iid, 3, start=100) == [200, 200, 200]
    async with httpx.AsyncClient(base_url=vendors.urls["billing"]) as billing:
        state = (await billing.get("/_sim/state", headers={"X-Sim-Admin-Token": vendors.billing_admin})).json()
    assert state, "billing simulator received no writes"

    async with db.session() as s:
        ok = await s.scalar(select(func.count()).select_from(ExecutionRecord).where(ExecutionRecord.ok.is_(True)))
        learned = await s.scalar(
            select(func.count()).select_from(KGNodeRecord).where(KGNodeRecord.node_id == "pat-crm-billing")
        )
    assert ok and ok >= 6
    assert learned == 1, "going live should teach the tenant KG the CRM->billing pattern"


async def test_vendor_outage_during_canary_rolls_back_automatically(
    client: httpx.AsyncClient, vendors: Vendors, platform: Worker, clock: ManualClock
) -> None:
    tokens = await _signup_team(client, "globex")
    await _connect_vendors(client, tokens["admin"], vendors)
    iid, vid = await _approved_loop1(client, tokens)
    r = await client.post(
        f"/v1/integrations/{iid}/deployments", headers=_auth(tokens["engineer"]), json={"version_id": vid}
    )
    dep_id = r.json()["id"]
    await platform.drain()
    assert (await _deployment(client, tokens["admin"], dep_id))["current_stage"] == "canary"

    async with httpx.AsyncClient(base_url=vendors.urls["billing"]) as billing:
        r = await billing.put(
            "/_sim/faults", headers={"X-Sim-Admin-Token": vendors.billing_admin}, json={"outage": True}
        )
        assert r.status_code == 200, r.text
    await _send_events(client, tokens["engineer"], iid, 12)
    clock.advance(31)
    await platform.drain()

    dep = await _deployment(client, tokens["admin"], dep_id)
    assert dep["status"] == DeploymentStatus.ROLLED_BACK, dep
    assert dep["failure_reason"], dep
    r = await client.get(f"/v1/integrations/{iid}", headers=_auth(tokens["admin"]))
    assert r.json()["activeVersionId"] is None
