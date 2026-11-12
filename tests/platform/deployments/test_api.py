"""Deployment HTTP API: roles, dual-approval gate, detail view, rollback, events, tenant isolation."""

from __future__ import annotations

from app.platform.principal import Role


async def test_start_requires_engineer_and_dual_approval(
    api, act_as, principal_for, tenant, make_integration, make_version
) -> None:
    integ = await make_integration(tenant)
    pm_only = await make_version(integ, approvals=("pm",))
    approved = await make_version(integ)

    act_as(principal_for(tenant, Role.PM))
    r = await api.post(f"/v1/integrations/{integ.id}/deployments", json={"version_id": approved.id})
    assert r.status_code == 403

    act_as(principal_for(tenant, Role.ENGINEER))
    r = await api.post(f"/v1/integrations/{integ.id}/deployments", json={"version_id": pm_only.id})
    assert r.status_code == 409 and r.json()["error"]["code"] == "approval_required"

    r = await api.post(f"/v1/integrations/{integ.id}/deployments", json={"version_id": approved.id})
    assert r.status_code == 202
    body = r.json()
    assert body["status"] == "pending" and body["version_id"] == approved.id and body["previous_version_id"] is None


async def test_detail_lists_stages_and_checks_then_manual_rollback(
    api, act_as, principal_for, tenant, worker, make_integration, make_version
) -> None:
    integ = await make_integration(tenant)
    v1 = await make_version(integ, make_active=True)
    v2 = await make_version(integ)
    act_as(principal_for(tenant, Role.ENGINEER))
    dep_id = (await api.post(f"/v1/integrations/{integ.id}/deployments", json={"version_id": v2.id})).json()["id"]
    await worker.drain()

    act_as(principal_for(tenant, Role.VIEWER))
    detail = (await api.get(f"/v1/deployments/{dep_id}")).json()
    assert detail["current_stage"] == "canary" and detail["status"] == "running"
    assert [s["stage"] for s in detail["stages"]] == ["sandbox", "staging", "canary", "production", "watch"]
    assert [s["status"] for s in detail["stages"]] == ["passed", "passed", "running", "pending", "pending"]
    synthetic = [c for c in detail["health_checks"] if c["check"] == "synthetic_transaction"]
    assert len(synthetic) == 2 and all(c["ok"] and c["threshold"] == 0.0 for c in synthetic)

    listed = (await api.get("/v1/deployments", params={"integration_id": integ.id})).json()
    assert [d["id"] for d in listed] == [dep_id]
    assert (await api.get("/v1/deployments", params={"status": "failed"})).json() == []

    r = await api.post(f"/v1/deployments/{dep_id}/rollback", json={"reason": "smoke test"})
    assert r.status_code == 403  # viewers cannot roll back
    act_as(principal_for(tenant, Role.ENGINEER))
    r = await api.post(f"/v1/deployments/{dep_id}/rollback", json={"reason": "smoke test"})
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "rolled_back" and body["failure_reason"] == "manual rollback: smoke test"
    assert body["stages"][2]["status"] == "failed"
    r = await api.post(f"/v1/deployments/{dep_id}/rollback", json={})
    assert r.status_code == 409

    # Events now all go to v1.
    r = await api.post(f"/v1/integrations/{integ.id}/events", json={"event_id": "e-1", "payload": {"a": 1}})
    assert r.status_code == 200
    assert (r.json()["version_id"], r.json()["stage"], r.json()["ok"]) == (v1.id, "production", True)


async def test_event_ingest_roles_and_no_live_version(api, act_as, principal_for, tenant, make_integration) -> None:
    integ = await make_integration(tenant)
    act_as(principal_for(tenant, Role.PM))
    r = await api.post(f"/v1/integrations/{integ.id}/events", json={"payload": {}})
    assert r.status_code == 403
    act_as(principal_for(tenant, Role.ADMIN))
    r = await api.post(f"/v1/integrations/{integ.id}/events", json={"payload": {}})
    assert r.status_code == 409 and r.json()["error"]["code"] == "no_live_version"


async def test_runner_crash_returns_502(
    api, act_as, principal_for, tenant, make_integration, make_version, runner
) -> None:
    integ = await make_integration(tenant)
    v1 = await make_version(integ, make_active=True)
    runner.raises.add(v1.id)
    act_as(principal_for(tenant, Role.ENGINEER))
    r = await api.post(f"/v1/integrations/{integ.id}/events", json={"event_id": "e", "payload": {}})
    assert r.status_code == 502 and r.json()["error"]["code"] == "runner_failed"
    assert "sandbox backend unavailable" not in r.text


async def test_tenant_isolation_on_every_endpoint(
    api, act_as, principal_for, tenant, other_tenant, make_integration, make_version
) -> None:
    integ = await make_integration(tenant)
    v1 = await make_version(integ, make_active=True)
    v2 = await make_version(integ)
    act_as(principal_for(tenant, Role.ENGINEER))
    dep_id = (await api.post(f"/v1/integrations/{integ.id}/deployments", json={"version_id": v2.id})).json()["id"]

    act_as(principal_for(other_tenant, Role.ADMIN, user_id="intruder"))
    assert (await api.get(f"/v1/deployments/{dep_id}")).status_code == 404
    assert (await api.get("/v1/deployments")).json() == []
    assert (await api.post(f"/v1/deployments/{dep_id}/rollback", json={})).status_code == 404
    r = await api.post(f"/v1/integrations/{integ.id}/deployments", json={"version_id": v1.id})
    assert r.status_code == 404
    r = await api.post(f"/v1/integrations/{integ.id}/events", json={"event_id": "x", "payload": {}})
    assert r.status_code == 404

    # A version of another tenant cannot be deployed onto one's own integration either.
    own = await make_integration(other_tenant, name="theirs")
    r = await api.post(f"/v1/integrations/{own.id}/deployments", json={"version_id": v2.id})
    assert r.status_code == 404


async def test_unwired_runtime_returns_503(client, app, act_as, principal_for, tenant, make_integration) -> None:
    from app.platform.deployments.deps import register_provider
    from app.platform.runtime import install

    register_provider(None)  # simulate a process where app.platform.runtime was never installed
    app.state.deployment_deps = None
    integ = await make_integration(tenant)
    act_as(principal_for(tenant, Role.ENGINEER))
    r = await client.post(f"/v1/integrations/{integ.id}/events", json={"payload": {}})
    try:
        assert r.status_code == 503 and r.json()["error"]["code"] == "deployments_unavailable"
    finally:
        install()
