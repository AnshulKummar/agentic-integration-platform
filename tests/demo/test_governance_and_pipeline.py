from __future__ import annotations

import pytest

from app.demo.governance import GovernanceError, approve, deploy_blockers, ensure_deployable
from app.demo.orchestrator import DemoError, DemoOrchestrator


async def _reviewed(o: DemoOrchestrator) -> str:
    integ = await o.loop1_intent()
    await o.loop1_refine(integration_id=integ.id)
    await o.review(integ.id)
    return integ.id


async def test_dual_approval_required(orch: DemoOrchestrator) -> None:
    iid = await _reviewed(orch)
    integ = orch.integrations[iid]
    with pytest.raises(GovernanceError) as err:
        ensure_deployable(integ)
    assert err.value.code == "not_deployable"
    approve(integ, "pm", "Priya (PM)", "fit")
    assert "missing eng approval" in deploy_blockers(integ)
    with pytest.raises(DemoError) as derr:
        await orch.deploy(iid)
    assert derr.value.status == 409
    approve(integ, "eng", "Marco (Eng)", "code ok")
    assert integ.status == "approved" and deploy_blockers(integ) == []


async def test_unknown_role_rejected(orch: DemoOrchestrator) -> None:
    iid = await _reviewed(orch)
    with pytest.raises(DemoError) as err:
        orch.approve(iid, "cfo", "Someone", "lgtm")
    assert err.value.status == 422 and err.value.code == "unknown_role"


async def test_cannot_approve_with_failing_tests(orch: DemoOrchestrator) -> None:
    integ = await orch.loop1_intent()
    await orch.loop1_refine(integration_id=integ.id)  # opens review with 3 failing tests
    with pytest.raises(GovernanceError) as err:
        approve(integ, "pm", "Priya (PM)", "")
    assert err.value.code == "tests_failing"


async def test_risk_checks(after_release: DemoOrchestrator) -> None:
    o = after_release
    int2 = await o.draft_opportunity("rm-contract-to-cash")
    risks = {r.label: r for r in int2.risks}
    assert set(risks) == {"Data access boundary", "Identity propagation", "Tool budget", "Deprecated field"}
    assert risks["Data access boundary"].level == "medium" and "Contact.email" in risks["Data access boundary"].note
    assert risks["Identity propagation"].level == "low" and "c2c-agent" in risks["Identity propagation"].note
    assert risks["Tool budget"].level == "low"
    assert risks["Deprecated field"].level == "medium" and "Migration bundled" in risks["Deprecated field"].note
    # Without a service principal identity propagation is a blocking risk.
    int2.service_principal = None
    from app.demo.governance import assess_risks

    risks2 = {r.label: r for r in assess_risks(int2, o.kg, o.field_catalog, o.context_store["integrations"])}
    assert risks2["Identity propagation"].level == "high"


async def test_staged_rollout_passes_all_stages(orch: DemoOrchestrator) -> None:
    integ = await orch.run_loop1()
    assert [s.name for s in integ.pipeline] == ["sandbox", "staging", "canary", "production"]
    assert all(s.status == "passed" for s in integ.pipeline)
    assert integ.status == "live"


async def test_canary_failure_rolls_back(orch: DemoOrchestrator) -> None:
    iid = await _reviewed(orch)
    orch.approve(iid, "pm", "Priya (PM)", "ok")
    orch.approve(iid, "eng", "Marco (Eng)", "ok")
    orch.apps.world.faults["billing"] = 5  # production Billing starts failing
    integ = await orch.deploy(iid)
    stages = {s.name: s.status for s in integ.pipeline}
    assert stages == {"sandbox": "rolled_back", "staging": "rolled_back", "canary": "failed", "production": "pending"}
    assert integ.status == "rolled_back"
    assert orch.roadmap["rm-billing-sync"].status != "shipped"
    assert not orch.kg.has_node("pat-crm-billing")  # nothing learned from a failed rollout
    assert any("rollback" in e.text.lower() for e in orch.trace)
