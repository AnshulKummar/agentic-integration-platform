"""Integration governance: dual approval (PM + Eng) and risk checks.

Policy (paper, "Findings: integration governance"):
* Product approves business fit, engineering approves code; both are required.
* Unknown roles are rejected; approvals need a green test suite.
* Nothing deploys before both approvals, with failing tests, or with a high risk.
"""

from __future__ import annotations

import logging
from typing import Any

from app.demo.kg_store import KnowledgeGraph
from app.demo.models import Approval, Integration, RiskCheck

logger = logging.getLogger(__name__)

REQUIRED_ROLES: tuple[str, ...] = ("pm", "eng")
TOOL_BUDGET = 6
MAX_CALLS_PER_EVENT = 20
APPROVABLE_STATUSES = {"in_review", "approved"}


class GovernanceError(PermissionError):
    """A governance rule blocked the action. ``code`` is machine-readable."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def approve(integration: Integration, role: str, by: str, comment: str = "") -> Approval:
    if role not in REQUIRED_ROLES:
        raise GovernanceError("unknown_role", f"role {role!r} cannot approve; expected one of {REQUIRED_ROLES}")
    if not by.strip():
        raise GovernanceError("missing_approver", "an approval must name the approver")
    if integration.status not in APPROVABLE_STATUSES:
        raise GovernanceError(
            "not_in_review", f"{integration.id} is {integration.status!r}; only integrations in review can be approved"
        )
    failing = [t.name for t in integration.tests if not t.passed]
    if not integration.tests or failing:
        raise GovernanceError(
            "tests_failing",
            f"{integration.id} cannot be approved: "
            + (f"{len(failing)} failing test(s): {', '.join(failing)}" if failing else "no test results"),
        )
    approval = Approval(role=role, by=by, comment=comment)  # type: ignore[arg-type]
    setattr(integration.approvals, role, approval)
    if all(getattr(integration.approvals, r) is not None for r in REQUIRED_ROLES):
        integration.status = "approved"
    logger.info("governance.approved", extra={"integration": integration.id, "role": role, "by": by})
    return approval


def deploy_blockers(integration: Integration) -> list[str]:
    blockers: list[str] = []
    for role in REQUIRED_ROLES:
        if getattr(integration.approvals, role) is None:
            blockers.append(f"missing {role} approval")
    if not integration.tests or any(not t.passed for t in integration.tests):
        blockers.append("test suite not green")
    blockers += [f"high risk: {r.label}" for r in integration.risks if r.level == "high"]
    if integration.status not in ("approved",):
        blockers.append(f"status is {integration.status!r}, expected 'approved'")
    return blockers


def ensure_deployable(integration: Integration) -> None:
    blockers = deploy_blockers(integration)
    if blockers:
        raise GovernanceError("not_deployable", f"{integration.id} cannot deploy: " + "; ".join(blockers))


# ------------------------------------------------------------------ risks
def deprecated_nodes(kg: KnowledgeGraph) -> dict[str, str]:
    """``{deprecated_node_id: replacement_node_id}`` from DEPRECATES edges."""
    return {e.target: e.source for e in kg.edges("DEPRECATES")}


def assess_risks(
    integration: Integration,
    kg: KnowledgeGraph,
    field_catalog: dict[str, dict[str, Any]],
    context_integrations: list[dict[str, Any]],
) -> list[RiskCheck]:
    risks: list[RiskCheck] = []

    # 1. Data access boundary: which objects/fields are read, any PII?
    sources = sorted({r.source for r in integration.mappings if "." in r.source and not r.source.startswith("webhook")})
    pii = [s for s in sources if field_catalog.get(s, {}).get("pii")]
    pii += [r.target for r in integration.mappings if field_catalog.get(r.target, {}).get("pii") and r.target not in pii]
    owners = sorted({s.split(".")[0] for s in sources})
    if pii:
        risks.append(
            RiskCheck(
                label="Data access boundary",
                level="medium",
                note=f"Reads PII ({', '.join(sorted(set(pii)))}). Scoped to those fields only; no document bodies.",
            )
        )
    else:
        risks.append(
            RiskCheck(label="Data access boundary", level="low", note=f"{' and '.join(owners) or 'No'} fields only.")
        )

    # 2. Identity propagation.
    if integration.service_principal:
        risks.append(
            RiskCheck(
                label="Identity propagation",
                level="low",
                note=f'Runs as service principal "{integration.service_principal}"; actions attributed in audit log.',
            )
        )
    else:
        risks.append(RiskCheck(label="Identity propagation", level="high", note="No service principal assigned."))

    # 3. Tool budget.
    n_tools = len(integration.tools_used)
    over = n_tools > TOOL_BUDGET or integration.max_calls_per_event > MAX_CALLS_PER_EVENT
    risks.append(
        RiskCheck(
            label="Tool budget",
            level="high" if over else "low",
            note=f"{n_tools} MCP tools, max {integration.max_calls_per_event} calls per event, 30s timeout"
            + (f" (budget {TOOL_BUDGET} tools / {MAX_CALLS_PER_EVENT} calls exceeded)." if over else "."),
        )
    )

    # 4. Deprecated-field usage (this integration, and live integrations it touches).
    deprecated = deprecated_nodes(kg)
    labels = {nid: kg.require_node(nid).label for nid in deprecated}
    own_use = [
        labels[nid] for nid in deprecated if any(labels[nid] in (r.source, r.target) for r in integration.mappings)
    ]
    live_use = [
        (ci, labels[nid])
        for ci in context_integrations
        if ci.get("status") == "live"
        for nid in deprecated
        if nid in ci.get("uses", [])
    ]
    if own_use:
        risks.append(
            RiskCheck(label="Deprecated field", level="high", note=f"Uses deprecated {', '.join(own_use)}.")
        )
    elif live_use:
        ci, label = live_use[0]
        bundled = any(label.split(".")[-1] in m for m in integration.migrations)
        replacement = kg.require_node(deprecated[next(n for n in deprecated if labels[n] == label)]).label
        risks.append(
            RiskCheck(
                label="Deprecated field",
                level="medium",
                note=f'Existing "{ci["name"]}" integration uses {label} (replaced by {replacement}). '
                + ("Migration bundled." if bundled else "Migration NOT bundled."),
            )
        )
    else:
        risks.append(RiskCheck(label="Deprecated field", level="low", note="No deprecated fields used."))
    return risks
