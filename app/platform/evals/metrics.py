"""Mapping-accuracy metrics.

Per target field the prediction is classified against gold:

=================  ==================  =====================  ==========================
gold \\ predicted   same source         other source           abstain (None)
=================  ==================  =====================  ==========================
source S            correct (TP)        wrong (FP + FN)        missing (FN)
None                -                   spurious (FP)          correct abstention
=================  ==================  =====================  ==========================

* precision = TP / (TP + wrong + spurious); recall = TP / (TP + wrong + missing);
  F1 is their harmonic mean (micro-averaged over (target, source) pairs).
* abstention accuracy = correct abstentions / gold-None targets.
* transform accuracy = correct transform among TP (None == direct copy).
* human edits = wrong + missing + spurious (what a reviewer must change on
  sources); ``transform_edits`` counts TP rows whose transform must change.
* exact case = no edits and no transform edits.
* reuse rate = emitted mappings with origin ``kg_reused`` / emitted mappings.
* calibration uses emitted (non-abstaining) mappings; "correct" = right source.
  ECE = sum_b (n_b / N) * |accuracy_b - mean confidence_b| over equal-width bins.
* bootstrap CIs resample *cases* with replacement (seeded ``random.Random``).
"""

from __future__ import annotations

import random
from collections.abc import Callable, Iterable, Sequence
from dataclasses import asdict, dataclass, field
from typing import Literal

from app.platform.evals.dataset import EvalCase
from app.platform.evals.predictors import CasePrediction

Outcome = Literal["correct", "wrong", "missing", "spurious", "correct_abstention"]
N_BINS = 10
N_BOOTSTRAP = 1000
CI_LEVEL = 0.95


@dataclass(frozen=True)
class MappingOutcome:
    target: str
    gold_source: str | None
    gold_transform: str | None
    predicted_source: str | None
    predicted_transform: str | None
    confidence: float
    origin: str
    outcome: Outcome
    #: Only for TP rows: was the transform right too?
    transform_ok: bool | None
    #: The predicted source is a deprecated source field.
    picked_deprecated: bool


@dataclass
class CaseScore:
    case_id: str
    suite: str
    difficulty: str
    category: str
    vendor_family: str
    tp: int = 0
    wrong: int = 0
    missing: int = 0
    spurious: int = 0
    correct_abstention: int = 0
    gold_none: int = 0
    transform_ok: int = 0
    transform_edits: int = 0
    emitted: int = 0
    reused: int = 0
    picked_deprecated: int = 0
    outcomes: list[MappingOutcome] = field(default_factory=list)

    @property
    def edits(self) -> int:
        return self.wrong + self.missing + self.spurious

    @property
    def exact(self) -> bool:
        return self.edits == 0 and self.transform_edits == 0

    def summary(self) -> dict[str, object]:
        return {
            "case_id": self.case_id, "suite": self.suite, "difficulty": self.difficulty,
            "category": self.category, "vendor_family": self.vendor_family, "tp": self.tp,
            "wrong": self.wrong, "missing": self.missing, "spurious": self.spurious,
            "correct_abstention": self.correct_abstention, "transform_edits": self.transform_edits,
            "edits": self.edits, "reused": self.reused, "emitted": self.emitted, "exact": self.exact,
        }


def score_case(case: EvalCase, prediction: CasePrediction) -> CaseScore:
    if prediction.case_id != case.id:
        raise ValueError(f"prediction for {prediction.case_id} scored against case {case.id}")
    score = CaseScore(case.id, case.suite, case.tags.difficulty, case.tags.category, case.tags.vendor_family)
    deprecated = {f.path for f in case.source_fields if f.deprecated}
    for g in case.gold:
        pred = prediction.for_target(g.target)
        p_src = pred.source if pred else None
        p_tr = pred.transform if pred else None
        conf = pred.confidence if pred else 0.0
        origin = pred.origin if pred else "none"
        transform_ok: bool | None = None
        if g.source is None:
            outcome: Outcome = "correct_abstention" if p_src is None else "spurious"
            score.gold_none += 1
        elif p_src is None:
            outcome = "missing"
        elif p_src == g.source:
            outcome = "correct"
            transform_ok = p_tr == g.transform
        else:
            outcome = "wrong"
        picked_dep = p_src is not None and p_src in deprecated
        score.outcomes.append(MappingOutcome(g.target, g.source, g.transform, p_src, p_tr, conf, origin, outcome,
                                             transform_ok, picked_dep))
        if outcome == "correct":
            score.tp += 1
            if transform_ok:
                score.transform_ok += 1
            else:
                score.transform_edits += 1
        elif outcome == "wrong":
            score.wrong += 1
        elif outcome == "missing":
            score.missing += 1
        elif outcome == "spurious":
            score.spurious += 1
        else:
            score.correct_abstention += 1
        if p_src is not None:
            score.emitted += 1
            score.reused += origin == "kg_reused"
            score.picked_deprecated += picked_dep
    return score


# ----------------------------------------------------------------- aggregates
def _ratio(num: float, den: float) -> float:
    return num / den if den else 0.0


@dataclass(frozen=True)
class Counts:
    tp: int
    wrong: int
    missing: int
    spurious: int
    correct_abstention: int
    gold_none: int
    transform_ok: int
    emitted: int
    reused: int
    edits: int
    transform_edits: int
    exact: int
    cases: int

    @classmethod
    def of(cls, scores: Sequence[CaseScore]) -> Counts:
        return cls(
            tp=sum(s.tp for s in scores), wrong=sum(s.wrong for s in scores), missing=sum(s.missing for s in scores),
            spurious=sum(s.spurious for s in scores),
            correct_abstention=sum(s.correct_abstention for s in scores), gold_none=sum(s.gold_none for s in scores),
            transform_ok=sum(s.transform_ok for s in scores), emitted=sum(s.emitted for s in scores),
            reused=sum(s.reused for s in scores), edits=sum(s.edits for s in scores),
            transform_edits=sum(s.transform_edits for s in scores), exact=sum(s.exact for s in scores),
            cases=len(scores),
        )

    @property
    def precision(self) -> float:
        return _ratio(self.tp, self.tp + self.wrong + self.spurious)

    @property
    def recall(self) -> float:
        return _ratio(self.tp, self.tp + self.wrong + self.missing)

    @property
    def f1(self) -> float:
        p, r = self.precision, self.recall
        return _ratio(2 * p * r, p + r)

    @property
    def edits_per_case(self) -> float:
        return _ratio(self.edits, self.cases)


def counts_metrics(c: Counts) -> dict[str, float | int]:
    return {
        "cases": c.cases,
        "targets": c.tp + c.wrong + c.missing + c.spurious + c.correct_abstention,
        "tp": c.tp, "wrong": c.wrong, "missing": c.missing, "spurious": c.spurious,
        "correct_abstention": c.correct_abstention, "gold_none": c.gold_none,
        "precision": round(c.precision, 4),
        "recall": round(c.recall, 4),
        "f1": round(c.f1, 4),
        "abstention_accuracy": round(_ratio(c.correct_abstention, c.gold_none), 4),
        "transform_accuracy": round(_ratio(c.transform_ok, c.tp), 4),
        "exact_case_accuracy": round(_ratio(c.exact, c.cases), 4),
        "edits_total": c.edits,
        "edits_per_case": round(c.edits_per_case, 4),
        "transform_edits_total": c.transform_edits,
        "edits_incl_transforms_per_case": round(_ratio(c.edits + c.transform_edits, c.cases), 4),
        "reuse_rate": round(_ratio(c.reused, c.emitted), 4),
    }


@dataclass(frozen=True)
class ReliabilityBin:
    lo: float
    hi: float
    count: int
    mean_confidence: float
    accuracy: float


def reliability(pairs: Iterable[tuple[float, bool]], n_bins: int = N_BINS) -> tuple[float, list[ReliabilityBin]]:
    """(ECE, reliability table) over (confidence, correct) pairs."""
    bins: list[list[tuple[float, bool]]] = [[] for _ in range(n_bins)]
    total = 0
    for conf, ok in pairs:
        c = min(1.0, max(0.0, conf))
        bins[min(n_bins - 1, int(c * n_bins))].append((c, ok))
        total += 1
    table: list[ReliabilityBin] = []
    ece = 0.0
    for i, items in enumerate(bins):
        lo, hi = i / n_bins, (i + 1) / n_bins
        if not items:
            table.append(ReliabilityBin(round(lo, 2), round(hi, 2), 0, 0.0, 0.0))
            continue
        mean_conf = sum(c for c, _ in items) / len(items)
        acc = sum(ok for _, ok in items) / len(items)
        ece += len(items) / total * abs(acc - mean_conf)
        table.append(ReliabilityBin(round(lo, 2), round(hi, 2), len(items), round(mean_conf, 4), round(acc, 4)))
    return round(ece, 4), table


def bootstrap_ci(values: Sequence[CaseScore], stat: Callable[[Sequence[CaseScore]], float], *,
                 seed: int, n: int = N_BOOTSTRAP, level: float = CI_LEVEL) -> tuple[float, float]:
    if not values:
        return (0.0, 0.0)
    rng = random.Random(seed)
    k = len(values)
    samples = sorted(stat([values[rng.randrange(k)] for _ in range(k)]) for _ in range(n))
    lo_i = int((1 - level) / 2 * n)
    hi_i = min(n - 1, int((1 + level) / 2 * n) - 1)
    return (round(samples[lo_i], 4), round(samples[hi_i], 4))


def percentile_ci(samples: list[float], level: float = CI_LEVEL) -> tuple[float, float]:
    if not samples:
        return (0.0, 0.0)
    s = sorted(samples)
    n = len(s)
    return (round(s[int((1 - level) / 2 * n)], 4), round(s[min(n - 1, int((1 + level) / 2 * n) - 1)], 4))


def _breakdown(scores: Sequence[CaseScore], key: Callable[[CaseScore], str]) -> dict[str, dict[str, float | int]]:
    groups: dict[str, list[CaseScore]] = {}
    for s in scores:
        groups.setdefault(key(s), []).append(s)
    return {k: counts_metrics(Counts.of(v)) for k, v in sorted(groups.items())}


def aggregate(scores: Sequence[CaseScore], *, seed: int = 0, n_bootstrap: int = N_BOOTSTRAP) -> dict[str, object]:
    counts = Counts.of(scores)
    out: dict[str, object] = dict(counts_metrics(counts))
    pairs = [(o.confidence, o.outcome == "correct") for s in scores for o in s.outcomes
             if o.predicted_source is not None]
    ece, table = reliability(pairs)
    out["ece"] = ece
    out["reliability"] = [asdict(b) for b in table]
    out["picked_deprecated"] = sum(s.picked_deprecated for s in scores)
    out["ci95"] = {
        "precision": bootstrap_ci(scores, lambda xs: Counts.of(xs).precision, seed=seed, n=n_bootstrap),
        "recall": bootstrap_ci(scores, lambda xs: Counts.of(xs).recall, seed=seed + 1, n=n_bootstrap),
        "f1": bootstrap_ci(scores, lambda xs: Counts.of(xs).f1, seed=seed + 2, n=n_bootstrap),
        "edits_per_case": bootstrap_ci(scores, lambda xs: Counts.of(xs).edits_per_case, seed=seed + 3,
                                       n=n_bootstrap),
    }
    out["by_difficulty"] = _breakdown(scores, lambda s: s.difficulty)
    out["by_category"] = _breakdown(scores, lambda s: s.category)
    out["by_vendor_family"] = _breakdown(scores, lambda s: s.vendor_family)
    out["by_origin"] = _origin_breakdown(scores)
    return out


def _origin_breakdown(scores: Sequence[CaseScore]) -> dict[str, dict[str, float | int]]:
    groups: dict[str, list[MappingOutcome]] = {}
    for s in scores:
        for o in s.outcomes:
            if o.predicted_source is not None:
                groups.setdefault(o.origin, []).append(o)
    return {
        k: {"emitted": len(v), "correct": sum(o.outcome == "correct" for o in v),
            "precision": round(_ratio(sum(o.outcome == "correct" for o in v), len(v)), 4)}
        for k, v in sorted(groups.items())
    }
