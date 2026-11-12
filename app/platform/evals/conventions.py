"""Org-conventions suite: does a KG of reviewed decisions teach company-specific knowledge?

PRE-REGISTERED DESIGN (committed before any live run; do not edit after results)
================================================================================
Claim under test (narrower than "KG improves mapping"): a KG of human-reviewed
mapping decisions lets the agent apply a company's *private conventions* to its
next integrations, where the right answer cannot be inferred from field names.

Setup
- 4 fictional companies (tenants). Each has one hub ERP ``Customer`` object (the
  target, fixed) and integrates 5 different CRM-style source systems over time
  (5 naming styles, rotated so every style appears at every step position).
- Target fields: 6 STANDARD (control: unambiguous from names) and 6 CONVENTION
  fields whose correct source depends only on the company's rule. Target field
  descriptions are neutral; source descriptions are truthful.
- Each convention is binary and the 4 companies are balanced (each option is
  used by exactly 2 companies), so no single prior/default is right for all:

  | target field  | rule options                                   | t1      | t2       | t3      | t4       |
  |---------------|------------------------------------------------|---------|----------|---------|----------|
  | customer_name | legal registered name / display name           | legal   | display  | legal   | display  |
  | contact_email | billing email / primary contact email          | billing | primary  | primary | billing  |
  | annual_value  | ARR excluding tax / including tax              | net     | gross    | gross   | net      |
  | sales_rep     | leave empty (ERP assigns) / record owner       | empty   | owner    | empty   | owner    |
  | ref2          | master agreement id / customer PO number       | msa     | po       | po      | msa      |
  | udf_07        | sales region / industry                        | region  | industry | region  | industry |

- Standard fields: external_id <- source id, currency, country, created_on,
  phone, website (no transforms anywhere; transforms are not scored as edits).

Protocol
- Arms: ``live_llm_kg`` with learning ON vs OFF (OFF = empty KG = identical
  prompt to plain live_llm). One fresh KG per company per arm (tenant isolation).
- After each integration the KG learns the gold (reviewed) decisions.
- Replicates: R independent runs (LLM sampling noise); step 1 has identical
  prompts in both arms and estimates that noise.

Pre-registered outcomes
- Primary: mean CONVENTION-field edits per integration at steps 2-5, OFF minus ON,
  with a bootstrap 95% CI over (company x replicate) sequences. The claim is
  supported only if the CI lower bound is > 0.
- Control: STANDARD-field edits should not differ between arms.
- Secondary: per-step curve, per-rule breakdown, cost.
"""

from __future__ import annotations

import random
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from app.platform.config import PlatformSettings
from app.platform.evals.dataset import CaseTags, EvalCase, EvalField, GoldMapping, ObjectRef
from app.platform.evals.metrics import CI_LEVEL, N_BOOTSTRAP, percentile_ci, score_case
from app.platform.evals.predictors import CostCapReached, CostTracker, LiveLLMKGPredictor, MessagesClient

# canonical key -> (field description, type)
CANONICAL: dict[str, tuple[str, str]] = {
    "id": ("Record id", "string"),
    "legal_name": ("Legal registered name of the company", "string"),
    "display_name": ("Display name shown in the CRM", "string"),
    "billing_email": ("Billing / accounts-payable email address", "string"),
    "primary_email": ("Email address of the primary contact", "string"),
    "arr_net": ("Annual recurring revenue, excluding tax", "number"),
    "arr_gross": ("Annual recurring revenue, including tax", "number"),
    "owner": ("Record owner (CRM user id)", "string"),
    "msa_id": ("Master agreement id", "string"),
    "po_number": ("Customer purchase order number", "string"),
    "region": ("Sales region / territory", "string"),
    "industry": ("Industry", "string"),
    "currency": ("Currency (ISO 4217 code)", "string"),
    "country": ("Billing country (ISO 3166 alpha-2)", "string"),
    "created": ("Created date (ISO 8601)", "string"),
    "phone": ("Main phone number", "string"),
    "website": ("Company website URL", "string"),
    "state": ("Billing state / province", "string"),
    "employees": ("Number of employees", "integer"),
}

#: Naming styles of the 5 source systems (field names per canonical key).
STYLES: dict[str, dict[str, str]] = {
    "sf": {
        "id": "Id", "legal_name": "Registered_Legal_Name__c", "display_name": "Name",
        "billing_email": "Billing_Email__c", "primary_email": "Primary_Contact_Email__c",
        "arr_net": "ARR_Net__c", "arr_gross": "ARR_Gross__c", "owner": "OwnerId",
        "msa_id": "Master_Agreement_Id__c", "po_number": "Customer_PO__c", "region": "Sales_Region__c",
        "industry": "Industry", "currency": "CurrencyIsoCode", "country": "BillingCountryCode",
        "created": "CreatedDate", "phone": "Phone", "website": "Website", "state": "BillingState",
        "employees": "NumberOfEmployees",
    },
    "hs": {
        "id": "hs_object_id", "legal_name": "legal_entity_name", "display_name": "company_name",
        "billing_email": "billing_email", "primary_email": "primary_contact_email",
        "arr_net": "arr_excl_tax", "arr_gross": "arr_incl_tax", "owner": "hubspot_owner_id",
        "msa_id": "agreement_number", "po_number": "po_number", "region": "territory", "industry": "industry",
        "currency": "currency_code", "country": "country", "created": "createdate", "phone": "phone",
        "website": "domain", "state": "state", "employees": "numberofemployees",
    },
    "dyn": {
        "id": "accountid", "legal_name": "new_registeredname", "display_name": "name",
        "billing_email": "new_billingemail", "primary_email": "emailaddress1",
        "arr_net": "new_arrnet", "arr_gross": "new_arrgross", "owner": "ownerid", "msa_id": "new_msaid",
        "po_number": "new_ponumber", "region": "new_salesregion", "industry": "industrycode",
        "currency": "new_currencycode", "country": "address1_countrycode", "created": "createdon",
        "phone": "telephone1", "website": "websiteurl", "state": "address1_stateorprovince",
        "employees": "numberofemployees",
    },
    "zoho": {
        "id": "Record_Id", "legal_name": "Legal_Name", "display_name": "Account_Name",
        "billing_email": "Billing_Email", "primary_email": "Email", "arr_net": "ARR_Excl_Tax",
        "arr_gross": "ARR_Incl_Tax", "owner": "Account_Owner", "msa_id": "Contract_Number",
        "po_number": "PO_Number", "region": "Region", "industry": "Industry", "currency": "Currency",
        "country": "Billing_Country_Code", "created": "Created_Time", "phone": "Phone", "website": "Website",
        "state": "Billing_State", "employees": "Employees",
    },
    "pd": {
        "id": "orgId", "legal_name": "registeredName", "display_name": "displayName",
        "billing_email": "invoiceEmail", "primary_email": "mainContactEmail", "arr_net": "annualValueNet",
        "arr_gross": "annualValueGross", "owner": "ownerUserId", "msa_id": "msaReference",
        "po_number": "purchaseOrderRef", "region": "salesTerritory", "industry": "sector",
        "currency": "currencyCode", "country": "countryCode", "created": "addTime", "phone": "phoneNumber",
        "website": "webUrl", "state": "stateRegion", "employees": "headcount",
    },
}
STYLE_ORDER = ("sf", "hs", "dyn", "zoho", "pd")

#: Target ERP Customer: field -> neutral description.
TARGET_FIELDS: dict[str, str] = {
    "external_id": "External id of the record in the source system",
    "currency": "Currency",
    "country": "Country",
    "created_on": "Created date",
    "phone": "Phone",
    "website": "Website",
    "customer_name": "Customer name",
    "contact_email": "Contact email",
    "annual_value": "Annual value",
    "sales_rep": "Sales representative",
    "ref2": "Reference 2",
    "udf_07": "User-defined field 07",
}
STANDARD_GOLD: dict[str, str] = {
    "external_id": "id", "currency": "currency", "country": "country", "created_on": "created",
    "phone": "phone", "website": "website",
}
CONVENTION_FIELDS = ("customer_name", "contact_email", "annual_value", "sales_rep", "ref2", "udf_07")

#: Company rules: convention target -> canonical source key (None = leave empty).
TENANT_RULES: dict[str, dict[str, str | None]] = {
    "t1": {"customer_name": "legal_name", "contact_email": "billing_email", "annual_value": "arr_net",
           "sales_rep": None, "ref2": "msa_id", "udf_07": "region"},
    "t2": {"customer_name": "display_name", "contact_email": "primary_email", "annual_value": "arr_gross",
           "sales_rep": "owner", "ref2": "po_number", "udf_07": "industry"},
    "t3": {"customer_name": "legal_name", "contact_email": "primary_email", "annual_value": "arr_gross",
           "sales_rep": None, "ref2": "po_number", "udf_07": "region"},
    "t4": {"customer_name": "display_name", "contact_email": "billing_email", "annual_value": "arr_net",
           "sales_rep": "owner", "ref2": "msa_id", "udf_07": "industry"},
}


def _case(tenant: str, step: int, style: str) -> EvalCase:
    names = STYLES[style]
    rules = TENANT_RULES[tenant]
    source_fields = tuple(
        EvalField(path=names[key], type=CANONICAL[key][1], description=CANONICAL[key][0])  # type: ignore[arg-type]
        for key in CANONICAL
    )
    target_fields = tuple(EvalField(path=t, description=d) for t, d in TARGET_FIELDS.items())
    gold: list[GoldMapping] = [GoldMapping(target=t, source=names[k]) for t, k in STANDARD_GOLD.items()]
    for target in CONVENTION_FIELDS:
        key = rules[target]
        gold.append(GoldMapping(target=target, source=names[key] if key else None))
    return EvalCase(
        id=f"conv-{tenant}-s{step}-{style}",
        suite="conventions",
        source=ObjectRef(system=f"{tenant}-{style}", kind="crm", object="Account"),
        target=ObjectRef(system=f"{tenant}-erp", kind="erp", object="Customer"),
        source_fields=source_fields,
        target_fields=target_fields,
        gold=tuple(gold),
        tags=CaseTags(difficulty="hard", category="customer", vendor_family=f"{style}->erp"),
        sequence=f"conv-{tenant}",
        step=step,
    )


def conventions_sequences() -> list[list[EvalCase]]:
    """One 5-step history per company; style order rotated per company."""
    out: list[list[EvalCase]] = []
    for i, tenant in enumerate(TENANT_RULES):
        order = STYLE_ORDER[i:] + STYLE_ORDER[:i]
        out.append([_case(tenant, step + 1, style) for step, style in enumerate(order)])
    return out


# ------------------------------------------------------------------ experiment
@dataclass
class ArmStep:
    replicate: int
    tenant: str
    arm: str  # "on" | "off"
    step: int
    case_id: str
    convention_edits: int
    standard_edits: int
    kg_facts: int
    cost_usd: float
    wrong_fields: list[str] = field(default_factory=list)


def _tenant_of(case: EvalCase) -> str:
    return case.source.system.split("-", 1)[0]


def run_conventions(settings: PlatformSettings, *, replicates: int = 3, seed: int = 1234,
                    client: MessagesClient | None = None) -> dict[str, Any]:
    cost = CostTracker(cap_usd=settings.eval_cost_cap_usd)
    rows: list[ArmStep] = []
    stopped: str | None = None
    try:
        for rep in range(replicates):
            for seq in conventions_sequences():
                for arm in ("on", "off"):
                    predictor = LiveLLMKGPredictor(settings, client=client, cost=cost, learning=arm == "on")
                    for case in seq:
                        before = cost.spent_usd
                        pred = predictor.predict(case)
                        score = score_case(case, pred)
                        predictor.observe(case, pred)
                        bad = [o.target for o in score.outcomes if o.outcome in ("wrong", "missing", "spurious")]
                        rows.append(ArmStep(
                            replicate=rep, tenant=_tenant_of(case), arm=arm, step=case.step or 0, case_id=case.id,
                            convention_edits=sum(t in CONVENTION_FIELDS for t in bad),
                            standard_edits=sum(t not in CONVENTION_FIELDS for t in bad),
                            kg_facts=int(pred.meta.get("kg_facts", 0)), cost_usd=round(cost.spent_usd - before, 6),
                            wrong_fields=bad,
                        ))
    except CostCapReached as exc:
        stopped = f"cost cap reached: {exc}"
    return {
        "kind": "conventions", "seed": seed, "replicates": replicates, "model": settings.llm_model,
        "stopped_early": stopped, "summary": summarize_conventions(rows, seed=seed),
        "llm": {"calls": cost.calls, "input_tokens": cost.input_tokens, "output_tokens": cost.output_tokens,
                "cost_usd": round(cost.spent_usd, 4), "cost_cap_usd": cost.cap_usd},
        "rows": [r.__dict__ for r in rows],
    }


def _mean(xs: Sequence[float]) -> float:
    return sum(xs) / len(xs) if xs else 0.0


def summarize_conventions(rows: Sequence[ArmStep], *, seed: int, n_bootstrap: int = N_BOOTSTRAP) -> dict[str, Any]:
    by = {(r.replicate, r.tenant, r.arm, r.step): r for r in rows}
    seqs = sorted({(r.replicate, r.tenant) for r in rows})
    steps = sorted({r.step for r in rows})

    def per_seq_diff(kind: str, step_filter: Sequence[int]) -> list[float]:
        out = []
        for rep, tenant in seqs:
            on = [getattr(by[(rep, tenant, "on", s)], kind) for s in step_filter if (rep, tenant, "on", s) in by]
            off = [getattr(by[(rep, tenant, "off", s)], kind) for s in step_filter if (rep, tenant, "off", s) in by]
            if on and off and len(on) == len(off):
                out.append(_mean(off) - _mean(on))
        return out

    rng = random.Random(seed)

    def boot(xs: list[float]) -> list[float]:
        if not xs:
            return [0.0, 0.0]
        means = [_mean([rng.choice(xs) for _ in xs]) for _ in range(n_bootstrap)]
        lo, hi = percentile_ci(means, CI_LEVEL)
        return [round(lo, 4), round(hi, 4)]

    later = [s for s in steps if s >= 2]
    primary = per_seq_diff("convention_edits", later)
    control = per_seq_diff("standard_edits", later)
    step1 = per_seq_diff("convention_edits", [1])
    curve = []
    for s in steps:
        on = [r.convention_edits for r in rows if r.arm == "on" and r.step == s]
        off = [r.convention_edits for r in rows if r.arm == "off" and r.step == s]
        curve.append({"step": s, "n": len(on), "convention_edits_on": round(_mean(on), 3),
                      "convention_edits_off": round(_mean(off), 3),
                      "standard_edits_on": round(_mean([r.standard_edits for r in rows
                                                         if r.arm == "on" and r.step == s]), 3),
                      "standard_edits_off": round(_mean([r.standard_edits for r in rows
                                                          if r.arm == "off" and r.step == s]), 3)})
    per_rule: dict[str, dict[str, float]] = {}
    for target in CONVENTION_FIELDS:
        for arm in ("on", "off"):
            later_rows = [r for r in rows if r.arm == arm and r.step >= 2]
            if later_rows:
                acc = 1 - _mean([target in r.wrong_fields for r in later_rows])
                per_rule.setdefault(target, {})[f"accuracy_{arm}_steps2plus"] = round(acc, 3)
    return {
        "primary_convention_edits_saved_steps2plus": {"mean": round(_mean(primary), 4), "ci95": boot(primary),
                                                      "n_sequences": len(primary),
                                                      "supported": bool(primary) and boot(primary)[0] > 0},
        "control_standard_edits_saved_steps2plus": {"mean": round(_mean(control), 4), "ci95": boot(control)},
        "noise_step1_identical_prompts": {"mean": round(_mean(step1), 4), "ci95": boot(step1)},
        "curve": curve,
        "per_rule": per_rule,
    }


def conventions_markdown(report: dict[str, Any]) -> str:
    s = report["summary"]
    p, c, n = (s["primary_convention_edits_saved_steps2plus"], s["control_standard_edits_saved_steps2plus"],
               s["noise_step1_identical_prompts"])
    lines = [
        f"## Org-conventions experiment ({report['model']}, {report['replicates']} replicates, seed {report['seed']})",
        "",
        f"**Primary** (convention edits saved per integration, steps 2+): {p['mean']:+.2f} "
        f"[{p['ci95'][0]:+.2f}, {p['ci95'][1]:+.2f}] over {p['n_sequences']} sequences -> "
        f"{'SUPPORTED' if p['supported'] else 'NOT supported'}",
        f"**Control** (standard-field edits saved, steps 2+): {c['mean']:+.2f} "
        f"[{c['ci95'][0]:+.2f}, {c['ci95'][1]:+.2f}]",
        f"**Noise** (step 1, identical prompts): {n['mean']:+.2f} [{n['ci95'][0]:+.2f}, {n['ci95'][1]:+.2f}]",
        "",
        "| step | n | convention edits ON | convention edits OFF | standard edits ON | standard edits OFF |",
        "|---|---|---|---|---|---|",
    ]
    for row in s["curve"]:
        lines.append(f"| {row['step']} | {row['n']} | {row['convention_edits_on']:.2f} | "
                     f"{row['convention_edits_off']:.2f} | {row['standard_edits_on']:.2f} | "
                     f"{row['standard_edits_off']:.2f} |")
    lines += ["", "| rule (target) | accuracy ON (steps 2+) | accuracy OFF (steps 2+) |", "|---|---|---|"]
    for target, acc in s["per_rule"].items():
        lines.append(f"| {target} | {acc.get('accuracy_on_steps2plus', 0):.0%} | "
                     f"{acc.get('accuracy_off_steps2plus', 0):.0%} |")
    llm = report["llm"]
    lines += ["", f"LLM: {llm['calls']} calls, ${llm['cost_usd']:.2f} of ${llm['cost_cap_usd']:.2f} cap"]
    if report.get("stopped_early"):
        lines.append(f"**Stopped early:** {report['stopped_early']}")
    return "\n".join(lines)
