"""Run suites through a predictor, build reports, markdown and the CI gate.

Synthetic and hand-labeled results are always computed and reported
separately; there is no blended headline number.
"""

from __future__ import annotations

import json
import logging
import subprocess
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from app.platform.config import PlatformSettings
from app.platform.evals.dataset import EvalCase, Suite, dataset_stats, group_sequences, load_handlabeled
from app.platform.evals.learning_curve import run_curve
from app.platform.evals.metrics import CaseScore, aggregate, score_case
from app.platform.evals.predictors import (
    CostCapReached,
    CostTracker,
    LiveLLMKGPredictor,
    LiveLLMPredictor,
    Predictor,
    make_predictor,
)
from app.platform.evals.synthetic import DEFAULT_SEED, generate_sequences, generate_suite

log = logging.getLogger(__name__)

SCHEMA_VERSION = 1
REPO_ROOT = Path(__file__).resolve().parents[3]
BASELINE_PATH = Path(__file__).parent / "baseline.json"
SuiteArg = Literal["synthetic", "handlabeled", "all"]


def load_suites(suite: SuiteArg, seed: int = DEFAULT_SEED) -> dict[Suite, list[EvalCase]]:
    out: dict[Suite, list[EvalCase]] = {}
    if suite in ("synthetic", "all"):
        out["synthetic"] = generate_suite(seed)
    if suite in ("handlabeled", "all"):
        out["handlabeled"] = load_handlabeled()
    if not out:
        raise ValueError(f"unknown suite {suite!r}")
    return out


def git_sha(cwd: Path = REPO_ROOT) -> str | None:
    try:
        proc = subprocess.run(["git", "rev-parse", "HEAD"], cwd=cwd, capture_output=True, text=True, timeout=10,
                              check=True)
    except (OSError, subprocess.SubprocessError) as exc:
        log.info("evals.git_sha_unavailable: %s", exc)
        return None
    return proc.stdout.strip() or None


@dataclass
class SuiteRun:
    scores: list[CaseScore]
    stopped_early: str | None = None


def run_cases(cases: Sequence[EvalCase], predictor: Predictor) -> SuiteRun:
    scores: list[CaseScore] = []
    for case in cases:
        try:
            prediction = predictor.predict(case)
        except CostCapReached as exc:
            log.warning("evals.cost_cap_reached: %s", exc)
            return SuiteRun(scores, stopped_early=f"cost cap reached after {len(scores)} cases: {exc}")
        scores.append(score_case(case, prediction))
        predictor.observe(case, prediction)
    return SuiteRun(scores)


def run_eval(suite: SuiteArg, predictor_name: str, *, seed: int = DEFAULT_SEED,
             settings: PlatformSettings | None = None, family_retrieval: bool = True,
             predictor_factory: Callable[[], Predictor] | None = None) -> dict[str, Any]:
    """Run ``suite`` through a predictor and return the JSON report.

    Stateful predictors (kg_learning) get a fresh instance per suite so the
    synthetic stream never teaches the hand-labeled one. live_llm shares one
    instance (one cost cap) and stops cleanly when the cap would be exceeded.
    """
    suites = load_suites(suite, seed)
    shared: Predictor | None = None
    if predictor_name == "live_llm" and predictor_factory is None:
        shared = make_predictor("live_llm", settings)
    cost: CostTracker | None = None
    live_kg: list[LiveLLMKGPredictor] = []
    if predictor_name == "live_llm_kg" and predictor_factory is None:
        # A fresh KG per suite (synthetic never teaches hand-labeled), one cost cap for the run.
        live_settings = settings or PlatformSettings.from_env()
        cost = CostTracker(cap_usd=live_settings.eval_cost_cap_usd)

        def predictor_factory() -> Predictor:
            p = LiveLLMKGPredictor(live_settings, cost=cost, learning=True)
            live_kg.append(p)
            return p
    report: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "kind": "run",
        "suite": suite,
        "predictor": predictor_name,
        "seed": seed,
        "options": {"family_retrieval": family_retrieval},
        "git_sha": git_sha(),
        "created_at": datetime.now(UTC).isoformat(),
        "results": {},
        "stopped_early": None,
    }
    for name, cases in suites.items():
        if shared is not None:
            predictor = shared
        elif predictor_factory is not None:
            predictor = predictor_factory()
        else:
            predictor = make_predictor(predictor_name, settings, family_retrieval=family_retrieval)
        run = run_cases(cases, predictor)
        report["results"][name] = {
            "dataset": dataset_stats(cases),
            "scored_cases": len(run.scores),
            "metrics": aggregate(run.scores, seed=seed),
            "cases": [s.summary() for s in run.scores],
        }
        if run.stopped_early:
            report["stopped_early"] = f"{name}: {run.stopped_early}"
            break
    if isinstance(shared, LiveLLMPredictor):
        report["llm"] = _llm_usage(shared.model, shared.cost)
    elif cost is not None and live_kg:
        report["llm"] = _llm_usage(live_kg[0].model, cost)
    return report


def _llm_usage(model: str, c: CostTracker) -> dict[str, Any]:
    return {"model": model, "calls": c.calls, "input_tokens": c.input_tokens, "output_tokens": c.output_tokens,
            "cost_usd": round(c.spent_usd, 4), "cost_cap_usd": c.cap_usd}


def curve_sequences(suite: SuiteArg, seed: int, n_sequences: int, length: int) -> dict[Suite, list[list[EvalCase]]]:
    out: dict[Suite, list[list[EvalCase]]] = {}
    if suite in ("synthetic", "all"):
        out["synthetic"] = generate_sequences(seed, n_sequences=n_sequences, length=length)
    if suite in ("handlabeled", "all"):
        out["handlabeled"] = list(group_sequences(load_handlabeled()).values())
    return out


def run_learning_curve(suite: SuiteArg, *, seed: int = DEFAULT_SEED, n_sequences: int = 8, length: int = 6,
                       family_retrieval: bool = True, predictor: str = "kg_learning",
                       settings: PlatformSettings | None = None) -> dict[str, Any]:
    """Learning ON vs OFF over sequences of related cases.

    ``predictor="live_llm_kg"``: Claude with vs without the KG of reviewed mappings
    (OFF = an empty KG = plain live_llm prompt); one cost cap for the whole curve.
    """
    cost: CostTracker | None = None
    live_settings: PlatformSettings | None = None
    if predictor == "live_llm_kg":
        live_settings = settings or PlatformSettings.from_env()
        cost = CostTracker(cap_usd=live_settings.eval_cost_cap_usd)
    elif predictor != "kg_learning":
        raise ValueError(f"curve supports kg_learning or live_llm_kg, not {predictor!r}")

    def make(learning: bool) -> Predictor:
        if live_settings is not None:
            return LiveLLMKGPredictor(live_settings, cost=cost, learning=learning)
        return make_predictor("kg_learning", learning=learning, family_retrieval=family_retrieval)

    report: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION, "kind": "curve", "suite": suite, "predictor": predictor,
        "seed": seed, "options": {"family_retrieval": family_retrieval, "n_sequences": n_sequences,
                                  "length": length},
        "git_sha": git_sha(), "created_at": datetime.now(UTC).isoformat(), "results": {},
    }
    for name, seqs in curve_sequences(suite, seed, n_sequences, length).items():
        try:
            report["results"][name] = run_curve(seqs, make, seed=seed)
        except CostCapReached as exc:
            log.warning("evals.cost_cap_reached: %s", exc)
            report["stopped_early"] = f"{name}: cost cap reached: {exc}"
            break
        if name == "synthetic" and predictor == "kg_learning":
            # The two regimes differ by construction: patterns are keyed on target labels, which recur only
            # when the target API is the fixed hub.
            for regime in ("hub_target", "hub_source"):
                subset = [s for s in seqs if s and regime in (s[0].sequence or "")]
                if subset:
                    report["results"][f"synthetic/{regime}"] = run_curve(subset, make, seed=seed)
    if cost is not None and live_settings is not None:
        report["llm"] = _llm_usage(live_settings.llm_model, cost)
    return report


# ------------------------------------------------------------------ markdown
def _pct(x: object) -> str:
    return f"{float(x) * 100:.1f}%" if isinstance(x, int | float) else "-"


def _ci(ci: object, pct: bool = True) -> str:
    if isinstance(ci, list | tuple) and len(ci) == 2:
        lo, hi = float(ci[0]), float(ci[1])
        return f"[{lo * 100:.1f}, {hi * 100:.1f}]" if pct else f"[{lo:.2f}, {hi:.2f}]"
    return "-"


def run_markdown(report: dict[str, Any]) -> str:
    lines = [f"## Eval run: predictor `{report['predictor']}`, suite `{report['suite']}`, seed {report['seed']}", ""]
    if report.get("stopped_early"):
        lines += [f"**Stopped early:** {report['stopped_early']}", ""]
    lines += [
        "| suite | cases | targets | precision | recall | F1 (95% CI) | abstain acc | transform acc | exact cases "
        "| edits/case (95% CI) | reuse | ECE |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for name, res in report["results"].items():
        m = res["metrics"]
        lines.append(
            f"| {name} | {m['cases']} | {m['targets']} | {_pct(m['precision'])} | {_pct(m['recall'])} | "
            f"{_pct(m['f1'])} {_ci(m['ci95']['f1'])} | {_pct(m['abstention_accuracy'])} | "
            f"{_pct(m['transform_accuracy'])} | {_pct(m['exact_case_accuracy'])} | {m['edits_per_case']:.2f} "
            f"{_ci(m['ci95']['edits_per_case'], pct=False)} | {_pct(m['reuse_rate'])} | {m['ece']:.3f} |"
        )
    for name, res in report["results"].items():
        m = res["metrics"]
        lines += ["", f"### {name}: by difficulty", "", "| difficulty | cases | F1 | edits/case | exact |",
                  "|---|---|---|---|---|"]
        for diff, dm in m["by_difficulty"].items():
            lines.append(f"| {diff} | {dm['cases']} | {_pct(dm['f1'])} | {dm['edits_per_case']:.2f} | "
                         f"{_pct(dm['exact_case_accuracy'])} |")
        lines += ["", f"### {name}: by category", "", "| category | cases | F1 | edits/case |", "|---|---|---|---|"]
        for cat, cm in m["by_category"].items():
            lines.append(f"| {cat} | {cm['cases']} | {_pct(cm['f1'])} | {cm['edits_per_case']:.2f} |")
        lines += ["", f"### {name}: reliability (emitted mappings)", "", "| confidence | n | mean conf | accuracy |",
                  "|---|---|---|---|"]
        for b in m["reliability"]:
            if b["count"]:
                lines.append(f"| {b['lo']:.1f}-{b['hi']:.1f} | {b['count']} | {b['mean_confidence']:.2f} | "
                             f"{_pct(b['accuracy'])} |")
    if report.get("llm"):
        llm = report["llm"]
        lines += ["", f"LLM: {llm['model']}, {llm['calls']} calls, {llm['input_tokens']} in / "
                  f"{llm['output_tokens']} out tokens, ${llm['cost_usd']:.4f} of ${llm['cost_cap_usd']:.2f} cap"]
    return "\n".join(lines) + "\n"


def curve_markdown(report: dict[str, Any]) -> str:
    opts = report["options"]
    lines = [f"## Learning curve (kg_learning ON vs OFF), seed {report['seed']}, family_retrieval="
             f"{opts['family_retrieval']}", ""]
    for name, res in report["results"].items():
        t, comp = res["total"], res["compounding"]
        lines += [
            f"### {name}: {res['sequences']} sequences x up to {res['steps']} steps", "",
            "| step | n | edits ON | edits OFF | diff OFF-ON (95% CI) | cum ON | cum OFF | F1 ON | F1 OFF | reuse ON |",
            "|---|---|---|---|---|---|---|---|---|---|",
        ]
        for s in res["by_step"]:
            lines.append(
                f"| {s['step']} | {s['n']} | {s['mean_edits_on']:.2f} | {s['mean_edits_off']:.2f} | "
                f"{s['mean_diff']:+.2f} {_ci(s['diff_ci95'], pct=False)} | {s['mean_cum_on']:.2f} | "
                f"{s['mean_cum_off']:.2f} | {_pct(s['mean_f1_on'])} | {_pct(s['mean_f1_off'])} | "
                f"{_pct(s['mean_reuse_on'])} |"
            )
        lines += [
            "",
            f"Cumulative edits per sequence: ON {t['mean_cum_edits_on']:.2f} vs OFF {t['mean_cum_edits_off']:.2f}; "
            f"diff {t['mean_diff']:+.2f} {_ci(t['diff_ci95'], pct=False)} "
            f"({_pct(t['relative_reduction'])} fewer edits with learning).",
            f"Compounding: diff slope {comp['diff_slope_per_step']:+.3f} edits/step "
            f"{_ci(comp['slope_ci95'], pct=False)}; mean diff first half {comp['mean_diff_first_half']:+.2f}, "
            f"second half {comp['mean_diff_second_half']:+.2f}. Step 1 identical: {res['step1_identical']}.",
            "",
        ]
    return "\n".join(lines)


# ------------------------------------------------------------------ gate
DEFAULT_TOLERANCE = {"f1_drop_abs": 0.02, "edits_per_case_rise_abs": 0.25}


def make_baseline(report: dict[str, Any], *, source: str,
                  tolerance: dict[str, float] | None = None) -> dict[str, Any]:
    tol = dict(tolerance or DEFAULT_TOLERANCE)
    return {
        "schema_version": SCHEMA_VERSION,
        "suite": report["suite"],
        "predictor": report["predictor"],
        "seed": report["seed"],
        "options": report.get("options", {}),
        "git_sha": report.get("git_sha"),
        "created_at": report["created_at"],
        "generated_from": source,
        "tolerance": {
            **tol,
            "note": "The deterministic predictor is exactly reproducible for a given seed, so any change is a code "
                    "or data change. A run fails the gate when, for any suite, F1 drops by more than f1_drop_abs "
                    "(absolute, 0-1 scale) or mean human edits per case rise by more than edits_per_case_rise_abs. "
                    "0.02 F1 is ~1.4 mapping decisions on the hand-labeled set and ~0.25 edits/case is ~18 edits "
                    "on the synthetic suite: small enough to catch real regressions, loose enough to allow "
                    "deliberate trade-offs that are recorded by regenerating this file.",
        },
        "metrics": {
            name: {k: res["metrics"][k] for k in ("f1", "precision", "recall", "edits_per_case",
                                                  "abstention_accuracy", "transform_accuracy")}
            for name, res in report["results"].items()
        },
    }


def gate(current: dict[str, Any], baseline: dict[str, Any]) -> list[str]:
    """Regressions of ``current`` against ``baseline`` (empty = pass)."""
    tol = baseline["tolerance"]
    failures: list[str] = []
    for name, base in baseline["metrics"].items():
        res = current["results"].get(name)
        if res is None:
            failures.append(f"{name}: missing from current run")
            continue
        m = res["metrics"]
        if m["f1"] < base["f1"] - tol["f1_drop_abs"]:
            failures.append(f"{name}: F1 {m['f1']:.4f} < baseline {base['f1']:.4f} - {tol['f1_drop_abs']}")
        if m["edits_per_case"] > base["edits_per_case"] + tol["edits_per_case_rise_abs"]:
            failures.append(f"{name}: edits/case {m['edits_per_case']:.4f} > baseline {base['edits_per_case']:.4f} "
                            f"+ {tol['edits_per_case_rise_abs']}")
    return failures


def load_json(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as fh:
        data: dict[str, Any] = json.load(fh)
    return data


def write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as fh:
        json.dump(data, fh, indent=2, sort_keys=False)
        fh.write("\n")
