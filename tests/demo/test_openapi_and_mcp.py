from __future__ import annotations

import pytest

from app.demo.mcp_tools import McpToolError, generate_tools, tools_for_release
from app.demo.mocks import create_mock_apps
from app.demo.openapi_diff import OpenApiError, diff_specs
from app.demo.runtime import build_mcp_runtime


def test_diff_detects_storyline_changes(spec_v2: dict, spec_v3: dict) -> None:
    diff = diff_specs(spec_v2, spec_v3)
    assert (diff.from_version, diff.to_version) == ("2.4.0", "3.0.0")
    assert {e.key for e in diff.added_endpoints} == {"POST /v3/agreements", "GET /v3/agreements/{id}/fields"}
    assert [w.name for w in diff.added_webhooks] == ["agreement.completed"]
    assert diff.removed_endpoints == []
    [dep] = diff.deprecated_fields
    assert dep.path == "envelope.signer_email"
    assert dep.replaced_by == "signers[].email"
    assert diff.summary() == "+2 endpoints, +1 webhook, 1 deprecated field"
    changes = {(c.kind, c.method, c.path) for c in diff.to_api_changes()}
    assert changes == {
        ("added", "POST", "/v3/agreements"),
        ("added", "GET", "/v3/agreements/{id}/fields"),
        ("added", "WEBHOOK", "agreement.completed"),
        ("deprecated", "FIELD", "envelope.signer_email"),
    }
    # Existing operations that reference the changed Envelope schema are reported too.
    assert {e.key for e in diff.changed_endpoints} == {"POST /v2/envelopes", "GET /v2/envelopes/{id}/signers"}


def test_diff_supports_openapi_31_webhooks_and_removals(spec_v2: dict, spec_v3: dict) -> None:
    v31 = {**spec_v3, "openapi": "3.1.0", "webhooks": spec_v3["x-webhooks"]}
    del v31["x-webhooks"]
    v31["paths"] = {k: v for k, v in spec_v3["paths"].items() if k != "/v2/envelopes/{id}/signers"}
    diff = diff_specs(spec_v2, v31)
    assert [w.name for w in diff.added_webhooks] == ["agreement.completed"]
    assert [e.key for e in diff.removed_endpoints] == ["GET /v2/envelopes/{id}/signers"]


def test_diff_rejects_non_openapi() -> None:
    with pytest.raises(OpenApiError):
        diff_specs({"swagger": "2.0", "paths": {}}, {"openapi": "3.0.0", "paths": {}})


def test_mcp_tools_from_release(spec_v2: dict, spec_v3: dict) -> None:
    tools = tools_for_release(spec_v3, diff_specs(spec_v2, spec_v3), "esign")
    assert [t.name for t in tools] == [
        "esign.create_agreement", "esign.get_agreement_fields", "esign.subscribe_completed", "esign.get_signers",
    ]
    create = tools[0].mcp_definition()
    assert set(create) == {"name", "description", "inputSchema"}
    schema = create["inputSchema"]
    assert schema["type"] == "object"
    assert set(schema["required"]) == {"template_id", "signers"}
    assert schema["properties"]["signers"]["items"]["properties"]["email"]["format"] == "email"
    fields = tools[1]
    assert fields.input_schema["required"] == ["id"]
    assert fields.annotations["readOnlyHint"] is True
    sub = tools[2]
    assert sub.annotations["webhook"] == "agreement.completed"
    assert sub.input_schema["required"] == ["callback_url"]


def test_generated_tools_mark_deprecated_fields(spec_v3: dict) -> None:
    send = next(t for t in generate_tools(spec_v3, "esign") if t.name == "esign.send_envelope")
    prop = send.input_schema["properties"]["signer_email"]
    assert prop["deprecated"] is True and "signers[].email" in prop["description"]
    assert "id" not in send.input_schema["properties"]  # readOnly dropped


async def test_mcp_runtime_calls_mock_apps(spec_v3: dict) -> None:
    apps = create_mock_apps()
    mcp = build_mcp_runtime(apps, spec_v3)
    acct = await mcp.call("crm.get_account", {"account_id": "acc-002"})
    assert acct["currency"] == "Euro"
    fields = await mcp.call("esign.get_agreement_fields", {"id": "agr-002"})
    assert fields == {"amount": 120000.0, "payment_terms": "Net 30 days"}
    with pytest.raises(McpToolError) as err:
        await mcp.call(
            "billing.create_customer",
            {"legal_name": "A", "address": "x", "currency_code": "Euro", "terms_code": "NET30", "external_ref": "o"},
        )
    assert err.value.status == 422
    assert err.value.detail[0]["field"] == "currency_code"
