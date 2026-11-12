"""Predictor smoke tests, KG learning feedback, LLM cost accounting (fake client) and a live test."""

from __future__ import annotations

import json
import os
from types import SimpleNamespace
from typing import Any

import pytest

from app.platform.config import PlatformSettings
from app.platform.evals.adapter import normalize_transform
from app.platform.evals.dataset import load_handlabeled
from app.platform.evals.metrics import score_case
from app.platform.evals.predictors import (
    CostCapReached,
    DeterministicPredictor,
    KGLearningPredictor,
    LiveLLMPredictor,
    make_predictor,
)
from app.platform.evals.runner import run_cases
from app.platform.observability.metrics import LLM_COST, REGISTRY


def _case(case_id: str = "hl-sf-account-to-st-customer"):  # type: ignore[no-untyped-def]
    return next(c for c in load_handlabeled() if c.id == case_id)


def test_deterministic_smoke_one_case() -> None:
    case = _case()
    p1 = DeterministicPredictor().predict(case)
    p2 = DeterministicPredictor().predict(case)
    assert p1 == p2  # deterministic
    assert {m.target for m in p1.mappings} == {f.path for f in case.target_fields}
    assert all(0.0 <= m.confidence <= 1.0 for m in p1.mappings)
    sources = {f.path for f in case.source_fields}
    assert all(m.source is None or m.source in sources for m in p1.mappings)
    # Exact-name matches must be found.
    assert p1.for_target("phone").source == "Phone"  # type: ignore[union-attr]


def test_kg_learning_reuses_after_review() -> None:
    case = _case()
    predictor = KGLearningPredictor(learning=True)
    first = predictor.predict(case)
    predictor.observe(case, first)
    second = predictor.predict(case)
    s1, s2 = score_case(case, first), score_case(case, second)
    assert s2.reused > 0 and s1.reused == 0
    assert s2.edits < s1.edits


def test_kg_learning_off_learns_nothing() -> None:
    case = _case()
    predictor = KGLearningPredictor(learning=False)
    first = predictor.predict(case)
    predictor.observe(case, first)
    second = predictor.predict(case)
    assert score_case(case, second).reused == 0
    assert [m.source for m in first.mappings] == [m.source for m in second.mappings]


def test_observe_without_predict_fails() -> None:
    with pytest.raises(ValueError):
        KGLearningPredictor().observe(_case(), DeterministicPredictor().predict(_case()))


def test_normalize_transform() -> None:
    assert normalize_transform("copy") is None
    assert normalize_transform("match first signer") == "first_item"
    assert normalize_transform("enum_map") == "enum_map"
    assert normalize_transform("+ tax") == "other"
    assert normalize_transform(None) is None


def test_unknown_predictor_rejected() -> None:
    with pytest.raises(ValueError):
        make_predictor("oracle")


# ------------------------------------------------------------------ live LLM (fake client)
class FakeMessages:
    def __init__(self, payload: dict[str, Any], usage: tuple[int, int] = (1000, 500)) -> None:
        self.payload, self.usage, self.calls = payload, usage, 0

    def create(self, **kwargs: Any) -> Any:
        self.calls += 1
        assert kwargs["output_config"]["format"]["type"] == "json_schema"
        return SimpleNamespace(
            model=kwargs["model"], stop_reason="end_turn",
            usage=SimpleNamespace(input_tokens=self.usage[0], output_tokens=self.usage[1],
                                  cache_read_input_tokens=0, cache_creation_input_tokens=0),
            content=[SimpleNamespace(type="text", text=json.dumps(self.payload))],
        )


def _settings(cap: float) -> PlatformSettings:
    return PlatformSettings(env="test", eval_cost_cap_usd=cap, llm_model="claude-opus-5-5")


def test_live_llm_parses_and_accounts_cost() -> None:
    case = _case()
    payload = {"mappings": [{"target": "name", "source": "Name", "transform": None, "confidence": 0.9},
                            {"target": "id", "source": None, "transform": None, "confidence": 0.8},
                            {"target": "bogus", "source": "Name", "transform": None, "confidence": 0.9}]}
    before = REGISTRY.get_sample_value("pmaip_llm_cost_usd_total", {"model": "claude-opus-5-5"}) or 0.0
    predictor = LiveLLMPredictor(_settings(5.0), client=FakeMessages(payload))
    pred = predictor.predict(case)
    assert pred.for_target("name").source == "Name"  # type: ignore[union-attr]
    assert pred.for_target("id").source is None  # type: ignore[union-attr]
    assert pred.for_target("email").source is None  # skipped target -> abstain  # type: ignore[union-attr]
    expected = (1000 * 4.0 + 500 * 20.0) / 1e6
    assert predictor.cost.spent_usd == pytest.approx(expected)
    after = REGISTRY.get_sample_value("pmaip_llm_cost_usd_total", {"model": "claude-opus-5-5"})
    assert after == pytest.approx(before + expected)
    assert LLM_COST is not None


def test_live_llm_hard_stops_at_cost_cap() -> None:
    cases = load_handlabeled()[:5]
    fake = FakeMessages({"mappings": []}, usage=(1000, 3000))  # $0.064 per call
    # Worst case per call ~ 8000 output tokens * $20/M = $0.16 + input (~$0.17): a $0.20 cap allows one call,
    # because a second could take spend to ~$0.23.
    predictor = LiveLLMPredictor(_settings(0.20), client=fake)
    run = run_cases(cases, predictor)
    assert fake.calls == 1 and len(run.scores) == 1
    assert run.stopped_early is not None and "cost cap" in run.stopped_early
    assert predictor.cost.spent_usd <= 0.20
    with pytest.raises(CostCapReached):
        predictor.cost.check("claude-opus-5-5", 10_000, 8000)


def test_live_llm_requires_key_without_client() -> None:
    with pytest.raises(ValueError, match="ANTHROPIC_API_KEY"):
        LiveLLMPredictor(_settings(1.0))


@pytest.mark.live_llm
def test_live_llm_real_call() -> None:
    key = os.environ.get("LIVE_ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        pytest.skip("needs ANTHROPIC_API_KEY (the autouse fixture clears it; set LIVE_ANTHROPIC_API_KEY)")
    settings = PlatformSettings(env="test", anthropic_api_key=key, eval_cost_cap_usd=0.5)  # type: ignore[arg-type]
    predictor = LiveLLMPredictor(settings)
    case = _case()
    score = score_case(case, predictor.predict(case))
    assert predictor.cost.calls == 1 and predictor.cost.spent_usd <= 0.5
    assert score.tp > 0
