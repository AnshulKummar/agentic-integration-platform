"""Learning-curve shape (fake predictor), the CI gate and the committed baseline."""

from __future__ import annotations

import copy
import json
from pathlib import Path

from app.platform.evals.cli import main
from app.platform.evals.learning_curve import run_curve
from app.platform.evals.runner import BASELINE_PATH, gate, load_json, make_baseline, run_eval


def _sequences(make_case, n_seq: int = 3, length: int = 4):  # type: ignore[no-untyped-def]
    gold = [(f"t{i}", f"s{i}", None) for i in range(6)]
    return [[make_case(gold, case_id=f"q{q}-{s}", sequence=f"q{q}", step=s + 1) for s in range(length)]
            for q in range(n_seq)]


def test_curve_shape_with_fake_learner(make_case, fake_learner) -> None:  # type: ignore[no-untyped-def]
    out = run_curve(_sequences(make_case), fake_learner, seed=3, n_bootstrap=200)
    steps = out["by_step"]
    assert [s["step"] for s in steps] == [1, 2, 3, 4]
    assert out["step1_identical"] is True
    assert steps[0]["mean_diff"] == 0.0
    # Learner gets one more target right per observed case: edits ON 5,4,3,2 vs OFF 5,5,5,5.
    assert [s["mean_edits_on"] for s in steps] == [5.0, 4.0, 3.0, 2.0]
    assert [s["mean_edits_off"] for s in steps] == [5.0, 5.0, 5.0, 5.0]
    assert [s["mean_diff"] for s in steps] == [0.0, 1.0, 2.0, 3.0]
    assert steps[-1]["mean_cum_on"] == 14.0 and steps[-1]["mean_cum_off"] == 20.0
    assert out["total"]["mean_diff"] == 6.0
    assert out["total"]["relative_reduction"] == 0.3
    assert out["compounding"]["diff_slope_per_step"] == 1.0
    assert out["compounding"]["slope_ci95"] == (1.0, 1.0)
    assert steps[-1]["mean_reuse_on"] > steps[0]["mean_reuse_on"]


def test_curve_without_learning_shows_no_difference(make_case, fake_learner) -> None:  # type: ignore[no-untyped-def]
    out = run_curve(_sequences(make_case), lambda learning: fake_learner(False), seed=1, n_bootstrap=50)
    assert out["total"]["mean_diff"] == 0.0 and out["compounding"]["diff_slope_per_step"] == 0.0


def _report(f1: float, edits: float) -> dict:  # type: ignore[type-arg]
    return {"suite": "all", "predictor": "deterministic", "seed": 1, "created_at": "x",
            "results": {"synthetic": {"metrics": {"f1": f1, "precision": f1, "recall": f1, "edits_per_case": edits,
                                                  "abstention_accuracy": 0.5, "transform_accuracy": 0.5}}}}


def test_gate_pass_and_fail() -> None:
    baseline = make_baseline(_report(0.50, 4.0), source="test")
    assert gate(_report(0.50, 4.0), baseline) == []
    assert gate(_report(0.485, 4.2), baseline) == []  # within tolerance
    failures = gate(_report(0.47, 4.0), baseline)
    assert len(failures) == 1 and "F1" in failures[0]
    failures = gate(_report(0.50, 4.3), baseline)
    assert len(failures) == 1 and "edits/case" in failures[0]
    missing = copy.deepcopy(_report(0.5, 4.0))
    missing["results"] = {}
    assert gate(missing, baseline) == ["synthetic: missing from current run"]


def test_committed_baseline_reproduces_exactly() -> None:
    baseline = load_json(BASELINE_PATH)
    assert baseline["predictor"] == "deterministic" and baseline["suite"] == "all"
    current = run_eval(baseline["suite"], baseline["predictor"], seed=baseline["seed"])
    assert gate(current, baseline) == []
    for name, base in baseline["metrics"].items():
        assert current["results"][name]["metrics"]["f1"] == base["f1"]
        assert current["results"][name]["metrics"]["edits_per_case"] == base["edits_per_case"]


def test_cli_gate_exit_codes(tmp_path: Path, capsys) -> None:  # type: ignore[no-untyped-def]
    baseline = load_json(BASELINE_PATH)
    current = {"results": {name: {"metrics": dict(m)} for name, m in baseline["metrics"].items()}}
    good = tmp_path / "good.json"
    good.write_text(json.dumps(current))
    assert main(["gate", "--baseline", str(BASELINE_PATH), "--current", str(good)]) == 0
    current["results"]["handlabeled"]["metrics"]["f1"] -= 0.05
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps(current))
    assert main(["gate", "--baseline", str(BASELINE_PATH), "--current", str(bad)]) == 1
    assert "GATE FAILED" in capsys.readouterr().out
