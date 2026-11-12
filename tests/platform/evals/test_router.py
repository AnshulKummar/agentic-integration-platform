"""Eval-run API: persistence, listing, auth."""

from __future__ import annotations

from sqlalchemy import select

from app.platform.db import Database
from app.platform.evals.models import EvalRun
from app.platform.principal import Role


async def test_admin_creates_run_and_it_is_persisted(client, db: Database, tenant, act_as, principal_for) -> None:  # type: ignore[no-untyped-def]
    act_as(principal_for(tenant, Role.ADMIN, user_id="admin-1"))
    r = await client.post("/v1/evals/runs", json={"suite": "handlabeled", "predictor": "deterministic", "seed": 5})
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["predictor"] == "deterministic" and body["seed"] == 5
    assert body["summary"]["handlabeled"]["cases"] >= 15
    assert "synthetic" not in body["summary"]
    assert body["metrics"]["results"]["handlabeled"]["metrics"]["f1"] == body["summary"]["handlabeled"]["f1"]

    async with db.session() as s:
        rows = (await s.execute(select(EvalRun))).scalars().all()
    assert [(x.id, x.created_by) for x in rows] == [(body["id"], "admin-1")]

    act_as(principal_for(tenant, Role.VIEWER))
    listing = await client.get("/v1/evals/runs")
    assert listing.status_code == 200
    assert [x["id"] for x in listing.json()] == [body["id"]]
    assert "metrics" not in listing.json()[0]
    detail = await client.get(f"/v1/evals/runs/{body['id']}")
    assert detail.status_code == 200 and detail.json()["metrics"]["predictor"] == "deterministic"


async def test_non_admin_cannot_create(client, tenant, act_as, principal_for) -> None:  # type: ignore[no-untyped-def]
    act_as(principal_for(tenant, Role.PM, Role.ENGINEER))
    r = await client.post("/v1/evals/runs", json={"suite": "handlabeled"})
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "forbidden"


async def test_live_llm_and_bad_suite_rejected(client, tenant, act_as, principal_for) -> None:  # type: ignore[no-untyped-def]
    act_as(principal_for(tenant, Role.ADMIN))
    r = await client.post("/v1/evals/runs", json={"predictor": "live_llm"})
    assert r.status_code == 422 and "CLI" in r.json()["error"]["message"]
    r = await client.post("/v1/evals/runs", json={"suite": "everything"})
    assert r.status_code == 422


async def test_unknown_run_is_404_and_auth_required(client, tenant, act_as, principal_for) -> None:  # type: ignore[no-untyped-def]
    assert (await client.get("/v1/evals/runs")).status_code == 401
    act_as(principal_for(tenant, Role.VIEWER))
    r = await client.get("/v1/evals/runs/does-not-exist")
    assert r.status_code == 404 and r.json()["error"]["code"] == "not_found"
