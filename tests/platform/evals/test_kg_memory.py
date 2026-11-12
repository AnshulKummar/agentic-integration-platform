"""KG of reviewed mappings + the live_llm_kg predictor (fake Claude client; no network)."""

from __future__ import annotations

import json
from collections.abc import Callable
from types import SimpleNamespace
from typing import Any

import pytest

from app.platform.config import PlatformSettings
from app.platform.evals.dataset import EvalCase, ObjectRef
from app.platform.evals.kg_memory import KGMappingMemory, render_context
from app.platform.evals.learning_curve import run_sequence
from app.platform.evals.predictors import CostTracker, LiveLLMKGPredictor, LiveLLMPredictor


class EchoMessages:
    """Records prompts; maps a target to the source the KG context names for the same target path."""

    def __init__(self) -> None:
        self.prompts: list[str] = []

    def create(self, **kwargs: Any) -> Any:
        prompt = kwargs["messages"][0]["content"]
        self.prompts.append(prompt)
        mappings = []
        for line in prompt.splitlines():
            if line.startswith("- ") and " <- " in line and "(no source" not in line:
                target, source = line[2:].split(" <- ")
                tpath = target.split(".", 2)[2]
                spath = source.split(" [")[0].split(".", 2)[2]
                mappings.append({"target": tpath, "source": spath, "transform": None, "confidence": 0.9})
        return SimpleNamespace(
            model=kwargs["model"], stop_reason="end_turn",
            usage=SimpleNamespace(input_tokens=1000, output_tokens=200, cache_read_input_tokens=0,
                                  cache_creation_input_tokens=0),
            content=[SimpleNamespace(type="text", text=json.dumps({"mappings": mappings}))],
        )


def _settings() -> PlatformSettings:
    return PlatformSettings(env="test", eval_cost_cap_usd=5.0, llm_model="claude-opus-5-5")


def _with_systems(case: EvalCase, src: str, tgt: str, case_id: str) -> EvalCase:
    return case.model_copy(update={
        "id": case_id,
        "source": ObjectRef(system=src, kind=case.source.kind, object=case.source.object),
        "target": ObjectRef(system=tgt, kind=case.target.kind, object=case.target.object),
    })


@pytest.fixture
def two_cases(make_case: Callable[..., EvalCase]) -> tuple[EvalCase, EvalCase]:
    gold = [("name", "Name", None), ("currency", "CurrencyIsoCode", "currency_name_to_code"), ("id", None, None)]
    first = _with_systems(make_case(gold, case_id="a"), "crm-a", "billing-y", "a")
    second = _with_systems(make_case(gold, case_id="b"), "crm-b", "billing-y", "b")
    return first, second


def test_memory_learns_reviewed_facts_including_no_source(two_cases: tuple[EvalCase, EvalCase]) -> None:
    first, second = two_cases
    memory = KGMappingMemory()
    assert memory.retrieve(second) == []
    assert memory.learn(first) == 3
    facts = memory.retrieve(second)
    rendered = render_context(facts)
    assert "billing-y.Customer.name <- crm-a.Account.Name" in rendered
    assert "[transform: currency_name_to_code]" in rendered
    assert "billing-y.Customer.id <- (no source field; reviewed)" in rendered
    # Stored as a real KG: typed field nodes + MAPS_TO edges.
    assert memory.kg.counts()["edges"] == 2


def test_memory_never_contains_the_case_being_predicted(two_cases: tuple[EvalCase, EvalCase]) -> None:
    first, _ = two_cases
    memory = KGMappingMemory()
    assert memory.retrieve(first) == []  # nothing learned yet, including first itself


def test_memory_prefers_same_integration_type(make_case: Callable[..., EvalCase]) -> None:
    crm_billing = make_case([("name", "Name", None)], case_id="cb")
    esign = crm_billing.model_copy(update={
        "id": "eb", "source": ObjectRef(system="esign-z", kind="esign", object="Envelope"),
    })
    memory = KGMappingMemory()
    memory.learn(esign)
    memory.learn(crm_billing)
    facts = memory.retrieve(crm_billing)
    assert {f.source_kind for f in facts} == {"crm"}  # same-type facts exist, so other types are left out


def test_off_arm_prompt_is_exactly_live_llm(two_cases: tuple[EvalCase, EvalCase]) -> None:
    first, second = two_cases
    off, plain = EchoMessages(), EchoMessages()
    kg_off = LiveLLMKGPredictor(_settings(), client=off, learning=False)
    base = LiveLLMPredictor(_settings(), client=plain)
    for case in (first, second):
        kg_off.observe(case, kg_off.predict(case))
        base.predict(case)
    assert off.prompts == plain.prompts
    assert "Knowledge graph" not in "".join(off.prompts)


def test_on_arm_gets_context_from_earlier_cases_and_marks_reuse(two_cases: tuple[EvalCase, EvalCase]) -> None:
    first, second = two_cases
    fake = EchoMessages()
    cost = CostTracker(cap_usd=5.0)
    on = LiveLLMKGPredictor(_settings(), client=fake, cost=cost, learning=True)
    p1 = on.predict(first)
    on.observe(first, p1)
    p2 = on.predict(second)
    assert "Knowledge graph" not in fake.prompts[0]
    assert "Knowledge graph" in fake.prompts[1]
    assert p2.meta["kg_facts"] == 2  # two mapped facts offered (the no-source fact has no pair)
    assert {m.target for m in p2.mappings if m.origin == "kg_reused"} == {"name", "currency"}
    assert cost.calls == 2 and on.cost is cost


def test_curve_arms_share_one_cost_cap(two_cases: tuple[EvalCase, EvalCase]) -> None:
    cost = CostTracker(cap_usd=5.0)
    fake = EchoMessages()

    def make(learning: bool) -> LiveLLMKGPredictor:
        return LiveLLMKGPredictor(_settings(), client=fake, cost=cost, learning=learning)

    run = run_sequence(list(two_cases), make)
    assert cost.calls == 4
    assert run.edits_on[1] < run.edits_off[1]  # with the fake echo client, KG context removes the edits
