"""Renders the reviewable integration code from mapping rows.

The runtime (``runtime.IntegrationExecutor``) executes the same rows, so the code
in the review packet is a faithful rendering of what will run.
"""

from __future__ import annotations

import re

from app.demo.models import Integration, MappingRow

VARS = {"Account": "acct", "Opportunity": "opp", "TaxQuote": "tax", "Contact": "contact", "Customer": "customer"}


def _expr(source: str) -> str:
    owner, *path = source.split(".")
    if owner == "Agreement":
        if path and path[0] == "fields":
            return "fields." + ".".join(path[1:])
        if path and path[0].endswith("[]"):
            return f"{path[0][:-2]}[0]." + ".".join(path[1:])
    return ".".join([VARS.get(owner, owner.lower()), *path])


def _value(row: MappingRow) -> str:
    src = _expr(row.source)
    t = (row.transform or "copy").strip()
    if t == "to_iso4217()":
        return f"to_iso4217({src})"
    if t == "TERMS_MAP lookup":
        return f"TERMS_MAP[{src}]"
    if t in ("geocode", "geocode(ship-to)"):
        return f"geocode({src})"
    if t == "+ tax":
        return f"{src} + tax.amount"
    return src


def _comment(row: MappingRow) -> str:
    if row.origin == "human_edit":
        return "  # human edit (review)"
    if row.origin == "kg_reused":
        return f"  # reused from KG{': ' + row.rule_id if row.rule_id else ''}"
    if row.flag:
        return f"  # ⚠ {row.flag.removeprefix('Low confidence: ')}"
    return ""


def _call(tool: str, rows: list[MappingRow], extra: list[str], assign: str | None) -> list[str]:
    head = f"    {assign + ' = ' if assign else ''}await mcp.{tool}("
    lines = [head]
    for r in rows:
        lines.append(f"        {r.target.split('.', 1)[1]}={_value(r)},{_comment(r)}")
    lines += [f"        {e}," for e in extra]
    lines.append("    )")
    return lines


def render(integration: Integration) -> str:
    fn = re.sub(r"[^a-z0-9]+", "_", integration.name.lower()).strip("_")[:40] or "integration"
    by_owner: dict[str, list[MappingRow]] = {}
    for r in integration.mappings:
        by_owner.setdefault(r.target.split(".")[0], []).append(r)
    ops = integration.operations
    out: list[str] = []
    if integration.trigger_node == "proc-closedwon":
        out += [
            '@on_event("crm.opportunity.closed_won")',
            f"async def {fn}(opp: Opportunity, mcp: MCPClient) -> None:",
            "    acct = await mcp.crm.get_account(opp.account_id)",
        ]
    else:
        event = integration.trigger.split(" ")[0]
        out += [
            f'@on_webhook("esign.{event}")',
            f"async def {fn}(evt: AgreementCompleted, mcp: MCPClient) -> None:",
            "    fields = await mcp.esign.get_agreement_fields(evt.agreement_id)",
            "    signers = (await mcp.esign.get_signers(evt.agreement_id)).signers",
        ]
        if "crm.match_contact" in ops and "Contact" in by_owner:
            out.append(f"    contact = await mcp.crm.find_contact(email={_value(by_owner['Contact'][0])})"
                       + _comment(by_owner["Contact"][0]))
        out += [
            "    opp = await mcp.crm.find_opportunity(contract_id=evt.agreement_id)",
            "    acct = await mcp.crm.get_account(opp.account_id)",
            '    await mcp.crm.update_opportunity(opp.id, stage="Contract Signed")',
        ]
    if "tax.calculate" in ops:
        out += _call("tax.calculate", by_owner.get("TaxQuote", []), [], "tax")
    if "billing.create_customer" in ops:
        extra = ["external_ref=opp.id"] + (["tax_profile=tax.profile_id"] if "tax.calculate" in ops else [])
        out += _call("billing.create_customer", by_owner.get("Customer", []), extra, "customer")
    if "billing.create_invoice" in ops:
        out += _call("billing.create_invoice", by_owner.get("Invoice", []),
                     ["customer_id=customer.id", 'external_ref=f"{opp.id}-first-invoice"'], None)
    if "signer_email" in integration.migrations:
        out += ["", "# Bundled migration: send-for-signature now uses signers[].email (v3);",
                "# Envelope.signer_email is deprecated."]
    return "\n".join(out)
