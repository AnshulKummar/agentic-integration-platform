"""Learning curve: the compound-advantage test.

For each ordered sequence of related integrations, the same predictor runs twice
on a fresh state: learning ON and learning OFF. After every step the gold
mappings are fed back (``observe``) - that is the human review the KG learns
from. We record human edits per integration and compare.

Reported (diff = OFF - ON, positive means learning saved edits):

* per step: mean edits ON/OFF across sequences, mean diff with bootstrap CI
  (sequences resampled), mean cumulative edits;
* total: mean per-sequence cumulative edits ON/OFF, diff with CI, relative reduction;
* compounding: OLS slope of the per-step diff against step index (with CI),
  and mean diff over the first vs second half of the steps. A compound
  advantage needs a positive diff that *grows* with steps (slope CI > 0).

Step 1 is a built-in sanity check: nothing has been learned yet, so ON and OFF
must agree there.
"""

from __future__ import annotations

import random
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field

from app.platform.evals.dataset import EvalCase
from app.platform.evals.metrics import CI_LEVEL, N_BOOTSTRAP, Counts, percentile_ci, score_case
from app.platform.evals.predictors import Predictor

PredictorFactory = Callable[[bool], Predictor]


@dataclass
class SequenceRun:
    sequence: str
    case_ids: list[str]
    edits_on: list[int] = field(default_factory=list)
    edits_off: list[int] = field(default_factory=list)
    f1_on: list[float] = field(default_factory=list)
    f1_off: list[float] = field(default_factory=list)
    reuse_on: list[float] = field(default_factory=list)
    transform_edits_on: list[int] = field(default_factory=list)
    transform_edits_off: list[int] = field(default_factory=list)

    def as_dict(self) -> dict[str, object]:
        return {
            "sequence": self.sequence, "cases": self.case_ids, "edits_on": self.edits_on,
            "edits_off": self.edits_off, "f1_on": self.f1_on, "f1_off": self.f1_off, "reuse_on": self.reuse_on,
            "transform_edits_on": self.transform_edits_on, "transform_edits_off": self.transform_edits_off,
        }


def run_sequence(cases: Sequence[EvalCase], make: PredictorFactory) -> SequenceRun:
    run = SequenceRun(sequence=cases[0].sequence or cases[0].id, case_ids=[c.id for c in cases])
    for learning in (True, False):
        predictor = make(learning)
        for case in cases:
            prediction = predictor.predict(case)
            score = score_case(case, prediction)
            predictor.observe(case, prediction)
            counts = Counts.of([score])
            if learning:
                run.edits_on.append(score.edits)
                run.f1_on.append(round(counts.f1, 4))
                run.reuse_on.append(round(score.reused / score.emitted, 4) if score.emitted else 0.0)
                run.transform_edits_on.append(score.transform_edits)
            else:
                run.edits_off.append(score.edits)
                run.f1_off.append(round(counts.f1, 4))
                run.transform_edits_off.append(score.transform_edits)
    return run


def _mean(xs: Sequence[float]) -> float:
    return sum(xs) / len(xs) if xs else 0.0


def _slope(points: Sequence[tuple[float, float]]) -> float:
    if len(points) < 2:
        return 0.0
    mx = _mean([x for x, _ in points])
    my = _mean([y for _, y in points])
    den = sum((x - mx) ** 2 for x, _ in points)
    return sum((x - mx) * (y - my) for x, y in points) / den if den else 0.0


def _diff_points(runs: Sequence[SequenceRun]) -> list[tuple[float, float]]:
    return [(float(i + 1), float(off - on)) for r in runs for i, (on, off) in enumerate(zip(r.edits_on, r.edits_off,
                                                                                            strict=True))]


def summarize(runs: Sequence[SequenceRun], *, seed: int, n_bootstrap: int = N_BOOTSTRAP) -> dict[str, object]:
    rng = random.Random(seed)
    k = len(runs)
    resamples = [[runs[rng.randrange(k)] for _ in range(k)] for _ in range(n_bootstrap)] if k else []
    max_len = max((len(r.case_ids) for r in runs), default=0)

    by_step: list[dict[str, object]] = []
    for i in range(max_len):
        present = [r for r in runs if len(r.edits_on) > i]
        on = [float(r.edits_on[i]) for r in present]
        off = [float(r.edits_off[i]) for r in present]
        cum_on = [float(sum(r.edits_on[: i + 1])) for r in present]
        cum_off = [float(sum(r.edits_off[: i + 1])) for r in present]
        boot = [_mean([float(r.edits_off[i] - r.edits_on[i]) for r in s if len(r.edits_on) > i]) for s in resamples]
        by_step.append({
            "step": i + 1, "n": len(present),
            "mean_edits_on": round(_mean(on), 4), "mean_edits_off": round(_mean(off), 4),
            "mean_diff": round(_mean(off) - _mean(on), 4), "diff_ci95": percentile_ci(boot, CI_LEVEL),
            "mean_cum_on": round(_mean(cum_on), 4), "mean_cum_off": round(_mean(cum_off), 4),
            "mean_f1_on": round(_mean([r.f1_on[i] for r in present]), 4),
            "mean_f1_off": round(_mean([r.f1_off[i] for r in present]), 4),
            "mean_reuse_on": round(_mean([r.reuse_on[i] for r in present]), 4),
        })

    total_on = [float(sum(r.edits_on)) for r in runs]
    total_off = [float(sum(r.edits_off)) for r in runs]
    total_boot = [_mean([float(sum(r.edits_off) - sum(r.edits_on)) for r in s]) for s in resamples]
    slope_boot = [_slope(_diff_points(s)) for s in resamples]
    half = max_len // 2
    early = [float(off - on) for r in runs for i, (on, off) in enumerate(zip(r.edits_on, r.edits_off, strict=True))
             if i < half]
    late = [float(off - on) for r in runs for i, (on, off) in enumerate(zip(r.edits_on, r.edits_off, strict=True))
            if i >= half]
    sum_off = sum(total_off)
    return {
        "sequences": k,
        "steps": max_len,
        "by_step": by_step,
        "total": {
            "mean_cum_edits_on": round(_mean(total_on), 4),
            "mean_cum_edits_off": round(_mean(total_off), 4),
            "mean_diff": round(_mean(total_off) - _mean(total_on), 4),
            "diff_ci95": percentile_ci(total_boot, CI_LEVEL),
            "relative_reduction": round((sum_off - sum(total_on)) / sum_off, 4) if sum_off else 0.0,
        },
        "compounding": {
            "diff_slope_per_step": round(_slope(_diff_points(runs)), 4),
            "slope_ci95": percentile_ci(slope_boot, CI_LEVEL),
            "mean_diff_first_half": round(_mean(early), 4),
            "mean_diff_second_half": round(_mean(late), 4),
        },
        "step1_identical": all(r.edits_on[0] == r.edits_off[0] for r in runs if r.edits_on),
    }


def run_curve(sequences: Sequence[Sequence[EvalCase]], make: PredictorFactory, *, seed: int,
              n_bootstrap: int = N_BOOTSTRAP) -> dict[str, object]:
    runs = [run_sequence(seq, make) for seq in sequences if seq]
    out = summarize(runs, seed=seed, n_bootstrap=n_bootstrap)
    out["runs"] = [r.as_dict() for r in runs]
    return out
