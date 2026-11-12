"""Render reviewable, sandbox-runnable integration code from mapping rows.

``render_runnable`` turns the ``app.demo.models.MappingRow`` dicts stored on
``IntegrationVersion.mappings`` into a module following the generated-code
contract (``def run(event, tools) -> dict``). It makes exactly the tool calls,
with exactly the payloads, that ``app.demo.runtime.IntegrationExecutor`` makes
for the same rows (tests prove this for the demo's loop-1 and loop-2
integrations), but as straight-line code a PM/engineer can read and approve.

Transforms (``to_iso4217``, ``terms_lookup`` for "TERMS_MAP lookup",
``geocode``, ``plus_tax`` for "+ tax", ``first`` for ``x[]`` paths) are NOT
generated: they come from the trusted prelude (``sandbox/prelude.py``).

Safety: mapping labels come from an LLM and from people, so nothing from a row
is ever pasted into code verbatim. Path segments must be identifiers, keys are
emitted with ``repr()``, comments are stripped of control characters, and the
result is run through the validator before it is returned.

Tool names use the MCP form ``<system>.<operation>`` (``crm.get_account``) that
``app.demo.mcp_tools.generate_tools`` produces.
"""

from __future__ import annotations

import json
import re
import textwrap
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import ValidationError

from app.demo.models import MappingRow
from app.platform.errors import ValidationFailed
from app.platform.sandbox.validate import validate_code

TriggerKind = Literal["crm.opportunity.closed_won", "esign.agreement.completed"]
CLOSED_WON: TriggerKind = "crm.opportunity.closed_won"
AGREEMENT_COMPLETED: TriggerKind = "esign.agreement.completed"

WEBHOOK_PREFIX = "webhook "
SIGNED_STAGE = "Contract Signed"

OP_MATCH_CONTACT = "crm.match_contact"
OP_TAX = "tax.calculate"
OP_CUSTOMER = "billing.create_customer"
OP_INVOICE = "billing.create_invoice"
KNOWN_OPERATIONS = (OP_MATCH_CONTACT, OP_TAX, OP_CUSTOMER, OP_INVOICE)
#: Target business object -> operation that writes it.
OWNER_OPERATION = {"Contact": OP_MATCH_CONTACT, "TaxQuote": OP_TAX, "Customer": OP_CUSTOMER, "Invoice": OP_INVOICE}

#: Business object -> variable name in the generated code.
VARS = {
    "Opportunity": "opp",
    "Account": "acct",
    "Contact": "contact",
    "TaxQuote": "tax",
    "Customer": "customer",
    "Invoice": "invoice",
}
#: transform label -> prelude call template ({v} = source expression, {tax} = tax quote variable).
TRANSFORMS = {
    "copy": "{v}",
    "match first signer": "{v}",
    "to_iso4217()": "to_iso4217({v})",
    "TERMS_MAP lookup": "terms_lookup({v})",
    "geocode": "geocode({v})",
    "geocode(ship-to)": "geocode({v})",
    "+ tax": "plus_tax({v}, {tax})",
}
ORIGIN_NOTES = {
    "kg_reused": "reused from KG",
    "kg_inferred": "inferred from KG",
    "llm_new": "new (LLM)",
    "human_edit": "human edit",
}

_SEGMENT = re.compile(r"^[A-Za-z][A-Za-z0-9_]*(\[\])?$")
_CONTROL = re.compile(r"[\x00-\x1f\x7f  ]")


class CodegenError(ValidationFailed):
    """The mapping rows cannot be rendered into a runnable integration."""

    code = "codegen_failed"


@dataclass(frozen=True)
class _Plan:
    name: str
    trigger: TriggerKind
    trigger_label: str
    source_system: str
    target_system: str
    rows: tuple[MappingRow, ...]
    ops: frozenset[str]

    def owner_rows(self, owner: str) -> list[MappingRow]:
        return [r for r in self.rows if _owner(r.target) == owner and not r.source.startswith(WEBHOOK_PREFIX)]

    @property
    def has_webhook_row(self) -> bool:
        return any(r.source.startswith(WEBHOOK_PREFIX) for r in self.rows)

    @property
    def contact_row(self) -> MappingRow | None:
        return next((r for r in self.rows if r.target == "Contact.email"), None)

    @property
    def finds_contact(self) -> bool:
        return self.trigger == AGREEMENT_COMPLETED and OP_MATCH_CONTACT in self.ops and self.contact_row is not None


def _owner(label: str) -> str:
    return label.split(".", 1)[0].strip()


def _clean(text: object, limit: int = 160) -> str:
    out = _CONTROL.sub(" ", str(text)).strip()
    return out if len(out) <= limit else out[: limit - 3] + "..."


def _q(text: str) -> str:
    """A double-quoted Python string literal (JSON string syntax is a subset of Python's)."""
    return json.dumps(text)


def _coerce_rows(mappings: Sequence[Mapping[str, Any] | MappingRow]) -> tuple[MappingRow, ...]:
    rows: list[MappingRow] = []
    for i, raw in enumerate(mappings):
        if isinstance(raw, MappingRow):
            rows.append(raw)
            continue
        try:
            rows.append(MappingRow.model_validate(dict(raw)))
        except ValidationError as exc:
            raise CodegenError(f"mapping row {i} is malformed: {exc.error_count()} validation error(s)") from exc
    return tuple(rows)


def detect_trigger(trigger: str, rows: Sequence[MappingRow]) -> TriggerKind:
    """Webhook (signed agreement) if any row comes from the webhook or the agreement; closed-won otherwise."""
    if any(r.source.startswith(WEBHOOK_PREFIX) or _owner(r.source) == "Agreement" for r in rows):
        return AGREEMENT_COMPLETED
    low = trigger.lower()
    if any(word in low for word in ("agreement", "webhook", "signed", "signature")):
        return AGREEMENT_COMPLETED
    return CLOSED_WON


def derive_operations(rows: Sequence[MappingRow]) -> list[str]:
    """Operations implied by the mapped target objects (the demo stores them separately)."""
    owners = {_owner(r.target) for r in rows if not r.source.startswith(WEBHOOK_PREFIX)}
    ops = [op for owner, op in OWNER_OPERATION.items() if owner in owners]
    if OP_MATCH_CONTACT in ops and not any(r.target == "Contact.email" for r in rows):
        ops.remove(OP_MATCH_CONTACT)
    return ops


def _plan(
    mappings: Sequence[Mapping[str, Any] | MappingRow],
    *,
    name: str,
    source_system: str,
    target_system: str,
    trigger: str,
    operations: Sequence[str] | None,
    trigger_kind: TriggerKind | None,
) -> _Plan:
    rows = _coerce_rows(mappings)
    ops = list(operations) if operations is not None else derive_operations(rows)
    unknown = [op for op in ops if op not in KNOWN_OPERATIONS]
    if unknown:
        raise CodegenError(f"unsupported operations {unknown}; supported: {list(KNOWN_OPERATIONS)}")
    if OP_INVOICE in ops and OP_CUSTOMER not in ops:
        raise CodegenError(f"{OP_INVOICE} needs {OP_CUSTOMER} (the invoice is created for that customer)")
    return _Plan(
        name=name,
        trigger=trigger_kind or detect_trigger(trigger, rows),
        trigger_label=trigger,
        source_system=source_system,
        target_system=target_system,
        rows=rows,
        ops=frozenset(ops),
    )


def _tool_sequence(plan: _Plan) -> list[str]:
    if plan.trigger == CLOSED_WON:
        tools = ["crm.get_opportunity", "crm.get_account"]
    else:
        tools = ["esign.get_agreement_fields", "esign.get_signers"]
        if plan.finds_contact:
            tools.append("crm.find_contact")
        tools += ["crm.find_opportunity", "crm.get_account"]
        if plan.has_webhook_row:
            tools.append("crm.update_opportunity")
    tools += [op for op in (OP_TAX, OP_CUSTOMER, OP_INVOICE) if op in plan.ops]
    return tools


# --------------------------------------------------------------------- rendering
class _Writer:
    """Emits code while tracking which business objects already exist (the demo's ``ctx``)."""

    def __init__(self, plan: _Plan) -> None:
        self.plan = plan
        self.lines: list[str] = []
        self.available: set[str] = set()

    def emit(self, line: str = "") -> None:
        self.lines.append(line)

    def source_expr(self, row: MappingRow) -> str:
        owner, *path = [seg.strip() for seg in row.source.split(".")]
        for seg in path:
            if not _SEGMENT.match(seg):
                raise CodegenError(f"mapping {row.id}: unsupported path segment {seg!r} in {row.source!r}")
        if owner == "Agreement" and self.plan.trigger == AGREEMENT_COMPLETED:
            if not path:
                raise CodegenError(f"mapping {row.id}: {row.source!r} needs a field of Agreement")
            head, rest = path[0], path[1:]
            if head == "id":
                base = "agreement_id"
            elif head == "fields":
                base = "fields"
            elif head == "signers[]":
                base = f"first(signers, {_q(row.source)})"
            else:
                raise CodegenError(f"mapping {row.id}: Agreement has no {head!r} (id, fields, signers[])")
            path = rest
        elif owner in VARS and owner in self.available:
            base = VARS[owner]
        else:
            raise CodegenError(f"mapping {row.id}: {owner} is not available when {row.target} is computed")
        expr = base
        for seg in path:
            if seg.endswith("[]"):
                expr = f"first({expr}[{_q(seg[:-2])}], {_q(row.source)})"
            else:
                expr = f"{expr}[{_q(seg)}]"
        return expr

    def value_expr(self, row: MappingRow) -> str:
        transform = (row.transform or "copy").strip()
        template = TRANSFORMS.get(transform)
        if template is None:
            raise CodegenError(f"mapping {row.id}: unknown transform {transform!r}; supported: {sorted(TRANSFORMS)}")
        tax = VARS["TaxQuote"] if "TaxQuote" in self.available else "None"
        return template.format(v=self.source_expr(row), tax=tax)

    @staticmethod
    def comment(row: MappingRow) -> str:
        parts = [_clean(row.id, 60), ORIGIN_NOTES.get(row.origin, _clean(row.origin, 30))]
        if row.rule_id:
            parts.append(f"rule {_clean(row.rule_id, 60)}")
        parts.append(f"conf {row.confidence:.2f}")
        if row.flag:
            parts.append(f"FLAG: {_clean(row.flag)}")
        return "  # " + " | ".join(parts)

    def call(self, owner: str, tool: str, extra: list[tuple[str, str]]) -> None:
        var = VARS[owner]
        rows = self.plan.owner_rows(owner)
        self.emit(f"    {var} = tools.call({_q(tool)}, {{")
        for row in rows:
            leaf = row.target.split(".", 1)[1].strip() if "." in row.target else ""
            if not leaf:
                raise CodegenError(f"mapping {row.id}: target {row.target!r} has no field")
            self.emit(f"        {_q(leaf)}: {self.value_expr(row)},{self.comment(row)}")
        for key, expr in extra:
            self.emit(f"        {_q(key)}: {expr},")
        self.emit("    })")
        self.available.add(owner)


def _header(plan: _Plan, tools: list[str]) -> list[str]:
    rows = len(plan.rows)
    lines = [
        f"# Integration: {_clean(plan.name)}",
        f"# Trigger: {plan.trigger} ({_clean(plan.trigger_label) or 'event'})",
        f"# Flow: {_clean(plan.source_system, 80)} -> {_clean(plan.target_system, 80)}",
        f"# Generated by the PMAIP platform from {rows} mapping row{'s' if rows != 1 else ''}.",
        "# Edit the mappings and regenerate; do not edit this code by hand.",
        *textwrap.wrap(", ".join(tools), 100, initial_indent="# Tools: ", subsequent_indent="#        "),
        "# to_iso4217 / terms_lookup / geocode / plus_tax / first come from the trusted sandbox prelude.",
        "",
    ]
    if plan.trigger == AGREEMENT_COMPLETED and plan.has_webhook_row:
        lines += [f"SIGNED_STAGE = {_q(SIGNED_STAGE)}", ""]
    return lines + [""]


def _render(plan: _Plan) -> str:
    w = _Writer(plan)
    w.lines += _header(plan, _tool_sequence(plan))
    w.emit("def run(event, tools):")
    if plan.trigger == CLOSED_WON:
        w.emit('    opp = tools.call("crm.get_opportunity", {"opportunity_id": event["opportunity_id"]})')
        w.emit('    acct = tools.call("crm.get_account", {"account_id": opp["account_id"]})')
        w.available |= {"Opportunity", "Account"}
    else:
        w.emit('    agreement_id = event["agreement_id"]')
        w.emit('    fields = tools.call("esign.get_agreement_fields", {"id": agreement_id})')
        w.emit('    signers = tools.call("esign.get_signers", {"id": agreement_id}).get("signers", [])')
        contact_row = plan.contact_row
        if plan.finds_contact and contact_row is not None:
            w.emit('    contact = tools.call("crm.find_contact", {')
            w.emit(f'        "email": {w.value_expr(contact_row)},{w.comment(contact_row)}')
            w.emit("    })")
            w.available.add("Contact")
        w.emit('    opp = tools.call("crm.find_opportunity", {"contract_id": agreement_id})')
        w.emit('    acct = tools.call("crm.get_account", {"account_id": opp["account_id"]})')
        w.available |= {"Opportunity", "Account"}
        trigger_row = next((r for r in plan.rows if r.source.startswith(WEBHOOK_PREFIX)), None)
        if trigger_row is not None:
            w.emit(
                '    tools.call("crm.update_opportunity", {"opportunity_id": opp["id"], "stage": SIGNED_STAGE})'
                + w.comment(trigger_row)
            )
    w.emit('    external_ref = opp["id"]')
    result = ['"opportunity_id": external_ref']
    if OP_TAX in plan.ops:
        w.emit()
        w.call("TaxQuote", OP_TAX, [])
        result.append('"tax_profile": tax.get("profile_id")')
    if OP_CUSTOMER in plan.ops:
        w.emit()
        extra = [("external_ref", "external_ref")]
        if OP_TAX in plan.ops:
            extra.append(("tax_profile", 'tax["profile_id"]'))
        w.call("Customer", OP_CUSTOMER, extra)
        result.append('"customer_id": customer.get("id")')
    if OP_INVOICE in plan.ops:
        w.emit()
        extra = [("customer_id", 'customer["id"]'), ("external_ref", 'external_ref + "-first-invoice"')]
        w.call("Invoice", OP_INVOICE, extra)
        result.append('"invoice_id": invoice.get("id")')
    w.emit()
    w.emit("    return {")
    w.lines += [f"        {item}," for item in result]
    w.emit("    }")
    return "\n".join(w.lines) + "\n"


def render_runnable(
    mappings: Sequence[Mapping[str, Any] | MappingRow],
    *,
    name: str,
    source_system: str,
    target_system: str,
    trigger: str,
    operations: Sequence[str] | None = None,
    trigger_kind: TriggerKind | None = None,
) -> str:
    """Generated module source (validated). Raises :class:`CodegenError` for unrenderable rows.

    ``operations`` (demo names such as ``"billing.create_customer"``) default to
    those implied by the mapped target objects; ``trigger_kind`` defaults to
    :func:`detect_trigger`.
    """
    plan = _plan(
        mappings,
        name=name,
        source_system=source_system,
        target_system=target_system,
        trigger=trigger,
        operations=operations,
        trigger_kind=trigger_kind,
    )
    code = _render(plan)
    validate_code(code)
    return code


def required_tools(
    mappings: Sequence[Mapping[str, Any] | MappingRow],
    *,
    trigger: str,
    operations: Sequence[str] | None = None,
    trigger_kind: TriggerKind | None = None,
) -> list[str]:
    """The tools the rendered code calls, in call order: store as ``IntegrationVersion.allowed_tools``."""
    plan = _plan(
        mappings,
        name="",
        source_system="",
        target_system="",
        trigger=trigger,
        operations=operations,
        trigger_kind=trigger_kind,
    )
    return _tool_sequence(plan)
