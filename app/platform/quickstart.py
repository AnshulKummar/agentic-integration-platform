"""One-command walkthrough of the whole platform, on your laptop, with nothing to install
beyond requirements-platform.txt (no Docker, no database server, no API key):

    python -m app.platform.quickstart                 # KG learning on
    python -m app.platform.quickstart --no-learning   # the ablation: same story, KG learning off

It runs the real code paths end to end: login and roles -> encrypted vendor
credentials -> connectors generated from each vendor's OpenAPI spec -> the
company knowledge graph -> loop 1 (a PM's intent becomes a reviewed, approved
integration that deploys sandbox -> staging -> canary -> production, running in
the code sandbox against vendor simulators over real HTTP) -> the KG learns from
the live integration -> loop 2 (a vendor ships a new API version; the agent maps
it to the roadmap and drafts the next integration, reusing what the KG learned).

Time is simulated (the canary and watch windows advance instantly); everything
else is real. The database is a temporary SQLite file.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import secrets
import sys
import tempfile
from collections.abc import AsyncIterator, Iterator
from contextlib import ExitStack, asynccontextmanager, contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx
from sqlalchemy import func, select, update

from app.demo.mocks import MockWorld, create_tax_app
from app.platform.app import create_app
from app.platform.config import PlatformSettings, generate_master_key
from app.platform.core_models import ExecutionRecord, KGEdgeRecord, KGNodeRecord, Tenant
from app.platform.deployments.clock import ManualClock
from app.platform.deployments.routing import routes_to_canary
from app.platform.runtime import build_deployment_deps
from app.platform.simulators.billing import create_billing_app
from app.platform.simulators.crm import TOKEN_PATH, create_crm_app
from app.platform.simulators.esign import create_esign_app
from app.platform.simulators.harness import run_simulator
from app.platform.worker import Worker

FIXTURES = Path(__file__).resolve().parents[1] / "demo" / "fixtures"
PASSWORD = "quickstart-" + "x" * 16
LOOP1_INTENT = (
    "Connect our CRM to the billing system so every closed-won account becomes a billing customer, "
    "including tax calculations."
)
#: The engineer's three corrections during the loop-1 review (what the KG learns from).
ENGINEER_EDITS = [
    {"target": "Customer.currency_code", "transform": "to_iso4217()"},
    {"target": "Customer.terms_code", "transform": "TERMS_MAP lookup"},
    {"target": "TaxQuote.jurisdiction", "source": "Account.shipping_address", "transform": "geocode(ship-to)"},
]
CANARY_PERCENT = 50


class QuickstartError(RuntimeError):
    """A step of the walkthrough did not behave as expected."""


@dataclass
class Summary:
    learning: bool
    loop1_mappings: int = 0
    loop1_human_edits: int = 0
    loop1_status: str = ""
    production_runs_ok: int = 0
    billing_customers_created: int = 0
    kg_nodes_before: int = 0
    kg_nodes_after: int = 0
    loop2_roadmap_item: str = ""
    loop2_mappings: int = 0
    loop2_reused_from_kg: int = 0
    loop2_deprecated_flagged: list[str] = field(default_factory=list)


class Narrator:
    def __init__(self, quiet: bool) -> None:
        self.quiet = quiet

    def act(self, title: str) -> None:
        if not self.quiet:
            print(f"\n=== {title} ===")

    def say(self, text: str) -> None:
        if not self.quiet:
            print(f"  {text}")


def _check(response: httpx.Response, expected: int) -> dict[str, Any]:
    if response.status_code != expected:
        raise QuickstartError(
            f"{response.request.method} {response.request.url.path} -> {response.status_code}: {response.text[:500]}"
        )
    body: dict[str, Any] = response.json() if response.content else {}
    return body


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


@contextmanager
def _vendors() -> Iterator[tuple[dict[str, str], dict[str, dict[str, Any]], dict[str, str], str]]:
    creds = {
        "crm_client_id": "crm-" + secrets.token_hex(4),
        "crm_client_secret": secrets.token_urlsafe(24),
        "billing_token": secrets.token_urlsafe(24),
        "esign_api_key": secrets.token_urlsafe(24),
    }
    billing_admin = secrets.token_urlsafe(16)
    apps = {
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
        yield urls, {n: a.openapi() for n, a in apps.items()}, creds, billing_admin


@asynccontextmanager
async def _platform(db_path: Path, clock: ManualClock) -> AsyncIterator[tuple[httpx.AsyncClient, Worker, Any]]:
    settings = PlatformSettings(
        env="development",
        database_url=f"sqlite+aiosqlite:///{db_path.as_posix()}",
        jwt_secret=secrets.token_urlsafe(48),  # type: ignore[arg-type]
        master_key=generate_master_key(),  # type: ignore[arg-type]
        sandbox_backend="subprocess",
        sandbox_timeout_s=20.0,
        log_level="WARNING",
        log_json=False,
        canary_percent=CANARY_PERCENT,
        canary_min_events=3,
        post_deploy_watch_s=60,
        health_check_interval_s=30,
        connector_max_retries=0,
    )
    app = create_app(settings)
    async with app.router.lifespan_context(app):
        deps = build_deployment_deps(app.state.settings, app.state.db)
        deps.clock = clock
        app.state.deployment_deps = deps
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://platform.local") as client:
            yield client, Worker(app.state.db, app.state.settings, deps), app.state.db


async def run(*, learning: bool = True, quiet: bool = False) -> Summary:
    out = Narrator(quiet)
    summary = Summary(learning=learning)
    clock = ManualClock()
    with tempfile.TemporaryDirectory(prefix="pmaip-quickstart-") as tmp, _vendors() as (urls, specs, creds, b_admin):
        async with _platform(Path(tmp) / "platform.db", clock) as (client, worker, db):
            # ---------------------------------------------------------------- the company
            out.act("1. Acme signs up and invites its team")
            body = _check(
                await client.post(
                    "/v1/auth/signup",
                    json={
                        "org_name": "Acme Corp",
                        "org_slug": "acme",
                        "email": "admin@acme.test",
                        "password": PASSWORD,
                    },
                ),
                201,
            )
            tokens = {"admin": body["access_token"]}
            for role in ("pm", "engineer"):
                email = f"{role}@acme.test"
                _check(
                    await client.post(
                        "/v1/members",
                        headers=_auth(tokens["admin"]),
                        json={"email": email, "roles": [role], "initial_password": "temp-" + PASSWORD},
                    ),
                    201,
                )
                login = _check(
                    await client.post("/v1/auth/login", json={"email": email, "password": "temp-" + PASSWORD}), 200
                )
                changed = _check(
                    await client.post(
                        "/v1/auth/password",
                        headers=_auth(login["access_token"]),
                        json={"current_password": "temp-" + PASSWORD, "new_password": PASSWORD},
                    ),
                    200,
                )
                tokens[role] = changed["access_token"]
            out.say("admin, product manager and engineer accounts; each role sees and does only what it may")
            if not learning:
                async with db.session() as s:
                    await s.execute(update(Tenant).where(Tenant.slug == "acme").values(learning_enabled=False))
                    await s.commit()
                out.say("KG learning is OFF for this company (the ablation)")

            out.act("2. Acme connects its SaaS vendors")
            for name, value in creds.items():
                _check(
                    await client.put(f"/v1/secrets/{name}", headers=_auth(tokens["admin"]), json={"value": value}), 200
                )
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
            for system, url in urls.items():
                conn = _check(
                    await client.put(
                        f"/v1/connections/{system}",
                        headers=_auth(tokens["admin"]),
                        json={"base_url": url, "spec": specs[system], "auth": auth[system], "tool_prefix": system},
                    ),
                    200,
                )
                _check(await client.post(f"/v1/connections/{system}/health", headers=_auth(tokens["admin"])), 200)
                out.say(
                    f"{system:8} {conn['tool_count']:2} agent tools generated from its OpenAPI spec, "
                    f"credentials encrypted (write-only), health OK"
                )

            out.act("3. The company knowledge graph is seeded")
            _check(await client.post("/v1/kg/bootstrap", headers=_auth(tokens["admin"])), 200)
            kg = _check(await client.get("/v1/kg", headers=_auth(tokens["admin"])), 200)
            summary.kg_nodes_before = len(kg.get("nodes", []))
            roadmap = await client.get("/v1/roadmap", headers=_auth(tokens["admin"]))
            items = roadmap.json() if roadmap.status_code == 200 else []
            out.say(f"{summary.kg_nodes_before} nodes: systems, business objects, fields, processes, roadmap")
            for item in items:
                if item.get("status") == "blocked":
                    out.say(f"roadmap item BLOCKED: {item['title']} ({item.get('blockedReason') or ''})")

            # ---------------------------------------------------------------- loop 1
            out.act("4. Loop 1: a PM states an intent; the agent drafts the integration")
            draft = _check(
                await client.post(
                    "/v1/integrations",
                    headers=_auth(tokens["admin"]),
                    json={
                        "intent": LOOP1_INTENT,
                        "sourceSystem": "sys-crm",
                        "targetSystem": "sys-billing",
                        "trigger": "proc-closedwon",
                    },
                ),
                201,
            )
            iid, vid = draft["integration"]["id"], draft["version"]["id"]
            summary.loop1_mappings = len(draft["version"]["mappings"])
            out.say(f'intent: "{LOOP1_INTENT}"')
            out.say(f"agent drafted {summary.loop1_mappings} field mappings + runnable code for review")
            base = f"/v1/integrations/{iid}/versions/{vid}"
            reviewed = _check(
                await client.patch(
                    f"{base}/mappings", headers=_auth(tokens["engineer"]), json={"edits": ENGINEER_EDITS}
                ),
                200,
            )
            summary.loop1_human_edits = int(reviewed["humanEdits"])
            out.say(f"engineer corrected {summary.loop1_human_edits} mappings (currency, payment terms, tax geo)")
            for role, who in (("pm", "pm"), ("eng", "engineer")):
                approved = _check(
                    await client.post(
                        f"{base}/approvals", headers=_auth(tokens[who]), json={"role": role, "comment": "ok"}
                    ),
                    200,
                )
            summary.loop1_status = approved["status"]
            out.say("product AND engineering approved (the author cannot approve; one person cannot do both)")

            out.act("5. Loop 1 ships: staged deployment with health checks")
            dep = _check(
                await client.post(
                    f"/v1/integrations/{iid}/deployments", headers=_auth(tokens["engineer"]), json={"version_id": vid}
                ),
                202,
            )
            await worker.drain()
            sent = 0
            i = 0
            while sent < 4:  # real events, picked to land on the canary
                if routes_to_canary(iid, f"evt-{i}", CANARY_PERCENT):
                    _check(
                        await client.post(
                            f"/v1/integrations/{iid}/events",
                            headers=_auth(tokens["engineer"]),
                            json={"event_id": f"evt-{i}", "payload": {"opportunity_id": f"opp-00{1 + i % 3}"}},
                        ),
                        200,
                    )
                    sent += 1
                i += 1
            for _ in range(5):
                clock.advance(31)
                await worker.drain()
            detail = _check(await client.get(f"/v1/deployments/{dep['id']}", headers=_auth(tokens["admin"])), 200)
            for stage in detail.get("stages", []):
                out.say(f"{stage['stage']:10} {stage['status']}")
            if detail["status"] != "succeeded":
                raise QuickstartError(f"deployment ended {detail['status']}: {detail.get('failure_reason')}")
            for n in range(3):
                _check(
                    await client.post(
                        f"/v1/integrations/{iid}/events",
                        headers=_auth(tokens["engineer"]),
                        json={"event_id": f"prod-{n}", "payload": {"opportunity_id": "opp-002"}},
                    ),
                    200,
                )
            async with httpx.AsyncClient(base_url=urls["billing"]) as billing:
                state = (await billing.get("/_sim/state", headers={"X-Sim-Admin-Token": b_admin})).json()
            summary.billing_customers_created = len(state.get("customers", {})) if isinstance(state, dict) else 0
            async with db.session() as s:
                summary.production_runs_ok = int(
                    await s.scalar(
                        select(func.count()).select_from(ExecutionRecord).where(ExecutionRecord.ok.is_(True))
                    )
                    or 0
                )
            out.say(
                f"{summary.production_runs_ok} sandboxed runs succeeded against the vendor APIs (deploy checks + live "
                f"events); {summary.billing_customers_created} customers created in billing"
            )

            async with db.session() as s:
                nodes = int(await s.scalar(select(func.count()).select_from(KGNodeRecord)) or 0)
                edges = int(await s.scalar(select(func.count()).select_from(KGEdgeRecord)) or 0)
            summary.kg_nodes_after = nodes
            out.act("6. The KG learns from the live integration")
            if learning:
                out.say(
                    f"KG now {nodes} nodes / {edges} edges: a reusable CRM->billing pattern plus the "
                    f"engineer's 3 corrections as rules"
                )
            else:
                out.say("learning is off: nothing learned")

            # ---------------------------------------------------------------- loop 2
            out.act("7. Loop 2: the e-signature vendor ships API v3.0")
            spec_v3 = json.loads((FIXTURES / "esign_openapi_v3_0.json").read_text(encoding="utf-8"))
            res = _check(
                await client.post(
                    "/v1/api-releases",
                    headers=_auth(tokens["engineer"]),
                    json={"systemId": "sys-esign", "app": "e-Signature App", "spec": spec_v3},
                ),
                201,
            )
            changes = res["release"]["changes"]
            out.say(f"agent diffed the spec: {len(changes)} changes, {len(res['release']['mcpTools'])} new agent tools")
            summary.loop2_deprecated_flagged = [
                f"{d.get('field')} -> {d.get('replacedBy')}" for d in res.get("deprecatedFields", [])
            ]
            for d in summary.loop2_deprecated_flagged:
                out.say(f"deprecated field flagged: {d}")
            matches = sorted(res.get("roadmapMatches", []), key=lambda m: -float(m.get("fitScore") or 0))
            for m in matches[:3]:
                out.say(f"roadmap match: {m.get('title', m.get('itemId'))} (fit {float(m.get('fitScore') or 0):.2f})")
            drafted = res.get("drafted")
            if drafted is None:
                raise QuickstartError(f"agent did not draft an integration: {res.get('agentError')}")
            mappings = drafted["version"]["mappings"]
            summary.loop2_mappings = len(mappings)
            summary.loop2_reused_from_kg = sum(m.get("origin") == "kg_reused" for m in mappings)
            item_id = str(drafted["integration"].get("roadmapItemId") or "")
            match: dict[str, Any] = next((m for m in matches if m.get("itemId") == item_id), {})
            summary.loop2_roadmap_item = str(match.get("title") or item_id)
            out.say(f"agent drafted an integration for '{summary.loop2_roadmap_item}' on its own")
            out.say(
                f"{summary.loop2_reused_from_kg} of {summary.loop2_mappings} mappings reused from the KG "
                f"(what the engineer taught it in loop 1)"
            )
            out.say("it now waits for product and engineering approval, exactly like loop 1")

    out.act("Done")
    out.say("re-run with --no-learning to see loop 2 without the KG's help")
    out.say("explore the API: uvicorn app.platform.main:app  ->  http://127.0.0.1:8000/docs")
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.platform.quickstart", description=__doc__.splitlines()[0])
    parser.add_argument("--no-learning", action="store_true", help="the ablation: KG learning off")
    parser.add_argument("--quiet", action="store_true")
    parser.add_argument("--json", action="store_true", help="print the summary as JSON")
    args = parser.parse_args(argv)
    summary = asyncio.run(run(learning=not args.no_learning, quiet=args.quiet or args.json))
    if args.json:
        print(json.dumps(summary.__dict__, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
