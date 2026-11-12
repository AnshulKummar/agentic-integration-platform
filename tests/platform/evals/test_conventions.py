"""Org-conventions suite: design invariants and the experiment loop (fake Claude client)."""

from __future__ import annotations

import json
from collections import Counter
from types import SimpleNamespace
from typing import Any

from app.platform.config import PlatformSettings
from app.platform.evals.conventions import (
    CONVENTION_FIELDS,
    TENANT_RULES,
    ArmStep,
    conventions_markdown,
    conventions_sequences,
    run_conventions,
    summarize_conventions,
)


def test_suite_shape_and_balanced_rules() -> None:
    seqs = conventions_sequences()
    assert len(seqs) == 4 and all(len(s) == 5 for s in seqs)
    # every naming style appears once per company and at every step position across companies
    for i in range(5):
        assert len({s[i].vendor_family if hasattr(s[i], "vendor_family") else s[i].tags.vendor_family
                    for s in seqs}) == 4
    for target in CONVENTION_FIELDS:
        counts = Counter(rules[target] for rules in TENANT_RULES.values())
        assert sorted(counts.values()) == [2, 2], f"{target} must be balanced 2/2 across companies"


def test_convention_gold_follows_company_rule_and_target_descriptions_are_neutral() -> None:
    case = conventions_sequences()[1][0]  # company t2
    gold = {g.target: g.source for g in case.gold}
    assert gold["sales_rep"] is not None and gold["customer_name"] is not None
    t1 = {g.target: g.source for g in conventions_sequences()[0][0].gold}
    assert t1["sales_rep"] is None
    descriptions = " ".join((f.description or "").lower() for f in case.target_fields)
    for hint in ("legal", "display", "billing", "primary", "tax", "owner", "region", "industry", "po", "agreement"):
        assert f" {hint}" not in f" {descriptions}", f"target descriptions must not hint the rule ({hint})"


class GoldOracleAfterFirst:
    """Answers the standard fields right; convention fields right only when KG context is present."""

    def __init__(self) -> None:
        self.calls = 0

    def create(self, **kwargs: Any) -> Any:
        self.calls += 1
        prompt: str = kwargs["messages"][0]["content"]
        has_kg = "Knowledge graph" in prompt
        case = next(c for seq in conventions_sequences() for c in seq
                    if f"{c.source.system} (crm) object" in prompt)
        mappings = []
        for g in case.gold:
            if g.target in CONVENTION_FIELDS and not has_kg:
                mappings.append({"target": g.target, "source": None, "transform": None, "confidence": 0.3})
            else:
                mappings.append({"target": g.target, "source": g.source, "transform": None, "confidence": 0.9})
        return SimpleNamespace(
            model=kwargs["model"], stop_reason="end_turn",
            usage=SimpleNamespace(input_tokens=1500, output_tokens=400, cache_read_input_tokens=0,
                                  cache_creation_input_tokens=0),
            content=[SimpleNamespace(type="text", text=json.dumps({"mappings": mappings}))],
        )


def test_experiment_loop_measures_kg_effect_and_isolates_companies() -> None:
    fake = GoldOracleAfterFirst()
    settings = PlatformSettings(env="test", eval_cost_cap_usd=5.0, llm_model="claude-opus-5-5")
    report = run_conventions(settings, replicates=1, client=fake)
    assert fake.calls == 4 * 5 * 2
    rows = report["rows"]
    # step 1 of every company has no KG facts (fresh KG per company and arm: no cross-company leakage)
    assert all(r["kg_facts"] == 0 for r in rows if r["step"] == 1)
    assert all(r["kg_facts"] == 0 for r in rows if r["arm"] == "off")
    summary = report["summary"]
    assert summary["primary_convention_edits_saved_steps2plus"]["mean"] > 0
    assert summary["control_standard_edits_saved_steps2plus"]["mean"] == 0
    assert "Org-conventions experiment" in conventions_markdown(report)


def test_summary_requires_ci_above_zero() -> None:
    rows = [ArmStep(0, "t1", arm, step, f"c{step}", conv, 0, 0, 0.0)
            for step in (1, 2) for arm, conv in (("on", 1), ("off", 1))]
    summary = summarize_conventions(rows, seed=1)
    assert summary["primary_convention_edits_saved_steps2plus"]["supported"] is False
