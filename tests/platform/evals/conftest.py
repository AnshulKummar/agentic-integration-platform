"""Eval-area fixtures (importlib mode: share helpers via fixtures, not imports)."""

from __future__ import annotations

from collections.abc import Callable, Sequence

import pytest

from app.platform.evals.dataset import CaseTags, EvalCase, EvalField, GoldMapping, ObjectRef
from app.platform.evals.predictors import CasePrediction, MappingPrediction

GoldSpec = tuple[str, str | None, str | None]
PredSpec = tuple[str, str | None, str | None, float, str]


def _make_case(
    gold: Sequence[GoldSpec],
    *,
    case_id: str = "c1",
    sources: Sequence[str] | None = None,
    deprecated: Sequence[str] = (),
    difficulty: str = "easy",
    sequence: str | None = None,
    step: int | None = None,
) -> EvalCase:
    src_paths = list(sources) if sources is not None else sorted({s for _, s, _ in gold if s})
    return EvalCase(
        id=case_id,
        suite="synthetic",
        source=ObjectRef(system="crm-x", kind="crm", object="Account"),
        target=ObjectRef(system="billing-y", kind="billing", object="Customer"),
        source_fields=tuple(EvalField(path=p, deprecated=p in deprecated) for p in src_paths),
        target_fields=tuple(EvalField(path=t) for t, _, _ in gold),
        gold=tuple(GoldMapping(target=t, source=s, transform=tr) for t, s, tr in gold),
        tags=CaseTags(difficulty=difficulty, category="customer", vendor_family="snake->snake"),  # type: ignore[arg-type]
        sequence=sequence,
        step=step,
    )


@pytest.fixture
def make_case() -> Callable[..., EvalCase]:
    """make_case([(target, source|None, transform|None), ...], case_id=..., sources=[...], deprecated=[...])"""
    return _make_case


@pytest.fixture
def make_prediction() -> Callable[..., CasePrediction]:
    """make_prediction(case_id, [(target, source, transform, confidence, origin), ...])"""

    def _make(case_id: str, rows: Sequence[PredSpec], predictor: str = "test") -> CasePrediction:
        return CasePrediction(case_id, predictor, [MappingPrediction(t, s, tr, c, o) for t, s, tr, c, o in rows])

    return _make


class FakeLearner:
    """Gets one more target right per observed case when learning (else always 1 right)."""

    def __init__(self, learning: bool) -> None:
        self.name = "fake"
        self.learning = learning
        self.seen = 0

    def predict(self, case: EvalCase) -> CasePrediction:
        right = 1 + (self.seen if self.learning else 0)
        rows = []
        for i, g in enumerate(case.gold):
            src = g.source if i < right else "nonexistent"
            rows.append(MappingPrediction(g.target, src, g.transform, 0.9, "kg_reused" if i < right else "llm_new"))
        return CasePrediction(case.id, self.name, rows)

    def observe(self, case: EvalCase, prediction: CasePrediction) -> None:
        self.seen += 1


@pytest.fixture
def fake_learner() -> type[FakeLearner]:
    return FakeLearner
