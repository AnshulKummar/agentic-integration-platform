"""Executes a drafted integration against the mock apps via MCP tools, and runs
the synthetic test suite in an isolated sandbox world.

The integration "code" shown to reviewers is generated from the same mapping rows
this interpreter executes, so what is reviewed is what runs.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Coroutine
from dataclasses import dataclass, field
from typing import Any

from app.demo.kg_store import load_fixture_json
from app.demo.mcp_tools import McpRuntime, McpToolError, generate_tools
from app.demo.mocks import MockApps, create_mock_apps, geocode, terms_lookup, to_iso4217
from app.demo.models import Integration, MappingRow, TestResult

logger = logging.getLogger(__name__)

WEBHOOK_TRIGGER_PREFIX = "webhook "
SIGNED_STAGE = "Contract Signed"


class ExecutionError(RuntimeError):
    pass


def build_mcp_runtime(apps: MockApps, esign_spec: dict[str, Any] | None = None) -> McpRuntime:
    """MCP runtime with tools generated from each mock app's OpenAPI document."""
    runtime = McpRuntime()
    runtime.register_app("crm", apps.crm, generate_tools(apps.crm.openapi(), "crm"))
    runtime.register_app("billing", apps.billing, generate_tools(apps.billing.openapi(), "billing"))
    runtime.register_app("tax", apps.tax, generate_tools(apps.tax.openapi(), "tax"))
    spec = esign_spec or load_fixture_json("esign_openapi_v2_4.json")
    runtime.register_app("esign", apps.esign, generate_tools(spec, "esign"))
    return runtime


# ------------------------------------------------------------ transforms
def apply_transform(transform: str | None, value: Any, ctx: dict[str, Any]) -> Any:
    t = (transform or "copy").strip()
    if t in ("copy", "match first signer"):
        return value
    if t == "to_iso4217()":
        return to_iso4217(str(value))
    if t == "TERMS_MAP lookup":
        return terms_lookup(str(value))
    if t in ("geocode", "geocode(ship-to)"):
        return geocode(str(value))
    if t == "+ tax":
        tax = float(ctx.get("TaxQuote", {}).get("tax", 0.0))
        return round(float(value) + tax, 2)
    raise ExecutionError(f"unknown transform {transform!r}")


def resolve(label: str, ctx: dict[str, Any]) -> Any:
    owner, *path = label.split(".")
    if owner not in ctx:
        raise ExecutionError(f"no {owner} in context for {label}")
    value: Any = ctx[owner]
    for seg in path:
        if seg.endswith("[]"):
            items = value[seg[:-2]]
            if not items:
                raise ExecutionError(f"{label}: empty collection")
            value = items[0]
        else:
            value = value[seg]
    return value


@dataclass
class Call:
    tool: str
    args: dict[str, Any]
    ok: bool
    result: Any = None
    error: Any = None


@dataclass
class ExecutionResult:
    ok: bool = True
    calls: list[Call] = field(default_factory=list)
    errors: list[dict[str, Any]] = field(default_factory=list)
    ctx: dict[str, Any] = field(default_factory=dict)

    def field_errors(self) -> set[str]:
        out: set[str] = set()
        for err in self.errors:
            detail = err.get("detail")
            if isinstance(detail, list):
                out |= {d.get("field", "") for d in detail if isinstance(d, dict)}
        return out

    def called(self, tool: str) -> list[Call]:
        return [c for c in self.calls if c.tool == tool]


class IntegrationExecutor:
    """Interprets an Integration's mappings as a sequence of MCP tool calls."""

    def __init__(self, mcp: McpRuntime) -> None:
        self.mcp = mcp

    async def _call(self, result: ExecutionResult, tool: str, args: dict[str, Any]) -> Any:
        try:
            out = await self.mcp.call(tool, args)
        except McpToolError as exc:
            result.calls.append(Call(tool, args, ok=False, error=exc.detail))
            result.errors.append({"tool": tool, "status": exc.status, "detail": exc.detail})
            result.ok = False
            raise
        result.calls.append(Call(tool, args, ok=True, result=out))
        return out

    def _payload(self, integration: Integration, owner: str, ctx: dict[str, Any]) -> dict[str, Any]:
        payload: dict[str, Any] = {}
        for row in integration.mappings:
            if row.target.split(".")[0] != owner or row.source.startswith(WEBHOOK_TRIGGER_PREFIX):
                continue
            leaf = row.target.split(".", 1)[1]
            payload[leaf] = apply_transform(row.transform, resolve(row.source, ctx), ctx)
        return payload

    def _row(self, integration: Integration, target: str) -> MappingRow | None:
        return next((r for r in integration.mappings if r.target == target), None)

    async def run(self, integration: Integration, event: dict[str, Any]) -> ExecutionResult:
        result = ExecutionResult()
        ctx = result.ctx
        try:
            await self._run(integration, event, ctx, result)
        except McpToolError:
            pass  # already recorded in result.errors
        except (ExecutionError, ValueError, KeyError) as exc:
            result.ok = False
            result.errors.append({"tool": None, "status": None, "detail": str(exc)})
            logger.info("runtime.execution_failed", extra={"integration": integration.id, "error": str(exc)})
        return result

    async def _run(
        self, integration: Integration, event: dict[str, Any], ctx: dict[str, Any], result: ExecutionResult
    ) -> None:
        ops = integration.operations
        if integration.trigger_node == "proc-closedwon":
            opp = await self._call(result, "crm.get_opportunity", {"opportunity_id": event["opportunity_id"]})
            ctx["Opportunity"] = opp
            ctx["Account"] = await self._call(result, "crm.get_account", {"account_id": opp["account_id"]})
        else:
            agreement_id = event["agreement_id"]
            fields = await self._call(result, "esign.get_agreement_fields", {"id": agreement_id})
            signers = await self._call(result, "esign.get_signers", {"id": agreement_id})
            ctx["Agreement"] = {"id": agreement_id, "fields": fields, "signers": signers.get("signers", [])}
            if "crm.match_contact" in ops:
                row = self._row(integration, "Contact.email")
                if row is not None:
                    email = apply_transform(row.transform, resolve(row.source, ctx), ctx)
                    ctx["Contact"] = await self._call(result, "crm.find_contact", {"email": email})
            opp = await self._call(result, "crm.find_opportunity", {"contract_id": agreement_id})
            ctx["Opportunity"] = opp
            ctx["Account"] = await self._call(result, "crm.get_account", {"account_id": opp["account_id"]})
            if any(r.source.startswith(WEBHOOK_TRIGGER_PREFIX) for r in integration.mappings):
                await self._call(
                    result, "crm.update_opportunity", {"opportunity_id": opp["id"], "stage": SIGNED_STAGE}
                )

        external_ref = ctx["Opportunity"]["id"]
        if "tax.calculate" in ops:
            ctx["TaxQuote"] = await self._call(result, "tax.calculate", self._payload(integration, "TaxQuote", ctx))
        if "billing.create_customer" in ops:
            payload = self._payload(integration, "Customer", ctx)
            payload["external_ref"] = external_ref
            if "TaxQuote" in ctx:
                payload["tax_profile"] = ctx["TaxQuote"]["profile_id"]
            ctx["Customer"] = await self._call(result, "billing.create_customer", payload)
        if "billing.create_invoice" in ops:
            payload = self._payload(integration, "Invoice", ctx)
            payload["customer_id"] = ctx["Customer"]["id"]
            payload["external_ref"] = f"{external_ref}-first-invoice"
            ctx["Invoice"] = await self._call(result, "billing.create_invoice", payload)


# ------------------------------------------------------------- test suite
SandboxFactory = Callable[[], tuple[MockApps, McpRuntime]]


def sandbox_factory(esign_spec: dict[str, Any] | None = None) -> SandboxFactory:
    def make() -> tuple[MockApps, McpRuntime]:
        apps = create_mock_apps()
        return apps, build_mcp_runtime(apps, esign_spec or load_fixture_json("esign_openapi_v3_0.json"))

    return make


def _event_for(integration: Integration, record: str) -> dict[str, Any]:
    """record is an opportunity id (opp-00N); webhook integrations use its contract."""
    if integration.trigger_node == "proc-closedwon":
        return {"opportunity_id": record}
    return {"agreement_id": record.replace("opp-", "agr-"), "event": "agreement.completed"}


TestFn = Callable[[Integration, SandboxFactory], Coroutine[Any, Any, TestResult]]


async def _t_creates(integration: Integration, make: SandboxFactory) -> TestResult:
    apps, mcp = make()
    res = await IntegrationExecutor(mcp).run(integration, _event_for(integration, "opp-001"))
    webhook = integration.trigger_node != "proc-closedwon"
    ok = res.ok and len(apps.world.customers) == 1 and (not webhook or len(apps.world.invoices) == 1)
    name = "Signed agreement creates customer + invoice" if webhook else "Creates customer for closed-won deal"
    return TestResult(name=name, passed=ok, detail=None if ok else str(res.errors[:1]))


async def _field_test(
    integration: Integration, make: SandboxFactory, field_name: str, name: str, target: str
) -> TestResult:
    _, mcp = make()
    res = await IntegrationExecutor(mcp).run(integration, _event_for(integration, "opp-002"))
    reached = bool(res.called("billing.create_customer"))
    ok = reached and field_name not in res.field_errors()
    detail = None if ok else (str(res.errors[:1]) if res.errors else "customer step not reached")
    return TestResult(name=name, passed=ok, detail=detail, target_field=target)


async def _t_currency(integration: Integration, make: SandboxFactory) -> TestResult:
    return await _field_test(
        integration, make, "currency_code", "Currency accepted by Billing (ISO 4217)", "Customer.currency_code"
    )


async def _t_terms(integration: Integration, make: SandboxFactory) -> TestResult:
    return await _field_test(
        integration, make, "terms_code", "Payment terms accepted by Billing (terms code)", "Customer.terms_code"
    )


async def _t_tax_shipto(integration: Integration, make: SandboxFactory) -> TestResult:
    apps, mcp = make()
    res = await IntegrationExecutor(mcp).run(integration, _event_for(integration, "opp-002"))
    expected = geocode(apps.world.accounts["acc-002"]["shipping_address"])
    calls = res.called("tax.calculate")
    actual = calls[0].args.get("jurisdiction") if calls else None
    ok = actual == expected
    return TestResult(
        name="Tax uses ship-to when ship-to ≠ bill-to",
        passed=ok,
        detail=None if ok else f"jurisdiction {actual!r}, expected {expected!r} (ship-to)",
        target_field="TaxQuote.jurisdiction",
    )


async def _t_idempotent(integration: Integration, make: SandboxFactory) -> TestResult:
    apps, mcp = make()
    executor = IntegrationExecutor(mcp)
    event = _event_for(integration, "opp-001")
    first, second = await executor.run(integration, event), await executor.run(integration, event)
    ok = first.ok and second.ok and len(apps.world.customers) == 1 and len(apps.world.invoices) <= 1
    return TestResult(name="Idempotent on webhook retry", passed=ok, detail=None if ok else "duplicate records")


async def _t_migration(integration: Integration, make: SandboxFactory) -> TestResult:
    apps, mcp = make()
    contact = apps.world.contacts["con-001"]["email"]
    try:
        env = await mcp.call(
            "esign.send_envelope", {"document_id": "doc-contract-001", "signers": [{"email": contact}]}
        )
        ok = env.get("signers", [{}])[0].get("email") == contact
        detail = None
    except McpToolError as exc:
        ok, detail = False, str(exc)
    return TestResult(name="signer_email migration: send flow still works", passed=ok, detail=detail)


def test_plan(integration: Integration) -> list[TestFn]:
    plan: list[TestFn] = [_t_creates]
    if "billing.create_customer" in integration.operations:
        plan += [_t_currency, _t_terms]
    if "tax.calculate" in integration.operations:
        plan.append(_t_tax_shipto)
    plan.append(_t_idempotent)
    if "signer_email" in integration.migrations:
        plan.append(_t_migration)
    return plan


async def run_synthetic_tests(integration: Integration, make: SandboxFactory | None = None) -> list[TestResult]:
    """Each test runs in its own fresh sandbox world."""
    make = make or sandbox_factory()
    return [await test(integration, make) for test in test_plan(integration)]


HISTORICAL_RECORDS = ["opp-001", "opp-002", "opp-003"]


async def replay_history(integration: Integration, make: SandboxFactory | None = None) -> tuple[int, int]:
    """Replay all historical records in a fresh staging world: ``(replayed, errors)``."""
    make = make or sandbox_factory()
    _, mcp = make()
    executor = IntegrationExecutor(mcp)
    errors = 0
    for record in HISTORICAL_RECORDS:
        res = await executor.run(integration, _event_for(integration, record))
        errors += 0 if res.ok else 1
    return len(HISTORICAL_RECORDS), errors
