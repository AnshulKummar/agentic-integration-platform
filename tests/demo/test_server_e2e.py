from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from app.demo.server import app


@pytest.fixture
def client() -> Iterator[TestClient]:
    with TestClient(app) as c:
        c.post("/demo/reset", json={"learningEnabled": True, "mode": "scripted"})
        yield c


def _loop1(c: TestClient) -> str:
    r = c.post("/demo/loop1/intent", json={"text": "Connect our CRM to the billing system so every closed-won "
                                                    "account becomes a billing customer."})
    assert r.status_code == 200, r.text
    body = r.json()
    iid = body["integration"]["id"]
    assert body["integration"]["status"] == "planned"
    assert {e["agent"] for e in body["trace"]} >= {"user", "llm", "kg", "mcp", "context"}
    assert {e["lane"] for e in body["trace"] if e.get("lane")} == {1, 2, 3}
    r = c.post("/demo/loop1/refine", json={"text": "Modify to include tax calculations."})
    assert r.json()["integration"]["status"] == "in_review"
    r = c.post(f"/demo/integrations/{iid}/review")
    assert r.json()["integration"]["humanEdits"] == 3
    assert c.post(f"/demo/integrations/{iid}/deploy").status_code == 409  # no approvals yet
    for role, by in (("pm", "Priya (PM)"), ("eng", "Marco (Eng)")):
        r = c.post(f"/demo/integrations/{iid}/approve", json={"role": role, "by": by, "comment": "ok"})
        assert r.status_code == 200, r.text
    r = c.post(f"/demo/integrations/{iid}/deploy")
    assert r.json()["integration"]["status"] == "live"
    return iid


def test_full_storyline_through_api(client: TestClient) -> None:
    state = client.get("/demo/state").json()
    assert state["mode"] == "scripted" and state["counts"]["nodes"] == 36
    _loop1(client)
    state = client.get("/demo/state").json()
    assert any(n["id"] == "pat-crm-billing" and n["learnedIn"] == "loop1" for n in state["nodes"])

    r = client.post("/demo/api-releases/check")
    assert r.status_code == 200
    [release] = r.json()["releases"]
    assert release["id"] == "rel-esign-v3" and release["status"] == "analyzed"
    assert release["mcpTools"] == ["esign.create_agreement", "esign.get_agreement_fields",
                                   "esign.subscribe_completed", "esign.get_signers"]
    roadmap = {i["id"]: i for i in r.json()["roadmap"]}
    assert roadmap["rm-contract-to-cash"]["status"] == "in_progress"
    assert roadmap["rm-contract-to-cash"]["fitScore"] >= 0.85
    assert client.post("/demo/api-releases/check").json()["releases"] == []  # already known

    r = client.post("/demo/opportunities/rm-contract-to-cash/draft")
    int2 = r.json()["integration"]
    assert int2["initiatedBy"] == "agent_from_roadmap"
    assert sum(m["origin"] == "kg_reused" for m in int2["mappings"]) == 7
    iid = int2["id"]
    client.post(f"/demo/integrations/{iid}/review", json={"edits": []})
    for role, by in (("pm", "Priya (PM)"), ("eng", "Marco (Eng)")):
        client.post(f"/demo/integrations/{iid}/approve", json={"role": role, "by": by, "comment": "ok"})
    r = client.post(f"/demo/integrations/{iid}/deploy")
    assert r.json()["integration"]["status"] == "live"

    state = client.get("/demo/state").json()
    assert state["metrics"]["integrations"][iid]["humanEdits"] == 0
    assert next(n for n in state["nodes"] if n["id"] == "pat-crm-billing")["confidence"] == 0.92
    assert state["pipelines"][iid][-1]["status"] == "passed"
    trace = client.get("/demo/trace", params={"since": 0}).json()
    assert trace["total"] == len(trace["events"]) > 50

    mock = client.get("/mock/_control/state").json()
    assert any(s["event"] == "agreement.completed" for s in mock["subscriptions"])


def test_ablation_through_api(client: TestClient) -> None:
    client.post("/demo/reset", json={"learningEnabled": False})
    r = client.post("/demo/run/all")
    metrics = r.json()["metrics"]["integrations"]
    assert metrics["int-1"]["humanEdits"] == 3
    assert metrics["int-2"]["humanEdits"] == 3 and metrics["int-2"]["reused"] == 0


def test_errors_are_structured(client: TestClient) -> None:
    r = client.post("/demo/opportunities/rm-contract-to-cash/draft")
    assert r.status_code == 409 and r.json()["error"] == "not_matched"
    r = client.post("/demo/integrations/int-9/deploy")
    assert r.status_code == 404
    iid = client.post("/demo/loop1/intent", json={}).json()["integration"]["id"]
    client.post("/demo/loop1/refine", json={})
    client.post(f"/demo/integrations/{iid}/review")
    r = client.post(f"/demo/integrations/{iid}/approve", json={"role": "ceo", "by": "X"})
    assert r.status_code == 422 and r.json()["error"] == "unknown_role"


def test_cors_and_mocks(client: TestClient) -> None:
    r = client.options("/demo/state", headers={"Origin": "https://anshulkummar.github.io",
                                               "Access-Control-Request-Method": "GET"})
    assert r.headers["access-control-allow-origin"] == "https://anshulkummar.github.io"
    spec = client.get("/mock/esign/openapi.json", params={"version": "2.4.0"}).json()
    assert spec["info"]["version"] == "2.4.0"
    assert client.get("/mock/billing/health").json() == {"status": "ok"}
    tools = client.get("/demo/mcp/tools").json()["tools"]
    assert any(t["name"] == "billing.create_customer" and "inputSchema" in t for t in tools)
